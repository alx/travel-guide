# Temple Walk Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CLI script that generates a self-guided Buddhist temple walk from any starting point (Overpass discovery → greedy OSRM chaining → Wikimedia photos → GeoJSON + Hugo map page), stopping before the walk exceeds `--max-km` (default 10).

**Architecture:** Single-file uv Python script `scripts/temple-walk/generate.py` modeled on `scripts/bangkok-citywalk/generate.py`. Pure logic (haversine, Overpass parsing, greedy planner, GeoJSON assembly) lives in plain data-in/data-out functions tested with pytest; network functions wrap them with caching and rate-limiting. A shared Hugo layout `layouts/temple-walk/single.html` (adapted from `layouts/bangkok-citywalk/list.html`) renders one page per walk.

**Tech Stack:** Python ≥3.11 (uv single-file script, stdlib `urllib` + `tqdm` only), pytest (via `uv run --with pytest`), Overpass API, OSRM foot routing, Nominatim, Wikimedia Commons API, Hugo + Leaflet 1.9.4.

**Spec:** `docs/superpowers/specs/2026-07-03-temple-walk-design.md`

## Global Constraints

- Python ≥3.11; uv script header with dependencies `["tqdm"]` only; HTTP via stdlib `urllib` (no requests).
- User-Agent header on all requests: `maps.girard-davila.net/temple-walk (girard.davila@gmail.com)`.
- Endpoints: `https://overpass-api.de/api/interpreter`, `http://router.project-osrm.org/route/v1/foot`, `https://nominatim.openstreetmap.org/search`, `https://commons.wikimedia.org/w/api.php`.
- Rate limits: 1.0 s sleep after each uncached OSRM call; 0.3 s between photo downloads; 0.5 s between temples in the photo phase.
- Error policy: Overpass/OSRM/Nominatim requests retry once then abort printing the failing URL. Photo failures are non-fatal.
- Output GeoJSON must stay schema-compatible with `static/bangkok-citywalk` (`Point` features with `name`/`order`/`slug`/`photos`; one `LineString` with `segment_breaks`). New additive properties: `osm_id`, `distance_km`, `attribution`, `total_km`, and a start `Point` with `order: 0`.
- Caches live in `scripts/temple-walk/cache/` (gitignored), keyed by slug: `<slug>.overpass.json`, `<slug>.routes.json`, `<slug>.media.json`.
- Test command: `uv run --with pytest --with tqdm python -m pytest scripts/temple-walk/tests/ -v` (run from repo root).
- `--max-km` is a hard ceiling: a leg is added only if `total + leg ≤ max_km`.

## File Structure

```
scripts/temple-walk/
  generate.py               ← the whole pipeline (pure functions + network + main)
  .gitignore                ← ignores cache/
  tests/
    test_generate.py        ← pytest for the pure functions
layouts/temple-walk/
  single.html               ← Leaflet map page, one per walk
static/temple-walks/<slug>/ ← generated: walk.geojson + photos/   (committed)
content/temple-walks/<slug>.md ← generated once, preserved on re-run (committed)
```

---

### Task 1: Scaffold script with pure geometry helpers

**Files:**
- Create: `scripts/temple-walk/generate.py`
- Create: `scripts/temple-walk/.gitignore`
- Create: `scripts/temple-walk/tests/test_generate.py`

**Interfaces:**
- Produces: `haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float` (great-circle km); `slugify(text: str) -> str`; `parse_start(value: str) -> tuple[float, float] | None` (`(lat, lng)` if value is `"lat,lng"`, else `None` meaning "treat as address"); `load_json(path: Path) -> dict`; `save_json(path: Path, data) -> None`; module constants `SCRIPT_DIR`, `REPO_ROOT`, `CACHE_DIR`, `USER_AGENT`, `NOMINATIM_URL`, `OVERPASS_URL`, `OSRM_BASE`, `COMMONS_API`.

- [ ] **Step 1: Create `scripts/temple-walk/.gitignore`**

```
cache/
```

- [ ] **Step 2: Write the failing tests**

Create `scripts/temple-walk/tests/test_generate.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import generate  # noqa: E402


# ── haversine_km ──────────────────────────────────────────────────────────────

def test_haversine_zero_distance():
    assert generate.haversine_km(13.75, 100.49, 13.75, 100.49) == 0.0


def test_haversine_one_degree_longitude_at_equator():
    # 1° of longitude at the equator ≈ 111.19 km
    d = generate.haversine_km(0.0, 0.0, 0.0, 1.0)
    assert abs(d - 111.19) < 0.5


def test_haversine_grand_palace_to_wat_arun():
    # Grand Palace (13.7500, 100.4913) → Wat Arun (13.7437, 100.4889) ≈ 0.75 km
    d = generate.haversine_km(13.7500, 100.4913, 13.7437, 100.4889)
    assert 0.5 < d < 1.0


# ── parse_start ───────────────────────────────────────────────────────────────

def test_parse_start_valid_latlng():
    assert generate.parse_start("13.7516,100.4927") == (13.7516, 100.4927)


def test_parse_start_with_spaces():
    assert generate.parse_start(" 13.7516 , 100.4927 ") == (13.7516, 100.4927)


def test_parse_start_address_returns_none():
    assert generate.parse_start("Democracy Monument, Bangkok") is None


def test_parse_start_out_of_range_returns_none():
    assert generate.parse_start("113.7,100.4") is None


def test_parse_start_single_token_returns_none():
    assert generate.parse_start("Bangkok") is None


# ── slugify ───────────────────────────────────────────────────────────────────

def test_slugify_temple_name():
    assert generate.slugify("Wat Phra Chetuphon (Wat Pho)") == "wat-phra-chetuphon-wat-pho"
```

