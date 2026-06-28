#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["tqdm"]
# ///
"""
Generate bangkok-citywalk GeoJSON and download Wikimedia photos.

Usage:
    uv run scripts/bangkok-citywalk/generate.py
    uv run scripts/bangkok-citywalk/generate.py --dry-run
"""

import argparse
import csv
import json
import re
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
ROUTECACHE_PATH = SCRIPT_DIR / ".routecache.json"
MEDIACACHE_PATH = SCRIPT_DIR / ".mediacache.json"

STATIC_DIR = REPO_ROOT / "static/bangkok-citywalk"
PHOTOS_DIR = STATIC_DIR / "photos"
GEOJSON_OUT = STATIC_DIR / "walk.geojson"

NOMINATIM_HEADERS = {"User-Agent": "maps.girard-davila.net/bangkok-citywalk"}
WIKIMEDIA_HEADERS = {"User-Agent": "maps.girard-davila.net/bangkok-citywalk (girard.davila@gmail.com)"}
OSRM_BASE = "http://router.project-osrm.org/route/v1/foot"


# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower()
    for src, dst in [("é","e"),("è","e"),("ê","e"),("à","a"),("â","a"),("ù","u"),("û","u"),("î","i"),("ô","o"),("ç","c")]:
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


# Fallback coordinates for venues that Nominatim cannot find by name.
# Values sourced from OpenStreetMap manually.
GEOCODE_FALLBACKS: dict[str, tuple[float, float]] = {
    "Tha Tien Market": (13.74648, 100.49056),        # Tha Tien pier/market area
    "Pak Khlong Talat (Flower Market)": (13.74171, 100.49633),  # Pak Khlong Talat
    "Wat Traimit (Temple of the Golden Buddha)": (13.73800, 100.51365),  # Wat Traimit
    "Odeon Circle": (13.73843, 100.51398),           # Odean Circle roundabout, Chinatown
}


# ── Phase 1: Geocode ──────────────────────────────────────────────────────────

def geocode_nominatim(name: str) -> tuple[float, float] | None:
    # Try progressively simpler queries to work around Nominatim limitations
    queries = [f"{name} Bangkok Thailand"]
    # Strip parenthetical suffixes for a simpler retry
    simplified = re.sub(r"\s*\([^)]*\)", "", name).strip()
    if simplified != name:
        queries.append(f"{simplified} Bangkok Thailand")

    for q in queries:
        params = urllib.parse.urlencode({
            "q": q,
            "format": "json",
            "limit": 1,
            "countrycodes": "th",
            "accept-language": "en",
        })
        url = f"https://nominatim.openstreetmap.org/search?{params}"
        try:
            req = urllib.request.Request(url, headers=NOMINATIM_HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                results = json.loads(r.read())
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])
        except Exception as e:
            print(f"  ⚠ Nominatim error for '{name}': {e}", file=sys.stderr)
        if len(queries) > 1 and q != queries[-1]:
            time.sleep(1.1)
    return None


def geocode_venues(venues: list[dict], dry_run: bool) -> list[dict]:
    """Fill in lat/lng for venues missing coordinates. Mutates venues in place."""
    cache = load_json(GEOCACHE_PATH)
    updated = False

    for v in tqdm(venues, desc="Geocoding", unit="venue"):
        if v["lat"] and v["lng"]:
            continue
        if v["name"] in cache:
            v["lat"], v["lng"] = cache[v["name"]]["lat"], cache[v["name"]]["lng"]
            continue
        if dry_run:
            tqdm.write(f"  [dry-run] would geocode: {v['name']}")
            continue
        result = geocode_nominatim(v["name"])
        if result is None and v["name"] in GEOCODE_FALLBACKS:
            result = GEOCODE_FALLBACKS[v["name"]]
            tqdm.write(f"  → {v['name']}: {result[0]:.5f}, {result[1]:.5f} (fallback)")
        if result:
            lat, lng = result
            v["lat"], v["lng"] = lat, lng
            cache[v["name"]] = {"lat": lat, "lng": lng}
            if "fallback" not in str(result):
                tqdm.write(f"  → {v['name']}: {lat:.5f}, {lng:.5f}")
            updated = True
        else:
            print(f"  ✗ Geocode FAILED: {v['name']}", file=sys.stderr)
        time.sleep(1.1)

    if updated:
        save_json(GEOCACHE_PATH, cache)

    # Write back to venues.csv with filled coordinates
    if updated and not dry_run:
        fieldnames = ["name", "lat", "lng"]
        with VENUES_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(venues)
        print("  ✓ venues.csv updated with coordinates")

    return venues


