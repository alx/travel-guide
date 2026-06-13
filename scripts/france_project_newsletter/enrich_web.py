#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Web-search enrichment for GPS companies via local SearXNG (DDG engine).

Two modes:

  --mode profile  (run weekly)
    For every company: queries LinkedIn + website + description.
    Writes linkedin_url, website_url, description_fr, employee_count_est,
    website_checked_at into company_enrichment.

  --mode news  (run daily)
    For companies with no company_rss: queries DDG for recent news.
    Inserts new items into news_items (source="web_search", pending_classification=1).
    Deduplicates via seen_items using the same sha1(url|title)[:12] hash as
    fetch_digest.py.

Usage:
  uv run scripts/france_project_newsletter/enrich_web.py --mode profile
  uv run scripts/france_project_newsletter/enrich_web.py --mode news
  uv run scripts/france_project_newsletter/enrich_web.py --mode profile --company france-projet-001-imerys

Env vars / config:
  SEARXNG_URL  (default: http://127.0.0.1:8888)
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import time
from datetime import datetime, timezone

import requests

DB_PATH = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter/state.db"
GEOJSON_PATH = (
    pathlib.Path(__file__).parents[2]
    / "static/france-grands-projets-strategiques/locations.geojson"
)

DEFAULT_SEARXNG_URL = "http://127.0.0.1:8888"
QUERY_DELAY = 2.5  # seconds between DDG queries

# Domains to skip when extracting website_url
SKIP_DOMAINS = {
    "linkedin.com", "wikipedia.org", "pagesjaunes.fr",
    "societe.com", "verif.com", "infogreffe.fr", "pappers.fr",
    "bfmtv.com", "lefigaro.fr", "lemonde.fr",
}


def load_env() -> None:
    env_file = pathlib.Path(__file__).parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def searxng_url() -> str:
    return os.environ.get("SEARXNG_URL", DEFAULT_SEARXNG_URL)


def search(query: str, session: requests.Session, engines: str = "duckduckgo") -> list[dict]:
    try:
        r = session.get(
            f"{searxng_url()}/search",
            params={"q": query, "format": "json", "engines": engines},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as exc:
        print(f"  [SEARXNG ERR] {query!r}: {exc}")
        return []


def extract_linkedin(results: list[dict]) -> tuple[str | None, int | None]:
    """Return (canonical_linkedin_url, follower_count_or_None)."""
    pattern = re.compile(r"^https://www\.linkedin\.com/company/[^/]+$")
    for r in results:
        url = r.get("url", "")
        if pattern.match(url):
            count = None
            content = r.get("content", "")
            m = re.search(r"([\d\s,]+)\s+followers? on LinkedIn", content, re.IGNORECASE)
            if m:
                count_str = m.group(1).replace(",", "").replace("\xa0", "").replace(" ", "")
                try:
                    count = int(count_str)
                except ValueError:
                    pass
            return url, count
    return None, None


def extract_website(results: list[dict], existing_url: str | None) -> str | None:
    """Return the best official website URL from results."""
    existing_domain = None
    if existing_url:
        m = re.search(r"https?://(?:www\.)?([^/]+)", existing_url)
        if m:
            existing_domain = m.group(1)

    for r in results:
        url = r.get("url", "")
        m = re.search(r"https?://(?:www\.)?([^/]+)", url)
        if not m:
            continue
        domain = m.group(1)
        if any(skip in domain for skip in SKIP_DOMAINS):
            continue
        # Prefer the domain already in GeoJSON if it appears in results
        if existing_domain and existing_domain in domain:
            return url
        return url
    return existing_url


def extract_description(results: list[dict]) -> str | None:
    """Return a French description snippet, preferring Wikipedia."""
    for r in results:
        url = r.get("url", "")
        if "wikipedia.org" in url and "fr.wikipedia" in url:
            content = r.get("content", "").strip()
            if content:
                return content[:400]
    # Fallback: first non-linkedin, non-skip-domain result with meaningful content
    for r in results:
        url = r.get("url", "")
        if any(skip in url for skip in SKIP_DOMAINS):
            continue
        content = r.get("content", "").strip()
        if len(content) > 60:
            return content[:400]
    return None


def migrate_profile_columns(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(company_enrichment)")}
    new_cols = [
        ("linkedin_url", "TEXT"),
        ("website_url", "TEXT"),
        ("description_fr", "TEXT"),
        ("employee_count_est", "INTEGER"),
        ("website_checked_at", "TEXT"),
    ]
    for col, col_type in new_cols:
        if col not in existing:
            con.execute(f"ALTER TABLE company_enrichment ADD COLUMN {col} {col_type}")
            print(f"  [MIGRATE] Added column company_enrichment.{col}")
    con.commit()


def item_hash(url: str, title: str) -> str:
    return hashlib.sha1(f"{url}|{title}".encode()).hexdigest()[:12]


def load_seen(con: sqlite3.Connection) -> set[str]:
    return {row[0] for row in con.execute("SELECT hash FROM seen_items")}


# ---------------------------------------------------------------------------
# Mode: profile
# ---------------------------------------------------------------------------

def run_profile(features: list[dict], con: sqlite3.Connection, session: requests.Session, filter_id: str | None) -> None:
    migrate_profile_columns(con)
    now = datetime.now(timezone.utc).isoformat()

    for feat in features:
        company_id = feat.get("id", "")
        if filter_id and company_id != filter_id:
            continue

        props = feat["properties"]
        name = props.get("name", "")
        existing_url = props.get("feeds", {}).get("company_url")

        print(f"\n[PROFILE] {name}")

        # --- LinkedIn ---
        time.sleep(QUERY_DELAY)
        linkedin_results = search(f'site:linkedin.com/company "{name}"', session)
        linkedin_url, employee_count = extract_linkedin(linkedin_results)
        print(f"  linkedin: {linkedin_url}  employees~: {employee_count}")

        # --- Website + description ---
        time.sleep(QUERY_DELAY)
        web_results = search(f'"{name}" site officiel france', session)
        website_url = extract_website(web_results, existing_url)
        description_fr = extract_description(web_results)
        print(f"  website:  {website_url}")
        print(f"  desc:     {(description_fr or '')[:80]}...")

        # Upsert enrichment
        con.execute(
            """INSERT INTO company_enrichment
               (company_id, linkedin_url, website_url, description_fr,
                employee_count_est, website_checked_at, enriched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(company_id) DO UPDATE SET
                 linkedin_url        = excluded.linkedin_url,
                 website_url         = excluded.website_url,
                 description_fr      = excluded.description_fr,
                 employee_count_est  = excluded.employee_count_est,
                 website_checked_at  = excluded.website_checked_at,
                 enriched_at         = excluded.enriched_at""",
            (company_id, linkedin_url, website_url, description_fr,
             employee_count, now, now),
        )
        con.commit()

    print("\n[PROFILE] Done.")


# ---------------------------------------------------------------------------
# Mode: news
# ---------------------------------------------------------------------------

def run_news(features: list[dict], con: sqlite3.Connection, session: requests.Session, filter_id: str | None) -> None:
    seen = load_seen(con)
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).date().isoformat()
    inserted = 0

    for feat in features:
        company_id = feat.get("id", "")
        if filter_id and company_id != filter_id:
            continue

        props = feat["properties"]
        feeds = props.get("feeds", {})

        if feeds.get("company_rss"):
            continue  # already covered by fetch_digest.py

        name = props.get("name", "")
        category = props.get("category", "")
        region = props.get("region", "")
        keywords = feeds.get("keywords", [name])
        primary_kw = keywords[0] if keywords else name

        print(f"\n[NEWS] {name}")
        time.sleep(QUERY_DELAY)

        query = f'"{name}" {primary_kw} actualité OR communiqué OR investissement'
        results = search(query, session)

        for r in results:
            url = r.get("url", "")
            title = r.get("title", "").strip()
            content = r.get("content", "").strip()
            if not url or not title:
                continue

            h = item_hash(url, title)
            if h in seen:
                continue

            # Basic keyword relevance gate — skip results with no company name match
            combined = (title + " " + content).lower()
            if name.lower() not in combined:
                continue

            con.execute(
                "INSERT OR IGNORE INTO seen_items (hash, first_seen) VALUES (?, ?)",
                (h, now),
            )
            con.execute(
                """INSERT OR IGNORE INTO news_items
                   (hash, company_id, company, category, region, title, summary,
                    url, date, source, fetched_at, pending_classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'web_search', ?, 1)""",
                (h, company_id, name, category, region, title, content, url, today, now),
            )
            seen.add(h)
            inserted += 1
            print(f"  + {title[:70]}")

        con.commit()

    print(f"\n[NEWS] Done. {inserted} new items inserted.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["profile", "news"], required=True)
    parser.add_argument("--company", default=None, help="Restrict to one company_id")
    args = parser.parse_args()

    load_env()

    features = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))["features"]
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")

    session = requests.Session()
    session.headers["Accept"] = "application/json"

    # Verify SearXNG reachability
    try:
        session.get(f"{searxng_url()}/", timeout=5).raise_for_status()
    except Exception as exc:
        print(f"[ABORT] SearXNG unreachable at {searxng_url()}: {exc}")
        return

    if args.mode == "profile":
        run_profile(features, con, session, args.company)
    else:
        run_news(features, con, session, args.company)

    con.close()


if __name__ == "__main__":
    main()