- [ ] **Step 3: Run tests to verify they fail**

Run (from repo root): `uv run --with pytest --with tqdm python -m pytest scripts/temple-walk/tests/ -v`
Expected: FAIL / error — `ModuleNotFoundError: No module named 'generate'`

- [ ] **Step 4: Write the scaffold + helpers**

Create `scripts/temple-walk/generate.py`:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["tqdm"]
# ///
"""
Generate a temple walk GeoJSON + Hugo page from a starting point.

Finds named Buddhist temples via the Overpass API, chains them greedily with
OSRM walking legs, and stops before the cumulative walking distance exceeds
--max-km (default 10). Output follows the bangkok-citywalk GeoJSON schema.

Usage:
    uv run scripts/temple-walk/generate.py --start "13.7516,100.4927" --slug rattanakosin
    uv run scripts/temple-walk/generate.py --start "Democracy Monument, Bangkok" --slug democracy --max-km 8
    uv run scripts/temple-walk/generate.py --start "13.7516,100.4927" --slug rattanakosin --dry-run
"""

import argparse
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
CACHE_DIR = SCRIPT_DIR / "cache"

USER_AGENT = {"User-Agent": "maps.girard-davila.net/temple-walk (girard.davila@gmail.com)"}
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSRM_BASE = "http://router.project-osrm.org/route/v1/foot"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"


# ── Pure helpers ──────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower()
    for src, dst in [("é","e"),("è","e"),("ê","e"),("à","a"),("â","a"),("ù","u"),("û","u"),("î","i"),("ô","o"),("ç","c")]:
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_start(value: str) -> tuple[float, float] | None:
    """Return (lat, lng) if value parses as "lat,lng", else None (treat as address)."""
    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        lat, lng = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


if __name__ == "__main__":
    pass  # main() arrives in Task 5
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --with pytest --with tqdm python -m pytest scripts/temple-walk/tests/ -v`
Expected: 9 PASSED

- [ ] **Step 6: Commit**

```bash
git add scripts/temple-walk/
git commit -m "feat(temple-walk): scaffold generator with pure geometry helpers"
```

---

### Task 2: Overpass element parsing

**Files:**
- Modify: `scripts/temple-walk/generate.py` (append after `parse_start`)
- Test: `scripts/temple-walk/tests/test_generate.py` (append)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `parse_overpass_elements(elements: list[dict]) -> list[dict]` — each returned temple dict has keys `name: str`, `lat: float`, `lng: float`, `osm_type: str` (`"node"|"way"|"relation"`), `osm_id: str` (`"way/123"`). Named elements only; deduped by name with ways/relations preferred over nodes.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/temple-walk/tests/test_generate.py`:

```python
# ── parse_overpass_elements ───────────────────────────────────────────────────

def test_parse_overpass_node():
    elements = [{"type": "node", "id": 1, "lat": 13.75, "lon": 100.49,
                 "tags": {"name": "Wat Pho"}}]
    temples = generate.parse_overpass_elements(elements)
    assert temples == [{"name": "Wat Pho", "lat": 13.75, "lng": 100.49,
                        "osm_type": "node", "osm_id": "node/1"}]


def test_parse_overpass_way_uses_center():
    elements = [{"type": "way", "id": 2, "center": {"lat": 13.74, "lon": 100.48},
                 "tags": {"name": "Wat Arun"}}]
    temples = generate.parse_overpass_elements(elements)
    assert temples[0]["lat"] == 13.74
    assert temples[0]["osm_id"] == "way/2"


def test_parse_overpass_skips_unnamed():
    elements = [{"type": "node", "id": 3, "lat": 13.7, "lon": 100.5, "tags": {}},
                {"type": "node", "id": 4, "lat": 13.7, "lon": 100.5}]
    assert generate.parse_overpass_elements(elements) == []


def test_parse_overpass_skips_missing_center():
    elements = [{"type": "way", "id": 5, "tags": {"name": "Wat Ghost"}}]
    assert generate.parse_overpass_elements(elements) == []


def test_parse_overpass_dedupes_by_name_preferring_way():
    elements = [
        {"type": "node", "id": 6, "lat": 13.70, "lon": 100.50, "tags": {"name": "Wat Saket"}},
        {"type": "way", "id": 7, "center": {"lat": 13.71, "lon": 100.51}, "tags": {"name": "Wat Saket"}},
    ]
    temples = generate.parse_overpass_elements(elements)
    assert len(temples) == 1
    assert temples[0]["osm_id"] == "way/7"


def test_parse_overpass_node_never_replaces_way():
    elements = [
        {"type": "way", "id": 8, "center": {"lat": 13.71, "lon": 100.51}, "tags": {"name": "Wat Saket"}},
        {"type": "node", "id": 9, "lat": 13.70, "lon": 100.50, "tags": {"name": "Wat Saket"}},
    ]
    temples = generate.parse_overpass_elements(elements)
    assert len(temples) == 1
    assert temples[0]["osm_id"] == "way/8"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest --with tqdm python -m pytest scripts/temple-walk/tests/ -v`
Expected: 6 new tests FAIL with `AttributeError: module 'generate' has no attribute 'parse_overpass_elements'`

- [ ] **Step 3: Implement**

Append to `scripts/temple-walk/generate.py` (after `parse_start`):

