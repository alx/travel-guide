#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["tqdm"]
# ///
"""
Generate a temple walk GeoJSON + Hugo page from a starting point.

Finds named Buddhist temples via the Overpass API, chains them greedily with
OSRM walking legs, and stops before the cumulative walking distance exceeds
--max-km (default 10). Output follows the bangkok-citywalk GeoJSON schema.

Usage:
    uv run scripts/temple-walk/generate.py --start "13.7516,100.4927" --slug rattanakosin
    uv run scripts/temple-walk/generate.py --start "Democracy Monument, Bangkok" --slug democracy --max-km 8
    uv run scripts/temple-walk/generate.py --start "13.7516,100.4927" --slug rattanakosin --dry-run
"""

import argparse
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
CACHE_DIR = SCRIPT_DIR / "cache"

USER_AGENT = {"User-Agent": "maps.girard-davila.net/temple-walk (girard.davila@gmail.com)"}
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSRM_BASE = "http://router.project-osrm.org/route/v1/foot"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"


# ── Pure helpers ──────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower()
    for src, dst in [("é","e"),("è","e"),("ê","e"),("à","a"),("â","a"),("ù","u"),("û","u"),("î","i"),("ô","o"),("ç","c")]:
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def stop_slug(stop: dict) -> str:
    """Filesystem-safe identifier for a stop; falls back to the OSM id when the name has no Latin characters."""
    return slugify(stop["name"]) or stop["osm_id"].replace("/", "-")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_start(value: str) -> tuple[float, float] | None:
    """Return (lat, lng) if value parses as "lat,lng", else None (treat as address)."""
    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        lat, lng = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def parse_overpass_elements(elements: list[dict]) -> list[dict]:
    """
    Extract named temples with coordinates from Overpass `out center` elements.
    Dedupes by name, preferring ways/relations over nodes (richer geometry).
    """
    temples: dict[str, dict] = {}
    for el in elements:
        name = el.get("tags", {}).get("name")
        if not name:
            continue
        if el["type"] == "node":
            lat, lng = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            lat, lng = center.get("lat"), center.get("lon")
        if lat is None or lng is None:
            continue
        existing = temples.get(name)
        if existing is None or (existing["osm_type"] == "node" and el["type"] != "node"):
            temples[name] = {
                "name": name,
                "lat": lat,
                "lng": lng,
                "osm_type": el["type"],
                "osm_id": f"{el['type']}/{el['id']}",
            }
    return list(temples.values())


def plan_walk(start: tuple[float, float], temples: list[dict], max_km: float, fetch_leg) -> dict:
    """
    Greedy nearest-neighbour chain from start through temples.

    Haversine picks each candidate; the real routed distance from fetch_leg
    gates acceptance. Stops when the next leg would push the total past
    max_km (a farther candidate implies an even longer leg, so no retries).

    fetch_leg(lat1, lng1, lat2, lng2) -> ([[lng, lat], ...], distance_km)
    """
    current = start
    total = 0.0
    stops: list[dict] = []
    all_coords: list[list[float]] = []
    segment_breaks: list[int] = []
    remaining = list(temples)

    while remaining:
        candidate = min(remaining, key=lambda t: haversine_km(current[0], current[1], t["lat"], t["lng"]))
        coords, dist = fetch_leg(current[0], current[1], candidate["lat"], candidate["lng"])
        if total + dist > max_km:
            break
        segment_breaks.append(len(all_coords))
        # Avoid duplicating the junction point between legs
        if all_coords and coords:
            coords = coords[1:]
        all_coords.extend(coords)
        total += dist
        stops.append({**candidate, "order": len(stops) + 1, "distance_km": round(total, 2)})
        current = (candidate["lat"], candidate["lng"])
        remaining.remove(candidate)

    # Final segment_break terminator (same convention as bangkok-citywalk)
    segment_breaks.append(len(all_coords) - 1 if all_coords else 0)
    if not stops:
        segment_breaks = []
        all_coords = []
    return {
        "stops": stops,
        "route_coords": all_coords,
        "segment_breaks": segment_breaks,
        "total_km": round(total, 2),
    }


def build_geojson(start: tuple[float, float], walk: dict, slug: str,
                  photos_by_name: dict[str, list[dict]]) -> dict:
    """Assemble the FeatureCollection: start point, temple stops, route line."""
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [start[1], start[0]]},
        "properties": {"name": "Start", "order": 0, "slug": "start", "photos": []},
    }]

    for stop in walk["stops"]:
        tslug = stop_slug(stop)
        entries = photos_by_name.get(stop["name"], [])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [stop["lng"], stop["lat"]]},
            "properties": {
                "name": stop["name"],
                "order": stop["order"],
                "slug": tslug,
                "osm_id": stop["osm_id"],
                "distance_km": stop["distance_km"],
                "photos": [f"/temple-walks/{slug}/photos/{tslug}-{j+1}.jpg" for j in range(len(entries))],
                "attribution": entries[0]["attribution"] if entries else "",
            },
        })

    if walk["route_coords"]:
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": walk["route_coords"]},
            "properties": {
                "type": "route",
                "segment_breaks": walk["segment_breaks"],
                "total_km": walk["total_km"],
            },
        })

    return {"type": "FeatureCollection", "features": features}


