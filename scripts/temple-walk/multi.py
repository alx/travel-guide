#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Generate multiple stochastic temple-walk routes from a single starting point.

Runs plan_walk_random N times with independent seeds, producing a combined
GeoJSON for visualization. Shares Overpass + OSRM caches with generate.py.
Photos are never fetched.

Usage:
    uv run scripts/temple-walk/multi.py --start "13.7516,100.4927" --slug rattanakosin
    uv run scripts/temple-walk/multi.py --start "13.7516,100.4927" --slug rattanakosin --runs 30 --max-km 10
"""

import argparse
import json
import random
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
CACHE_DIR = SCRIPT_DIR / "cache"

from lib import (  # noqa: E402
    slugify, parse_start, geocode_start,
    fetch_temples, make_leg_fetcher, plan_walk_random,
)


def build_multi_geojson(start: tuple[float, float], walks: list[dict]) -> dict:
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [start[1], start[0]]},
        "properties": {"type": "start", "name": "Start", "order": 0},
    }]

    seen: set[str] = set()
    for walk in walks:
        for stop in walk["stops"]:
            if stop["name"] not in seen:
                seen.add(stop["name"])
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [stop["lng"], stop["lat"]]},
                    "properties": {"type": "stop", "name": stop["name"]},
                })

    for i, walk in enumerate(walks):
        if walk["route_coords"]:
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": walk["route_coords"]},
                "properties": {
                    "type": "route",
                    "walk_index": i,
                    "n_stops": len(walk["stops"]),
                    "total_km": walk["total_km"],
                },
            })

    return {"type": "FeatureCollection", "features": features}


def build_multi_content_page(slug: str, start_label: str, n_runs: int,
                              min_km: float, max_km: float) -> str:
    title = f"Temple Walk Explorer — {slug.replace('-', ' ').title()}"
    return (
        "---\n"
        f'title: "{title}"\n'
        f'description: "{n_runs} walks, {min_km}–{max_km} km, from {start_label}"\n'
        'type: "temple-walk-multi"\n'
        f'geojson: "/temple-walks/{slug}/multi-walk.geojson"\n'
        "---\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", required=True, help='"lat,lng" or an address')
    parser.add_argument("--slug", required=True, help="output identifier, shared with generate.py")
    parser.add_argument("--runs", type=int, default=20, help="number of walks to generate (default 20)")
    parser.add_argument("--max-km", type=float, default=10.0, help="walking-distance budget per walk (default 10)")
    args = parser.parse_args()

    slug = slugify(args.slug)
    static_dir = REPO_ROOT / "static/temple-walks" / slug
    content_path = REPO_ROOT / "content/temple-walks" / f"{slug}-multi.md"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print("Phase 1: Resolving start…")
    start = parse_start(args.start)
    if start is None:
        start = geocode_start(args.start)
    print(f"  start: {start[0]:.5f}, {start[1]:.5f}")

    print("\nPhase 2: Overpass temple discovery…")
    overpass_cache = CACHE_DIR / f"{slug}.overpass.json"
    temples = fetch_temples(start[0], start[1], args.max_km * 1000, overpass_cache)
    print(f"  {len(temples)} named Buddhist temples within {args.max_km:.1f} km")
    if not temples:
        sys.exit(f"✗ no named Buddhist temples within {args.max_km:.1f} km of start")

    print(f"\nPhase 3: Generating {args.runs} stochastic walks…")
    fetch_leg = make_leg_fetcher(CACHE_DIR / f"{slug}.routes.json")
    walks = []
    for i in range(args.runs):
        rng = random.Random(i)
        walk = plan_walk_random(start, list(temples), args.max_km, fetch_leg, rng)
        walks.append(walk)
        print(f"  walk {i+1:3d}: {len(walk['stops'])} temples, {walk['total_km']:.2f} km")

    nonempty = [w for w in walks if w["stops"]]
    if not nonempty:
        sys.exit("✗ all walks produced zero stops — check start point and radius")

    km_values = [w["total_km"] for w in nonempty]
    print(f"  {len(nonempty)}/{args.runs} non-empty · {min(km_values):.2f}–{max(km_values):.2f} km range")

    print("\nPhase 4: Writing outputs…")
    fc = build_multi_geojson(start, walks)
    static_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = static_dir / "multi-walk.geojson"
    geojson_path.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    print(f"  ✓ {geojson_path} — {len(fc['features'])} features")

    if content_path.exists():
        print(f"  = {content_path} exists — left untouched")
    else:
        content_path.parent.mkdir(parents=True, exist_ok=True)
        content_path.write_text(
            build_multi_content_page(
                slug, args.start, len(nonempty),
                round(min(km_values), 2), round(max(km_values), 2),
            )
        )
        print(f"  ✓ {content_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