```python
def parse_overpass_elements(elements: list[dict]) -> list[dict]:
    """
    Extract named temples with coordinates from Overpass `out center` elements.
    Dedupes by name, preferring ways/relations over nodes (richer geometry).
    """
    temples: dict[str, dict] = {}
    for el in elements:
        name = el.get("tags", {}).get("name")
        if not name:
            continue
        if el["type"] == "node":
            lat, lng = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            lat, lng = center.get("lat"), center.get("lon")
        if lat is None or lng is None:
            continue
        existing = temples.get(name)
        if existing is None or (existing["osm_type"] == "node" and el["type"] != "node"):
            temples[name] = {
                "name": name,
                "lat": lat,
                "lng": lng,
                "osm_type": el["type"],
                "osm_id": f"{el['type']}/{el['id']}",
            }
    return list(temples.values())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest --with tqdm python -m pytest scripts/temple-walk/tests/ -v`
Expected: 15 PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/temple-walk/
git commit -m "feat(temple-walk): parse Overpass elements with name dedupe"
```

---

### Task 3: Greedy walk planner

**Files:**
- Modify: `scripts/temple-walk/generate.py` (append after `parse_overpass_elements`)
- Test: `scripts/temple-walk/tests/test_generate.py` (append)

**Interfaces:**
- Consumes: `haversine_km` (Task 1); temple dicts from `parse_overpass_elements` (Task 2).
- Produces: `plan_walk(start: tuple[float, float], temples: list[dict], max_km: float, fetch_leg) -> dict` where `fetch_leg(lat1, lng1, lat2, lng2) -> tuple[list[list[float]], float]` returns (`[[lng, lat], ...]` leg coordinates, distance in km). Return dict keys: `stops` (temple dicts + `order` 1-based + `distance_km` cumulative, rounded to 2), `route_coords` (`[[lng, lat], ...]`), `segment_breaks` (`list[int]`, one entry per leg start + final terminator — same convention as bangkok-citywalk `build_route`), `total_km` (float, rounded to 2).

- [ ] **Step 1: Write the failing tests**

Append to `scripts/temple-walk/tests/test_generate.py`:

```python
# ── plan_walk ─────────────────────────────────────────────────────────────────

def fake_leg(lat1, lng1, lat2, lng2):
    """Straight-line 2-point leg with haversine distance."""
    return [[lng1, lat1], [lng2, lat2]], generate.haversine_km(lat1, lng1, lat2, lng2)


def temple(name, lat, lng):
    return {"name": name, "lat": lat, "lng": lng, "osm_type": "node", "osm_id": f"node/{name}"}


def test_plan_walk_chains_nearest_first():
    # On the equator: 0.01° lng ≈ 1.112 km
    temples = [temple("B", 0.0, 0.03), temple("A", 0.0, 0.01), temple("C", 0.0, 0.10)]
    walk = generate.plan_walk((0.0, 0.0), temples, 5.0, fake_leg)
    # start→A (1.11) + A→B (2.22) = 3.34; B→C (7.78) would exceed 5 → stop
    assert [s["name"] for s in walk["stops"]] == ["A", "B"]
    assert walk["stops"][0]["order"] == 1
    assert walk["stops"][1]["order"] == 2
    assert abs(walk["total_km"] - 3.34) < 0.02


def test_plan_walk_cumulative_distance_on_stops():
    temples = [temple("A", 0.0, 0.01), temple("B", 0.0, 0.03)]
    walk = generate.plan_walk((0.0, 0.0), temples, 10.0, fake_leg)
    assert abs(walk["stops"][0]["distance_km"] - 1.11) < 0.02
    assert abs(walk["stops"][1]["distance_km"] - 3.34) < 0.02


def test_plan_walk_first_temple_beyond_budget():
    temples = [temple("Far", 0.0, 0.5)]  # ≈ 55.6 km away
    walk = generate.plan_walk((0.0, 0.0), temples, 10.0, fake_leg)
    assert walk["stops"] == []
    assert walk["route_coords"] == []
    assert walk["total_km"] == 0.0


def test_plan_walk_never_revisits():
    temples = [temple("A", 0.0, 0.01)]
    walk = generate.plan_walk((0.0, 0.0), temples, 100.0, fake_leg)
    assert len(walk["stops"]) == 1


def test_plan_walk_route_junction_dedup_and_segment_breaks():
    temples = [temple("A", 0.0, 0.01), temple("B", 0.0, 0.03)]
    walk = generate.plan_walk((0.0, 0.0), temples, 10.0, fake_leg)
    # leg1 contributes 2 points, leg2 contributes 1 (junction trimmed)
    assert walk["route_coords"] == [[0.0, 0.0], [0.01, 0.0], [0.03, 0.0]]
    # one break per leg start + final terminator (bangkok-citywalk convention)
    assert walk["segment_breaks"] == [0, 2, 2]


