#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["feedparser", "requests"]
# ///
"""
Fetch news items from company RSS feeds, sector RSS feeds, and changedetection.io.
Write new items to SQLite (seen_items + news_items). Skip already-seen hashes.
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
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import gps_config

GEOJSON_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/locations.geojson"
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


def fetch_company_rss(features: list[dict], cutoff: datetime, con: sqlite3.Connection, seen: set[str]) -> int:
    inserted = 0
    seen_rss: set[str] = set()
    for feat in features:
        props = feat["properties"]
        feeds = props.get("feeds", {})
        rss_url = feeds.get("company_rss")
        if not rss_url or rss_url in seen_rss:
            continue
        seen_rss.add(rss_url)
        try:
            parsed = feedparser.parse(rss_url)
        except Exception as exc:
            print(f"  [RSS ERR] {props.get('name')}: {exc}")
            continue
        for entry in parsed.entries:
            published = getattr(entry, "published_parsed", None)
            if published and datetime(*published[:6], tzinfo=timezone.utc) < cutoff:
                continue
            item = parse_entry(
                entry, feat.get("id", ""), props.get("name", ""),
                props.get("category", ""), props.get("region", ""), "company_rss",
            )
            if insert_item(con, item, seen):
                inserted += 1
    return inserted


def fetch_sector_rss(features: list[dict], cutoff: datetime, con: sqlite3.Connection, seen: set[str]) -> int:
    sector_map: dict[str, list[tuple[dict, list[str]]]] = {}
    for feat in features:
        props = feat["properties"]
        feeds = props.get("feeds", {})
        keywords = [k.lower() for k in feeds.get("keywords", [props.get("name", "")])]
        for rss_url in feeds.get("sector_rss", []):
            sector_map.setdefault(rss_url, []).append((feat, keywords))

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
            for feat, keywords in watchers:
                if any(kw in text for kw in keywords):
                    props = feat["properties"]
                    item = parse_entry(
                        entry, feat.get("id", ""), props.get("name", ""),
                        props.get("category", ""), props.get("region", ""), "sector_rss",
                    )
                    if insert_item(con, item, seen):
                        inserted += 1
                    break
    return inserted


def fetch_changedetection(
    features: list[dict], session: requests.Session, base_url: str,
    cutoff: datetime, con: sqlite3.Connection, seen: set[str],
) -> int:
    inserted = 0
    for feat in features:
        props = feat["properties"]
        feeds = props.get("feeds", {})
        uuid = feeds.get("changedetection_uuid")
        if not uuid:
            continue
        try:
            resp = session.get(f"{base_url}/api/v1/watch/{uuid}/history")
            resp.raise_for_status()
            history = resp.json()
        except Exception as exc:
            print(f"  [CD ERR] {props.get('name')} ({uuid}): {exc}")
            continue
        watch_url = feeds.get("changedetection_url") or feeds.get("company_url", "")
        for ts_str in history:
            try:
                ts = datetime.fromtimestamp(int(ts_str), tz=timezone.utc)
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            title = f"Mise à jour détectée : {props.get('name')}"
            h = item_hash(watch_url, ts_str)
            item = {
                "hash": h,
                "company_id": feat.get("id", ""),
                "company": props.get("name", ""),
                "category": props.get("category", ""),
                "region": props.get("region", ""),
                "title": title,
                "summary": f"Le site de {props.get('name')} a été modifié.",
                "url": watch_url,
                "date": ts.isoformat(),
                "source": "changedetection",
            }
            if insert_item(con, item, seen):
                inserted += 1
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=["daily", "weekly"], default="daily")
    args = parser.parse_args()

    load_env()
    api_key = os.environ.get("CHANGEDETECTION_API_KEY")
    base_url = gps_config.changedetection_url().rstrip("/")

    hours = 24 if args.period == "daily" else 7 * 24
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    features = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))["features"]

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    seen = load_seen(con)
    print(f"Seen hashes loaded: {len(seen)}")

    print("→ Company RSS feeds...")
    n = fetch_company_rss(features, cutoff, con, seen)
    print(f"  {n} new items")

    print("→ Sector RSS feeds...")
    n = fetch_sector_rss(features, cutoff, con, seen)
    print(f"  {n} new items")

    if api_key:
        print("→ changedetection.io...")
        session = requests.Session()
        session.headers["x-api-key"] = api_key
        n = fetch_changedetection(features, session, base_url, cutoff, con, seen)
        print(f"  {n} new items")
    else:
        print("  [SKIP] CHANGEDETECTION_API_KEY not set")

    con.commit()

    total = con.execute("SELECT COUNT(*) FROM news_items WHERE pending_classification=1").fetchone()[0]
    print(f"\nDone. Pending classification: {total} items")
    con.close()


if __name__ == "__main__":
    main()
