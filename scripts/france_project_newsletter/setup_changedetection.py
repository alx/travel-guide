#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Register changedetection.io watches for companies that lack a direct RSS feed.
Writes UUIDs back into locations.geojson (feeds.changedetection_uuid).
Idempotent: skips features with an existing UUID.

Env vars required (or in .env):
  CHANGEDETECTION_API_KEY  — API key for self-hosted changedetection.io
  CHANGEDETECTION_BASE_URL — e.g. http://lamai270:5008 (default)
"""

import json
import os
import pathlib
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import gps_config

GEOJSON_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/locations.geojson"


def load_env() -> None:
    env_file = pathlib.Path(__file__).parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


OVERVIEW_TAG = "France_Project"


def create_watch(session: requests.Session, base_url: str, url: str, title: str, tag: str) -> str:
    payload = {
        "url": url,
        "title": title,
        "tag": f"{OVERVIEW_TAG},{tag}",
    }
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

    data = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))

    created = 0
    skipped = 0
    errors = 0

    for feature in data["features"]:
        props = feature["properties"]
        feeds = props.get("feeds")
        if not feeds:
            print(f"  [WARN] {feature.get('id')} has no feeds property — run enrich_geojson.py first")
            continue

        if feeds.get("company_rss"):
            skipped += 1
            continue

        if feeds.get("changedetection_uuid"):
            skipped += 1
            continue

        name = props.get("name", feature.get("id"))
        category = props.get("category", "Industrie")
        watch_url = feeds.get("changedetection_url") or feeds.get("company_url")
        if not watch_url:
            print(f"  [WARN] {name}: no URL to watch")
            continue

        try:
            uuid = create_watch(session, base_url, watch_url, name, category)
            feeds["changedetection_uuid"] = uuid
            print(f"  [OK] {name} → {uuid}")
            created += 1
        except requests.HTTPError as exc:
            print(f"  [ERR] {name}: {exc}")
            errors += 1

    GEOJSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDone: {created} watches created, {skipped} skipped, {errors} errors.")
    print(f"UUIDs written back to {GEOJSON_PATH}")


if __name__ == "__main__":
    main()