def test_plan_walk_exact_budget_leg_is_accepted():
    # A is ≈ 1.112 km away; budget exactly that distance (not strictly greater)
    temples = [temple("A", 0.0, 0.01)]
    dist = generate.haversine_km(0.0, 0.0, 0.0, 0.01)
    walk = generate.plan_walk((0.0, 0.0), temples, dist, fake_leg)
    assert len(walk["stops"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest --with tqdm python -m pytest scripts/temple-walk/tests/ -v`
Expected: 6 new tests FAIL with `AttributeError: module 'generate' has no attribute 'plan_walk'`

- [ ] **Step 3: Implement**

Append to `scripts/temple-walk/generate.py`:

```python
def plan_walk(start: tuple[float, float], temples: list[dict], max_km: float, fetch_leg) -> dict:
    """
    Greedy nearest-neighbour chain from start through temples.

    Haversine picks each candidate; the real routed distance from fetch_leg
    gates acceptance. Stops when the next leg would push the total past
    max_km (a farther candidate implies an even longer leg, so no retries).

    fetch_leg(lat1, lng1, lat2, lng2) -> ([[lng, lat], ...], distance_km)
    """
    current = start
    total = 0.0
    stops: list[dict] = []
    all_coords: list[list[float]] = []
    segment_breaks: list[int] = []
    remaining = list(temples)

    while remaining:
        candidate = min(remaining, key=lambda t: haversine_km(current[0], current[1], t["lat"], t["lng"]))
        coords, dist = fetch_leg(current[0], current[1], candidate["lat"], candidate["lng"])
        if total + dist > max_km:
            break
        segment_breaks.append(len(all_coords))
        # Avoid duplicating the junction point between legs
        if all_coords and coords:
            coords = coords[1:]
        all_coords.extend(coords)
        total += dist
        stops.append({**candidate, "order": len(stops) + 1, "distance_km": round(total, 2)})
        current = (candidate["lat"], candidate["lng"])
        remaining.remove(candidate)

    # Final segment_break terminator (same convention as bangkok-citywalk)
    segment_breaks.append(len(all_coords) - 1 if all_coords else 0)
    if not stops:
        segment_breaks = []
        all_coords = []
    return {
        "stops": stops,
        "route_coords": all_coords,
        "segment_breaks": segment_breaks,
        "total_km": round(total, 2),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest --with tqdm python -m pytest scripts/temple-walk/tests/ -v`
Expected: 21 PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/temple-walk/
git commit -m "feat(temple-walk): greedy walk planner with budget gate"
```

---

### Task 4: GeoJSON and content-page builders

**Files:**
- Modify: `scripts/temple-walk/generate.py` (append after `plan_walk`)
- Test: `scripts/temple-walk/tests/test_generate.py` (append)

**Interfaces:**
- Consumes: `slugify` (Task 1); `plan_walk` return dict (Task 3).
- Produces:
  - `build_geojson(start: tuple[float, float], walk: dict, slug: str, photos_by_name: dict[str, list[dict]]) -> dict` — FeatureCollection. `photos_by_name[name]` is a list of `{"url": str, "attribution": str}` entries (order matches downloaded `<temple-slug>-N.jpg` files).
  - `build_content_page(slug: str, start_label: str, n_stops: int, total_km: float) -> str` — Hugo front-matter markdown.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/temple-walk/tests/test_generate.py`:

```python
# ── build_geojson / build_content_page ────────────────────────────────────────

def make_walk():
    temples = [temple("Wat A", 0.0, 0.01), temple("Wat B", 0.0, 0.03)]
    return generate.plan_walk((0.0, 0.0), temples, 10.0, fake_leg)


def test_build_geojson_start_point_order_zero():
    fc = generate.build_geojson((0.0, 0.0), make_walk(), "testwalk", {})
    start = fc["features"][0]
    assert start["geometry"] == {"type": "Point", "coordinates": [0.0, 0.0]}
    assert start["properties"]["order"] == 0
    assert start["properties"]["name"] == "Start"


def test_build_geojson_stop_properties():
    photos = {"Wat A": [{"url": "http://x/1.jpg", "attribution": "© Alice / CC"}]}
    fc = generate.build_geojson((0.0, 0.0), make_walk(), "testwalk", photos)
    stop = fc["features"][1]
    p = stop["properties"]
    assert p["name"] == "Wat A"
    assert p["order"] == 1
    assert p["slug"] == "wat-a"
    assert p["osm_id"] == "node/Wat A"
    assert p["photos"] == ["/temple-walks/testwalk/photos/wat-a-1.jpg"]
    assert p["attribution"] == "© Alice / CC"
    assert p["distance_km"] > 0


def test_build_geojson_stop_without_photos():
    fc = generate.build_geojson((0.0, 0.0), make_walk(), "testwalk", {})
    p = fc["features"][1]["properties"]
    assert p["photos"] == []
    assert p["attribution"] == ""


def test_build_geojson_route_linestring():
    walk = make_walk()
    fc = generate.build_geojson((0.0, 0.0), walk, "testwalk", {})
    route = fc["features"][-1]
    assert route["geometry"]["type"] == "LineString"
    assert route["geometry"]["coordinates"] == walk["route_coords"]
    assert route["properties"]["type"] == "route"
    assert route["properties"]["segment_breaks"] == walk["segment_breaks"]
    assert route["properties"]["total_km"] == walk["total_km"]


def test_build_content_page():
    md = generate.build_content_page("rattanakosin", "13.7516,100.4927", 7, 9.4)
    assert 'title: "Temple Walk — Rattanakosin"' in md
    assert 'description: "7 temples, 9.4 km walking from 13.7516,100.4927"' in md
    assert 'type: "temple-walk"' in md
    assert 'geojson: "/temple-walks/rattanakosin/walk.geojson"' in md
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest --with tqdm python -m pytest scripts/temple-walk/tests/ -v`
Expected: 5 new tests FAIL with `AttributeError: module 'generate' has no attribute 'build_geojson'`

- [ ] **Step 3: Implement**

Append to `scripts/temple-walk/generate.py`:

```python
def build_geojson(start: tuple[float, float], walk: dict, slug: str,
                  photos_by_name: dict[str, list[dict]]) -> dict:
    """Assemble the FeatureCollection: start point, temple stops, route line."""
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [start[1], start[0]]},
        "properties": {"name": "Start", "order": 0, "slug": "start", "photos": []},
    }]

    for stop in walk["stops"]:
        tslug = slugify(stop["name"])
        entries = photos_by_name.get(stop["name"], [])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [stop["lng"], stop["lat"]]},
            "properties": {
                "name": stop["name"],
                "order": stop["order"],
                "slug": tslug,
                "osm_id": stop["osm_id"],
                "distance_km": stop["distance_km"],
                "photos": [f"/temple-walks/{slug}/photos/{tslug}-{j+1}.jpg" for j in range(len(entries))],
                "attribution": entries[0]["attribution"] if entries else "",
            },
        })

    if walk["route_coords"]:
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": walk["route_coords"]},
            "properties": {
                "type": "route",
                "segment_breaks": walk["segment_breaks"],
                "total_km": walk["total_km"],
            },
        })

    return {"type": "FeatureCollection", "features": features}


