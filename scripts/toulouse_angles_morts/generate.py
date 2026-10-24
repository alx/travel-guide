#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["shapely>=2.0"]
# ///
"""Toulouse camera blind-spots — streets in the city-centre zone with no camera vision coverage.

Reads the existing videoprotection-toulouse camera dataset (OpenStreetMap),
restricts it to the official Toulouse hyper-centre quarter (admin_level=10,
relation 1684025 "Capitole / Arnaud Bernard / Carmes"), fetches the street
network inside that zone from Overpass, and emits GeoJSON where every street
segment is classified as:

  - "uncovered"  → no camera has line-of-sight to it (the blind spots)
  - "covered"    → at least one camera can see it (context layer, faint)

Coverage model (hybrid, documented as an assumption on the map):
  - camera has a known `camera:direction`  → visibility CONE (FOV 100°, range 50 m)
  - camera direction unknown / 360°        → full disk (range 50 m)
Direction values may be multi (`0;300;68`) or textual (`north`).

Privacy: identical suppression to the source fetch script — operator/contact/
description/camera:direction/height/note fields are never written to the output.

Usage:
    uv run scripts/toulouse_angles_morts/generate.py > static/toulouse-angles-morts/locations.geojson
"""
from __future__ import annotations

import datetime
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMERA_GEOJSON = REPO_ROOT / "static" / "videoprotection-toulouse" / "locations.geojson"

UA = "travel-guide-toulouse-angles-morts/1.0"
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Official Toulouse quarter — "Capitole / Arnaud Bernard / Carmes"
ZONE_RELATION = 1684025

# Coverage model parameters
RANGE_M = 50.0          # camera effective range (metres)
FOV_DEG = 100.0         # cone half-angle is FOV/2
SEGMENT_M = 10.0        # resample streets to ~10 m segments before testing

# Street classes kept (walkable/visible public space; excludes corridors/elevators/platforms)
KEEP_HIGHWAY = {
    "footway", "pedestrian", "living_street", "residential", "service",
    "steps", "cycleway", "path", "primary", "secondary", "tertiary",
    "primary_link", "secondary_link", "tertiary_link", "unclassified", "track",
}

COMPASS = {
    "n": 0, "north": 0, "nne": 22.5, "ne": 45, "northeast": 45, "ene": 67.5,
    "e": 90, "east": 90, "ese": 112.5, "se": 135, "southeast": 135, "sse": 157.5,
    "s": 180, "south": 180, "ssw": 202.5, "sw": 225, "southwest": 225, "wsw": 247.5,
    "w": 270, "west": 270, "wnw": 292.5, "nw": 315, "northwest": 315, "nnw": 337.5,
}


# ── Overpass ─────────────────────────────────────────────────────────────────

