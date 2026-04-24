#!/usr/bin/env python3
"""
validate_geojson.py — GeoJSON validation for alx/travel-guide
==============================================================
Checks all static/**/locations.geojson files for:
  1. Valid JSON syntax
  2. GeoJSON FeatureCollection structure (RFC 7946)
  3. Coordinate bounds for WGS84 (lon: -180..180, lat: -90..90)
  4. Required feature properties: name, category
  5. coord_source enum values (if present)
  6. coord_accuracy enum values (if present)
  7. Feature id presence and uniqueness across all files
  8. bbox presence on FeatureCollection (warning only)

Exit code: 0 = all pass, 1 = one or more errors found.
"""

import json
import sys
from pathlib import Path

VALID_COORD_SOURCES = {
    "google_maps_pin", "google_places", "nominatim", "research", "estimated", "on_site_gps"
}
VALID_COORD_ACCURACIES = {"high", "medium", "low"}

errors = []
warnings = []
all_ids = {}  # id -> file path, for uniqueness check


def err(path, msg):
    errors.append(f"  ERROR  {path}: {msg}")


def warn(path, msg):
    warnings.append(f"  WARN   {path}: {msg}")


def validate_file(fpath: Path):
    rel = fpath.relative_to(Path(__file__).parent.parent)

    # 1. JSON syntax
    try:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        err(rel, f"Invalid JSON — {e}")
        return

    # 2. GeoJSON structure
    if data.get("type") != "FeatureCollection":
        err(rel, f"Top-level 'type' must be 'FeatureCollection', got {data.get('type')!r}")
        return

    features = data.get("features")
    if not isinstance(features, list):
        err(rel, "'features' must be an array")
        return

    # 3. bbox (warning only)
    if "bbox" not in data:
        warn(rel, "Missing 'bbox' on FeatureCollection (RFC 7946 §5)")

    # 4. _meta CRS annotation (warning only)
    meta = data.get("_meta", {})
    if meta.get("crs") != "EPSG:4326":
        warn(rel, "Missing or incorrect '_meta.crs' (expected 'EPSG:4326')")

    for i, feature in enumerate(features):
        loc = f"features[{i}]"

        if feature.get("type") != "Feature":
            err(rel, f"{loc}: 'type' must be 'Feature'")
            continue

        # 5. Feature id
        fid = feature.get("id")
        if fid is None:
            err(rel, f"{loc}: missing 'id' field (RFC 7946 §3.2)")
        else:
            if fid in all_ids:
                err(rel, f"{loc}: duplicate id {fid!r} (also in {all_ids[fid]})")
            else:
                all_ids[fid] = str(rel)

        # 6. Geometry coordinates
        geom = feature.get("geometry")
        if geom is None:
            warn(rel, f"{loc}: null geometry")
        elif geom.get("type") == "Point":
            coords = geom.get("coordinates", [])
            if len(coords) < 2:
                err(rel, f"{loc}: Point coordinates must have at least [lon, lat]")
            else:
                lon, lat = coords[0], coords[1]
                if not (-180 <= lon <= 180):
                    err(rel, f"{loc}: longitude {lon} out of range [-180, 180]")
                if not (-90 <= lat <= 90):
                    err(rel, f"{loc}: latitude {lat} out of range [-90, 90]")

        # 7. Required properties
        props = feature.get("properties") or {}
        for field in ("name", "category"):
            if not props.get(field):
                err(rel, f"{loc}: missing required property '{field}'")

        # 8. coord_source enum
        cs = props.get("coord_source")
        if cs is not None and cs not in VALID_COORD_SOURCES:
            err(rel, f"{loc}: invalid coord_source {cs!r}. Must be one of: {sorted(VALID_COORD_SOURCES)}")

        # 9. coord_accuracy enum
        ca = props.get("coord_accuracy")
        if ca is not None and ca not in VALID_COORD_ACCURACIES:
            err(rel, f"{loc}: invalid coord_accuracy {ca!r}. Must be one of: {sorted(VALID_COORD_ACCURACIES)}")

        # 10. Warn if no provenance at all
        if cs is None and ca is None:
            warn(rel, f"{loc} ({props.get('name', '?')}): no coord_source / coord_accuracy — add provenance metadata")


def main():
    repo_root = Path(__file__).parent.parent
    geojson_files = sorted(repo_root.glob("static/**/locations.geojson"))

    if not geojson_files:
        print("No locations.geojson files found under static/")
        sys.exit(1)

    print(f"Validating {len(geojson_files)} GeoJSON file(s)…\n")
    for fpath in geojson_files:
        validate_file(fpath)

    if warnings:
        print("Warnings:")
        for w in warnings:
            print(w)
        print()

    if errors:
        print("Errors:")
        for e in errors:
            print(e)
        print(f"\n❌ {len(errors)} error(s) found — fix before merging.")
        sys.exit(1)
    else:
        total = len(all_ids)
        print(f"✅ All files valid — {total} unique feature IDs across {len(geojson_files)} file(s).")
        if warnings:
            print(f"   ({len(warnings)} warning(s) — not blocking)")
        sys.exit(0)


if __name__ == "__main__":
    main()
