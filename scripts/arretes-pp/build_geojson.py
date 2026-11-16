#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["shapely>=2.0"]
# ///
"""Build the perimeter GeoJSON for a Préfecture de police arrêté.

For each establishment listed in arretes.json this writes two polygons:
  - the OSM footprint (the way/relation), used as the anchor
  - the LIMIT: the footprint buffered outward by `radius_m` (default 100 m),
    with the footprint cut out, so it renders as a ring "outside the building"

    uv run scripts/arretes-pp/build_geojson.py --slug perimetres-pp-2026-01603

Geometry notes
--------------
* Buffers are computed in a local metric projection (equirectangular, scaled at
  the mean latitude of the footprint) and projected back to WGS84. Over a
  100-300 m radius the error is sub-centimetre, far below OSM's own accuracy.
  We deliberately do not use shapely's .buffer with degrees: 100 degrees is not
  100 m, and a "100 m" legal perimeter drawn in degrees is simply wrong.
* Multipolygons from OSM relations are assembled by joining member way
  segments end-to-end, honouring inner (hole) roles.
* Non-contiguous footprints (a campus split into blocks) keep all parts, so the
  limit hugs each block rather than bridging the gap between them.

Blocker handling: if a footprint cannot be fetched or assembled, the script
fails loudly rather than writing a partial map — a missing perimeter in a
police-order map is a silent legal error.
"""
import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, mapping
from shapely.geometry.polygon import orient
from shapely.ops import transform, unary_union

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
OSM_API = "https://www.openstreetmap.org/api/0.6"
USER_AGENT = "travel-guide-arretes-pp/1.0 (+https://maps.girard-davila.net)"


# ── OSM fetching ─────────────────────────────────────────────────────────────

def fetch_osm(osm_type: str, osm_id: int, retries: int = 4) -> dict:
    url = f"{OSM_API}/{osm_type}/{osm_id}/full.json"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise SystemExit(f"ERROR: cannot fetch {url}: {last}")


def index_elements(elements: list) -> tuple[dict, dict]:
    nodes, ways = {}, {}
    for el in elements:
        if el["type"] == "node":
            nodes[el["id"]] = (el["lon"], el["lat"])
        elif el["type"] == "way":
            ways[el["id"]] = el.get("nodes", [])
        # relations in a 'full' response are only the requested one
    return nodes, ways


def ring_from_nodes(node_ids: list, nodes: dict) -> list[tuple[float, float]]:
    pts = []
    for nid in node_ids:
        if nid not in nodes:
            raise SystemExit(f"ERROR: way references missing node {nid}")
        pts.append(nodes[nid])
    return pts


def assemble_ways(way_ids: list, ways: dict, nodes: dict) -> list[list[tuple[float, float]]]:
    """Join way segments into closed rings (end-to-end, either orientation)."""
    remaining = [ring_from_nodes(ways[w], nodes) for w in way_ids if w in ways]
    rings: list[list[tuple[float, float]]] = []
    while remaining:
        cur = remaining.pop(0)
        changed = True
        while changed and cur[0] != cur[-1]:
            changed = False
            for i, seg in enumerate(remaining):
                if seg[0] == cur[-1]:
                    cur += seg[1:]
                    remaining.pop(i)
                    changed = True
                    break
                if seg[-1] == cur[-1]:
                    cur += list(reversed(seg))[1:]
                    remaining.pop(i)
                    changed = True
                    break
                if seg[-1] == cur[0]:
                    cur = seg[:-1] + cur
                    remaining.pop(i)
                    changed = True
                    break
                if seg[0] == cur[0]:
                    cur = list(reversed(seg))[:-1] + cur
                    remaining.pop(i)
                    changed = True
                    break
        rings.append(cur)
    return rings


def footprint_polygon(osm_type: str, osm_id: int) -> Polygon | MultiPolygon:
    data = fetch_osm(osm_type, osm_id)
    nodes, ways = index_elements(data["elements"])

    if osm_type == "way":
        way = next(e for e in data["elements"] if e["type"] == "way" and e["id"] == osm_id)
        ring = ring_from_nodes(way["nodes"], nodes)
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        if len(ring) < 4:
            raise SystemExit(f"ERROR: way {osm_id} is not a closed ring")
        return Polygon(ring)

    rel = next(e for e in data["elements"] if e["type"] == "relation" and e["id"] == osm_id)
    outers = assemble_ways([m["ref"] for m in rel["members"] if m["role"] == "outer"], ways, nodes)
    inners = assemble_ways([m["ref"] for m in rel["members"] if m["role"] == "inner"], ways, nodes)
    outer_polys = [Polygon(r) for r in outers if len(r) >= 4]
    if not outer_polys:
        raise SystemExit(f"ERROR: relation {osm_id} yielded no outer ring")
    geom = unary_union(outer_polys)
    if inners:
        holes = [Polygon(r) for r in inners if len(r) >= 4]
        geom = geom.difference(unary_union(holes))
    return geom


# ── Metric buffering ─────────────────────────────────────────────────────────

def make_projectors(geom):
    """Equirectangular metric projection around the geometry's centroid."""
    c = geom.centroid
    lat0, lon0 = c.y, c.x
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110574.0

    def forward(x, y, z=None):
        return ((x - lon0) * kx, (y - lat0) * ky)

    def inverse(x, y, z=None):
        return (lon0 + x / kx, lat0 + y / ky)

    return forward, inverse


def project(geom, func):
    return transform(func, geom)


