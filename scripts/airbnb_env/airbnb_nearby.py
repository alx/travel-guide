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

    # With PR:
    uv run --script scripts/airbnb_env/airbnb_nearby.py \\
        https://www.airbnb.fr/rooms/1612148974271274765 \\
        --gmaps "https://www.google.com/maps?ll=4.588657,101.095776&z=16&..." \\
        --slug ipoh-airbnb --title "Ipoh — Autour de l'Airbnb" --pr
"""

import argparse
import base64
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
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
UPSTREAM_REPO = "alx/travel-guide"
GITHUB_API = "https://api.github.com"

_overpass_api = overpass_lib.API(timeout=40, headers={"User-Agent": "travel-guide-airbnb-nearby/1.0"})

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
        "radius": 300,
        "overpass": (
            'node(around:{r},{lat},{lon})["shop"~"^(supermarket|grocery|convenience)$"];'
            'way(around:{r},{lat},{lon})["shop"~"^(supermarket|grocery|convenience)$"];'
        ),
        "google_types": ["supermarket", "grocery_store", "convenience_store"],
    },
    "park": {
        "label": "Park",
        "icon": "🌳",
        "radius": 1000,
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
        "radius": 1000,
        "overpass": (
            'node(around:{r},{lat},{lon})["leisure"="playground"];'
            'way(around:{r},{lat},{lon})["leisure"="playground"];'
        ),
        "google_types": ["playground"],
    },
    "transit": {
        "label": "Transit",
        "icon": "🚌",
        "radius": 300,
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
        "radius": 1000,
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
        help="Google Maps URL from Airbnb host page (?ll=lat,lon) — preferred coordinate source",
    )
    p.add_argument("--lat", type=float, help="Latitude override (skips all URL-based extraction)")
    p.add_argument("--lon", type=float, help="Longitude override (skips all URL-based extraction)")
    p.add_argument("--radius", type=float, default=1000, help="Search radius in metres (default: 1000)")
    p.add_argument(
        "--output",
        choices=["table", "json", "geojson"],
        default="table",
        help="Output format without --pr (default: table)",
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
    # PR options
    p.add_argument("--pr", action="store_true", help="Create a GitHub PR against alx/travel-guide")
    p.add_argument(
        "--slug",
        default=None,
        help="URL slug for the map page (e.g. ipoh-airbnb). Auto-derived from listing ID if omitted.",
    )
    p.add_argument(
        "--title",
        default=None,
        help="Map title shown in the sidebar (default: auto-generated from slug)",
    )
    p.add_argument(
        "--description",
        default=None,
        help="Map description for the Hugo content file",
    )
    p.add_argument(
        "--github-token",
        default=os.getenv("GITHUB_TOKEN", ""),
        help="GitHub PAT with repo scope (or set GITHUB_TOKEN env var)",
    )
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
    print("Fetching Airbnb page to extract coordinates...", file=sys.stderr)
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


def listing_id_from_url(url: str) -> str:
    """Extract the numeric listing ID from an Airbnb URL."""
    m = re.search(r"/rooms/(\d+)", url)
    return m.group(1) if m else "unknown"


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


def _fmt_dist(metres: float) -> str:
    return f"{int(metres)} m" if metres < 1000 else f"{metres/1000:.1f} km"


# ---------------------------------------------------------------------------
# Overpass
# ---------------------------------------------------------------------------

def _overpass_query(category_key: str, lat: float, lon: float, radius: float) -> list[dict]:
    overpass_radius = int(CATEGORIES[category_key].get("radius", radius))
    filters = CATEGORIES[category_key]["overpass"].format(r=overpass_radius, lat=lat, lon=lon)
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
# GeoJSON builder
# ---------------------------------------------------------------------------

def build_geojson(
    airbnb_url: str,
    lat: float,
    lon: float,
    results: dict[str, list[dict]],
    radius: float,
    slug: str,
) -> dict:
    features = []
    seq = 1
    for pois in results.values():
        for p in sorted(pois, key=lambda x: haversine(lat, lon, x["lat"], x["lon"])):
            features.append({
                "type": "Feature",
                "id": f"{slug}-{seq:03d}",
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
    return {
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


# ---------------------------------------------------------------------------
# Output (table / json / geojson to stdout)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# PR content builders
# ---------------------------------------------------------------------------

def _hugo_content(title: str, description: str, categories: list[str]) -> str:
    tags = [f"{CATEGORIES[c]['icon']} {CATEGORIES[c]['label']}" for c in categories if c in CATEGORIES]
    return f"""---
