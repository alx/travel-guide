#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["feedparser", "requests"]
# ///
"""
Fetch news items from all declared News sources and write new items to SQLite.

On startup, reads source declarations from:
  - static/france-grands-projets-strategiques/companies.json  (company-level sources)
  - Each project feature's feeds.sources list in locations.geojson (project-level sources)
Upserts them into the news_sources operational table, minting changedetection UUIDs as needed.
Then fetches each enabled source via a per-type dispatch table.

Items are written with pending_classification=1; classify.py processes them separately.

Usage:
  uv run scripts/france_project_newsletter/fetch_digest.py [--period daily|weekly]

Env vars:
  CHANGEDETECTION_API_KEY
  CHANGEDETECTION_BASE_URL  (default: http://lamai270:5008)
"""

import argparse
import hashlib
import json
import os
import pathlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import feedparser
import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import gps_config

GEOJSON_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/locations.geojson"
COMPANIES_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/companies.json"
DB_PATH = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter/state.db"

HIGH_PRIORITY_KEYWORDS = [
    "inauguration", "milliards", "gigafactory", "groundbreaking",
    "financement", "investissement", "mise en service", "premier coup de pioche",
    "levée de fonds", "contrat", "billion", "funding",
]


def load_env() -> None:
    env_file = pathlib.Path(__file__).parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def item_hash(url: str, title: str) -> str:
    return hashlib.sha1(f"{url}|{title}".encode()).hexdigest()[:12]


def load_seen(con: sqlite3.Connection) -> set[str]:
    return {row[0] for row in con.execute("SELECT hash FROM seen_items")}


