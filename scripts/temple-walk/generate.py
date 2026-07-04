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
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
CACHE_DIR = SCRIPT_DIR / "cache"

from lib import (  # noqa: E402
    slugify, stop_slug, load_json, save_json,
    haversine_km, parse_start, parse_overpass_elements,
    DEGENERATE_LEG_KM, resolve_leg, plan_walk,
    build_geojson, build_content_page,
    http_json_retry, geocode_start, fetch_temples,
    make_leg_fetcher, fetch_wikimedia_photos, fetch_photos,
)


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

    print("Phase 1: Resolving start…")
    start = parse_start(args.start)
    if start is None:
        if args.dry_run:
            sys.exit('✗ --dry-run needs a "lat,lng" start (address geocoding requires network)')
        start = geocode_start(args.start)
    print(f"  start: {start[0]:.5f}, {start[1]:.5f}")

    print("\nPhase 2: Overpass temple discovery…")
    overpass_cache = CACHE_DIR / f"{slug}.overpass.json"
    if args.dry_run and not overpass_cache.exists():
        print(f"  [dry-run] would query Overpass (around:{args.max_km * 1000:.0f} m) and chain temples with OSRM")
        return
    temples = fetch_temples(start[0], start[1], args.max_km * 1000, overpass_cache)
    print(f"  {len(temples)} named Buddhist temples within {args.max_km:.1f} km")
    if not temples:
        sys.exit(f"✗ no named Buddhist temples within {args.max_km:.1f} km of start")

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

    print("\nPhase 4: Wikimedia photos…")
    photos_by_name = fetch_photos(walk["stops"], photos_dir, CACHE_DIR / f"{slug}.media.json", args.dry_run)

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
