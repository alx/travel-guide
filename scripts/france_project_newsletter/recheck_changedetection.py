#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Trigger a recheck for all changedetection.io watches registered in locations.geojson.

Env vars required (or in .env):
  CHANGEDETECTION_API_KEY  — API key for self-hosted changedetection.io
  CHANGEDETECTION_BASE_URL — e.g. http://lamai270:5008 (default)
"""

import json
import os
import pathlib
import sys

import requests

GEOJSON_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/locations.geojson"
DEFAULT_BASE_URL = "http://lamai270:5008"


def load_env() -> None:
    env_file = pathlib.Path(__file__).parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    load_env()

    api_key = os.environ.get("CHANGEDETECTION_API_KEY")
    if not api_key:
        sys.exit("Error: CHANGEDETECTION_API_KEY not set")

    base_url = os.environ.get("CHANGEDETECTION_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    session = requests.Session()
    session.headers.update({"x-api-key": api_key})

    data = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))

    triggered = 0
    skipped = 0
    errors = 0

    for feature in data["features"]:
        props = feature["properties"]
        feeds = props.get("feeds", {})
        uuid = feeds.get("changedetection_uuid")
        name = props.get("name", feature.get("id"))

        if not uuid:
            skipped += 1
            continue

        try:
            resp = session.get(f"{base_url}/api/v1/watch/{uuid}/recheck")
            resp.raise_for_status()
            print(f"  [OK] {name} ({uuid})")
            triggered += 1
        except requests.HTTPError as exc:
            print(f"  [ERR] {name} ({uuid}): {exc}")
            errors += 1

    print(f"\nDone: {triggered} rechecks triggered, {skipped} skipped (no UUID), {errors} errors.")


if __name__ == "__main__":
    main()
