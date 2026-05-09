#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "python-dotenv"]
# ///
"""
Look up Toulouse burger places via Google Places Text Search API
and write static/toulouse-burgers/locations.geojson
"""

import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
if not API_KEY:
    sys.exit("Error: GOOGLE_MAPS_API_KEY not set")

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.regularOpeningHours",
    "places.rating",
])

# Places to look up: (query, final_name, category, notes)
PLACES = [
    ("Antidot burger Toulouse", "Antidot", "Burger Restaurant", ""),
    ("La Planque des Affranchis Sesquières Toulouse", "La Planque des Affranchis", "Burger Restaurant", ""),
    ("Seven Sisters burger Toulouse", "Seven Sisters", "Burger Restaurant", ""),
    ("Coyote Burger Saint-Sernin Toulouse", "Coyote Burger", "Burger Restaurant", ""),
    ("Novo Burger Toulouse", "Novo Burger", "Burger Restaurant", ""),
    ("Superette burger Toulouse", "Superette", "Burger Restaurant", ""),
    ("Five Guys Toulouse", "Five Guys", "Chain", ""),
    ("JFK Burger Toulouse", "JFK Burger", "Burger Restaurant", ""),
    ("Bouche B burger Toulouse", "Bouche B", "Burger Restaurant", ""),
    ("Le Mec au Camion food truck Toulouse", "Le Mec au Camion", "Food Truck", ""),
    ("Tommy's Diner Toulouse", "Tommy's Diner", "Chain", ""),
    ("Burgers de Papa Toulouse", "Burgers de Papa", "Burger Restaurant", ""),
    ("Carson City burger Toulouse", "Carson City", "Burger Restaurant", ""),
    ("Burger N Co Toulouse", "Burger N Co", "Burger Restaurant", ""),
    ("Le Malabar burger rue Saint-Michel Toulouse", "Le Malabar", "Burger Restaurant", ""),
    ("Junk burger Toulouse", "Junk", "Burger Restaurant", ""),
    ("Les Filoches burger Palais de Justice Toulouse", "Les Filoches", "Burger Restaurant", ""),
]

# Toulouse city center bias
CENTER_LAT = 43.6047
CENTER_LON = 1.4442

def search_place(query: str) -> dict | None:
    body = {
        "textQuery": query,
        "locationBias": {
            "circle": {
                "center": {"latitude": CENTER_LAT, "longitude": CENTER_LON},
                "radius": 15000.0,
            }
        },
        "maxResultCount": 1,
        "languageCode": "fr",
    }
    resp = requests.post(
        PLACES_SEARCH_URL,
        headers={
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": FIELD_MASK,
            "Content-Type": "application/json",
        },
        json=body,
        timeout=15,
    )
    if not resp.ok:
        print(f"  API error: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
        return None
    places = resp.json().get("places", [])
    return places[0] if places else None


def hours_text(place: dict) -> str | None:
    descs = place.get("regularOpeningHours", {}).get("weekdayDescriptions", [])
    return "; ".join(descs) if descs else None


def main():
    out_dir = Path(__file__).parent.parent / "static" / "toulouse-burgers"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "locations.geojson"

    features = []

    for idx, (query, name, category, extra_notes) in enumerate(PLACES):
        print(f"[{idx+1:2d}/{len(PLACES)}] {name} …", end=" ", flush=True)
        place = search_place(query)
        if not place:
            print(f"NOT FOUND — skipping")
            continue

        loc = place.get("location", {})
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        if lat is None or lon is None:
            print("no coords — skipping")
            continue

        address = place.get("formattedAddress", "")
        phone = place.get("nationalPhoneNumber")
        url = place.get("websiteUri")
        hours = hours_text(place)

        props = {
            "name": name,
            "category": category,
            "icon": "🚚" if category == "Food Truck" else "🍔",
            "address": address,
            "coord_source": "google_places_api",
            "coord_accuracy": "high",
        }
        if extra_notes:
            props["notes"] = extra_notes
        if hours:
            props["hours"] = hours
        if phone:
            props["phone"] = phone
        if url:
            props["url"] = url

        features.append({
            "type": "Feature",
            "id": f"toulouse-burgers-{idx:03d}",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props,
        })
        print(f"✓  {address}")
        time.sleep(0.15)

    # Add Hype Smash Burger manually (Uber Eats only, no physical address)
    features.append({
        "type": "Feature",
        "id": "toulouse-burgers-017",
        "geometry": {"type": "Point", "coordinates": [CENTER_LON, CENTER_LAT]},
        "properties": {
            "name": "Hype Smash Burger",
            "category": "Delivery Only",
            "icon": "📦",
            "address": "Toulouse (livraison uniquement)",
            "notes": "Uber Eats uniquement — pas d'adresse physique. Livraison sur Toulouse.",
            "coord_source": "city_center_fallback",
            "coord_accuracy": "low",
        },
    })
    print(f"[18/{len(PLACES)+1}] Hype Smash Burger → city center (Uber Eats only)")

    geojson = {
        "type": "FeatureCollection",
        "_meta": {
            "crs": "EPSG:4326",
            "generated": "2026-05-09",
            "source": "Google Places API + manual",
            "center": {"lat": CENTER_LAT, "lon": CENTER_LON},
        },
        "features": features,
    }

    out_path.write_text(json.dumps(geojson, ensure_ascii=False, indent=2))
    print(f"\n✅ Wrote {len(features)} features → {out_path}")


if __name__ == "__main__":
    main()
