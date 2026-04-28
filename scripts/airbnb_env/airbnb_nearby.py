#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "python-dotenv", "rich", "beautifulsoup4", "overpass"]
# ///
"""
Given an Airbnb listing URL and optionally a Google Maps URL (from the host
page map embed), find nearby family-friendly POIs: supermarkets, parks,
playgrounds, transit stops, and kid activities.

Coordinates are resolved in priority order:
  1. --lat / --lon flags
  2. --gmaps URL  (parses ?ll=lat,lon — fast, exact, no HTTP request)
  3. Airbnb page HTML scraping (fallback)

Usage:
    uv run --script scripts/airbnb_env/airbnb_nearby.py \\
        https://www.airbnb.fr/rooms/1612148974271274765 \\
        --gmaps "https://www.google.com/maps?ll=4.588657,101.095776&z=16&..."

    uv run --script scripts/airbnb_env/airbnb_nearby.py \\
        https://www.airbnb.fr/rooms/1612148974271274765 --output geojson
"""

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import overpass as overpass_lib
import requests
from bs4 import BeautifulSoup, Tag
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GOOGLE_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"

_overpass_api = overpass_lib.API(timeout=40, headers={"User-Agent": "travel-guide-airbnb-nearby/1.0"})
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

CATEGORIES: dict[str, dict] = {
    "supermarket": {
        "label": "Supermarket",
        "icon": "🛒",
        "overpass": (
            'node(around:{r},{lat},{lon})["shop"~"^(supermarket|grocery|convenience)$"];'
            'way(around:{r},{lat},{lon})["shop"~"^(supermarket|grocery|convenience)$"];'
        ),
        "google_types": ["supermarket", "grocery_store", "convenience_store"],
    },
    "park": {
        "label": "Park",
        "icon": "🌳",
        "overpass": (
            'node(around:{r},{lat},{lon})["leisure"~"^(park|garden)$"];'
            'way(around:{r},{lat},{lon})["leisure"~"^(park|garden)$"];'
            'relation(around:{r},{lat},{lon})["leisure"~"^(park|garden)$"];'
        ),
        "google_types": ["park", "national_park"],
    },
    "playground": {
        "label": "Playground",
        "icon": "🛝",
        "overpass": (
            'node(around:{r},{lat},{lon})["leisure"="playground"];'
            'way(around:{r},{lat},{lon})["leisure"="playground"];'
        ),
        "google_types": ["playground"],
    },
    "transit": {
        "label": "Transit",
        "icon": "🚌",
        "overpass": (
            'node(around:{r},{lat},{lon})["highway"="bus_stop"];'
            'node(around:{r},{lat},{lon})["amenity"="bus_station"];'
            'node(around:{r},{lat},{lon})["railway"~"^(station|tram_stop|halt)$"];'
            'way(around:{r},{lat},{lon})["railway"~"^(station|tram_stop|halt)$"];'
        ),
        "google_types": ["bus_station", "train_station", "subway_station", "transit_station"],
    },
    "activities": {
        "label": "Activity",
        "icon": "🎠",
        "overpass": (
            'node(around:{r},{lat},{lon})["tourism"~"^(museum|aquarium|theme_park)$"];'
            'way(around:{r},{lat},{lon})["tourism"~"^(museum|aquarium|theme_park)$"];'
            'node(around:{r},{lat},{lon})["leisure"~"^(swimming_pool|water_park|miniature_golf|sports_centre)$"]["access"!="private"];'
            'way(around:{r},{lat},{lon})["leisure"~"^(swimming_pool|water_park|miniature_golf|sports_centre)$"]["access"!="private"];'
        ),
        "google_types": ["museum", "aquarium", "amusement_park", "water_park", "swimming_pool"],
    },
}

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Find family-friendly POIs near an Airbnb listing"
    )
    p.add_argument("airbnb_url", help="Airbnb listing URL (used as the listing link in output)")
    p.add_argument(
        "--gmaps",
        metavar="URL",
        help='Google Maps URL from Airbnb host page (e.g. ?ll=4.588657,101.095776) — preferred coordinate source',
    )
    p.add_argument("--lat", type=float, help="Latitude override (skips all URL-based extraction)")
    p.add_argument("--lon", type=float, help="Longitude override (skips all URL-based extraction)")
    p.add_argument("--radius", type=float, default=1000, help="Search radius in metres (default: 1000)")
    p.add_argument(
        "--output",
        choices=["table", "json", "geojson"],
        default="table",
        help="Output format (default: table)",
    )
    p.add_argument(
        "--categories",
        default=",".join(CATEGORIES.keys()),
        help="Comma-separated categories to search (default: all)",
    )
    p.add_argument(
        "--no-google",
        action="store_true",
        help="Disable Google Places fallback even if GOOGLE_MAPS_API_KEY is set",
    )
    p.add_argument("--env", default=None, help="Path to .env file")
    return p.parse_args()


# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------

def load_env(env_arg: str | None) -> None:
    if env_arg:
        load_dotenv(env_arg)
        return
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent.parent
    for candidate in [Path(".env"), script_dir / ".env", repo_root / ".env"]:
        if candidate.exists():
            load_dotenv(candidate)
            return


# ---------------------------------------------------------------------------
# Coordinate resolution
# ---------------------------------------------------------------------------

def coords_from_gmaps_url(url: str) -> tuple[float, float]:
    qs = parse_qs(urlparse(url).query)
    ll = qs.get("ll", [None])[0]
    if ll:
        parts = ll.split(",")
        if len(parts) == 2:
            return float(parts[0]), float(parts[1])
    raise ValueError(f"No valid 'll' parameter in Google Maps URL: {url}")


def _find_coords_recursive(obj, depth: int = 0) -> tuple[float, float] | None:
    if depth > 20:
        return None
    if isinstance(obj, dict):
        lat = obj.get("lat") or obj.get("latitude")
        lng = obj.get("lng") or obj.get("longitude")
        if lat is not None and lng is not None:
            try:
                lat_f, lng_f = float(lat), float(lng)
                if -90 <= lat_f <= 90 and -180 <= lng_f <= 180 and (lat_f != 0 or lng_f != 0):
                    return lat_f, lng_f
            except (TypeError, ValueError):
                pass
        for v in obj.values():
            result = _find_coords_recursive(v, depth + 1)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_coords_recursive(item, depth + 1)
            if result:
                return result
    return None