def build_content_page(slug: str, start_label: str, n_stops: int, total_km: float) -> str:
    title = f"Temple Walk — {slug.replace('-', ' ').title()}"
    return (
        "---\n"
        f'title: "{title}"\n'
        f'description: "{n_stops} temples, {total_km} km walking from {start_label}"\n'
        'type: "temple-walk"\n'
        f'geojson: "/temple-walks/{slug}/walk.geojson"\n'
        "---\n"
    )


# ── Network layer ─────────────────────────────────────────────────────────────

def http_json_retry(url: str, data: bytes | None = None, timeout: int = 60):
    """GET/POST JSON with one retry; abort printing the failing URL."""
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, data=data, headers=USER_AGENT)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            print(f"  ⚠ request failed (attempt {attempt}): {e}", file=sys.stderr)
            if attempt == 1:
                time.sleep(2)
    sys.exit(f"✗ aborting — request failed twice: {url}")


def geocode_start(address: str) -> tuple[float, float]:
    params = urllib.parse.urlencode({"q": address, "format": "json", "limit": 1, "accept-language": "en"})
    results = http_json_retry(f"{NOMINATIM_URL}?{params}")
    if not results:
        sys.exit(f"✗ could not geocode start address: {address!r}")
    return float(results[0]["lat"]), float(results[0]["lon"])


def fetch_temples(lat: float, lng: float, radius_m: float, cache_path: Path) -> list[dict]:
    """One Overpass query: named Buddhist temples within radius_m of (lat, lng)."""
    if cache_path.exists():
        data = load_json(cache_path)
    else:
        query = (
            "[out:json][timeout:60];\n"
            f'nwr["amenity"="place_of_worship"]["religion"="buddhist"]["name"]'
            f"(around:{radius_m:.0f},{lat:.6f},{lng:.6f});\n"
            "out center;"
        )
        data = http_json_retry(OVERPASS_URL, data=urllib.parse.urlencode({"data": query}).encode())
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        save_json(cache_path, data)
    return parse_overpass_elements(data.get("elements", []))


def make_leg_fetcher(cache_path: Path):
    """Returns fetch_leg(lat1, lng1, lat2, lng2) -> (coords, km), cached per pair."""
    cache = load_json(cache_path)

    def fetch_leg(lat1, lng1, lat2, lng2):
        key = f"{lat1:.6f},{lng1:.6f}→{lat2:.6f},{lng2:.6f}"
        if key not in cache:
            url = f"{OSRM_BASE}/{lng1},{lat1};{lng2},{lat2}?overview=full&geometries=geojson"
            data = http_json_retry(url, timeout=15)
            route = data["routes"][0]
            cache[key] = {"coords": route["geometry"]["coordinates"],
                          "km": route["distance"] / 1000.0}
            save_json(cache_path, cache)
            time.sleep(1.0)
        leg = cache[key]
        return leg["coords"], leg["km"]

    return fetch_leg


def fetch_wikimedia_photos(name: str, max_results: int = 5) -> list[tuple[str, str]]:
    """Up to max_results (thumb_url, attribution) pairs from Wikimedia Commons."""
    params = urllib.parse.urlencode({
        "action": "query",
        "generator": "search",
        "gsrnamespace": 6,
        "gsrsearch": name,
        "gsrlimit": max_results * 3,  # fetch extra to filter duds
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 1080,
        "format": "json",
    })
    results: list[tuple[str, str]] = []
    try:
        req = urllib.request.Request(f"{COMMONS_API}?{params}", headers=USER_AGENT)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            if len(results) >= max_results:
                break
            ii = page.get("imageinfo", [{}])[0]
            thumb = ii.get("thumburl", "")
            if not thumb:
                continue
            meta = ii.get("extmetadata", {})
            artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
            license_name = meta.get("LicenseShortName", {}).get("value", "")
            attribution = f"© {artist} / {license_name}" if artist else license_name
            results.append((thumb, attribution))
    except Exception as e:
        print(f"  ⚠ Wikimedia error for '{name}': {e}", file=sys.stderr)
    return results


