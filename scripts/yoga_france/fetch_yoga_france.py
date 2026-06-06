#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Fetch all yoga places in France via Overpass API (OpenStreetMap).

Outputs GeoJSON to stdout — pipe directly into the dataset file:

    uv run scripts/yoga_france/fetch_yoga_france.py > static/yoga-france/locations.geojson

No API key required. Requires internet access to overpass-api.de.

OSM tags used:
  - sport=yoga  (yoga studios, classes, centres)

Elements returned as nodes are used directly; ways/relations use their
centroid (computed by Overpass with `out center`).
"""
import datetime
import json
import sys
import time
import urllib.request
import urllib.parse

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

OVERPASS_QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="FR"]["admin_level"="2"]->.country;
(
  nwr["sport"="yoga"](area.country);
);
out center tags;
""".strip()


def fetch_overpass(query: str) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(
        OVERPASS_URL,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "travel-guide-yoga-france/1.0",
        },
    )
    print("Querying Overpass API for yoga places in France...", file=sys.stderr)
    with urllib.request.urlopen(req, timeout=200) as resp:
        return json.loads(resp.read())


def build_address(tags: dict) -> str | None:
    parts = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
        tags.get("addr:postcode", ""),
        tags.get("addr:city", ""),
    ]
    address = " ".join(p for p in parts if p).strip()
    return address or None


def element_to_feature(el: dict, index: int) -> dict | None:
    el_type = el.get("type")
    tags = el.get("tags", {})

    if el_type == "node":
        lat, lon = el.get("lat"), el.get("lon")
    else:
        center = el.get("center", {})
        lat, lon = center.get("lat"), center.get("lon")

    if lat is None or lon is None:
        return None

    lon = round(lon, 7)
    lat = round(lat, 7)

    name = (
        tags.get("name")
        or tags.get("name:fr")
        or tags.get("operator")
        or tags.get("ref")
        or f"Yoga #{index}"
    )

    phone = tags.get("contact:phone") or tags.get("phone")
    website = tags.get("contact:website") or tags.get("website")
    hours = tags.get("opening_hours")
    address = build_address(tags)

    fid = f"yoga-france-{index:04d}"

    props: dict = {
        "name": name,
        "category": "Yoga",
        "icon": "🧘",
        "coord_source": "osm",
        "coord_accuracy": "high",
        "place_id": f"osm:{el_type}/{el['id']}",
    }
    if address:
        props["address"] = address
    if hours:
        props["hours"] = hours
    if phone:
        props["phone"] = phone
    if website:
        props["url"] = website

    return {
        "type": "Feature",
        "id": fid,
        "geometry": {
            "type": "Point",
            "coordinates": [lon, lat],
        },
        "properties": props,
    }


def main() -> None:
    result = fetch_overpass(OVERPASS_QUERY)
    elements = result.get("elements", [])
    print(f"Received {len(elements)} OSM elements", file=sys.stderr)

    features = []
    lons, lats = [], []
    skipped = 0

    for i, el in enumerate(elements, 1):
        feature = element_to_feature(el, i)
        if feature is None:
            skipped += 1
            continue
        features.append(feature)
        lon, lat = feature["geometry"]["coordinates"]
        lons.append(lon)
        lats.append(lat)

    if skipped:
        print(f"Skipped {skipped} elements (missing coordinates)", file=sys.stderr)
    print(f"Total features: {len(features)}", file=sys.stderr)

    geojson = {
        "type": "FeatureCollection",
        "bbox": [min(lons), min(lats), max(lons), max(lats)] if lons else [],
        "_meta": {
            "crs": "EPSG:4326",
            "generated": datetime.date.today().isoformat(),
            "source": "OpenStreetMap via Overpass API — sport=yoga in France",
        },
        "features": features,
    }

    print(json.dumps(geojson, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