def insert_item(con: sqlite3.Connection, item: dict, seen: set[str]) -> bool:
    h = item["hash"]
    if h in seen:
        return False
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        "INSERT OR IGNORE INTO seen_items (hash, first_seen) VALUES (?, ?)",
        (h, now),
    )
    con.execute(
        """INSERT OR IGNORE INTO news_items
           (hash, company_id, company, category, region, title, summary,
            url, date, source, fetched_at, pending_classification)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (
            h,
            item["company_id"],
            item["company"],
            item["category"],
            item["region"],
            item["title"],
            item["summary"],
            item["url"],
            item["date"],
            item["source"],
            now,
        ),
    )
    seen.add(h)
    return True


def parse_entry(entry, company_id: str, company: str, category: str, region: str, source: str) -> dict:
    title = getattr(entry, "title", "") or ""
    summary = getattr(entry, "summary", "") or ""
    link = getattr(entry, "link", "") or ""
    published = getattr(entry, "published_parsed", None)
    date_str = (
        datetime(*published[:6], tzinfo=timezone.utc).isoformat()
        if published
        else datetime.now(timezone.utc).isoformat()
    )
    return {
        "hash": item_hash(link, title),
        "company_id": company_id,
        "company": company,
        "category": category,
        "region": region,
        "title": title,
        "summary": summary[:500],
        "url": link,
        "date": date_str,
        "source": source,
    }


# ---------------------------------------------------------------------------
# Source declaration loading
# ---------------------------------------------------------------------------

def load_declarations() -> list[dict]:
    """
    Read all source declarations from companies.json and locations.geojson.
    Returns a flat list of dicts: {company_id, company_name, category, region,
                                   type, url, keywords}.
    """
    decls: list[dict] = []

    geojson = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))
    features = geojson["features"]

    # Build lookup: company_id → {category, region, company_name, keywords}
    # and feature_id → {category, region, company_name, keywords, project_id}
    company_meta: dict[str, dict] = {}
    feature_meta: dict[str, dict] = {}
    for feat in features:
        props = feat["properties"]
        company_id = props.get("company_id", feat.get("id", ""))
        meta = {
            "company_name": props.get("name", ""),
            "category": props.get("category", ""),
            "region": props.get("region", ""),
            "keywords": props.get("feeds", {}).get("keywords", [props.get("name", "")]),
        }
        company_meta.setdefault(company_id, meta)
        feature_meta[feat.get("id", "")] = {**meta, "project_id": feat.get("id", ""), "company_id": company_id}

    # Company-level declarations from companies.json
    if COMPANIES_PATH.exists():
        companies = json.loads(COMPANIES_PATH.read_text(encoding="utf-8"))
        for slug, entry in companies.items():
            meta = company_meta.get(slug, {"company_name": entry.get("name", slug), "category": "", "region": "", "keywords": []})
            for src in entry.get("sources", []):
                decls.append({
                    "company_id": slug,
                    "company_name": meta["company_name"],
                    "category": meta["category"],
                    "region": meta["region"],
                    "keywords": meta["keywords"],
                    "type": src["type"],
                    "url": src["url"],
                    "scope": "company",
                })

    # Project-level declarations from each feature's feeds.sources
    for feat in features:
        props = feat["properties"]
        fmeta = feature_meta[feat.get("id", "")]
        feeds = props.get("feeds", {})

        # v2 schema: feeds.sources list
        for src in feeds.get("sources", []):
            decls.append({
                "company_id": fmeta["company_id"],
                "project_id": fmeta["project_id"],
                "company_name": fmeta["company_name"],
                "category": fmeta["category"],
                "region": fmeta["region"],
                "keywords": fmeta["keywords"],
                "type": src["type"],
                "url": src["url"],
                "scope": "project",
                # Pass UUID from GeoJSON state field (Slice 2 migration: moves to news_sources)
                "_legacy_cd_uuid": feeds.get("changedetection_uuid"),
                "_legacy_rss_uuid": feeds.get("rss_uuid"),
            })

    return decls


def upsert_declarations(con: sqlite3.Connection, decls: list[dict]) -> None:
    """Upsert source declarations into news_sources table."""
    now = datetime.now(timezone.utc).isoformat()
    for d in decls:
        # Determine UUID from legacy GeoJSON fields if this is a changedetection source
        uuid = None
        if d["type"] == "changedetection":
            uuid = d.get("_legacy_cd_uuid")
        elif d["type"] == "company_rss":
            uuid = d.get("_legacy_rss_uuid")

        con.execute(
            """INSERT INTO news_sources (company_id, type, url, uuid, enabled, added_at)
               VALUES (?, ?, ?, ?, 1, ?)
               ON CONFLICT (company_id, type, url) DO UPDATE SET
                 uuid = COALESCE(uuid, excluded.uuid)""",
            (d["company_id"], d["type"], d["url"], uuid, now),
        )
    con.commit()


# ---------------------------------------------------------------------------
# Per-type fetch handlers
# ---------------------------------------------------------------------------

def fetch_rss_type(
    decls: list[dict], source_type: str, cutoff: datetime,
    con: sqlite3.Connection, seen: set[str],
) -> int:
    """Generic RSS fetcher for company_rss, youtube_channel_rss, linkedin_company_rss, bodacc_rss."""
    inserted = 0
    seen_urls: set[str] = set()
    for d in decls:
        if d["type"] != source_type:
            continue
        url = d["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            parsed = feedparser.parse(url)
        except Exception as exc:
            _record_error(con, d, str(exc))
            print(f"  [{source_type.upper()} ERR] {d['company_name']}: {exc}")
            continue
        for entry in parsed.entries:
            published = getattr(entry, "published_parsed", None)
            if published and datetime(*published[:6], tzinfo=timezone.utc) < cutoff:
                continue
            item = parse_entry(
                entry, d["company_id"], d["company_name"],
                d["category"], d["region"], source_type,
            )
            if insert_item(con, item, seen):
                inserted += 1
        _record_fetch(con, d)
    return inserted


def fetch_sector_rss(
    decls: list[dict], cutoff: datetime,
    con: sqlite3.Connection, seen: set[str],
) -> int:
    # Group sector_rss by URL; each URL has a set of (feat_decl, keywords) watchers
    sector_map: dict[str, list[dict]] = {}
    for d in decls:
        if d["type"] != "sector_rss":
            continue
        sector_map.setdefault(d["url"], []).append(d)

    inserted = 0
    for rss_url, watchers in sector_map.items():
        try:
            parsed = feedparser.parse(rss_url)
        except Exception as exc:
            print(f"  [SECTOR ERR] {rss_url}: {exc}")
            continue
        for entry in parsed.entries:
            published = getattr(entry, "published_parsed", None)
            if published and datetime(*published[:6], tzinfo=timezone.utc) < cutoff:
                continue
            title = (getattr(entry, "title", "") or "").lower()
            summary = (getattr(entry, "summary", "") or "").lower()
            text = title + " " + summary
            for d in watchers:
                keywords = [k.lower() for k in d.get("keywords", [d["company_name"]])]
                if any(kw in text for kw in keywords):
                    item = parse_entry(
                        entry, d["company_id"], d["company_name"],
                        d["category"], d["region"], "sector_rss",
                    )
                    if insert_item(con, item, seen):
                        inserted += 1
                    break
    return inserted


def fetch_changedetection(
    decls: list[dict], session: requests.Session, base_url: str,
    cutoff: datetime, con: sqlite3.Connection, seen: set[str],
) -> int:
    inserted = 0
    for d in decls:
        if d["type"] != "changedetection":
            continue
        # UUID is in news_sources table; fall back to GeoJSON legacy field
        row = con.execute(
            "SELECT uuid FROM news_sources WHERE company_id=? AND type='changedetection' AND url=?",
            (d["company_id"], d["url"]),
        ).fetchone()
        uuid = row[0] if row else d.get("_legacy_cd_uuid")
        if not uuid:
            continue
        try:
            resp = session.get(f"{base_url}/api/v1/watch/{uuid}/history")
            resp.raise_for_status()
            history = resp.json()
        except Exception as exc:
            _record_error(con, d, str(exc))
            print(f"  [CD ERR] {d['company_name']} ({uuid}): {exc}")
            continue
        for ts_str in history:
            try:
                ts = datetime.fromtimestamp(int(ts_str), tz=timezone.utc)
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            title = f"Mise à jour détectée : {d['company_name']}"
            h = item_hash(d["url"], ts_str)
            item = {
                "hash": h,
                "company_id": d["company_id"],
                "company": d["company_name"],
                "category": d["category"],
                "region": d["region"],
                "title": title,
                "summary": f"Le site de {d['company_name']} a été modifié.",
                "url": d["url"],
                "date": ts.isoformat(),
                "source": "changedetection",
            }
            if insert_item(con, item, seen):
                inserted += 1
        _record_fetch(con, d)
    return inserted


def fetch_google_news_query_rss(
    decls: list[dict], cutoff: datetime,
    con: sqlite3.Connection, seen: set[str],
) -> int:
    return fetch_rss_type(decls, "google_news_query_rss", cutoff, con, seen)


def _record_fetch(con: sqlite3.Connection, d: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        """UPDATE news_sources SET last_fetched_at=?, last_error=NULL
           WHERE company_id=? AND type=? AND url=?""",
        (now, d["company_id"], d["type"], d["url"]),
    )


def _record_error(con: sqlite3.Connection, d: dict, error: str) -> None:
    con.execute(
        """UPDATE news_sources SET last_error=?
           WHERE company_id=? AND type=? AND url=?""",
        (error[:500], d["company_id"], d["type"], d["url"]),
    )


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=["daily", "weekly"], default="daily")
    args = parser.parse_args()

    load_env()
    api_key = os.environ.get("CHANGEDETECTION_API_KEY")
    base_url = gps_config.changedetection_url().rstrip("/")

    hours = 24 if args.period == "daily" else 7 * 24
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    print("Loading source declarations…")
    decls = load_declarations()
    print(f"  {len(decls)} declarations loaded")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")

    print("Upserting into news_sources…")
    upsert_declarations(con, decls)

    seen = load_seen(con)
    print(f"Seen hashes loaded: {len(seen)}")

    print("→ Company RSS feeds (company_rss)…")
    n = fetch_rss_type(decls, "company_rss", cutoff, con, seen)
    print(f"  {n} new items")

    print("→ Sector RSS feeds…")
    n = fetch_sector_rss(decls, cutoff, con, seen)
    print(f"  {n} new items")

    print("→ Google News RSS…")
    n = fetch_google_news_query_rss(decls, cutoff, con, seen)
    print(f"  {n} new items")

    print("→ YouTube channel RSS…")
    n = fetch_rss_type(decls, "youtube_channel_rss", cutoff, con, seen)
    print(f"  {n} new items")

    print("→ LinkedIn company RSS (via rsshub)…")
    n = fetch_rss_type(decls, "linkedin_company_rss", cutoff, con, seen)
    print(f"  {n} new items")

    print("→ BODACC RSS…")
    n = fetch_rss_type(decls, "bodacc_rss", cutoff, con, seen)
    print(f"  {n} new items")

    if api_key:
        print("→ changedetection.io…")
        session = requests.Session()
        session.headers["x-api-key"] = api_key
        n = fetch_changedetection(decls, session, base_url, cutoff, con, seen)
        print(f"  {n} new items")
    else:
        print("  [SKIP] CHANGEDETECTION_API_KEY not set")

    con.commit()

    total = con.execute("SELECT COUNT(*) FROM news_items WHERE pending_classification=1").fetchone()[0]
    print(f"\nDone. Pending classification: {total} items")
    con.close()


if __name__ == "__main__":
    main()
