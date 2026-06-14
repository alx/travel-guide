#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["tqdm"]
# ///
"""
Auto-fill addresses and coordinates for bangkok-raco venues.

Searches each venue (with empty address) using Google Places Text Search,
writes the resolved address back to venues.csv, and stores coordinates
in .geocache.json so generate.py can build GeoJSONs immediately.

Falls back to Nominatim for any venue Google can't find.

Usage:
    uv run scripts/bangkok-raco/geocode.py
    uv run scripts/bangkok-raco/geocode.py --dry-run

Requires:
    GOOGLE_MAPS_API_KEY in .env or environment
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
VENUES_CSV = SCRIPT_DIR / "venues.csv"
GEOCACHE_PATH = SCRIPT_DIR / ".geocache.json"

PLACES_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "maps.girard-davila.net/bangkok-raco"}

SKIP_NAMES = {"TBA"}  # generic placeholder venues — skip geocoding


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(REPO_ROOT / ".env")


def load_geocache() -> dict:
    if GEOCACHE_PATH.exists():
        return json.loads(GEOCACHE_PATH.read_text())
    return {}


def save_geocache(cache: dict) -> None:
    GEOCACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def load_venues() -> list[dict]:
    with VENUES_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_venues(venues: list[dict]) -> None:
    fieldnames = ["name", "address", "category", "logo"]
    with VENUES_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(venues)


def google_places_search(name: str, api_key: str) -> tuple[str, dict] | tuple[None, None]:
    """Returns (formatted_address, {lat, lon}) or (None, None)."""
    params = urllib.parse.urlencode({
        "query": f"{name} Bangkok",
        "key": api_key,
        "language": "en",
        "region": "th",
    })
    url = f"{PLACES_URL}?{params}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        results = data.get("results", [])
        if not results:
            return None, None
        r0 = results[0]
        address = r0.get("formatted_address", "")
        loc = r0["geometry"]["location"]
        return address, {"lat": loc["lat"], "lon": loc["lng"]}
    except Exception as e:
        print(f"  ⚠ Google Places error for '{name}': {e}", file=sys.stderr)
    return None, None


def nominatim_search(name: str) -> tuple[str, dict] | tuple[None, None]:
    """Returns (display_name, {lat, lon}) or (None, None)."""
    params = urllib.parse.urlencode({
        "q": f"{name}, Bangkok, Thailand",
        "format": "json",
        "limit": 1,
        "countrycodes": "th",
        "accept-language": "en",
    })
    url = f"{NOMINATIM_URL}?{params}"
    try:
        req = urllib.request.Request(url, headers=NOMINATIM_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            results = json.loads(r.read())
        if results:
            best = results[0]
            return best.get("display_name", ""), {"lat": float(best["lat"]), "lon": float(best["lon"])}
    except Exception as e:
        print(f"  ⚠ Nominatim error for '{name}': {e}", file=sys.stderr)
    return None, None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        print("⚠ GOOGLE_MAPS_API_KEY not set — using Nominatim only", file=sys.stderr)

    venues = load_venues()
    geocache = load_geocache()

    pending = [
        v for v in venues
        if not v.get("address") and v["name"] not in SKIP_NAMES
    ]
    print(f"{len(pending)} venues need geocoding")

    resolved = 0
    failed = []

    for v in tqdm(pending, desc="Geocoding venues", unit="venue"):
        name = v["name"]
        address, coords = None, None

        if api_key:
            address, coords = google_places_search(name, api_key)
            time.sleep(0.3)

        if not coords:
            if api_key:
                tqdm.write(f"  ↳ Google failed for '{name}', trying Nominatim…")
            address, coords = nominatim_search(name)
            time.sleep(1.1)

        if coords:
            tqdm.write(f"  ✓ {name}: {address}")
            tqdm.write(f"      → {coords['lat']:.5f}, {coords['lon']:.5f}")
            if not args.dry_run:
                v["address"] = address
                geocache[address] = coords
            resolved += 1
        else:
            tqdm.write(f"  ✗ {name}: not found", file=sys.stderr)
            failed.append(name)

    if not args.dry_run and resolved > 0:
        save_venues(venues)
        save_geocache(geocache)
        print(f"\n✓ {resolved} venues resolved — venues.csv and .geocache.json updated")

    if failed:
        print(f"\n✗ {len(failed)} venues not found — fill manually in venues.csv:")
        for name in failed:
            print(f"  • {name}")

    print("Done.")


if __name__ == "__main__":
    main()
