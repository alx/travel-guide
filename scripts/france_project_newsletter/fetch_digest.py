#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["feedparser", "requests"]
# ///
"""
Aggregate RSS feeds + changedetection.io history into a newsletter digest JSON.

Usage:
  uv run scripts/france_project_newsletter/fetch_digest.py [--period daily|weekly]

Output:
  data/france_project_newsletter/digest_YYYYMMDD.json

Env vars required:
  CHANGEDETECTION_API_KEY
  CHANGEDETECTION_BASE_URL  (default: http://lamai270:5008)
"""

import argparse
import hashlib
import json
import os
import pathlib
from datetime import datetime, timedelta, timezone

import feedparser
import requests

GEOJSON_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/locations.geojson"
OUTPUT_DIR = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter"
DEFAULT_BASE_URL = "http://lamai270:5008"

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


def is_high_priority(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in HIGH_PRIORITY_KEYWORDS)


def parse_feed_entry(entry, company_id: str, company: str, category: str, region: str, source: str) -> dict:
    title = getattr(entry, "title", "") or ""
    summary = getattr(entry, "summary", "") or ""
    link = getattr(entry, "link", "") or ""
    published = getattr(entry, "published_parsed", None)
    date_str = datetime(*published[:6], tzinfo=timezone.utc).isoformat() if published else datetime.now(timezone.utc).isoformat()

    return {
        "hash": item_hash(link, title),
        "company_id": company_id,
        "company": company,
        "category": category,
        "region": region,
        "title_fr": title,
        "title_en": title,
        "summary_fr": summary[:500] if summary else "",
        "summary_en": summary[:500] if summary else "",
        "url": link,
        "date": date_str,
        "source": source,
        "high_priority": is_high_priority(title + " " + summary),
    }


def fetch_company_rss(features: list[dict], cutoff: datetime) -> list[dict]:
    items = []
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
            if published:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            items.append(parse_feed_entry(
                entry,
                feat.get("id", ""),
                props.get("name", ""),
                props.get("category", ""),
                props.get("region", ""),
                "company_rss",
            ))

    return items


def fetch_sector_rss(features: list[dict], cutoff: datetime) -> list[dict]:
    """Fetch sector RSS feeds once per URL, then filter entries by company keywords."""
    # Build sector_url → list of (feature, keywords) mapping
    sector_map: dict[str, list[tuple[dict, list[str]]]] = {}
    for feat in features:
        props = feat["properties"]
        feeds = props.get("feeds", {})
        keywords = [k.lower() for k in feeds.get("keywords", [props.get("name", "")])]
        for rss_url in feeds.get("sector_rss", []):
            sector_map.setdefault(rss_url, []).append((feat, keywords))

    items = []
    for rss_url, watchers in sector_map.items():
        try:
            parsed = feedparser.parse(rss_url)
        except Exception as exc:
            print(f"  [SECTOR ERR] {rss_url}: {exc}")
            continue

        for entry in parsed.entries:
            published = getattr(entry, "published_parsed", None)
            if published:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue

            title = (getattr(entry, "title", "") or "").lower()
            summary = (getattr(entry, "summary", "") or "").lower()
            text = title + " " + summary

            for feat, keywords in watchers:
                if any(kw in text for kw in keywords):
                    props = feat["properties"]
                    items.append(parse_feed_entry(
                        entry,
                        feat.get("id", ""),
                        props.get("name", ""),
                        props.get("category", ""),
                        props.get("region", ""),
                        "sector_rss",
                    ))
                    break  # one match per entry is enough

    return items


def fetch_changedetection(features: list[dict], session: requests.Session, base_url: str, cutoff: datetime) -> list[dict]:
    items = []

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

        for ts_str in history:
            try:
                ts = datetime.fromtimestamp(int(ts_str), tz=timezone.utc)
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue

            watch_url = feeds.get("changedetection_url") or feeds.get("company_url", "")
            items.append({
                "hash": item_hash(watch_url, ts_str),
                "company_id": feat.get("id", ""),
                "company": props.get("name", ""),
                "category": props.get("category", ""),
                "region": props.get("region", ""),
                "title_fr": f"Mise à jour détectée : {props.get('name')}",
                "title_en": f"Change detected: {props.get('name')}",
                "summary_fr": f"Le site de {props.get('name')} a été modifié. Consultez la page pour les détails.",
                "summary_en": f"The website of {props.get('name')} has been updated. Check the page for details.",
                "url": watch_url,
                "date": ts.isoformat(),
                "source": "changedetection",
                "high_priority": False,
            })

    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=["daily", "weekly"], default="daily")
    args = parser.parse_args()

    load_env()

    api_key = os.environ.get("CHANGEDETECTION_API_KEY")
    base_url = os.environ.get("CHANGEDETECTION_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    hours = 24 if args.period == "daily" else 7 * 24
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    data = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))
    features = data["features"]

    print(f"Fetching {args.period} digest (cutoff: {cutoff.date()})...")

    all_items: list[dict] = []

    # Company RSS
    print("→ Company RSS feeds...")
    all_items.extend(fetch_company_rss(features, cutoff))

    # Sector RSS
    print("→ Sector RSS feeds (L'Usine Nouvelle...)...")
    all_items.extend(fetch_sector_rss(features, cutoff))

    # changedetection.io
    if api_key:
        print("→ changedetection.io history...")
        session = requests.Session()
        session.headers["x-api-key"] = api_key
        all_items.extend(fetch_changedetection(features, session, base_url, cutoff))
    else:
        print("  [SKIP] CHANGEDETECTION_API_KEY not set, skipping changedetection.io")

    # Deduplicate by hash
    seen: set[str] = set()
    unique_items: list[dict] = []
    for item in all_items:
        if item["hash"] not in seen:
            seen.add(item["hash"])
            unique_items.append(item)

    # Sort by date DESC
    unique_items.sort(key=lambda x: x["date"], reverse=True)

    high_priority_count = sum(1 for i in unique_items if i["high_priority"])

    digest = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "period": args.period,
        "high_priority_count": high_priority_count,
        "total_items": len(unique_items),
        "items": unique_items,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    out_path = OUTPUT_DIR / f"digest_{date_str}.json"
    out_path.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nDigest: {len(unique_items)} items ({high_priority_count} high-priority)")
    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