# ── Phase 2: OSRM walking route ───────────────────────────────────────────────

def fetch_osrm_leg(lng1: float, lat1: float, lng2: float, lat2: float) -> list[list[float]] | None:
    """Returns list of [lng, lat] coordinates for one walking leg, or None on failure."""
    url = f"{OSRM_BASE}/{lng1},{lat1};{lng2},{lat2}?overview=full&geometries=geojson"
    try:
        req = urllib.request.Request(url, headers=NOMINATIM_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return data["routes"][0]["geometry"]["coordinates"]
    except Exception as e:
        print(f"  ⚠ OSRM error ({lng1},{lat1}) → ({lng2},{lat2}): {e}", file=sys.stderr)
        return None


def build_route(venues: list[dict], dry_run: bool) -> tuple[list[list[float]], list[int]]:
    """
    Fetch walking route between consecutive venues.
    Returns (all_coords, segment_breaks) where segment_breaks[i] is the index
    in all_coords where leg i starts. len(segment_breaks) == len(venues).
    """
    cache = load_json(ROUTECACHE_PATH)
    updated = False

    all_coords: list[list[float]] = []
    segment_breaks: list[int] = []

    for i in tqdm(range(len(venues) - 1), desc="Routing", unit="leg"):
        v1, v2 = venues[i], venues[i + 1]
        if not (v1["lat"] and v1["lng"] and v2["lat"] and v2["lng"]):
            print(f"  ⚠ Skipping leg {i}: missing coordinates", file=sys.stderr)
            continue

        cache_key = f"{v1['name']}→{v2['name']}"
        if cache_key in cache:
            leg_coords = cache[cache_key]
        elif dry_run:
            tqdm.write(f"  [dry-run] would fetch route: {cache_key}")
            leg_coords = []
        else:
            leg_coords = fetch_osrm_leg(
                float(v1["lng"]), float(v1["lat"]),
                float(v2["lng"]), float(v2["lat"]),
            )
            if leg_coords is None:
                leg_coords = []
            else:
                cache[cache_key] = leg_coords
                updated = True
            time.sleep(0.5)

        segment_breaks.append(len(all_coords))
        # Avoid duplicating the junction point between legs
        if all_coords and leg_coords:
            leg_coords = leg_coords[1:]
        all_coords.extend(leg_coords)

    # Final segment_break terminator
    segment_breaks.append(len(all_coords) - 1 if all_coords else 0)

    if updated:
        save_json(ROUTECACHE_PATH, cache)

    return all_coords, segment_breaks


# ── Phase 3: Wikimedia photos ─────────────────────────────────────────────────

def fetch_wikimedia_photo(name: str) -> tuple[str, str] | None:
    """
    Returns (thumb_url, attribution) for the best Wikimedia Commons result,
    or None if nothing found. Attribution format: '© Artist / License'.
    """
    params = urllib.parse.urlencode({
        "action": "query",
        "generator": "search",
        "gsrnamespace": 6,
        "gsrsearch": f"{name} Bangkok",
        "gsrlimit": 5,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 1080,
        "format": "json",
    })
    url = f"https://commons.wikimedia.org/w/api.php?{params}"
    try:
        req = urllib.request.Request(url, headers=WIKIMEDIA_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            ii = page.get("imageinfo", [{}])[0]
            thumb = ii.get("thumburl", "")
            if not thumb:
                continue
            meta = ii.get("extmetadata", {})
            artist = meta.get("Artist", {}).get("value", "")
            # Strip HTML tags from artist field
            artist = re.sub(r"<[^>]+>", "", artist).strip()
            license_name = meta.get("LicenseShortName", {}).get("value", "")
            attribution = f"© {artist} / {license_name}" if artist else license_name
            return thumb, attribution
    except Exception as e:
        print(f"  ⚠ Wikimedia error for '{name}': {e}", file=sys.stderr)
    return None


def fetch_photos(venues: list[dict], dry_run: bool) -> None:
    """Download one Wikimedia photo per venue into PHOTOS_DIR. Updates .mediacache.json."""
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    cache = load_json(MEDIACACHE_PATH)
    updated = False

    for v in tqdm(venues, desc="Wikimedia photos", unit="venue"):
        name = v["name"]
        slug = slugify(name)
        dest = PHOTOS_DIR / f"{slug}.jpg"

        if name in cache and dest.exists():
            continue

        if dry_run:
            tqdm.write(f"  [dry-run] would fetch photo: {name}")
            continue

        result = fetch_wikimedia_photo(name)
        if result is None:
            print(f"  ⚠ No photo found for: {name}", file=sys.stderr)
            cache[name] = {"thumb_url": "", "attribution": ""}
            updated = True
            continue

        thumb_url, attribution = result
        try:
            req = urllib.request.Request(thumb_url, headers=WIKIMEDIA_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                dest.write_bytes(r.read())
            cache[name] = {"thumb_url": thumb_url, "attribution": attribution}
            updated = True
            tqdm.write(f"  → {name}: {dest.name} ({attribution[:60]})")
        except Exception as e:
            print(f"  ⚠ Download failed for '{name}': {e}", file=sys.stderr)

        time.sleep(0.5)

    if updated:
        save_json(MEDIACACHE_PATH, cache)


# ── Phase 4: Write GeoJSON ────────────────────────────────────────────────────

def write_geojson(venues: list[dict], route_coords: list[list[float]], segment_breaks: list[int]) -> None:
    mediacache = load_json(MEDIACACHE_PATH)
    features = []

    for i, v in enumerate(venues):
        if not (v["lat"] and v["lng"]):
            continue
        slug = slugify(v["name"])
        attribution = mediacache.get(v["name"], {}).get("attribution", "")
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(v["lng"]), float(v["lat"])]},
            "properties": {
                "name": v["name"],
                "order": i + 1,
                "slug": slug,
                "photo": f"/bangkok-citywalk/photos/{slug}.jpg",
                "attribution": attribution,
            },
        })

    if route_coords:
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": route_coords},
            "properties": {
                "type": "route",
                "segment_breaks": segment_breaks,
            },
        })

    fc = {"type": "FeatureCollection", "features": features}
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    GEOJSON_OUT.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    print(f"✓ {GEOJSON_OUT} — {len(features)} features")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Skip network requests and file writes")
    args = parser.parse_args()

    # Load venues
    venues = []
    with VENUES_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            venues.append(row)
    print(f"Loaded {len(venues)} venues")

    # Phase 1: Geocode
    print("\nPhase 1: Geocoding…")
    venues = geocode_venues(venues, args.dry_run)

    # Phase 2: Route
    print("\nPhase 2: Walking route (OSRM)…")
    route_coords, segment_breaks = build_route(venues, args.dry_run)
    if route_coords:
        total_m = sum(
            ((route_coords[i+1][0]-route_coords[i][0])**2 + (route_coords[i+1][1]-route_coords[i][1])**2)**0.5 * 111_000
            for i in range(len(route_coords)-1)
        )
        print(f"  {len(route_coords)} route points, ~{total_m/1000:.1f} km")

    # Phase 3: Photos
    print("\nPhase 3: Wikimedia photos…")
    fetch_photos(venues, args.dry_run)

    # Phase 4: Write GeoJSON
    if not args.dry_run:
        print("\nPhase 4: Writing GeoJSON…")
        write_geojson(venues, route_coords, segment_breaks)

    print("\nDone.")


if __name__ == "__main__":
    main()