def overpass(query: str, tries: int = 6) -> dict:
    last: Exception | None = None
    for i in range(tries):
        ep = ENDPOINTS[i % len(ENDPOINTS)]
        try:
            data = urllib.parse.urlencode({"data": query}).encode()
            req = urllib.request.Request(
                ep, data=data, method="POST",
                headers={"User-Agent": UA,
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = resp.read().decode("utf-8", "replace")
            if body.lstrip().startswith("#") or "Gateway Timeout" in body[:200]:
                raise RuntimeError("endpoint busy")
            return json.loads(body)
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = 5 * (2 ** i)
            print(f"  overpass attempt {i+1}/{tries} ({ep.split('/')[2]}) failed: "
                  f"{exc!r}; retry in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"overpass failed after {tries} tries: {last!r}")


# ── Geometry helpers ─────────────────────────────────────────────────────────

def zone_polygon() -> Polygon:
    """Assemble the admin_level=10 quarter boundary ring(s) into a polygon.

    The relation's member ways are disjoint segments; use polygonize() to
    stitch them into a ring, then keep the largest resulting polygon.
    """
    data = overpass(f"[out:json][timeout:180];rel({ZONE_RELATION});way(r);out geom;")
    lines = []
    for el in data["elements"]:
        if el.get("type") == "way" and el.get("geometry"):
            coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
            if len(coords) >= 2:
                lines.append(LineString(coords))
    if not lines:
        raise RuntimeError("zone boundary not found")
    polys = list(polygonize(unary_union(lines)))
    if not polys:
        raise RuntimeError("zone boundary is not a closed ring")
    poly = max(polys, key=lambda p: p.area)
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda p: p.area)
    return poly


def zone_streets(bounds: tuple[float, float, float, float]) -> list[dict]:
    s, w, n, e = bounds
    data = overpass(
        f'[out:json][timeout:180];way["highway"]({s},{w},{n},{e});out geom;'
    )
    return [el for el in data["elements"] if el.get("geometry") and el.get("tags")]


def zone_cameras(bounds: tuple[float, float, float, float]) -> list[dict]:
    s, w, n, e = bounds
    data = overpass(f"""[out:json][timeout:180];
(
  node["man_made"="surveillance"]({s},{w},{n},{e});
  way["man_made"="surveillance"]({s},{w},{n},{e});
);
out center tags;""")
    cams = []
    for el in data["elements"]:
        tags = el.get("tags", {})
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        cams.append({"lon": lon, "lat": lat, "tags": tags})
    return cams


def parse_directions(raw: str | None) -> list[float] | None:
    """Return a list of compass bearings (deg, 0=N, clockwise), or None if unknown/360."""
    if not raw:
        return None
    out: list[float] = []
    for part in raw.replace(",", ";").split(";"):
        part = part.strip().lower()
        if not part:
            continue
        if part in COMPASS:
            out.append(COMPASS[part])
            continue
        try:
            val = float(part)
        except ValueError:
            continue
        if val >= 360:  # 360 is a full turn = no useful cone
            return None
        out.append(val % 360)
    return out or None


def meters_per_deg(lat: float) -> tuple[float, float]:
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians(lat))
    return mlat, mlon


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def angle_delta(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def resample(line: LineString, step_m: float, lat: float) -> list[tuple[float, float]]:
    """Return points spaced roughly step_m apart along a line (lon, lat)."""
    mlat, mlon = meters_per_deg(lat)
    length_m = line.length * ((mlat + mlon) / 2)
    n = max(2, int(length_m / step_m) + 1)
    return [(line.interpolate(i / (n - 1), normalized=True).x,
             line.interpolate(i / (n - 1), normalized=True).y) for i in range(n)]


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not CAMERA_GEOJSON.exists():
        raise SystemExit(f"missing source dataset: {CAMERA_GEOJSON}")

    print("Fetching Toulouse hyper-centre boundary…", file=sys.stderr)
    zone = zone_polygon()
    minx, miny, maxx, maxy = zone.bounds
    bounds = (miny, minx, maxy, maxx)
    print(f"  zone bbox {bounds}", file=sys.stderr)

    print("Fetching zone street network…", file=sys.stderr)
    raw_streets = zone_streets(bounds)
    print(f"  {len(raw_streets)} candidate ways", file=sys.stderr)

    print("Fetching zone cameras (with direction)…", file=sys.stderr)
    cameras = zone_cameras(bounds)
    print(f"  {len(cameras)} camera elements in bbox", file=sys.stderr)

    # Keep only cameras actually inside the zone polygon
    cams_in = [c for c in cameras if zone.contains(Point(c["lon"], c["lat"]))]
    print(f"  {len(cams_in)} cameras inside zone polygon", file=sys.stderr)

    # Build coverage disks/cones as shapely geometry in a local metric CRS.
    # Use a simple equirectangular projection anchored at zone centre.
    clat, clon = (miny + maxy) / 2, (minx + maxx) / 2
    mlat, mlon = meters_per_deg(clat)

    def to_xy(lon: float, lat: float) -> tuple[float, float]:
        return ((lon - clon) * mlon, (lat - clat) * mlat)

    def to_lonlat(x: float, y: float) -> tuple[float, float]:
        return (clon + x / mlon, clat + y / mlat)

    seen_polys = []
    n_cone = n_disk = 0
    for cam in cams_in:
        cx, cy = to_xy(cam["lon"], cam["lat"])
        dirs = parse_directions(cam["tags"].get("camera:direction"))
        if dirs is None:
            n_disk += 1
            seen_polys.append(Point(cx, cy).buffer(RANGE_M, resolution=16))
        else:
            n_cone += 1
            half = FOV_DEG / 2
            for d in dirs:
                # wedge in local XY: bearing 0 = north (+y), clockwise
                pts = [(cx, cy)]
                steps = 12
                for k in range(steps + 1):
                    ang = math.radians(d - half + (2 * half) * k / steps)
                    pts.append((cx + RANGE_M * math.sin(ang),
                                cy + RANGE_M * math.cos(ang)))
                seen_polys.append(Polygon(pts))
    print(f"  coverage: {n_cone} cones + {n_disk} disks", file=sys.stderr)

    ground_truth = unary_union(seen_polys) if seen_polys else Polygon()

    # Classify street segments
    features: list[dict] = []
    n_unc = n_cov = 0
    for el in raw_streets:
        hw = el["tags"].get("highway")
        if hw not in KEEP_HIGHWAY:
            continue
        coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
        if len(coords) < 2:
            continue
        line = LineString(coords)
        if not zone.intersects(line):
            continue
        # clip line to zone
        clipped = line.intersection(zone)
        if clipped.is_empty:
            continue
        parts = [clipped] if clipped.geom_type == "LineString" else list(clipped.geoms)
        for part in parts:
            if part.length <= 0:
                continue
            pts = resample(part, SEGMENT_M, clat)
            covered_pts = 0
            for lon, lat in pts:
                x, y = to_xy(lon, lat)
                if ground_truth.contains(Point(x, y)):
                    covered_pts += 1
            frac = covered_pts / len(pts)
            status = "covered" if frac >= 0.5 else "uncovered"
            if status == "uncovered":
                n_unc += 1
            else:
                n_cov += 1
            name = (el["tags"].get("name")
                    or el["tags"].get("addr:street")
                    or f"Voie sans nom ({hw})")
            features.append({
                "type": "Feature",
                "id": f"tal-{el['id']}-{len(features)}",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[round(x, 7), round(y, 7)] for x, y in part.coords],
                },
                "properties": {
                    "name": name,
                    "category": "Angle mort" if status == "uncovered" else "Couvert",
                    "status": status,
                    "highway": hw,
                    "coverage_frac": round(frac, 3),
                    "length_m": round(part.length * ((mlat + mlon) / 2), 1),
                    "osm_way": f"way/{el['id']}",
                },
            })

    print(f"  street parts: {n_unc} uncovered, {n_cov} covered", file=sys.stderr)

    lons = [c[0] for f in features for c in f["geometry"]["coordinates"]]
    lats = [c[1] for f in features for c in f["geometry"]["coordinates"]]

    out = {
        "type": "FeatureCollection",
        "bbox": [min(lons), min(lats), max(lons), max(lats)] if lons else [],
        "_meta": {
            "crs": "EPSG:4326",
            "generated": datetime.date.today().isoformat(),
            "source": ("OpenStreetMap via Overpass API — streets clipped to Toulouse "
                       "hyper-centre quarter (admin_level=10, relation 1684025); cameras "
                       "from man_made=surveillance"),
            "license": "ODbL — https://www.openstreetmap.org/copyright",
            "coverage_model": (f"cone FOV {FOV_DEG:.0f}° / range {RANGE_M:.0f} m where "
                               f"camera:direction is known, else full disk {RANGE_M:.0f} m; "
                               f"a street part is 'covered' when ≥50% of its {SEGMENT_M:.0f} m "
                               "samples fall inside any camera's field of view"),
            "cameras_in_zone": len(cams_in),
            "cameras_with_direction": n_cone,
            "cameras_direction_unknown": n_disk,
        },
        "features": features,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
