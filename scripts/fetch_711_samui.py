#!/usr/bin/env python3
"""Fetch all 7-Eleven stores on Koh Samui via Google Places API (New).

Outputs GeoJSON to stdout — pipe directly into the dataset file:

    export GOOGLE_MAPS_API_KEY=AIza...
    python scripts/fetch_711_samui.py > static/711-samui/locations.geojson

Requires: GOOGLE_MAPS_API_KEY environment variable (Google Cloud project with
Places API (New) enabled).
"""
import datetime
import json
import os
import sys
import time
import urllib.request

API_KEY: str = os.environ.get("GOOGLE_MAPS_API_KEY", "")
if not API_KEY:
    sys.exit("Error: GOOGLE_MAPS_API_KEY environment variable not set")

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"

# Koh Samui bounding box
BBOX = {
    "low":  {"latitude": 9.38, "longitude": 99.88},
    "high": {"latitude": 9.66, "longitude": 100.10},
}

FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.location",
    "places.addressComponents",
    "places.regularOpeningHours",
    "nextPageToken",
])

# Google address component names → canonical area labels used in the map.
# Covers English names, Thai names, and "Tambon" (subdistrict) prefixed variants.
AREA_ALIASES = {
    # Bophut / Bo Put
    "Bo Put": "Bophut", "Bo Phut": "Bophut", "Bophut": "Bophut",
    "Tambon Bo Put": "Bophut", "Tambon Bophut": "Bophut",
    "ตำบล บ่อผุด": "Bophut", "ต.บ่อผุด": "Bophut", "บ่อผุด": "Bophut",
    # Chaweng
    "Chaweng": "Chaweng", "Chaweng Noi": "Chaweng",
    "ตำบล บ่อผุด": "Bophut",  # some Chaweng stores fall in Bo Put tambon
    # Lamai / Maret (Maret tambon covers Lamai beach area)
    "Lamai": "Lamai",
    "Maret": "Lamai", "Tambon Maret": "Lamai",
    "ตำบล มะเร็ต": "Lamai", "มะเร็ต": "Lamai",
    # Maenam / Mae Nam
    "Mae Nam": "Maenam", "Maenam": "Maenam",
    "Tambon Mae Nam": "Maenam",
    "ตำบล แม่น้ำ": "Maenam", "แม่น้ำ": "Maenam",
    # Choeng Mon
    "Choeng Mon": "Choeng Mon", "ตำบล เชิงมน": "Choeng Mon",
    # Bang Rak
    "Bang Rak": "Bang Rak",
    # Nathon / Na Thon
    "Na Thon": "Nathon", "Nathon": "Nathon",
    "Tambon Ang Thong": "Nathon",  # Na Thon town falls in Ang Thong tambon
    "Ang Thong": "Nathon",
    "ตำบล อ่างทอง": "Nathon", "อ่างทอง": "Nathon",
    # Hua Thanon
    "Hua Thanon": "Hua Thanon",
    # Thong Krut / Na Mueang interior
    "Thong Krut": "Thong Krut",
    "Na Mueang": "South Samui", "Tambon Na Mueang": "South Samui",
    "ตำบล นาเมือง": "South Samui", "นาเมือง": "South Samui",
    # Lipa Noi / Taling Ngam (west coast)
    "Lipa Noi": "Lipa Noi",
    "Taling Ngam": "Taling Ngam", "Tambon Taling Ngam": "Taling Ngam",
    "ตำบล ตลิ่งงาม": "Taling Ngam", "ตลิ่งงาม": "Taling Ngam",
    # Thong Yang
    "Thong Yang": "Thong Yang",
    # South Samui catch-all
    "South Samui": "South Samui",
}

# Address component types checked in priority order
AREA_TYPES = ("sublocality_level_1", "neighborhood", "sublocality", "locality")


def text_search(query: str, page_token: str | None = None) -> dict:
    body: dict = {
        "textQuery": query,
        "locationRestriction": {"rectangle": BBOX},
        "maxResultCount": 20,
    }
    if page_token:
        body["pageToken"] = page_token

    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": FIELD_MASK,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def extract_area(address_components: list) -> str:
    """Return the canonical area label from Google address components."""
    candidates: dict[str, str] = {}
    for component in address_components:
        types = component.get("types", [])
        name = component.get("longText", "")
        for t in AREA_TYPES:
            if t in types:
                candidates.setdefault(t, name)

    for t in AREA_TYPES:
        raw = candidates.get(t)
        if raw:
            return AREA_ALIASES.get(raw, raw)

    return "Koh Samui"


def is_24h(opening_hours: dict | None) -> bool:
    if not opening_hours:
        return True  # 7-Eleven are always 24h; assume true when not returned
    periods = opening_hours.get("periods", [])
    # A single period with no close time = open 24h
    if len(periods) == 1 and "close" not in periods[0]:
        return True
    return False


def main() -> None:
    places: list[dict] = []
    page_token: str | None = None
    page = 0

    while True:
        page += 1
        print(f"Fetching page {page}...", file=sys.stderr)
        resp = text_search("7-Eleven Koh Samui", page_token)
        batch = resp.get("places", [])
        places.extend(batch)
        print(f"  → {len(batch)} results (total so far: {len(places)})", file=sys.stderr)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break
        time.sleep(2)

    print(f"\nTotal places fetched: {len(places)}", file=sys.stderr)

    # Deduplicate by place_id (shouldn't happen but safe)
    seen: set[str] = set()
    unique: list[dict] = []
    for p in places:
        pid = p.get("id", "")
        if pid and pid not in seen:
            seen.add(pid)
            unique.append(p)

    print(f"Unique places: {len(unique)}", file=sys.stderr)

    lons, lats = [], []
    features: list[dict] = []

    for i, place in enumerate(unique, 1):
        loc = place["location"]
        lon = round(loc["longitude"], 7)
        lat = round(loc["latitude"], 7)
        lons.append(lon)
        lats.append(lat)

        area = extract_area(place.get("addressComponents", []))
        hours = "24h" if is_24h(place.get("regularOpeningHours")) else "check hours"
        fid = f"samui-711-{i:03d}"

        features.append({
            "type": "Feature",
            "id": fid,
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat],
            },
            "properties": {
                "id": fid,
                "name": f"7-Eleven {area}",
                "category": "Convenience",
                "icon": "\U0001f3ea",
                "area": area,
                "hours": hours,
                "brand": "7-Eleven",
                "place_id": place.get("id", ""),
                "coord_source": "google_places",
                "coord_accuracy": "high",
            },
        })

    geojson = {
        "type": "FeatureCollection",
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "_meta": {
            "crs": "EPSG:4326",
            "generated": datetime.date.today().isoformat(),
            "source": "Google Places API (New)",
        },
        "features": features,
    }

    print(json.dumps(geojson, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
