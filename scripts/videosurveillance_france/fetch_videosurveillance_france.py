#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Fetch surveillance cameras and speed radars in France via Overpass API (OpenStreetMap).

Outputs GeoJSON to stdout — pipe directly into the dataset file:

    uv run scripts/videosurveillance_france/fetch_videosurveillance_france.py > static/videosurveillance-france/locations.geojson

No API key required. Requires internet access to overpass-api.de.

OSM tags queried:
  - man_made=surveillance  (cameras, CCTV, ANPR)
  - highway=speed_camera   (speed radars)

Sensitive fields omitted: operator, contact:*, phone, email,
description, note, notes, camera:direction, surveillance (deprecated).
"""
import datetime
import json
import sys
import urllib.request
import urllib.parse

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Bounding box for metropolitan France + Corsica (south, west, north, east)
METRO_FRANCE_BBOX = (41.333, -5.143, 51.089, 9.560)

OVERPASS_QUERY = """
[out:json][timeout:240];
(
  nwr["man_made"="surveillance"]({south},{west},{north},{east});
  nwr["highway"="speed_camera"]({south},{west},{north},{east});
);
out center tags;
""".format(
    south=METRO_FRANCE_BBOX[0],
    west=METRO_FRANCE_BBOX[1],
    north=METRO_FRANCE_BBOX[2],
    east=METRO_FRANCE_BBOX[3],
).strip()

# Fields to suppress from published data (privacy / sensitivity)
SUPPRESSED_FIELDS = {
    "operator", "operator:type", "operator:wikidata",
    "contact:phone", "contact:email", "contact:website",
    "phone", "email",
    "description", "note", "notes",
    "camera:direction", "camera:mount",
    "surveillance",  # deprecated tag, can expose private/indoor scope
}


def get_category(tags: dict) -> tuple[str, str]:
    if tags.get("highway") == "speed_camera":
        return "Radar vitesse", "⚡"
    cam_type = tags.get("camera:type", "").lower()
    surv_type = tags.get("surveillance:type", "").lower()
    if surv_type in ("alpr", "anpr") or cam_type in ("anpr", "alpr", "lpr"):
        return "LAPI / ANPR", "🔍"
    if cam_type == "ptz":
        return "Caméra PTZ", "🎯"
    if cam_type == "dome":
        return "Caméra dôme", "🎥"
    return "Caméra fixe", "📹"


def fetch_overpass(query: str) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(
        OVERPASS_URL,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "travel-guide-videosurveillance-france/1.0",
        },
    )
    print("Querying Overpass API for surveillance cameras in France...", file=sys.stderr)
    with urllib.request.urlopen(req, timeout=260) as resp:
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

    south, west, north, east = METRO_FRANCE_BBOX
    if not (south <= lat <= north and west <= lon <= east):
        return None

    lon = round(lon, 7)
    lat = round(lat, 7)

    category, icon = get_category(tags)

    name = (
        tags.get("name")
        or tags.get("ref")
        or f"{category} #{index}"
    )

    address = build_address(tags)
    fid = f"videosurveillance-france-{index:05d}"

    props: dict = {
        "name": name,
        "category": category,
        "icon": icon,
        "coord_source": "osm",
        "coord_accuracy": "high",
        "place_id": f"osm:{el_type}/{el['id']}",
    }
    if address:
        props["address"] = address

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
        print(f"Skipped {skipped} elements (missing coordinates or out of bbox)", file=sys.stderr)
    print(f"Total features: {len(features)}", file=sys.stderr)

    geojson = {
        "type": "FeatureCollection",
        "bbox": [min(lons), min(lats), max(lons), max(lats)] if lons else [],
        "_meta": {
            "crs": "EPSG:4326",
            "generated": datetime.date.today().isoformat(),
            "source": "OpenStreetMap via Overpass API — man_made=surveillance + highway=speed_camera in metropolitan France and Corsica",
            "license": "ODbL — https://www.openstreetmap.org/copyright",
        },
        "features": features,
    }

    print(json.dumps(geojson, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
