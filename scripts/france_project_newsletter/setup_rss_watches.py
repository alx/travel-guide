#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Add company RSS feeds and Usine Nouvelle sector RSS feeds to changedetection.io
with the France_Project tag (RSS reader mode must be enabled in changedetection).

- Company RSS feeds (feeds.company_rss) → stored back as feeds.rss_uuid in GeoJSON
- Sector RSS feeds (feeds.sector_rss, unique) → registered once, reported to stdout

Idempotent: skips features/URLs already registered (checks by URL match).

Env vars (or .env file):
  CHANGEDETECTION_API_KEY
  CHANGEDETECTION_BASE_URL  (default: http://lamai270:5008)
"""

import json
import os
import pathlib
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import gps_config

GEOJSON_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/locations.geojson"
FRANCE_TAG = "France_Project"


def load_env() -> None:
    env_file = pathlib.Path(__file__).parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def create_watch(session: requests.Session, base_url: str, url: str, title: str, tags: str) -> str:
    payload = {"url": url, "title": title, "tag": tags}
    resp = session.post(f"{base_url}/api/v1/watch", json=payload)
    resp.raise_for_status()
    return resp.json().get("uuid", "")


def main() -> None:
    load_env()

    api_key = os.environ.get("CHANGEDETECTION_API_KEY")
    if not api_key:
        sys.exit("Error: CHANGEDETECTION_API_KEY not set")
    base_url = gps_config.changedetection_url().rstrip("/")

    session = requests.Session()
    session.headers.update({"x-api-key": api_key})

    # Fetch existing watches to avoid duplicates
    resp = session.get(f"{base_url}/api/v1/watch")
    resp.raise_for_status()
    existing_urls = {w["url"] for w in resp.json().values()}
    print(f"Existing watches in changedetection: {len(existing_urls)}")

    geojson = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))
    features = geojson["features"]

    created_company = 0
    skipped_company = 0
    created_sector = 0
    skipped_sector = 0

    # --- Company RSS feeds ---
    print("\n=== Company RSS feeds ===")
    for feat in features:
        props = feat["properties"]
        feeds = props.get("feeds", {})
        rss_url = feeds.get("company_rss", "")
        if not rss_url:
            continue

        name = props.get("name", "?")
        category = props.get("category", "Industrie")

        # Skip if already has an rss_uuid
        if feeds.get("rss_uuid"):
            print(f"  [SKIP] {name} — already registered ({feeds['rss_uuid']})")
            skipped_company += 1
            continue

        if rss_url in existing_urls:
            print(f"  [SKIP] {name} — URL already in changedetection")
            # Try to find and store the UUID
            for uuid, w in resp.json().items():
                if w["url"] == rss_url:
                    feeds["rss_uuid"] = uuid
                    break
            skipped_company += 1
            continue

        try:
            uuid = create_watch(session, base_url, rss_url, f"{name} RSS", f"{FRANCE_TAG},{category}")
            feeds["rss_uuid"] = uuid
            print(f"  [OK] {name} RSS → {uuid}")
            created_company += 1
        except requests.HTTPError as exc:
            print(f"  [ERR] {name}: {exc}")

    # --- Sector RSS feeds ---
    print("\n=== Sector RSS feeds ===")
    # Collect unique sector feeds with a representative label
    sector_feeds: dict[str, str] = {}  # url → label
    sector_labels = {
        "aeronautique": "Usine Nouvelle Aéronautique RSS",
        "energie": "Usine Nouvelle Énergie RSS",
        "materiaux": "Usine Nouvelle Matériaux RSS",
        "metallurgie": "Usine Nouvelle Métallurgie RSS",
        "chimie": "Usine Nouvelle Chimie RSS",
        "electronique": "Usine Nouvelle Électronique RSS",
        "automobile": "Usine Nouvelle Automobile RSS",
        "agroalimentaire": "Usine Nouvelle Agroalimentaire RSS",
    }
    for feat in features:
        feeds = feat["properties"].get("feeds", {})
        for url in feeds.get("sector_rss", []):
            if url not in sector_feeds:
                # Derive label from URL path
                slug = url.rstrip("/").split("/")[-2] if "/thematique/" in url else "general"
                label = sector_labels.get(slug, f"Usine Nouvelle {slug.capitalize()} RSS")
                if "usinenouvelle.com/rss" in url:
                    label = "Usine Nouvelle RSS (général)"
                sector_feeds[url] = label

    for rss_url, label in sorted(sector_feeds.items()):
        if rss_url in existing_urls:
            print(f"  [SKIP] {label} — already registered")
            skipped_sector += 1
            continue
        try:
            uuid = create_watch(session, base_url, rss_url, label, f"{FRANCE_TAG},Industrie France")
            print(f"  [OK] {label} → {uuid}")
            created_sector += 1
        except requests.HTTPError as exc:
            print(f"  [ERR] {label}: {exc}")

    # Write GeoJSON back
    GEOJSON_PATH.write_text(json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"Company RSS: {created_company} created, {skipped_company} skipped")
    print(f"Sector RSS:  {created_sector} created, {skipped_sector} skipped")
    print(f"GeoJSON updated: {GEOJSON_PATH}")


if __name__ == "__main__":
    main()