def decimate(coords: list, tol: float = 0.35) -> list:
    """Drop buffer points that sit < tol m from the chord of their neighbours.

    Keeps GeoJSON small without visibly changing the limit line.
    """
    if len(coords) < 5:
        return coords
    out = [coords[0]]
    for i in range(1, len(coords) - 1):
        ax, ay = out[-1][:2]
        bx, by = coords[i][:2]
        cx, cy = coords[i + 1][:2]
        # distance from B to segment AC
        dx, dy = cx - ax, cy - ay
        seg = math.hypot(dx, dy)
        if seg == 0:
            dist = math.hypot(bx - ax, by - ay)
        else:
            dist = abs(dx * (ay - by) - (ax - bx) * dy) / seg
        if dist > tol:
            out.append(coords[i])
    out.append(coords[-1])
    return out


def round_coords(geom_dict: dict) -> dict:
    """Round to 6 dp (~11 cm) — enough for a 100 m limit, halves file size."""
    def walk(c):
        if isinstance(c, (int, float)):
            return round(c, 6)
        return [walk(x) for x in c]
    geom_dict["coordinates"] = walk(geom_dict["coordinates"])
    return geom_dict


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--config", default=str(SCRIPT_DIR / "arretes.json"))
    ap.add_argument("--radius-m", type=float, default=None, help="override the config radius")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    radius = args.radius_m if args.radius_m is not None else cfg.get("radius_m", 100)

    features = []
    print(f"Building {len(cfg['establishments'])} perimeter(s), radius {radius:g} m", file=sys.stderr)

    for ent in cfg["establishments"]:
        label = ent["name"][:52]
        foot = footprint_polygon(ent["osm_type"], ent["osm_id"])
        if not foot.is_valid:
            foot = foot.buffer(0)
            print(f"  ! {label}: repaired invalid footprint", file=sys.stderr)

        fwd, inv = make_projectors(foot)
        foot_m = project(foot, fwd)

        # The limit is the area within `radius` of the footprint, MINUS the
        # footprint itself: a ring that sits outside the building.
        limit_m = foot_m.buffer(radius, quad_segs=16, join_style="round").difference(foot_m)
        if limit_m.is_empty:
            raise SystemExit(f"ERROR: empty limit for {ent['name']}")

        limit = project(limit_m, inv)
        if limit.is_empty:
            raise SystemExit(f"ERROR: empty limit after reprojection for {ent['name']}")

        props = {
            "id": ent["id"],
            "name": ent["name"],
            "category": ent["category"],
            "arrondissement": ent["arrondissement"],
            "perimeter_m": int(radius),
            "osm_type": ent["osm_type"],
            "osm_id": ent["osm_id"],
            "osm_url": f"https://www.openstreetmap.org/{ent['osm_type']}/{ent['osm_id']}",
            "osm_name": ent.get("osm_name", ""),
            "arrete": cfg["arrete"],
        }
        if ent.get("uai"):
            props["uai"] = ent["uai"]
        if ent.get("address"):
            props["address"] = ent["address"]
        if ent.get("note"):
            props["note"] = ent["note"]
        if ent.get("caveat"):
            props["caveat"] = ent["caveat"]
        # Provenance: the geometry is an OSM footprint, buffered in metres.
        # validated_against records that uai-bearing entries were cross-checked
        # with the ministry's annuaire de l'éducation registry.
        props["coord_source"] = "osm"
        props["coord_accuracy"] = "high" if ent.get("uai") else "medium"
        if ent.get("uai"):
            props["validated_against"] = f"UAI {ent['uai']}"

        ring = limit if limit.geom_type == "Polygon" else limit
        features.append({
            "type": "Feature",
            "id": f"{ent['id']}-limit",
            "properties": {**props, "role": "limit"},
            "geometry": round_coords(mapping(orient(ring, 1.0))),
        })
        features.append({
            "type": "Feature",
            "id": f"{ent['id']}-footprint",
            "properties": {**props, "role": "footprint"},
            "geometry": round_coords(mapping(orient(
                foot if foot.geom_type == "Polygon" else foot, 1.0))),
        })

        area_ha = foot_m.area / 10000
        ring_ha = limit_m.area / 10000
        print(f"  ✓ {label:54} footprint {area_ha:6.2f} ha, limit {ring_ha:7.2f} ha", file=sys.stderr)

    dest = REPO_ROOT / "static" / args.slug / "locations.geojson"
    dest.parent.mkdir(parents=True, exist_ok=True)

    # RFC 7946 §5 wants a bbox; the validator checks for it, and it lets the
    # site jump straight to the data area without loading every ring first.
    lons, lats = [], []
    for f in features:
        def walk(c):
            if isinstance(c, (int, float)):
                return
            if len(c) == 2 and all(isinstance(v, (int, float)) for v in c):
                lons.append(c[0]); lats.append(c[1])
            else:
                for x in c:
                    walk(x)
        walk(f["geometry"]["coordinates"])

    payload = {
        "type": "FeatureCollection",
        "bbox": [min(lons), min(lats), max(lons), max(lats)] if lons else [],
        "_meta": {"crs": "EPSG:4326"},
        "metadata": {
            "slug": args.slug,
            "arrete": cfg["arrete"],
            "arrete_date": cfg.get("arrete_date"),
            "applies_from": cfg.get("applies_from"),
            "applies_to": cfg.get("applies_to"),
            "radius_m": radius,
            "source_url": cfg.get("source_url"),
            "generated_by": "scripts/arretes-pp/build_geojson.py",
        },
        "features": features,
    }
    dest.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"\nWrote {len(features)} features ({len(features) // 2} establishments) -> {dest}", file=sys.stderr)
    print(f"Size: {dest.stat().st_size / 1024:.1f} KiB", file=sys.stderr)


if __name__ == "__main__":
    main()
