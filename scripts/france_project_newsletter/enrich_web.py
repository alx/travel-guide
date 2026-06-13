#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Web-search enrichment for GPS companies via local SearXNG (uses all configured engines).

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
import unicodedata
from datetime import datetime, timezone

import requests

DB_PATH = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter/state.db"
GEOJSON_PATH = (
    pathlib.Path(__file__).parents[2]
    / "static/france-grands-projets-strategiques/locations.geojson"
)

DEFAULT_SEARXNG_URL = "http://127.0.0.1:8888"
QUERY_DELAY = 2.5  # seconds between queries

# Domains always skipped when extracting website_url
SKIP_DOMAINS = {
    # social / aggregators
    "linkedin.com", "wikipedia.org", "facebook.com", "twitter.com", "x.com",
    "youtube.com", "instagram.com", "reddit.com", "tiktok.com", "shop.tiktok.com",
    # registries / legal
    "pagesjaunes.fr", "societe.com", "verif.com", "infogreffe.fr", "pappers.fr",
    # job boards / recruitment
    "lindustrie-recrute.fr", "welcometothejungle.com", "indeed.com",
    "glassdoor.com", "jobteaser.com", "hellowork.com",
    # French news media
    "bfmtv.com", "lefigaro.fr", "lemonde.fr", "lesechos.fr", "challenges.fr",
    "usinenouvelle.com", "latribune.fr", "capital.fr", "francetvinfo.fr",
    "leparisien.fr", "actu.fr", "echodescommunes.fr",
    # regional promo / misc
    "hautsdefrance.fr", "middle-france.com", "lowyat.net",
    # regulatory
    "asnr.fr", "asn.fr",
    # tech giants / noise
    "microsoft.com", "apple.com", "google.com", "marantmotortechniek.com",
}

# URL path patterns that indicate an article/press-release page
ARTICLE_PATH_RE = re.compile(
    r"/(?:20\d{2}[/\-_]|actualit|article|newsroom|news/[a-z]|communique|presse|"
    r"press-release|archives?|information/archives|details?/20|\d{7,})",
    re.IGNORECASE,
)

# "Site web officiel : www.xxx.fr" pattern found in description snippets
OFFICIAL_SITE_RE = re.compile(
    r"site\s*(?:web\s*)?officiel\s*[:]\s*(https?://\S+|www\.\S+)",
    re.IGNORECASE,
)

