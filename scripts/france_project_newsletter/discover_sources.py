#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Generate News source declaration candidates and write them to pending_sources.
Each candidate is reviewed via the Telegram bot before being written to companies.json
or feeds.sources (trust-by-construction).

Supported types:
  bodacc_rss            — BODACC RSS by SIREN (deterministic, requires Pappers to have run)
  google_news_query_rss — Google News RSS constructed from name+ville (deterministic, per project)
  youtube_channel_rss   — YouTube channel RSS discovered via SearXNG (search-based, up to 3/company)
  linkedin_company_rss  — LinkedIn RSS via rsshub bridge (search-based, up to 3/company)

Usage:
  uv run scripts/france_project_newsletter/discover_sources.py --type bodacc_rss
  uv run scripts/france_project_newsletter/discover_sources.py --type google_news_query_rss
  uv run scripts/france_project_newsletter/discover_sources.py --type youtube_channel_rss
  uv run scripts/france_project_newsletter/discover_sources.py --type linkedin_company_rss
  uv run scripts/france_project_newsletter/discover_sources.py --type all

  --company SLUG   Restrict to one company slug
  --dry-run        Preview without writing to DB

Env vars:
  SEARXNG_URL  (default: http://127.0.0.1:8888)
"""

import argparse
import json
import pathlib
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus

import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import gps_config

GEOJSON_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/locations.geojson"
COMPANIES_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/companies.json"
DB_PATH = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter/state.db"

QUERY_DELAY = 2.5  # seconds between SearXNG queries
MAX_CANDIDATES = 3  # max pending rows per company per type


def load_env() -> None:
    env_file = pathlib.Path(__file__).parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                import os
                os.environ.setdefault(k.strip(), v.strip())


def get_con() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def already_covered(con: sqlite3.Connection, company_id: str, src_type: str) -> bool:
    """True if company already has an approved or MAX_CANDIDATES pending rows of this type."""
    approved = con.execute(
        "SELECT 1 FROM pending_sources WHERE company_id=? AND type=? AND decision='approved' LIMIT 1",
        (company_id, src_type),
    ).fetchone()
    if approved:
        return True
    # Also check companies.json for already-declared sources
    if COMPANIES_PATH.exists():
        companies = json.loads(COMPANIES_PATH.read_text(encoding="utf-8"))
        entry = companies.get(company_id, {})
        for src in entry.get("sources", []):
            if src["type"] == src_type:
                return True
    pending_count = con.execute(
        "SELECT COUNT(*) FROM pending_sources WHERE company_id=? AND type=? AND decision='pending'",
        (company_id, src_type),
    ).fetchone()[0]
    return pending_count >= MAX_CANDIDATES


def insert_candidate(
    con: sqlite3.Connection,
    company_id: str,
    project_id: str | None,
    src_type: str,
    url: str,
    payload: dict,
    dry_run: bool,
) -> bool:
    """Insert a pending candidate. Returns True if inserted."""
    # Dedup: skip if this exact URL is already in the table for this company+type
    existing = con.execute(
        "SELECT 1 FROM pending_sources WHERE company_id=? AND type=? AND url=?",
        (company_id, src_type, url),
    ).fetchone()
    if existing:
        return False
    if not dry_run:
        now = datetime.now(timezone.utc).isoformat()
        con.execute(
            """INSERT INTO pending_sources
               (company_id, project_id, type, url, candidate_payload, discovered_by, discovered_at)
               VALUES (?, ?, ?, ?, ?, 'discover_sources.py', ?)""",
            (company_id, project_id, src_type, url, json.dumps(payload, ensure_ascii=False), now),
        )
        con.commit()
    return True


# ---------------------------------------------------------------------------
# Deterministic generators
# ---------------------------------------------------------------------------

def discover_bodacc_rss(con: sqlite3.Connection, filter_slug: str | None, dry_run: bool) -> int:
    """BODACC RSS by SIREN — requires company_enrichment.siren to be non-null."""
    rows = con.execute(
        "SELECT company_id, siren FROM company_enrichment WHERE siren IS NOT NULL"
    ).fetchall()

    inserted = 0
    for row in rows:
        company_id = row["company_id"]
        siren = row["siren"]
        if filter_slug and company_id != filter_slug:
            continue
        if already_covered(con, company_id, "bodacc_rss"):
            continue
        # BODACC open data RSS: one feed per SIREN
        url = f"https://www.bodacc.fr/api/datastore_search_sql?sql=SELECT%20*%20from%20%22b2e96dd4-7c9b-4f42-b6d3-0c46dc02bd4f%22%20WHERE%20%22registre%22%20LIKE%20%27%25{siren}%25%27%20LIMIT%2020"
        # Note: BODACC does not expose a native RSS per SIREN; use the DILA search API.
        # A more reliable endpoint is the data.gouv.fr BODACC dataset filtered by siren.
        # The URL below is a placeholder that should be verified at implementation time.
        url = f"https://bodacc.fr/annonces/rss/parution/all/siren/{siren}"
        payload = {"siren": siren, "snippet": f"BODACC legal announcements for SIREN {siren}"}
        if insert_candidate(con, company_id, None, "bodacc_rss", url, payload, dry_run):
            print(f"  [bodacc_rss] {company_id}: SIREN {siren}")
            inserted += 1

    return inserted


def discover_google_news_query_rss(con: sqlite3.Connection, filter_slug: str | None, dry_run: bool) -> int:
    """Google News RSS per project feature (one per feature, query = name + ville)."""
    geojson = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))
    inserted = 0

    for feat in geojson["features"]:
        props = feat["properties"]
        company_id = props.get("company_id", "")
        project_id = feat.get("id", "")
        if filter_slug and company_id != filter_slug:
            continue

        # Check per-project (not per-company) — google_news is project-level
        existing = con.execute(
            "SELECT 1 FROM pending_sources WHERE company_id=? AND project_id=? AND type='google_news_query_rss'",
            (company_id, project_id),
        ).fetchone()
        if existing:
            continue
        # Also check feeds.sources for existing declaration
        sources = props.get("feeds", {}).get("sources", [])
        if any(s["type"] == "google_news_query_rss" for s in sources):
            continue

        name = props.get("name", "")
        ville = props.get("ville", "")
        # Build query: quoted name + ville (first city if multiple separated by /)
        city = ville.split("/")[0].strip().split("(")[0].strip()
        query = f'"{name}" {city}'.strip()
        url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=fr&gl=FR&ceid=FR:fr"
        payload = {"query": query, "snippet": f"Google News pour {name} ({city})"}

        if insert_candidate(con, company_id, project_id, "google_news_query_rss", url, payload, dry_run):
            print(f"  [google_news] {project_id}: q={query!r}")
            inserted += 1

    return inserted


# ---------------------------------------------------------------------------
# Search-based generators (SearXNG)
# ---------------------------------------------------------------------------

def searxng_search(query: str, session: requests.Session, searxng_url: str) -> list[dict]:
    """Query SearXNG, return list of {url, title, content} results."""
    try:
        resp = session.get(
            searxng_url + "/search",
            params={"q": query, "format": "json", "engines": "ddg"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as exc:
        print(f"  [SEARXNG ERR] {exc}")
        return []


def extract_youtube_channel_id(url: str, session: requests.Session) -> str | None:
    """Scrape a YouTube URL for its channel_id."""
    try:
        resp = session.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        # channel_id appears as externalId or in canonical link
        match = re.search(r'"externalId":\s*"(UC[A-Za-z0-9_-]{22})"', resp.text)
        if match:
            return match.group(1)
        match = re.search(r'channel/(UC[A-Za-z0-9_-]{22})', resp.text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


def discover_youtube_channel_rss(
    con: sqlite3.Connection, filter_slug: str | None, dry_run: bool,
    searxng_url: str, session: requests.Session,
) -> int:
    if not COMPANIES_PATH.exists():
        print("  [SKIP] companies.json not found")
        return 0

    companies = json.loads(COMPANIES_PATH.read_text(encoding="utf-8"))
    inserted = 0

    for slug, entry in companies.items():
        if filter_slug and slug != filter_slug:
            continue
        if already_covered(con, slug, "youtube_channel_rss"):
            continue

        name = entry.get("name", slug)
        print(f"  Searching YouTube for: {name}")
        time.sleep(QUERY_DELAY)
        results = searxng_search(f"site:youtube.com {name}", session, searxng_url)

        count = 0
        for r in results:
            url = r.get("url", "")
            if not url or "youtube.com" not in url:
                continue
            if not any(p in url for p in ["/channel/", "/@", "/user/"]):
                continue

            # Try to get the channel_id
            channel_id = None
            if "/channel/UC" in url:
                m = re.search(r"/channel/(UC[A-Za-z0-9_-]{22})", url)
                if m:
                    channel_id = m.group(1)
            if not channel_id:
                time.sleep(1)
                channel_id = extract_youtube_channel_id(url, session)

            if not channel_id:
                continue

            rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            payload = {
                "channel_id": channel_id,
                "source_url": url,
                "title": r.get("title", ""),
                "snippet": r.get("content", "")[:120],
            }
            if insert_candidate(con, slug, None, "youtube_channel_rss", rss_url, payload, dry_run):
                print(f"    → {channel_id} ({url[:60]})")
                inserted += 1
                count += 1
            if count >= MAX_CANDIDATES:
                break

    return inserted


def discover_linkedin_company_rss(
    con: sqlite3.Connection, filter_slug: str | None, dry_run: bool,
    searxng_url: str, session: requests.Session,
) -> int:
    if not COMPANIES_PATH.exists():
        print("  [SKIP] companies.json not found")
        return 0

    rsshub_base = gps_config.rsshub_url().rstrip("/")
    companies = json.loads(COMPANIES_PATH.read_text(encoding="utf-8"))
    inserted = 0

    for slug, entry in companies.items():
        if filter_slug and slug != filter_slug:
            continue
        if already_covered(con, slug, "linkedin_company_rss"):
            continue

        name = entry.get("name", slug)
        print(f"  Searching LinkedIn for: {name}")
        time.sleep(QUERY_DELAY)
        results = searxng_search(f'site:linkedin.com/company "{name}"', session, searxng_url)

        count = 0
        for r in results:
            url = r.get("url", "")
            if not url or "linkedin.com/company/" not in url:
                continue
            # Extract company slug from LinkedIn URL
            m = re.search(r"linkedin\.com/company/([^/?#]+)", url)
            if not m:
                continue
            li_slug = m.group(1).rstrip("/")
            rss_url = f"{rsshub_base}/linkedin/company/{li_slug}"
            payload = {
                "linkedin_url": url,
                "linkedin_slug": li_slug,
                "title": r.get("title", ""),
                "snippet": r.get("content", "")[:120],
                "source_url": url,
            }
            if insert_candidate(con, slug, None, "linkedin_company_rss", rss_url, payload, dry_run):
                print(f"    → {li_slug} ({url[:60]})")
                inserted += 1
                count += 1
            if count >= MAX_CANDIDATES:
                break

    return inserted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

SUPPORTED_TYPES = ["bodacc_rss", "google_news_query_rss", "youtube_channel_rss", "linkedin_company_rss", "all"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=SUPPORTED_TYPES, required=True)
    parser.add_argument("--company", default=None, help="Restrict to one company slug")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env()
    import os
    searxng_url = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888").rstrip("/")
    session = requests.Session()
    session.headers["User-Agent"] = "GPS-Newsletter/1.0"

    con = get_con()
    types_to_run = SUPPORTED_TYPES[:-1] if args.type == "all" else [args.type]

    for src_type in types_to_run:
        print(f"\n→ {src_type}…")
        if src_type == "bodacc_rss":
            n = discover_bodacc_rss(con, args.company, args.dry_run)
        elif src_type == "google_news_query_rss":
            n = discover_google_news_query_rss(con, args.company, args.dry_run)
        elif src_type == "youtube_channel_rss":
            n = discover_youtube_channel_rss(con, args.company, args.dry_run, searxng_url, session)
        elif src_type == "linkedin_company_rss":
            n = discover_linkedin_company_rss(con, args.company, args.dry_run, searxng_url, session)
        else:
            n = 0
        suffix = " (dry-run)" if args.dry_run else ""
        print(f"  {n} candidates queued{suffix}")

    if args.dry_run:
        print("\n[dry-run] Nothing written to DB.")
    else:
        pending = con.execute(
            "SELECT COUNT(*) FROM pending_sources WHERE decision='pending'"
        ).fetchone()[0]
        print(f"\nDone. Total pending source reviews: {pending}")

    con.close()


if __name__ == "__main__":
    main()