def build_content_page(slug: str, start_label: str, n_stops: int, total_km: float) -> str:
    title = f"Temple Walk — {slug.replace('-', ' ').title()}"
    return (
        "---\n"
        f'title: "{title}"\n'
        f'description: "{n_stops} temples, {total_km} km walking from {start_label}"\n'
        'type: "temple-walk"\n'
        f'geojson: "/temple-walks/{slug}/walk.geojson"\n'
        "---\n"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest --with tqdm python -m pytest scripts/temple-walk/tests/ -v`
Expected: 26 PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/temple-walk/
git commit -m "feat(temple-walk): GeoJSON and Hugo content-page builders"
```

---

### Task 5: Network layer + CLI main

**Files:**
- Modify: `scripts/temple-walk/generate.py` (append network functions + `main()`, replace the `if __name__` stub)

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces (internal to the script, no later task depends on these):
  - `http_json_retry(url: str, data: bytes | None = None, timeout: int = 60) -> dict | list` — retry once then `sys.exit`.
  - `geocode_start(address: str) -> tuple[float, float]`
  - `fetch_temples(lat, lng, radius_m: float, cache_path: Path) -> list[dict]`
  - `make_leg_fetcher(cache_path: Path)` → returns a `fetch_leg` matching Task 3's contract.
  - `fetch_wikimedia_photos(name: str, max_results: int = 5) -> list[tuple[str, str]]`
  - `fetch_photos(stops: list[dict], photos_dir: Path, cache_path: Path, dry_run: bool) -> dict[str, list[dict]]`
  - `main() -> None`

No unit tests for this task (network wrappers; consistent with the repo — verification happens in Task 7). TDD does not apply to thin I/O wrappers here; the logic they wrap is already tested.

- [ ] **Step 1: Implement network helpers**

Append to `scripts/temple-walk/generate.py` (before the `if __name__` block):

```python
# ── Network layer ─────────────────────────────────────────────────────────────

def http_json_retry(url: str, data: bytes | None = None, timeout: int = 60):
    """GET/POST JSON with one retry; abort printing the failing URL."""
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, data=data, headers=USER_AGENT)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            print(f"  ⚠ request failed (attempt {attempt}): {e}", file=sys.stderr)
            if attempt == 1:
                time.sleep(2)
    sys.exit(f"✗ aborting — request failed twice: {url}")


def geocode_start(address: str) -> tuple[float, float]:
    params = urllib.parse.urlencode({"q": address, "format": "json", "limit": 1, "accept-language": "en"})
    results = http_json_retry(f"{NOMINATIM_URL}?{params}")
    if not results:
        sys.exit(f"✗ could not geocode start address: {address!r}")
    return float(results[0]["lat"]), float(results[0]["lon"])


def fetch_temples(lat: float, lng: float, radius_m: float, cache_path: Path) -> list[dict]:
    """One Overpass query: named Buddhist temples within radius_m of (lat, lng)."""
    if cache_path.exists():
        data = load_json(cache_path)
    else:
        query = (
            "[out:json][timeout:60];\n"
            f'nwr["amenity"="place_of_worship"]["religion"="buddhist"]["name"]'
            f"(around:{radius_m:.0f},{lat:.6f},{lng:.6f});\n"
            "out center;"
        )
        data = http_json_retry(OVERPASS_URL, data=urllib.parse.urlencode({"data": query}).encode())
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        save_json(cache_path, data)
    return parse_overpass_elements(data.get("elements", []))


def make_leg_fetcher(cache_path: Path):
    """Returns fetch_leg(lat1, lng1, lat2, lng2) -> (coords, km), cached per pair."""
    cache = load_json(cache_path)

    def fetch_leg(lat1, lng1, lat2, lng2):
        key = f"{lat1:.6f},{lng1:.6f}→{lat2:.6f},{lng2:.6f}"
        if key not in cache:
            url = f"{OSRM_BASE}/{lng1},{lat1};{lng2},{lat2}?overview=full&geometries=geojson"
            data = http_json_retry(url, timeout=15)
            route = data["routes"][0]
            cache[key] = {"coords": route["geometry"]["coordinates"],
                          "km": route["distance"] / 1000.0}
            save_json(cache_path, cache)
            time.sleep(1.0)
        leg = cache[key]
        return leg["coords"], leg["km"]

    return fetch_leg


def fetch_wikimedia_photos(name: str, max_results: int = 5) -> list[tuple[str, str]]:
    """Up to max_results (thumb_url, attribution) pairs from Wikimedia Commons."""
    params = urllib.parse.urlencode({
        "action": "query",
        "generator": "search",
        "gsrnamespace": 6,
        "gsrsearch": name,
        "gsrlimit": max_results * 3,  # fetch extra to filter duds
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 1080,
        "format": "json",
    })
    results: list[tuple[str, str]] = []
    try:
        req = urllib.request.Request(f"{COMMONS_API}?{params}", headers=USER_AGENT)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            if len(results) >= max_results:
                break
            ii = page.get("imageinfo", [{}])[0]
            thumb = ii.get("thumburl", "")
            if not thumb:
                continue
            meta = ii.get("extmetadata", {})
            artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
            license_name = meta.get("LicenseShortName", {}).get("value", "")
            attribution = f"© {artist} / {license_name}" if artist else license_name
            results.append((thumb, attribution))
    except Exception as e:
        print(f"  ⚠ Wikimedia error for '{name}': {e}", file=sys.stderr)
    return results


def fetch_photos(stops: list[dict], photos_dir: Path, cache_path: Path, dry_run: bool) -> dict[str, list[dict]]:
    """Download up to 5 photos per temple. Returns {name: [{"url", "attribution"}, ...]}."""
    photos_by_name: dict[str, list[dict]] = {}
    cache = load_json(cache_path)

    for stop in tqdm(stops, desc="Wikimedia photos", unit="temple"):
        name = stop["name"]
        tslug = slugify(name)

        cached = cache.get(name)
        if cached is not None:
            entries = cached["photos"]
            if all((photos_dir / f"{tslug}-{i+1}.jpg").exists() for i in range(len(entries))):
                photos_by_name[name] = entries
                continue

        if dry_run:
            tqdm.write(f"  [dry-run] would fetch photos: {name}")
            photos_by_name[name] = []
            continue

        photos_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        for i, (thumb_url, attribution) in enumerate(fetch_wikimedia_photos(name)):
            dest = photos_dir / f"{tslug}-{i+1}.jpg"
            try:
                req = urllib.request.Request(thumb_url, headers=USER_AGENT)
                with urllib.request.urlopen(req, timeout=30) as r:
                    dest.write_bytes(r.read())
                entries.append({"url": thumb_url, "attribution": attribution})
                tqdm.write(f"  → {name} [{i+1}]: {dest.name}")
            except Exception as e:
                print(f"  ⚠ download failed for '{name}' photo {i+1}: {e}", file=sys.stderr)
            time.sleep(0.3)

        if not entries:
            print(f"  ⚠ no photos for: {name}", file=sys.stderr)
        cache[name] = {"photos": entries}
        save_json(cache_path, cache)
        photos_by_name[name] = entries
        time.sleep(0.5)

    return photos_by_name
```

- [ ] **Step 2: Implement `main()` and replace the `if __name__` stub**

Append, replacing the previous `if __name__ == "__main__": pass` block:

```python
# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", required=True, help='"lat,lng" or an address (geocoded via Nominatim)')
    parser.add_argument("--slug", required=True, help="output identifier, used in all paths")
    parser.add_argument("--max-km", type=float, default=10.0, help="walking-distance budget (default 10)")
    parser.add_argument("--dry-run", action="store_true", help="print planned actions, no network writes")
    args = parser.parse_args()

    slug = slugify(args.slug)
    static_dir = REPO_ROOT / "static/temple-walks" / slug
    photos_dir = static_dir / "photos"
    content_path = REPO_ROOT / "content/temple-walks" / f"{slug}.md"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Phase 1: resolve start
    print("Phase 1: Resolving start…")
    start = parse_start(args.start)
    if start is None:
        if args.dry_run:
            sys.exit('✗ --dry-run needs a "lat,lng" start (address geocoding requires network)')
        start = geocode_start(args.start)
    print(f"  start: {start[0]:.5f}, {start[1]:.5f}")

    # Phase 2: discover temples (one Overpass query within the walk budget radius —
    # walking distance ≥ straight-line distance, so nothing reachable lies outside it)
    print("\nPhase 2: Overpass temple discovery…")
    overpass_cache = CACHE_DIR / f"{slug}.overpass.json"
    if args.dry_run and not overpass_cache.exists():
        print(f"  [dry-run] would query Overpass (around:{args.max_km * 1000:.0f} m) and chain temples with OSRM")
        return
    temples = fetch_temples(start[0], start[1], args.max_km * 1000, overpass_cache)
    print(f"  {len(temples)} named Buddhist temples within {args.max_km:.1f} km")
    if not temples:
        sys.exit(f"✗ no named Buddhist temples within {args.max_km:.1f} km of start")

    # Phase 3: greedy chain
    print("\nPhase 3: Greedy walk (OSRM)…")
    if args.dry_run:
        print(f"  [dry-run] would chain up to {len(temples)} temples with OSRM foot legs")
        return
    walk = plan_walk(start, temples, args.max_km, make_leg_fetcher(CACHE_DIR / f"{slug}.routes.json"))
    if not walk["stops"]:
        sys.exit(f"✗ nearest temple is already beyond the {args.max_km:.1f} km budget")
    if len(walk["stops"]) <= 2:
        print(f"  ⚠ short walk: only {len(walk['stops'])} temple stop(s)", file=sys.stderr)
    for s in walk["stops"]:
        print(f"  {s['order']:2d}. {s['name']} ({s['distance_km']:.2f} km)")
    print(f"  total: {walk['total_km']:.2f} km, {len(walk['stops'])} temples")

    # Phase 4: photos (non-fatal)
    print("\nPhase 4: Wikimedia photos…")
    photos_by_name = fetch_photos(walk["stops"], photos_dir, CACHE_DIR / f"{slug}.media.json", args.dry_run)

    # Phase 5: write outputs
    print("\nPhase 5: Writing outputs…")
    fc = build_geojson(start, walk, slug, photos_by_name)
    static_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = static_dir / "walk.geojson"
    geojson_path.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    print(f"  ✓ {geojson_path} — {len(fc['features'])} features")

    if content_path.exists():
        print(f"  = {content_path} exists — left untouched (only GeoJSON/photos regenerated)")
    else:
        content_path.parent.mkdir(parents=True, exist_ok=True)
        content_path.write_text(build_content_page(slug, args.start, len(walk["stops"]), walk["total_km"]))
        print(f"  ✓ {content_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the full test suite (must still pass — importing the module must not execute main)**

Run: `uv run --with pytest --with tqdm python -m pytest scripts/temple-walk/tests/ -v`
Expected: 26 PASSED

- [ ] **Step 4: Smoke-check the CLI parses and dry-run guards work**

Run: `uv run scripts/temple-walk/generate.py --start "13.7516,100.4927" --slug smoketest --dry-run`
Expected output (no network calls, exits after Phase 2 announcement):

```
Phase 1: Resolving start…
  start: 13.75160, 100.49270
...
Phase 2: Overpass temple discovery…
  [dry-run] would query Overpass (around:10000 m) and chain temples with OSRM
```

Run: `uv run scripts/temple-walk/generate.py --start "Democracy Monument, Bangkok" --slug smoketest --dry-run`
Expected: exits with `✗ --dry-run needs a "lat,lng" start (address geocoding requires network)`

- [ ] **Step 5: Commit**

```bash
git add scripts/temple-walk/
git commit -m "feat(temple-walk): network layer, caching, and CLI main"
```

---

### Task 6: Hugo layout for temple-walk pages

**Files:**
- Create: `layouts/temple-walk/single.html`

**Interfaces:**
- Consumes: the GeoJSON schema from Task 4 (`order: 0` start point; stop properties `name`, `order`, `photos`, `attribution`, `distance_km`; route `LineString`), and content front-matter from `build_content_page` (`type: "temple-walk"`, param `geojson`).
- Produces: the page rendered at `/temple-walks/<slug>/` for every `content/temple-walks/<slug>.md`. (Leaf pages with `type: temple-walk` resolve to `layouts/temple-walk/single.html`.)

- [ ] **Step 1: Create the layout**

Create `layouts/temple-walk/single.html` — adapted from `layouts/bangkok-citywalk/list.html` with four changes: geojson URL from `.Params.geojson`, title/description from front matter, a distinct green start marker for `order: 0`, and photo/distance handling for the new properties:

```html
{{ define "head" }}
{{ partial "map-poi-styles.html" . }}
<style>
body { background: #1a1a2e; }
.overlay-card {
  background: rgba(10,10,20,0.96) !important;
  border: 1px solid rgba(255,107,53,0.3) !important;
  box-shadow: 0 4px 24px rgba(0,0,0,0.8) !important;
  color: #eee;
}
.overlay-card h1, .overlay-card h2 { color: #fff; }
.overlay-card p { color: #999; }
.site-brand { color: #888 !important; }
.walk-badge {
  display: inline-block;
  width: 24px; height: 24px; border-radius: 50%;
  background: #FF6B35; color: #fff;
  font-size: 12px; font-weight: 700;
  text-align: center; line-height: 24px;
  margin-right: 0.4rem; flex-shrink: 0;
}
.poi-card {
  background: #111 !important;
  border: 1px solid rgba(255,255,255,0.08) !important;
  cursor: pointer;
}
.poi-card:hover, .poi-card.active { border-color: #FF6B35 !important; }
.poi-card h3 { color: #eee; font-size: 0.9rem; margin: 0; }
.poi-meta { font-size: 0.75rem; color: #666; margin-top: 0.25rem; }
.poi-photo { width: 100%; height: 140px; object-fit: cover; border-radius: 6px; margin-bottom: 0.5rem; }
.route-legend {
  display: flex; align-items: center; gap: 0.5rem;
  font-size: 0.75rem; color: #777; margin-top: 0.5rem;
}
.route-line-sample {
  width: 32px; height: 3px; background: #FF6B35;
  border-radius: 2px; flex-shrink: 0;
}
</style>
{{ end }}

{{ define "main" }}
<div id="map"></div>
<div class="map-overlay">
  <div class="overlay-card">
    <a href="{{ "/" | relURL }}" class="site-brand">🗺️ Maps</a>
    <div class="page-header">
      <h1 style="font-size:1.2rem;font-weight:700;margin:0 0 0.25rem;">{{ .Title }}</h1>
      <p style="font-size:0.8rem;margin:0;">{{ .Description }}</p>
    </div>
    <div class="route-legend">
      <div class="route-line-sample"></div>
      <span>Walking route</span>
    </div>
  </div>
  <div class="overlay-card scrollable">
    <div id="poi-list"></div>
  </div>
</div>
{{ end }}

{{ define "scripts" }}
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
(function () {
  const GEOJSON_URL = '{{ .Params.geojson | relURL }}';
  const ROUTE_COLOR = '#FF6B35';
  const START_COLOR = '#2ecc71';
  const TILE_URL = 'https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png?api_key={{ os.Getenv "HUGO_STADIA_API_KEY" }}';

  const map = L.map('map', { maxZoom: 18 }).setView([13.736717, 100.523186], 13);
  L.tileLayer(TILE_URL, {
    attribution: '© <a href="https://stadiamaps.com/">Stadia Maps</a>, © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 18,
  }).addTo(map);

  fetch(GEOJSON_URL)
    .then(r => r.json())
    .then(data => {
      const points = data.features
        .filter(f => f.geometry.type === 'Point')
        .sort((a, b) => a.properties.order - b.properties.order);
      const routeFeature = data.features.find(f => f.geometry.type === 'LineString');

      // Draw route polyline (GeoJSON [lng,lat] → Leaflet [lat,lng])
      if (routeFeature) {
        const latLngs = routeFeature.geometry.coordinates.map(([lng, lat]) => [lat, lng]);
        L.polyline(latLngs, {
          color: ROUTE_COLOR,
          weight: 3,
          opacity: 0.85,
          dashArray: '8 4',
        }).addTo(map);
        map.fitBounds(L.polyline(latLngs).getBounds(), { padding: [40, 40] });
      }

      // Draw markers: green "S" for the start (order 0), numbered orange for temples
      points.forEach(f => {
        const [lng, lat] = f.geometry.coordinates;
        const p = f.properties;
        const isStart = p.order === 0;
        const bg = isStart ? START_COLOR : ROUTE_COLOR;
        const label = isStart ? 'S' : p.order;
        const icon = L.divIcon({
          className: '',
          html: `<div style="width:28px;height:28px;border-radius:50%;background:${bg};color:#fff;font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,0.6);border:2px solid #fff;">${label}</div>`,
          iconSize: [28, 28],
          iconAnchor: [14, 14],
        });
        const marker = L.marker([lat, lng], { icon });
        marker.addTo(map);
        if (!isStart) marker.on('click', () => focusPOI(f));
      });

      // Render sidebar list (temples only, not the start point)
      const list = document.getElementById('poi-list');
      points.filter(f => f.properties.order > 0).forEach(f => {
        const p = f.properties;
        const photo = (p.photos && p.photos.length) ? p.photos[0] : null;
        const card = document.createElement('div');
        card.className = 'poi-card';
        card.id = `card-${p.order}`;
        card.style.cssText = 'border-radius:8px;padding:0.75rem;margin-bottom:0.4rem;';
        card.innerHTML = `
          ${photo ? `<img class="poi-photo" src="${photo}" alt="${p.name}" loading="lazy">` : ''}
          <div style="display:flex;align-items:center;">
            <span class="walk-badge">${p.order}</span>
            <h3>${p.name}</h3>
          </div>
          <div class="poi-meta">${p.distance_km} km from start${p.attribution ? ` · ${p.attribution}` : ''}</div>
        `;
        card.addEventListener('click', () => focusPOI(f));
        list.appendChild(card);
      });

      function focusPOI(f) {
        const [lng, lat] = f.geometry.coordinates;
        map.flyTo([lat, lng], 16, { duration: 1 });
        document.querySelectorAll('.poi-card').forEach(c => c.classList.remove('active'));
        const card = document.getElementById(`card-${f.properties.order}`);
        if (card) {
          card.classList.add('active');
          card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      }
    })
    .catch(err => console.error('Failed to load walk.geojson:', err));
})();
</script>
{{ end }}
```

- [ ] **Step 2: Verify Hugo builds with the new layout (no content yet — must not break the site)**

Run: `hugo --quiet --renderToMemory && echo BUILD_OK`
Expected: `BUILD_OK` (warnings acceptable; no errors)

- [ ] **Step 3: Commit**

```bash
git add layouts/temple-walk/
git commit -m "feat(temple-walk): Hugo layout for temple-walk pages"
```

---

### Task 7: End-to-end verification run (Rattanakosin, Bangkok)

**Files:**
- Create (generated): `static/temple-walks/rattanakosin/walk.geojson`, `static/temple-walks/rattanakosin/photos/*.jpg`, `content/temple-walks/rattanakosin.md`

**Interfaces:**
- Consumes: the complete script (Tasks 1–5) and layout (Task 6).
- Produces: the first committed walk, proving the pipeline end-to-end.

- [ ] **Step 1: Run the generator for real (Grand Palace area start)**

Run: `uv run scripts/temple-walk/generate.py --start "13.7516,100.4927" --slug rattanakosin`
Expected: Phase 2 reports dozens of temples (central Bangkok is dense); Phase 3 prints an ordered stop list with cumulative km, total ≤ 10.00 km, likely 8+ temples; Phase 5 writes `static/temple-walks/rattanakosin/walk.geojson` and `content/temple-walks/rattanakosin.md`. Takes a few minutes (1 s per OSRM leg + photo downloads).

- [ ] **Step 2: Sanity-check the generated GeoJSON**

Run:
```bash
python3 - <<'EOF'
import json
fc = json.load(open("static/temple-walks/rattanakosin/walk.geojson"))
pts = [f for f in fc["features"] if f["geometry"]["type"] == "Point"]
route = [f for f in fc["features"] if f["geometry"]["type"] == "LineString"]
assert pts[0]["properties"]["order"] == 0, "first point must be the start"
assert len(route) == 1, "exactly one route LineString"
total = route[0]["properties"]["total_km"]
assert 0 < total <= 10.0, f"total_km out of budget: {total}"
orders = [p["properties"]["order"] for p in pts]
assert orders == list(range(len(pts))), f"orders not sequential: {orders}"
print(f"OK — {len(pts)-1} temples, {total} km, {len(route[0]['geometry']['coordinates'])} route points")
EOF
```
Expected: `OK — <N> temples, <X> km, <M> route points` with X ≤ 10.

- [ ] **Step 3: Verify idempotent re-run (caches hit, content preserved)**

Run: `uv run scripts/temple-walk/generate.py --start "13.7516,100.4927" --slug rattanakosin`
Expected: finishes in seconds (no OSRM sleeps — all legs cached), prints `= …/content/temple-walks/rattanakosin.md exists — left untouched`.

- [ ] **Step 4: Verify the Hugo page renders**

Run: `hugo --quiet --renderToMemory && echo BUILD_OK`
Expected: `BUILD_OK`

Then run `hugo server` and open `http://localhost:1313/temple-walks/rattanakosin/` — expect: dark map, green "S" start marker near the Grand Palace, numbered orange temple markers, dashed route line, sidebar cards with photos and cumulative km. (If tiles are blank, `HUGO_STADIA_API_KEY` is unset — markers and route must still render.)

- [ ] **Step 5: Run the full test suite one final time**

Run: `uv run --with pytest --with tqdm python -m pytest scripts/temple-walk/tests/ -v`
Expected: 26 PASSED

- [ ] **Step 6: Commit the generated walk**

```bash
git add static/temple-walks/ content/temple-walks/
git commit -m "feat(temple-walk): add rattanakosin walk (generated)"
```