def fetch_photos(stops: list[dict], photos_dir: Path, cache_path: Path, dry_run: bool) -> dict[str, list[dict]]:
    """Download up to 5 photos per temple. Returns {name: [{"url", "attribution"}, ...]}."""
    photos_by_name: dict[str, list[dict]] = {}
    cache = load_json(cache_path)

    for stop in tqdm(stops, desc="Wikimedia photos", unit="temple"):
        name = stop["name"]
        tslug = stop_slug(stop)

        cached = cache.get(name)
        if cached is not None:
            entries = cached["photos"]
            if all((photos_dir / f"{tslug}-{i+1}.jpg").exists() for i in range(len(entries))):
                photos_by_name[name] = entries
                continue

        if dry_run:
            tqdm.write(f"  [dry-run] would fetch photos: {name}")
            photos_by_name[name] = []
            continue

        photos_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        for i, (thumb_url, attribution) in enumerate(fetch_wikimedia_photos(name)):
            dest = photos_dir / f"{tslug}-{i+1}.jpg"
            try:
                req = urllib.request.Request(thumb_url, headers=USER_AGENT)
                with urllib.request.urlopen(req, timeout=30) as r:
                    dest.write_bytes(r.read())
                entries.append({"url": thumb_url, "attribution": attribution})
                tqdm.write(f"  → {name} [{i+1}]: {dest.name}")
            except Exception as e:
                print(f"  ⚠ download failed for '{name}' photo {i+1}: {e}", file=sys.stderr)
            time.sleep(0.3)

        if not entries:
            print(f"  ⚠ no photos for: {name}", file=sys.stderr)
        cache[name] = {"photos": entries}
        save_json(cache_path, cache)
        photos_by_name[name] = entries
        time.sleep(0.5)

    return photos_by_name


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", required=True, help='"lat,lng" or an address (geocoded via Nominatim)')
    parser.add_argument("--slug", required=True, help="output identifier, used in all paths")
    parser.add_argument("--max-km", type=float, default=10.0, help="walking-distance budget (default 10)")
    parser.add_argument("--dry-run", action="store_true", help="print planned actions, no network writes")
    args = parser.parse_args()

    slug = slugify(args.slug)
    static_dir = REPO_ROOT / "static/temple-walks" / slug
    photos_dir = static_dir / "photos"
    content_path = REPO_ROOT / "content/temple-walks" / f"{slug}.md"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Phase 1: resolve start
    print("Phase 1: Resolving start…")
    start = parse_start(args.start)
    if start is None:
        if args.dry_run:
            sys.exit('✗ --dry-run needs a "lat,lng" start (address geocoding requires network)')
        start = geocode_start(args.start)
    print(f"  start: {start[0]:.5f}, {start[1]:.5f}")

    # Phase 2: discover temples (one Overpass query within the walk budget radius —
    # walking distance ≥ straight-line distance, so nothing reachable lies outside it)
    print("\nPhase 2: Overpass temple discovery…")
    overpass_cache = CACHE_DIR / f"{slug}.overpass.json"
    if args.dry_run and not overpass_cache.exists():
        print(f"  [dry-run] would query Overpass (around:{args.max_km * 1000:.0f} m) and chain temples with OSRM")
        return
    temples = fetch_temples(start[0], start[1], args.max_km * 1000, overpass_cache)
    print(f"  {len(temples)} named Buddhist temples within {args.max_km:.1f} km")
    if not temples:
        sys.exit(f"✗ no named Buddhist temples within {args.max_km:.1f} km of start")

    # Phase 3: greedy chain
    print("\nPhase 3: Greedy walk (OSRM)…")
    if args.dry_run:
        print(f"  [dry-run] would chain up to {len(temples)} temples with OSRM foot legs")
        return
    walk = plan_walk(start, temples, args.max_km, make_leg_fetcher(CACHE_DIR / f"{slug}.routes.json"))
    if not walk["stops"]:
        sys.exit(f"✗ nearest temple is already beyond the {args.max_km:.1f} km budget")
    if len(walk["stops"]) <= 2:
        print(f"  ⚠ short walk: only {len(walk['stops'])} temple stop(s)", file=sys.stderr)
    for s in walk["stops"]:
        print(f"  {s['order']:2d}. {s['name']} ({s['distance_km']:.2f} km)")
    print(f"  total: {walk['total_km']:.2f} km, {len(walk['stops'])} temples")

    # Phase 4: photos (non-fatal)
    print("\nPhase 4: Wikimedia photos…")
    photos_by_name = fetch_photos(walk["stops"], photos_dir, CACHE_DIR / f"{slug}.media.json", args.dry_run)

    # Phase 5: write outputs
    print("\nPhase 5: Writing outputs…")
    fc = build_geojson(start, walk, slug, photos_by_name)
    static_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = static_dir / "walk.geojson"
    geojson_path.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    print(f"  ✓ {geojson_path} — {len(fc['features'])} features")

    if content_path.exists():
        print(f"  = {content_path} exists — left untouched (only GeoJSON/photos regenerated)")
    else:
        content_path.parent.mkdir(parents=True, exist_ok=True)
        content_path.write_text(build_content_page(slug, args.start, len(walk["stops"]), walk["total_km"]))
        print(f"  ✓ {content_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