def coords_from_airbnb_url(url: str) -> tuple[float, float]:
    print(f"Fetching Airbnb page to extract coordinates...", file=sys.stderr)
    resp = requests.get(url, headers=BROWSER_HEADERS, timeout=20)
    if resp.status_code != 200:
        print(f"Error: Airbnb returned HTTP {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    soup = BeautifulSoup(resp.text, "html.parser")

    # Pass 1: targeted script IDs
    for script_id in ("data-deferred-state", "data-state", "__NEXT_DATA__"):
        tag = soup.find("script", id=script_id)
        if isinstance(tag, Tag) and tag.string:
            try:
                data = json.loads(tag.string)
                result = _find_coords_recursive(data)
                if result:
                    print(f"  Coordinates found in <script id={script_id!r}>", file=sys.stderr)
                    return result
            except (json.JSONDecodeError, ValueError):
                pass

    # Pass 2: all application/json script tags
    for tag in soup.find_all("script", type="application/json"):
        if isinstance(tag, Tag) and tag.string:
            try:
                data = json.loads(tag.string)
                result = _find_coords_recursive(data)
                if result:
                    print("  Coordinates found in <script type=application/json>", file=sys.stderr)
                    return result
            except (json.JSONDecodeError, ValueError):
                pass

    # Pass 3: regex scan over all inline scripts
    coord_re = re.compile(
        r'"(?:lat|latitude)"\s*:\s*(-?\d{1,3}\.\d{4,})'
        r'.*?"(?:lng|longitude)"\s*:\s*(-?\d{1,3}\.\d{4,})',
        re.DOTALL,
    )
    for tag in soup.find_all("script"):
        if not isinstance(tag, Tag) or tag.get("src"):
            continue
        text = tag.string or ""
        m = coord_re.search(text)
        if m:
            lat_f, lng_f = float(m.group(1)), float(m.group(2))
            if -90 <= lat_f <= 90 and -180 <= lng_f <= 180 and (lat_f != 0 or lng_f != 0):
                print("  Coordinates found via regex scan", file=sys.stderr)
                return lat_f, lng_f

    # Last resort: Nominatim geocode of page title
    title_tag = soup.find("title")
    og_desc = soup.find("meta", property="og:description")
    title_str = title_tag.string if isinstance(title_tag, Tag) else None
    og_content = og_desc.get("content") if isinstance(og_desc, Tag) else None  # type: ignore[union-attr]
    query_text = title_str or og_content
    if query_text:
        print(f"  Falling back to Nominatim geocode of: {query_text!r}", file=sys.stderr)
        nom_resp = requests.get(
            NOMINATIM_URL,
            params={"q": query_text, "format": "json", "limit": 1},
            headers={"User-Agent": "travel-guide-airbnb-nearby/1.0"},
            timeout=10,
        )
        if nom_resp.ok and nom_resp.json():
            hit = nom_resp.json()[0]
            print("  Warning: coordinates from Nominatim (low accuracy)", file=sys.stderr)
            return float(hit["lat"]), float(hit["lon"])

    print(
        "Error: could not extract coordinates from Airbnb page.\n"
        "  Use --gmaps or --lat/--lon to provide coordinates directly.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Distance (Haversine, metres)
# ---------------------------------------------------------------------------

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Overpass
# ---------------------------------------------------------------------------

def _overpass_query(category_key: str, lat: float, lon: float, radius: float) -> list[dict]:
    filters = CATEGORIES[category_key]["overpass"].format(r=int(radius), lat=lat, lon=lon)
    # build=False lets us pass the raw filter block; library wraps it in [out:json][timeout:...]
    # verbosity="body center" appends `out body center` so way/relation elements include a center point
    try:
        response = _overpass_api.get(
            f"({filters});",
            responseformat="json",
            verbosity="body center",
        )
    except Exception as e:
        print(f"  Overpass error for {category_key}: {e}", file=sys.stderr)
        return []
    elements = response.get("elements", [])
    pois = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("name:en")
        if not name:
            continue
        if el["type"] == "node":
            elat, elon = el["lat"], el["lon"]
        else:
            center = el.get("center", {})
            elat, elon = center.get("lat"), center.get("lon")
            if elat is None:
                continue
        pois.append({
            "name": name,
            "lat": elat,
            "lon": elon,
            "category": CATEGORIES[category_key]["label"],
            "icon": CATEGORIES[category_key]["icon"],
            "source": "osm",
            "coord_source": "osm",
            "coord_accuracy": "high",
        })
    return pois


def query_overpass(categories: list[str], lat: float, lon: float, radius: float) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    for i, cat in enumerate(categories):
        if i > 0:
            time.sleep(1)
        print(f"  Querying OSM for {cat}...", file=sys.stderr)
        results[cat] = _overpass_query(cat, lat, lon, radius)
    return results


# ---------------------------------------------------------------------------
# Google Places (optional)
# ---------------------------------------------------------------------------

def query_google_nearby(
    api_key: str,
    categories: list[str],
    lat: float,
    lon: float,
    radius: float,
) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    field_mask = "places.id,places.displayName,places.location,places.types"
    for cat in categories:
        types = CATEGORIES[cat]["google_types"]
        body = {
            "includedTypes": types,
            "maxResultCount": 20,
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lon},
                    "radius": radius,
                }
            },
        }
        try:
            resp = requests.post(
                GOOGLE_NEARBY_URL,
                json=body,
                headers={
                    "X-Goog-Api-Key": api_key,
                    "X-Goog-FieldMask": field_mask,
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if not resp.ok:
                print(f"  Google Places error for {cat}: {resp.status_code} {resp.text[:120]}", file=sys.stderr)
                results[cat] = []
                continue
        except requests.RequestException as e:
            print(f"  Google Places request error for {cat}: {e}", file=sys.stderr)
            results[cat] = []
            continue

        pois = []
        for place in resp.json().get("places", []):
            loc = place.get("location", {})
            plat = loc.get("latitude")
            plon = loc.get("longitude")
            if plat is None:
                continue
            name = place.get("displayName", {}).get("text") or place.get("id", "")
            pois.append({
                "name": name,
                "lat": plat,
                "lon": plon,
                "category": CATEGORIES[cat]["label"],
                "icon": CATEGORIES[cat]["icon"],
                "source": "google",
                "coord_source": "google_maps_pin",
                "coord_accuracy": "high",
            })
        results[cat] = pois
    return results


# ---------------------------------------------------------------------------
# Merge + dedup
# ---------------------------------------------------------------------------

def _dedup_key(poi: dict) -> tuple[float, float]:
    return round(poi["lat"], 4), round(poi["lon"], 4)


def merge_results(
    osm: dict[str, list[dict]],
    google: dict[str, list[dict]] | None,
) -> dict[str, list[dict]]:
    merged: dict[str, list[dict]] = {}
    for cat in osm:
        seen: set[tuple[float, float]] = set()
        pois = list(osm.get(cat, []))
        for p in pois:
            seen.add(_dedup_key(p))
        if google and cat in google:
            for gp in google[cat]:
                if _dedup_key(gp) not in seen:
                    pois.append(gp)
                    seen.add(_dedup_key(gp))
        merged[cat] = pois
    return merged


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _fmt_dist(metres: float) -> str:
    return f"{int(metres)} m" if metres < 1000 else f"{metres/1000:.1f} km"


def output_table(
    airbnb_url: str,
    lat: float,
    lon: float,
    results: dict[str, list[dict]],
    radius: float,
) -> None:
    console = Console()
    console.print(f"\n[bold]Airbnb listing:[/bold] {airbnb_url}")
    console.print(f"[bold]Coordinates:[/bold] {lat:.6f}°, {lon:.6f}°  (radius: {_fmt_dist(radius)})\n")

    for cat, pois in results.items():
        meta = CATEGORIES[cat]
        label = f"{meta['icon']} {meta['label'].upper()}"
        if not pois:
            console.print(f"[dim]{label} — none found within {_fmt_dist(radius)}[/dim]\n")
            continue
        t = Table(title=f"{label} ({len(pois)})", show_header=True, header_style="bold")
        t.add_column("Name", style="cyan", no_wrap=False)
        t.add_column("Distance", justify="right")
        t.add_column("Source", style="dim")
        for p in sorted(pois, key=lambda x: haversine(lat, lon, x["lat"], x["lon"])):
            dist = haversine(lat, lon, p["lat"], p["lon"])
            t.add_row(p["name"], _fmt_dist(dist), p["source"].upper())
        console.print(t)
        console.print()


def output_json(
    airbnb_url: str,
    lat: float,
    lon: float,
    results: dict[str, list[dict]],
    radius: float,
) -> None:
    payload = {
        "listing_url": airbnb_url,
        "coordinates": {"lat": lat, "lon": lon},
        "radius_m": radius,
        "generated": datetime.now(timezone.utc).isoformat(),
        "results": {
            cat: [
                {**p, "distance_m": round(haversine(lat, lon, p["lat"], p["lon"]))}
                for p in sorted(pois, key=lambda x: haversine(lat, lon, x["lat"], x["lon"]))
            ]
            for cat, pois in results.items()
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def output_geojson(
    airbnb_url: str,
    lat: float,
    lon: float,
    results: dict[str, list[dict]],
    radius: float,
) -> None:
    features = []
    seq = 1
    for pois in results.values():
        for p in sorted(pois, key=lambda x: haversine(lat, lon, x["lat"], x["lon"])):
            features.append({
                "type": "Feature",
                "id": f"airbnb-nearby-{seq:03d}",
                "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                "properties": {
                    "name": p["name"],
                    "category": p["category"],
                    "icon": p["icon"],
                    "coord_source": p["coord_source"],
                    "coord_accuracy": p["coord_accuracy"],
                    "source": p["source"],
                    "listing_url": airbnb_url,
                },
            })
            seq += 1

    fc = {
        "type": "FeatureCollection",
        "_meta": {
            "crs": "EPSG:4326",
            "generated": datetime.now(timezone.utc).isoformat(),
            "source": f"OSM Overpass + Google Places — {airbnb_url}",
            "listing_url": airbnb_url,
            "center": {"lat": lat, "lon": lon},
            "radius_m": radius,
        },
        "features": features,
    }
    print(json.dumps(fc, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    load_env(args.env)

    # --- Resolve coordinates ---
    if args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
        print(f"Using coordinates from --lat/--lon: {lat}, {lon}", file=sys.stderr)
    elif args.gmaps:
        lat, lon = coords_from_gmaps_url(args.gmaps)
        print(f"Coordinates from Google Maps URL: {lat}, {lon}", file=sys.stderr)
    else:
        lat, lon = coords_from_airbnb_url(args.airbnb_url)
        print(f"Coordinates from Airbnb page: {lat}, {lon}", file=sys.stderr)

    # --- Validate categories ---
    requested = [c.strip() for c in args.categories.split(",") if c.strip()]
    unknown = [c for c in requested if c not in CATEGORIES]
    if unknown:
        print(f"Error: unknown categories: {', '.join(unknown)}", file=sys.stderr)
        print(f"  Valid: {', '.join(CATEGORIES.keys())}", file=sys.stderr)
        sys.exit(1)

    # --- Query OSM Overpass ---
    print(f"\nSearching within {_fmt_dist(args.radius)} of {lat:.5f}, {lon:.5f}...", file=sys.stderr)
    osm_results = query_overpass(requested, lat, lon, args.radius)

    # --- Optional Google Places fallback ---
    google_results = None
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if api_key and not args.no_google:
        print("  Querying Google Places...", file=sys.stderr)
        google_results = query_google_nearby(api_key, requested, lat, lon, args.radius)
    elif not api_key:
        print("  GOOGLE_MAPS_API_KEY not set — using OSM only", file=sys.stderr)

    # --- Merge ---
    results = merge_results(osm_results, google_results)

    # --- Output ---
    if args.output == "table":
        output_table(args.airbnb_url, lat, lon, results, args.radius)
    elif args.output == "json":
        output_json(args.airbnb_url, lat, lon, results, args.radius)
    else:
        output_geojson(args.airbnb_url, lat, lon, results, args.radius)


if __name__ == "__main__":
    main()