title: "{title}"
description: "{description}"
emoji: "🏠"
section: "community"
weight: 55
accent_color: "#1a6b3c"
tags: {json.dumps(tags, ensure_ascii=False)}
---
"""


def _layout_html(slug: str, title: str, description: str, airbnb_url: str, lat: float, lon: float) -> str:
    # Escape single quotes for inline JS string literals
    js_title = title.replace("'", "\\'")
    return f"""{{{{ define "head" }}}}
<style>
  body {{ overflow: hidden; }}
  .site-header, footer {{ display: none !important; }}
  main {{ max-width: 100% !important; padding: 0 !important; margin: 0 !important; }}
  #map {{ position: fixed; inset: 0; z-index: 0; border-radius: 0 !important; box-shadow: none !important; }}
  .map-overlay {{
    position: fixed; left: 1rem; top: 1rem; bottom: 1rem;
    width: 340px; z-index: 100;
    display: flex; flex-direction: column; gap: 0.5rem;
    pointer-events: none;
  }}
  .map-overlay > * {{ pointer-events: all; }}
  .overlay-card {{
    background: rgba(255,255,255,0.95);
    backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
    border-radius: 12px; padding: 0.85rem 1rem;
    box-shadow: 0 4px 20px rgba(0,0,0,0.13); border: 1px solid rgba(255,255,255,0.6);
  }}
  .overlay-card.scrollable {{
    flex: 1; min-height: 0; overflow-y: auto;
    display: flex; flex-direction: column; gap: 0.5rem;
  }}
  .overlay-card .page-header {{ margin: 0; }}
  .overlay-card .page-header h1 {{ font-size: 1.3rem; font-weight: 700; margin-bottom: 0.25rem; }}
  .overlay-card .page-header p {{ font-size: 0.82rem; color: #666; }}
  .site-brand {{ font-size: 0.7rem; font-weight: 600; color: #1a3a5c; text-decoration: none; display: block; margin-bottom: 0.35rem; opacity: 0.65; }}
  .site-brand:hover {{ opacity: 1; }}
  #map-toolbar {{ display: none !important; margin: 0.6rem 0 0 !important; }}
  #map-toolbar.tb-visible {{ display: flex !important; }}
  .header-actions {{ display: flex; gap: 0.4rem; margin-top: 0.5rem; flex-wrap: wrap; align-items: center; }}
  .header-btn {{ padding: 0.2rem 0.6rem; border-radius: 16px; border: 1.5px solid #ddd; background: white; cursor: pointer; font-size: 0.72rem; color: #555; transition: all 0.15s; }}
  .header-btn:hover, .header-btn.active {{ border-color: #1a6b3c; color: #1a6b3c; background: #f0f8f4; }}
  #list-toggle {{ display: none; }}
  .filter-btn {{
    padding: 0.35rem 0.75rem; border-radius: 20px; border: 1.5px solid #ddd;
    background: white; cursor: pointer; font-size: 0.8rem; font-weight: 500; transition: all 0.15s;
  }}
  .filter-btn.active {{ background: #1a6b3c; color: white; border-color: #1a6b3c; }}
  #filter-btns {{ display: flex; gap: 0.5rem; flex-wrap: wrap; }}
  .poi-card {{
    background: white; border-radius: 10px; padding: 0.85rem 1rem;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08); cursor: pointer;
    border: 2px solid transparent; transition: all 0.15s; flex-shrink: 0;
  }}
  .poi-card:hover, .poi-card.active {{ border-color: #1a6b3c; }}
  .poi-card h3 {{ font-size: 0.9rem; font-weight: 600; margin-bottom: 0.25rem; }}
  .poi-card .poi-dist {{ font-size: 0.72rem; color: #888; margin-bottom: 0.2rem; }}
  .poi-card .poi-actions {{ margin-top: 0.5rem; display: flex; gap: 0.5rem; flex-wrap: wrap; }}
  .poi-card .poi-actions a {{ font-size: 0.75rem; color: #1a6b3c; text-decoration: none; font-weight: 500; }}
  .poi-card .poi-actions a:hover {{ text-decoration: underline; }}
  .cat-badge {{ display: inline-block; font-size: 0.7rem; padding: 0.15rem 0.5rem; border-radius: 20px; font-weight: 600; margin-bottom: 0.35rem; }}
  .search-box {{ width: 100%; padding: 0.5rem 0.75rem; border: 1.5px solid #ddd; border-radius: 8px; font-size: 0.85rem; }}
  .search-box:focus {{ outline: none; border-color: #1a6b3c; }}
  .airbnb-badge {{
    background: #fff0f0; border: 1.5px solid #ff5a5f; border-radius: 8px;
    padding: 0.35rem 0.7rem; font-size: 0.78rem; color: #cc2030; font-weight: 500;
    display: flex; align-items: center; gap: 0.4rem; flex-shrink: 0; text-decoration: none;
  }}
  .airbnb-badge:hover {{ background: #ffe0e1; }}
  .poi-card.faded {{ opacity: 0.25; transition: opacity 0.25s; }}
  .copy-toast {{
    position: fixed; bottom: 5rem; left: 50%; transform: translateX(-50%);
    background: rgba(26,107,60,0.92); color: white; padding: 0.35rem 0.9rem;
    border-radius: 20px; font-size: 0.78rem; z-index: 9000;
    opacity: 0; transition: opacity 0.2s; pointer-events: none; white-space: nowrap;
  }}
  .copy-toast.show {{ opacity: 1; }}
  @media (max-width: 768px) {{
    #list-toggle {{ display: inline-block; }}
    .map-overlay {{ left: 0; right: 0; bottom: 0; top: auto; width: 100%; flex-direction: column-reverse; gap: 0; }}
    .overlay-card {{ border-radius: 0; }}
    .overlay-card.scrollable {{ max-height: 0; overflow: hidden; padding: 0 !important; transition: max-height 0.3s ease, padding 0.15s ease; }}
    .overlay-card.scrollable.mobile-open {{ max-height: 45vh; overflow-y: auto; padding: 0.85rem 1rem !important; margin-bottom: 0.5rem; }}
  }}
</style>
{{{{ end }}}}

{{{{ define "main" }}}}
<div id="map"></div>
<div class="map-overlay">
  <div class="overlay-card">
    <a href="{{{{ "/" | relURL }}}}" class="site-brand">🗺️ Maps</a>
    <div class="page-header">
      <h1>🏠 {title}</h1>
      <p>{description}</p>
    </div>
    <div class="header-actions">
      <button class="header-btn" id="tb-toggle" onclick="this.classList.toggle('active');document.getElementById('map-toolbar').classList.toggle('tb-visible')">🛠️ Tools</button>
      <button class="header-btn" id="list-toggle" onclick="this.classList.toggle('active');document.querySelector('.overlay-card.scrollable').classList.toggle('mobile-open');this.textContent=document.querySelector('.overlay-card.scrollable').classList.contains('mobile-open')?'📍 Places ▾':'📍 Places'">📍 Places</button>
      <button class="header-btn" id="locate-btn" onclick="toggleLocation()" title="My location">📍 Location</button>
    </div>
    <a class="airbnb-badge" href="{airbnb_url}" target="_blank">🏠 View Airbnb listing ↗</a>
    {{{{- partial "map-toolbar.html" . }}}}
  </div>
  <div class="overlay-card scrollable">
    <input class="search-box" type="text" id="search" placeholder="🔍 Search..." oninput="filterPOIs()">
    <div id="filter-btns"></div>
    <div id="poi-list"></div>
  </div>
</div>
{{{{- partial "contact-form.html" . }}}}
<div class="copy-toast" id="copy-toast"></div>
{{{{ end }}}}

{{{{ define "scripts" }}}}
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
window.MAP_CONFIG = {{
  title: '{js_title}',
  geojsonUrl: '{{{{ "/{slug}/locations.geojson" | relURL }}}}',
  embedPath: '/{slug}/',
  previewImage: '/images/map-previews/{slug}.png'
}};

const GEOJSON_URL = window.MAP_CONFIG.geojsonUrl;
const AIRBNB_LAT = {lat}, AIRBNB_LON = {lon};

const CATEGORY_COLORS = {{
  'Supermarket': '#16a34a',
  'Park':        '#15803d',
  'Playground':  '#f97316',
  'Transit':     '#2563eb',
  'Activity':    '#9333ea',
}};
const CATEGORY_ICONS = {{
  'Supermarket': '🛒',
  'Park':        '🌳',
  'Playground':  '🛝',
  'Transit':     '🚌',
  'Activity':    '🎠',
}};

let map, allPOIs = [], markers = {{}}, activeFilter = 'All', activeCard = null;
Object.defineProperty(window, 'allPOIs', {{ get: () => allPOIs, configurable: true }});
window._mapCategoryColors = CATEGORY_COLORS;
window._mapCategoryIcons  = CATEGORY_ICONS;

function slugify(n) {{ return n.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); }}
function showToast(msg) {{
  const t = document.getElementById('copy-toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1800);
}}
function haversine(a, b, c, d) {{
  const R = 6371000, dL = (c-a)*Math.PI/180, dO = (d-b)*Math.PI/180;
  const x = Math.sin(dL/2)**2 + Math.cos(a*Math.PI/180)*Math.cos(c*Math.PI/180)*Math.sin(dO/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1-x));
}}
function fmtDist(m) {{ return m < 1000 ? Math.round(m) + ' m' : (m/1000).toFixed(1) + ' km'; }}

function fadePOIs(activeId) {{
  Object.entries(markers).forEach(([id, m]) => {{
    const el = m.getElement();
    if (el) el.style.opacity = (String(id) === String(activeId)) ? '1' : '0.2';
  }});
  document.querySelectorAll('.poi-card').forEach(c =>
    c.classList.toggle('faded', c.id !== 'card-' + activeId));
}}
function clearFocus() {{
  if (activeCard === null) return;
  document.getElementById('card-' + activeCard)?.classList.remove('active');
  activeCard = null;
  Object.values(markers).forEach(m => {{ const el = m.getElement(); if (el) el.style.opacity = '1'; }});
  document.querySelectorAll('.poi-card').forEach(c => c.classList.remove('faded'));
  const u = new URL(window.location.href);
  u.searchParams.delete('poi');
  history.replaceState({{}}, '', u);
  window.clearRoute?.();
}}

map = L.map('map').setView([AIRBNB_LAT, AIRBNB_LON], 15);
window._leafletMap = map;
let _flyingTo = false;
map.on('click', clearFocus);
map.on('dragstart', () => {{ if (!_flyingTo) clearFocus(); }});
map.on('zoomstart', () => {{ if (!_flyingTo) clearFocus(); }});
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '© OpenStreetMap contributors'
}}).addTo(map);

const airbnbIcon = L.divIcon({{
  className: '',
  html: `<div style="background:#ff5a5f;width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:18px;box-shadow:0 2px 8px rgba(0,0,0,0.4);border:3px solid white;">🏠</div>`,
  iconSize: [36, 36], iconAnchor: [18, 18]
}});
L.marker([AIRBNB_LAT, AIRBNB_LON], {{ icon: airbnbIcon, zIndexOffset: 1000 }})
  .addTo(map)
  .bindPopup('<b>🏠 Airbnb</b><br><a href="{airbnb_url}" target="_blank">View listing ↗</a>');

function makeIcon(category) {{
  const color = CATEGORY_COLORS[category] || '#6b7280';
  const icon = CATEGORY_ICONS[category] || '📍';
  return L.divIcon({{
    className: '',
    html: `<div style="background:${{color}};width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;box-shadow:0 2px 6px rgba(0,0,0,0.3);border:2px solid white;">${{icon}}</div>`,
    iconSize: [30, 30], iconAnchor: [15, 15], popupAnchor: [0, -18]
  }});
}}

function renderSidebar() {{
  const search = document.getElementById('search').value.toLowerCase();
  const list = document.getElementById('poi-list');
  list.innerHTML = '';
  const filtered = allPOIs.filter(f => {{
    const p = f.properties;
    return (activeFilter === 'All' || p.category === activeFilter) &&
           (!search || p.name.toLowerCase().includes(search));
  }});
  filtered.forEach(f => {{
    const p = f.properties;
    const cat = p.category || 'Other';
    const color = CATEGORY_COLORS[cat] || '#6b7280';
    const icon = CATEGORY_ICONS[cat] || '📍';
    const [lng, lat] = f.geometry.coordinates;
    const dist = haversine(AIRBNB_LAT, AIRBNB_LON, lat, lng);
    const slug = slugify(p.name);
    const card = document.createElement('div');
    card.className = 'poi-card';
    card.id = 'card-' + f._id;
    card.innerHTML = `
      <span class="cat-badge" style="background:${{color}}22;color:${{color}};">${{icon}} ${{cat}}</span>
      <h3>${{p.name}}</h3>
      <div class="poi-dist">📏 ${{fmtDist(dist)}} from Airbnb</div>
      <div class="poi-actions">
        <a href="geo:${{lat}},${{lng}}" title="Open in OsmAnd">🗺️ OsmAnd</a>
        <a href="https://www.google.com/maps/search/?api=1&query=${{lat}},${{lng}}" target="_blank">Google Maps ↗</a>
        <a href="#" onclick="event.preventDefault();event.stopPropagation();navigator.clipboard.writeText(location.origin+location.pathname+'?poi=${{slug}}').then(()=>showToast('🔗 Link copied!'));">🔗</a>
      </div>`;
    card.onclick = () => focusPOI(f);
    list.appendChild(card);
  }});
  if (!filtered.length) list.innerHTML = '<p style="color:#888;font-size:0.85rem;padding:0.5rem 0;">No results.</p>';
}}

function focusPOI(f) {{
  const [lng, lat] = f.geometry.coordinates;
  _flyingTo = true;
  map.flyTo([lat, lng], 17, {{ duration: 0.8 }});
  map.once('moveend', () => {{ _flyingTo = false; }});
  if (markers[f._id]) markers[f._id].openPopup();
  if (activeCard !== null) document.getElementById('card-' + activeCard)?.classList.remove('active');
  activeCard = f._id;
  document.getElementById('card-' + f._id)?.classList.add('active');
  document.getElementById('card-' + f._id)?.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
  const u = new URL(window.location.href);
  u.searchParams.set('poi', slugify(f.properties.name));
  history.replaceState({{}}, '', u);
  fadePOIs(f._id);
}}

function filterPOIs() {{ renderSidebar(); updateMarkers(); }}

function updateMarkers() {{
  const search = document.getElementById('search').value.toLowerCase();
  Object.values(markers).forEach(m => map.removeLayer(m));
  Object.keys(markers).forEach(k => delete markers[k]);
  allPOIs.forEach(f => {{
    const p = f.properties;
    const cat = p.category || 'Other';
    if ((activeFilter !== 'All' && cat !== activeFilter) ||
        (search && !p.name.toLowerCase().includes(search))) return;
    const [lng, lat] = f.geometry.coordinates;
    const marker = L.marker([lat, lng], {{ icon: makeIcon(cat) }})
      .addTo(map)
      .bindPopup(`<b>${{p.name}}</b><br><small>${{cat}}</small>`);
    marker.on('click', () => focusPOI(f));
    markers[f._id] = marker;
  }});
}}

function setupFilters(pois) {{
  const cats = ['All', ...new Set(pois.map(f => f.properties.category || 'Other'))];
  const container = document.getElementById('filter-btns');
  container.innerHTML = '';
  cats.forEach(cat => {{
    const btn = document.createElement('button');
    btn.className = 'filter-btn' + (cat === activeFilter ? ' active' : '');
    btn.dataset.cat = cat;
    btn.textContent = (CATEGORY_ICONS[cat] ? CATEGORY_ICONS[cat] + ' ' : '') + cat;
    btn.onclick = () => {{
      activeFilter = cat;
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterPOIs();
    }};
    container.appendChild(btn);
  }});
}}

fetch(GEOJSON_URL)
  .then(r => r.json())
  .then(data => {{
    allPOIs = data.features.filter(f => f.geometry?.type === 'Point').map((f, i) => ({{ ...f, _id: i }}));
    window.MAP_CONFIG.geojsonData = data;
    setupFilters(allPOIs);
    renderSidebar();
    updateMarkers();
    if (Object.keys(markers).length > 0)
      map.fitBounds(L.featureGroup(Object.values(markers)).getBounds().pad(0.12));
    const params = new URLSearchParams(window.location.search);
    const urlFilter = params.get('filter');
    if (urlFilter) {{
      const btn = [...document.querySelectorAll('.filter-btn')].find(b => b.dataset.cat === urlFilter);
      if (btn) btn.click();
    }}
    const urlPOI = params.get('poi');
    if (urlPOI) {{
      const match = allPOIs.find(f => slugify(f.properties.name) === urlPOI);
      if (match) setTimeout(() => focusPOI(match), 350);
    }}
  }})
  .catch(() => {{
    document.getElementById('poi-list').innerHTML = '<p style="color:#888;font-size:0.85rem;padding:1rem;">Could not load locations.</p>';
  }});
</script>
{{{{- partial "map-geolocation.html" . }}}}
{{{{ end }}}}
"""


# ---------------------------------------------------------------------------
# GitHub PR helpers
# ---------------------------------------------------------------------------

def _gh(method: str, path: str, token: str, **kwargs) -> requests.Response:
    return requests.request(
        method,
        f"{GITHUB_API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=20,
        **kwargs,
    )


def _ensure_fork(token: str, login: str) -> str:
    r = _gh("GET", f"/repos/{login}/travel-guide", token)
    if r.status_code == 200:
        return r.json()["full_name"]
    print("  Forking alx/travel-guide...", file=sys.stderr)
    r = _gh("POST", f"/repos/{UPSTREAM_REPO}/forks", token, json={"default_branch_only": True})
    r.raise_for_status()
    fork = r.json()["full_name"]
    for _ in range(20):
        time.sleep(3)
        if _gh("GET", f"/repos/{fork}", token).status_code == 200:
            return fork
    raise RuntimeError("Fork never became ready")


def _get_main_sha(token: str) -> str:
    r = _gh("GET", f"/repos/{UPSTREAM_REPO}/git/ref/heads/main", token)
    r.raise_for_status()
    return r.json()["object"]["sha"]


def _create_branch(token: str, repo: str, branch: str, sha: str) -> None:
    r = _gh("POST", f"/repos/{repo}/git/refs", token,
            json={"ref": f"refs/heads/{branch}", "sha": sha})
    if r.status_code not in (200, 201, 422):
        r.raise_for_status()


def _put_file(token: str, repo: str, path: str, content: str, branch: str, message: str) -> None:
    body: dict = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    r = _gh("GET", f"/repos/{repo}/contents/{path}", token, params={"ref": branch})
    if r.status_code == 200:
        body["sha"] = r.json()["sha"]
    r = _gh("PUT", f"/repos/{repo}/contents/{path}", token, json=body)
    r.raise_for_status()


def _get_maps_json(token: str) -> tuple[dict, str]:
    r = _gh("GET", f"/repos/{UPSTREAM_REPO}/contents/data/maps.json", token)
    r.raise_for_status()
    data = r.json()
    return json.loads(base64.b64decode(data["content"]).decode()), data["sha"]


def build_pr(
    token: str,
    slug: str,
    title: str,
    description: str,
    airbnb_url: str,
    lat: float,
    lon: float,
    radius: float,
    geojson: dict,
    categories: list[str],
) -> str:
    print("\nBuilding GitHub PR...", file=sys.stderr)

    r = _gh("GET", "/user", token)
    r.raise_for_status()
    login = r.json()["login"]
    print(f"  Authenticated as: {login}", file=sys.stderr)

    upstream_owner = UPSTREAM_REPO.split("/")[0]
    working_repo = UPSTREAM_REPO if login == upstream_owner else _ensure_fork(token, login)
    print(f"  Repo: {working_repo}", file=sys.stderr)

    branch = f"airbnb/{slug}"
    _create_branch(token, working_repo, branch, _get_main_sha(token))
    print(f"  Branch: {branch}", file=sys.stderr)

    geojson_path = f"static/{slug}/locations.geojson"
    _put_file(token, working_repo, geojson_path,
              json.dumps(geojson, ensure_ascii=False, indent=2),
              branch, f"feat({slug}): add locations.geojson from OSM Overpass")
    print(f"  ✓ {geojson_path}", file=sys.stderr)

    content_path = f"content/{slug}/_index.md"
    _put_file(token, working_repo, content_path,
              _hugo_content(title, description, categories),
              branch, f"feat({slug}): add Hugo content file")
    print(f"  ✓ {content_path}", file=sys.stderr)

    layout_path = f"layouts/{slug}/list.html"
    _put_file(token, working_repo, layout_path,
              _layout_html(slug, title, description, airbnb_url, lat, lon),
              branch, f"feat({slug}): add map layout")
    print(f"  ✓ {layout_path}", file=sys.stderr)

    maps_doc, maps_sha = _get_maps_json(token)
    n_pois = len(geojson["features"])
    cat_labels = sorted({CATEGORIES[c]["label"] for c in categories if c in CATEGORIES})
    maps_doc["maps"] = [m for m in maps_doc.get("maps", []) if m.get("slug") != slug]
    maps_doc["maps"].append({
        "slug": slug,
        "url": f"/{slug}/",
        "title": title,
        "description": description,
        "emoji": "🏠",
        "section": "community",
        "weight": 55,
        "accent_color": "#1a6b3c",
        "tags": [f"{CATEGORIES[c]['icon']} {CATEGORIES[c]['label']}" for c in categories if c in CATEGORIES],
        "poi_count": n_pois,
        "categories": cat_labels,
        "has_geojson": True,
    })
    maps_doc["_meta"]["map_count"] = len(maps_doc["maps"])
    maps_doc["_meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    r = _gh("PUT", f"/repos/{working_repo}/contents/data/maps.json", token, json={
        "message": f"feat({slug}): update maps.json",
        "content": base64.b64encode(json.dumps(maps_doc, ensure_ascii=False, indent=2).encode()).decode(),
        "branch": branch,
        "sha": maps_sha,
    })
    r.raise_for_status()
    print("  ✓ data/maps.json", file=sys.stderr)

    by_cat = {}
    for f in geojson["features"]:
        cat = f["properties"]["category"]
        by_cat[cat] = by_cat.get(cat, 0) + 1
    cat_table = "\n".join(
        f"| {CATEGORIES[c]['icon']} {CATEGORIES[c]['label']} | {by_cat.get(CATEGORIES[c]['label'], 0)} |"
        for c in categories if c in CATEGORIES
    )

    pr_body = f"""## {title}

**Airbnb listing:** {airbnb_url}
**Coordinates:** {lat:.6f}°N, {lon:.6f}°E
**Radius:** {_fmt_dist(radius)}
**Total POIs:** {n_pois}
**Source:** OSM Overpass

### POIs by category

| Category | Count |
|----------|-------|
{cat_table}

### Files added
- `static/{slug}/locations.geojson`
- `content/{slug}/_index.md`
- `layouts/{slug}/list.html`
- `data/maps.json` updated

🤖 Generated with [Claude Code](https://claude.com/claude-code) · `scripts/airbnb_env/airbnb_nearby.py`
"""

    head = branch if login == upstream_owner else f"{login}:{branch}"
    r = _gh("POST", f"/repos/{UPSTREAM_REPO}/pulls", token, json={
        "title": f"feat({slug}): add nearby POI map ({n_pois} places)",
        "head": head,
        "base": "main",
        "body": pr_body,
    })
    r.raise_for_status()
    return r.json()["html_url"]


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

    results = merge_results(osm_results, google_results)

    # --- Slug / title defaults ---
    listing_id = listing_id_from_url(args.airbnb_url)
    slug = args.slug or f"airbnb-{listing_id[-8:]}"
    title = args.title or f"Airbnb — Nearby Places ({slug})"
    n_pois = sum(len(v) for v in results.values())
    description = (
        args.description
        or f"{n_pois} POIs within {_fmt_dist(args.radius)} of the Airbnb listing. Source: OSM Overpass."
    )

    # --- PR mode ---
    if args.pr:
        token = args.github_token or os.getenv("GITHUB_TOKEN", "")
        if not token:
            print("Error: --pr requires a GitHub token (--github-token or GITHUB_TOKEN env var)", file=sys.stderr)
            sys.exit(1)
        geojson = build_geojson(args.airbnb_url, lat, lon, results, args.radius, slug)
        pr_url = build_pr(
            token=token,
            slug=slug,
            title=title,
            description=description,
            airbnb_url=args.airbnb_url,
            lat=lat,
            lon=lon,
            radius=args.radius,
            geojson=geojson,
            categories=requested,
        )
        print(f"\nPR created: {pr_url}", file=sys.stderr)
        return

    # --- Local output ---
    if args.output == "table":
        output_table(args.airbnb_url, lat, lon, results, args.radius)
    elif args.output == "json":
        output_json(args.airbnb_url, lat, lon, results, args.radius)
    else:
        geojson = build_geojson(args.airbnb_url, lat, lon, results, args.radius, slug)
        print(json.dumps(geojson, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