# Stopwords excluded from domain-matching heuristic
_STOP_WORDS = {
    "france", "group", "groupe", "energie", "energy", "tech", "technologies",
    "the", "and", "sur", "les", "des", "une", "pour",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def search(query: str, session: requests.Session) -> list[dict]:
    try:
        r = session.get(
            f"{searxng_url()}/search",
            params={"q": query, "format": "json"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as exc:
        print(f"  [SEARXNG ERR] {query!r}: {exc}")
        return []


def _nfc(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _company_words(name: str) -> list[str]:
    """Significant words (4+ chars, not stopwords) from a company name."""
    return [w for w in re.findall(r"[a-z]{4,}", _nfc(name)) if w not in _STOP_WORDS]


def _domain_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    return m.group(1) if m else ""


def _is_skip_domain(domain: str) -> bool:
    return any(skip in domain for skip in SKIP_DOMAINS)


def _is_own_domain(domain: str, company_name: str) -> bool:
    """True if at least one company word appears in the domain string."""
    return any(w in domain.lower() for w in _company_words(company_name))


def _to_root(url: str) -> str:
    m = re.match(r"(https?://[^/]+)", url)
    return m.group(1) + "/" if m else url


# ---------------------------------------------------------------------------
# Extraction functions
# ---------------------------------------------------------------------------

def extract_linkedin(results: list[dict], company_name: str) -> tuple[str | None, int | None]:
    """Return (canonical_linkedin_url, follower_count_or_None).

    Rejects slugs that share no word with the company name — avoids matching
    Chinese JVs, unrelated subsidiaries, etc.
    """
    pattern = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/([^/?#]+)")
    cwords = _company_words(company_name)

    for r in results:
        url = r.get("url", "")
        m = pattern.match(url)
        if not m:
            continue
        slug = m.group(1).rstrip("/")
        if cwords and not any(w in _nfc(slug) for w in cwords):
            continue
        canonical = f"https://www.linkedin.com/company/{slug}"
        count = None
        content = r.get("content", "")
        fm = re.search(r"([\d\s,]+)\s+followers? on LinkedIn", content, re.IGNORECASE)
        if fm:
            count_str = re.sub(r"[\s,]", "", fm.group(1))
            try:
                count = int(count_str)
            except ValueError:
                pass
        return canonical, count
    return None, None


def extract_website(
    results: list[dict],
    existing_url: str | None,
    company_name: str,
    description: str | None = None,
) -> str | None:
    """Return the best official website URL.

    Priority:
    1. URL explicitly named in description ("Site web officiel : www.xxx.fr")
    2. Known GeoJSON domain confirmed by a result on that domain → existing_url
    3. First result on the company's own domain (name words in domain) → root URL
    4. First non-skip, non-article result
    """
    # 1. Explicit official URL in description text
    if description:
        m = OFFICIAL_SITE_RE.search(description)
        if m:
            raw = m.group(1).rstrip(".,;)")
            return raw if raw.startswith("http") else "https://" + raw

    # 2. Validate known domain against results
    existing_domain = _domain_of(existing_url).lstrip("www.") if existing_url else ""
    if existing_domain:
        for r in results:
            if existing_domain in r.get("url", ""):
                return existing_url

    cwords = _company_words(company_name)

    # 3. Company's own domain detected by name words → strip to root (homepage)
    for r in results:
        url = r.get("url", "")
        domain = _domain_of(url)
        if _is_skip_domain(domain):
            continue
        if _is_own_domain(domain, company_name):
            return _to_root(url)

    # 4. First non-skip, non-article URL where content/title/url mentions the company
    for r in results:
        url = r.get("url", "")
        domain = _domain_of(url)
        if _is_skip_domain(domain):
            continue
        if ARTICLE_PATH_RE.search(url):
            continue
        combined = (r.get("title", "") + " " + r.get("content", "") + " " + url).lower()
        if cwords and not any(w in combined for w in cwords):
            continue
        return url

    return None  # prefer None over returning an irrelevant URL


def extract_description(results: list[dict]) -> str | None:
    """Return a French description snippet, preferring fr.wikipedia.org."""
    for r in results:
        if "fr.wikipedia.org" in r.get("url", ""):
            content = r.get("content", "").strip()
            if content:
                return content[:400]
    for r in results:
        domain = _domain_of(r.get("url", ""))
        if _is_skip_domain(domain):
            continue
        content = r.get("content", "").strip()
        if len(content) > 60:
            return content[:400]
    return None


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

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

def run_profile(
    features: list[dict],
    con: sqlite3.Connection,
    session: requests.Session,
    filter_id: str | None,
) -> None:
    migrate_profile_columns(con)
    now = datetime.now(timezone.utc).isoformat()

    for feat in features:
        company_id = feat.get("id", "")
        if filter_id and company_id != filter_id:
            continue

        props = feat["properties"]
        name = props.get("name", "")
        existing_url = props.get("feeds", {}).get("company_url")
        keywords = props.get("feeds", {}).get("keywords", [])
        disambig_kw = keywords[1] if len(keywords) > 1 else (keywords[0] if keywords else "")

        print(f"\n[PROFILE] {name}")

        # --- LinkedIn: include "france" to prefer French entities over foreign JVs ---
        time.sleep(QUERY_DELAY)
        linkedin_results = search(f'"{name}" france linkedin company', session)
        linkedin_url, employee_count = extract_linkedin(linkedin_results, name)
        print(f"  linkedin: {linkedin_url}  employees~: {employee_count}")

        # --- Website + description ---
        # For compound names (e.g. "DataOne - Oreus - Core 42"), quote only
        # the last segment which is usually the most specific entity.
        primary_name = name.split(" - ")[-1].strip() if " - " in name else name
        time.sleep(QUERY_DELAY)
        web_query = (
            f'"{primary_name}" {disambig_kw} site officiel'.strip()
            if disambig_kw
            else f'"{primary_name}" entreprise site officiel'
        )
        web_results = search(web_query, session)
        description_fr = extract_description(web_results)
        website_url = extract_website(web_results, existing_url, name, description_fr)
        print(f"  website:  {website_url}")
        print(f"  desc:     {(description_fr or '')[:80]}...")

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
            (company_id, linkedin_url, website_url, description_fr, employee_count, now, now),
        )
        con.commit()

    print("\n[PROFILE] Done.")


# ---------------------------------------------------------------------------
# Mode: news
# ---------------------------------------------------------------------------

def run_news(
    features: list[dict],
    con: sqlite3.Connection,
    session: requests.Session,
    filter_id: str | None,
) -> None:
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
