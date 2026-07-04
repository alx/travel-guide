# Multi-Walk Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract shared logic from `generate.py` into `lib.py`, add a stochastic `plan_walk_random`, then build `multi.py` + a Hugo layout that runs and displays 10–100 randomised temple walks from one starting point.

**Architecture:** All shared pure/network functions live in `lib.py` (plain module, no shebang). `generate.py` is reduced to `main()` + `from lib import ...`. `multi.py` is a new uv script that calls `plan_walk_random` N times (one `random.Random(i)` seed per run), writes a combined GeoJSON, and generates a Hugo content page rendered by a new `temple-walk-multi` layout.

**Tech Stack:** Python ≥ 3.11, uv script format, stdlib only for `multi.py` (tqdm already in `generate.py`; moved to lazy import in `lib.py`). Hugo with Leaflet 1.9.4 for the map layout.

## Global Constraints

- Python ≥ 3.11 (union type hints `X | Y`, `tuple[...]` built-in generics required)
- All scripts run via `uv run`; never `python` or `python3`
- `lib.py` has no shebang, no `if __name__ == "__main__"`, no uv inline metadata
- `tqdm` is a lazy import inside `fetch_photos` in `lib.py` (not top-level), so `import lib` never requires tqdm
- Cache files are git-ignored; Overpass + OSRM calls populate them on first run
- `multi.py` never fetches photos; no `--dry-run` flag
- GeoJSON property `type` field values: `"start"`, `"stop"`, `"route"` (exact strings — layout JS filters on these)
- Hugo layout inherits exact dark theme + Stadia Maps tile from `layouts/temple-walk/single.html`

---

### Task 1: Create `lib.py` with all shared functions from `generate.py`

**Files:**
- Create: `scripts/temple-walk/lib.py`

**Interfaces:**
- Produces (consumed by Tasks 2, 3, 4):
  - `slugify(text: str) -> str`
  - `stop_slug(stop: dict) -> str`
  - `load_json(path: Path) -> dict`
  - `save_json(path: Path, data) -> None`
  - `haversine_km(lat1, lng1, lat2, lng2: float) -> float`
  - `parse_start(value: str) -> tuple[float, float] | None`
  - `parse_overpass_elements(elements: list[dict]) -> list[dict]`
  - `DEGENERATE_LEG_KM: float` (= 0.05)
  - `resolve_leg(lat1, lng1, lat2, lng2: float, fetch_leg) -> tuple[list, float]`
  - `plan_walk(start, temples, max_km, fetch_leg, k_candidates=3) -> dict`
  - `build_geojson(start, walk, slug, photos_by_name) -> dict`
  - `build_content_page(slug, start_label, n_stops, total_km) -> str`
  - `http_json_retry(url, data=None, timeout=60)`
  - `geocode_start(address: str) -> tuple[float, float]`
  - `fetch_temples(lat, lng, radius_m, cache_path) -> list[dict]`
  - `make_leg_fetcher(cache_path: Path)` — returns `fetch_leg(lat1, lng1, lat2, lng2)`
  - `fetch_wikimedia_photos(name: str, max_results=5) -> list[tuple[str, str]]`
  - `fetch_photos(stops, photos_dir, cache_path, dry_run) -> dict[str, list[dict]]`

- [ ] **Step 1: Write `lib.py`**

  Create `scripts/temple-walk/lib.py` with the exact content below. This is a verbatim move from `generate.py` — all constants and functions except `main()`. Note `tqdm` is imported lazily inside `fetch_photos`.

  ```python
  """Shared pure helpers and network functions for the temple-walk scripts."""

  import json
  import math
  import re
  import sys
  import time
  import urllib.parse
  import urllib.request
  from pathlib import Path

  USER_AGENT = {"User-Agent": "maps.girard-davila.net/temple-walk (girard.davila@gmail.com)"}
  NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
  OVERPASS_URL = "https://overpass-api.de/api/interpreter"
  OSRM_BASE = "http://router.project-osrm.org/route/v1/foot"
  COMMONS_API = "https://commons.wikimedia.org/w/api.php"

  DEGENERATE_LEG_KM = 0.05


  def slugify(text: str) -> str:
      text = text.lower()
      for src, dst in [("é","e"),("è","e"),("ê","e"),("à","a"),("â","a"),
                       ("ù","u"),("û","u"),("î","i"),("ô","o"),("ç","c")]:
          text = text.replace(src, dst)
      text = re.sub(r"[^a-z0-9]+", "-", text)
      return text.strip("-")


  def stop_slug(stop: dict) -> str:
      return slugify(stop["name"]) or stop["osm_id"].replace("/", "-")


  def load_json(path: Path) -> dict:
      return json.loads(path.read_text()) if path.exists() else {}


  def save_json(path: Path, data) -> None:
      path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


  def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
      r = 6371.0
      p1, p2 = math.radians(lat1), math.radians(lat2)
      dp = math.radians(lat2 - lat1)
      dl = math.radians(lng2 - lng1)
      a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
      return 2 * r * math.asin(math.sqrt(a))


  def parse_start(value: str) -> tuple[float, float] | None:
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


  def parse_overpass_elements(elements: list[dict]) -> list[dict]:
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


  def resolve_leg(lat1: float, lng1: float, lat2: float, lng2: float, fetch_leg) -> tuple[list, float]:
      straight = haversine_km(lat1, lng1, lat2, lng2)
      if straight < DEGENERATE_LEG_KM:
          return [[lng1, lat1], [lng2, lat2]], straight
      return fetch_leg(lat1, lng1, lat2, lng2)


  def plan_walk(start: tuple[float, float], temples: list[dict], max_km: float, fetch_leg,
                k_candidates: int = 3) -> dict:
      current = start
      total = 0.0
      stops: list[dict] = []
      all_coords: list[list[float]] = []
      segment_breaks: list[int] = []
      remaining = list(temples)

      while remaining:
          by_haversine = sorted(
              remaining,
              key=lambda t: haversine_km(current[0], current[1], t["lat"], t["lng"]),
          )
          routed = [
              (t, *resolve_leg(current[0], current[1], t["lat"], t["lng"], fetch_leg))
              for t in by_haversine[:k_candidates]
          ]
          candidate, coords, dist = min(routed, key=lambda r: r[2])
          if total + dist > max_km:
              break
          segment_breaks.append(len(all_coords))
          if all_coords and coords:
              coords = coords[1:]
          all_coords.extend(coords)
          total += dist
          stops.append({**candidate, "order": len(stops) + 1, "distance_km": round(total, 2)})
          current = (candidate["lat"], candidate["lng"])
          remaining.remove(candidate)

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


  def build_geojson(start: tuple[float, float], walk: dict, slug: str,
                    photos_by_name: dict[str, list[dict]]) -> dict:
      features = [{
          "type": "Feature",
          "geometry": {"type": "Point", "coordinates": [start[1], start[0]]},
          "properties": {"name": "Start", "order": 0, "slug": "start", "photos": []},
      }]
      for stop in walk["stops"]:
          tslug = stop_slug(stop)
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


  def http_json_retry(url: str, data: bytes | None = None, timeout: int = 60):
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
      params = urllib.parse.urlencode({
          "action": "query",
          "generator": "search",
          "gsrnamespace": 6,
          "gsrsearch": name,
          "gsrlimit": max_results * 3,
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
      from tqdm import tqdm  # lazy import — only generate.py uses this
      photos_by_name: dict[str, list[dict]] = {}
      cache = load_json(cache_path)

      for stop in tqdm(stops, desc="Wikimedia photos", unit="temple"):
          name = stop["name"]
          tslug = stop_slug(stop)
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

- [ ] **Step 2: Verify lib.py is importable**

  ```bash
  cd scripts/temple-walk && uv run --no-project python -c "import lib; print('lib ok:', lib.haversine_km(0,0,0,0))"
  ```

  Expected output: `lib ok: 0.0`

- [ ] **Step 3: Commit**

  ```bash
  git add scripts/temple-walk/lib.py
  git commit -m "feat(temple-walk): extract shared functions into lib.py"
  ```

---

### Task 2: Refactor `generate.py` to import from `lib.py`

**Files:**
- Modify: `scripts/temple-walk/generate.py`
- Test: `scripts/temple-walk/tests/test_generate.py` (no changes needed — `from lib import *` re-exports all names into the `generate` namespace)

**Interfaces:**
- Consumes: all exports from `lib.py` (Task 1)
- Produces: `generate.py` reduced to shebang + imports + `main()` only; all function names still accessible as `generate.<name>` for backward-compat with tests

- [ ] **Step 1: Replace `generate.py` with the refactored version**

  Overwrite `scripts/temple-walk/generate.py` with:

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
  import sys
  from pathlib import Path

  SCRIPT_DIR = Path(__file__).parent
  REPO_ROOT = SCRIPT_DIR.parent.parent
  CACHE_DIR = SCRIPT_DIR / "cache"

  from lib import (  # noqa: E402
      slugify, stop_slug, load_json, save_json,
      haversine_km, parse_start, parse_overpass_elements,
      DEGENERATE_LEG_KM, resolve_leg, plan_walk,
      build_geojson, build_content_page,
      http_json_retry, geocode_start, fetch_temples,
      make_leg_fetcher, fetch_wikimedia_photos, fetch_photos,
  )


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

      print("Phase 1: Resolving start…")
      start = parse_start(args.start)
      if start is None:
          if args.dry_run:
              sys.exit('✗ --dry-run needs a "lat,lng" start (address geocoding requires network)')
          start = geocode_start(args.start)
      print(f"  start: {start[0]:.5f}, {start[1]:.5f}")

      print("\nPhase 2: Overpass temple discovery…")
      overpass_cache = CACHE_DIR / f"{slug}.overpass.json"
      if args.dry_run and not overpass_cache.exists():
          print(f"  [dry-run] would query Overpass (around:{args.max_km * 1000:.0f} m) and chain temples with OSRM")
          return
      temples = fetch_temples(start[0], start[1], args.max_km * 1000, overpass_cache)
      print(f"  {len(temples)} named Buddhist temples within {args.max_km:.1f} km")
      if not temples:
          sys.exit(f"✗ no named Buddhist temples within {args.max_km:.1f} km of start")

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

      print("\nPhase 4: Wikimedia photos…")
      photos_by_name = fetch_photos(walk["stops"], photos_dir, CACHE_DIR / f"{slug}.media.json", args.dry_run)

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

- [ ] **Step 2: Run existing tests to verify nothing broke**

  ```bash
  cd scripts/temple-walk && uv run --with pytest pytest tests/ -v
  ```

  Expected: all tests pass (same count as before the refactor).

- [ ] **Step 3: Commit**

  ```bash
  git add scripts/temple-walk/generate.py
  git commit -m "refactor(temple-walk): generate.py imports from lib.py"
  ```

---

### Task 3: Add `plan_walk_random` to `lib.py` with tests (TDD)

**Files:**
- Modify: `scripts/temple-walk/lib.py`
- Create: `scripts/temple-walk/tests/test_lib.py`

**Interfaces:**
- Consumes: `plan_walk`, `haversine_km`, `resolve_leg`, `DEGENERATE_LEG_KM` from `lib.py`
- Produces: `plan_walk_random(start: tuple[float, float], temples: list[dict], max_km: float, fetch_leg, rng, k_candidates: int = 3) -> dict`
  - `rng` is any object with a `.choice(seq)` method (e.g. `random.Random`)
  - return shape identical to `plan_walk`: `{stops, route_coords, segment_breaks, total_km}`

- [ ] **Step 1: Write failing tests in `tests/test_lib.py`**

  Create `scripts/temple-walk/tests/test_lib.py`:

  ```python
  import random
  import sys
  from pathlib import Path

  sys.path.insert(0, str(Path(__file__).parent.parent))

  import lib


  def fake_leg(lat1, lng1, lat2, lng2):
      return [[lng1, lat1], [lng2, lat2]], lib.haversine_km(lat1, lng1, lat2, lng2)


  def temple(name, lat, lng):
      return {"name": name, "lat": lat, "lng": lng, "osm_type": "node", "osm_id": f"node/{name}"}


  def test_plan_walk_random_reproducible():
      temples = [temple("A", 0.0, 0.01), temple("B", 0.0, 0.02), temple("C", 0.0, 0.03)]
      w1 = lib.plan_walk_random((0.0, 0.0), temples, 10.0, fake_leg, random.Random(42))
      w2 = lib.plan_walk_random((0.0, 0.0), temples, 10.0, fake_leg, random.Random(42))
      assert [s["name"] for s in w1["stops"]] == [s["name"] for s in w2["stops"]]


  def test_plan_walk_random_produces_variation():
      # Three temples almost equidistant — random seed should vary which is chosen first
      temples = [temple("A", 0.0, 0.0100), temple("B", 0.0, 0.0101), temple("C", 0.0, 0.0102)]
      first_stops = set()
      for seed in range(30):
          w = lib.plan_walk_random((0.0, 0.0), list(temples), 10.0, fake_leg, random.Random(seed))
          if w["stops"]:
              first_stops.add(w["stops"][0]["name"])
      assert len(first_stops) > 1, "Expected at least two different first-stop choices across 30 seeds"


  def test_plan_walk_random_never_exceeds_budget():
      temples = [temple("A", 0.0, 0.01), temple("B", 0.0, 0.5)]
      for seed in range(10):
          w = lib.plan_walk_random((0.0, 0.0), temples, 5.0, fake_leg, random.Random(seed))
          assert w["total_km"] <= 5.0


  def test_plan_walk_random_empty_when_first_temple_beyond_budget():
      temples = [temple("Far", 0.0, 0.5)]  # ~55 km away
      w = lib.plan_walk_random((0.0, 0.0), temples, 10.0, fake_leg, random.Random(0))
      assert w["stops"] == []
      assert w["route_coords"] == []
      assert w["total_km"] == 0.0


  def test_plan_walk_random_never_revisits():
      temples = [temple("A", 0.0, 0.01)]
      w = lib.plan_walk_random((0.0, 0.0), temples, 100.0, fake_leg, random.Random(0))
      assert len(w["stops"]) == 1


  def test_plan_walk_random_return_shape():
      temples = [temple("A", 0.0, 0.01)]
      w = lib.plan_walk_random((0.0, 0.0), temples, 10.0, fake_leg, random.Random(0))
      assert set(w.keys()) == {"stops", "route_coords", "segment_breaks", "total_km"}
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  cd scripts/temple-walk && uv run --with pytest pytest tests/test_lib.py -v
  ```

  Expected: `AttributeError: module 'lib' has no attribute 'plan_walk_random'`

- [ ] **Step 3: Add `plan_walk_random` to `lib.py`**

  Insert this function into `scripts/temple-walk/lib.py` immediately after the `plan_walk` function:

  ```python
  def plan_walk_random(start: tuple[float, float], temples: list[dict], max_km: float, fetch_leg,
                       rng, k_candidates: int = 3) -> dict:
      """Like plan_walk, but picks uniformly at random from the k routed candidates each step."""
      current = start
      total = 0.0
      stops: list[dict] = []
      all_coords: list[list[float]] = []
      segment_breaks: list[int] = []
      remaining = list(temples)

      while remaining:
          by_haversine = sorted(
              remaining,
              key=lambda t: haversine_km(current[0], current[1], t["lat"], t["lng"]),
          )
          routed = [
              (t, *resolve_leg(current[0], current[1], t["lat"], t["lng"], fetch_leg))
              for t in by_haversine[:k_candidates]
          ]
          candidate, coords, dist = rng.choice(routed)
          if total + dist > max_km:
              break
          segment_breaks.append(len(all_coords))
          if all_coords and coords:
              coords = coords[1:]
          all_coords.extend(coords)
          total += dist
          stops.append({**candidate, "order": len(stops) + 1, "distance_km": round(total, 2)})
          current = (candidate["lat"], candidate["lng"])
          remaining.remove(candidate)

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

- [ ] **Step 4: Run all tests to confirm they pass**

  ```bash
  cd scripts/temple-walk && uv run --with pytest pytest tests/ -v
  ```

  Expected: all tests pass (original test_generate.py + new test_lib.py).

- [ ] **Step 5: Commit**

  ```bash
  git add scripts/temple-walk/lib.py scripts/temple-walk/tests/test_lib.py
  git commit -m "feat(temple-walk): add plan_walk_random to lib.py with tests"
  ```

---

### Task 4: Create `multi.py`

**Files:**
- Create: `scripts/temple-walk/multi.py`

**Interfaces:**
- Consumes from `lib.py`: `slugify`, `parse_start`, `geocode_start`, `fetch_temples`, `make_leg_fetcher`, `plan_walk_random`, `save_json`
- Produces:
  - `build_multi_geojson(start: tuple[float, float], walks: list[dict]) -> dict` — FeatureCollection with `type` property `"start"` / `"stop"` / `"route"` on each feature
  - `build_multi_content_page(slug, start_label, n_runs, min_km, max_km) -> str`
  - CLI: `uv run scripts/temple-walk/multi.py --start "lat,lng" --slug SLUG [--runs N] [--max-km X]`

- [ ] **Step 1: Create `scripts/temple-walk/multi.py`**

  ```python
  #!/usr/bin/env -S uv run --script
  # /// script
  # requires-python = ">=3.11"
  # dependencies = []
  # ///
  """
  Generate multiple stochastic temple-walk routes from a single starting point.

  Runs plan_walk_random N times with independent seeds, producing a combined
  GeoJSON for visualization. Shares Overpass + OSRM caches with generate.py.
  Photos are never fetched.

  Usage:
      uv run scripts/temple-walk/multi.py --start "13.7516,100.4927" --slug rattanakosin
      uv run scripts/temple-walk/multi.py --start "13.7516,100.4927" --slug rattanakosin --runs 30 --max-km 10
  """

  import argparse
  import json
  import random
  import sys
  from pathlib import Path

  SCRIPT_DIR = Path(__file__).parent
  REPO_ROOT = SCRIPT_DIR.parent.parent
  CACHE_DIR = SCRIPT_DIR / "cache"

  from lib import (  # noqa: E402
      slugify, parse_start, geocode_start,
      fetch_temples, make_leg_fetcher, plan_walk_random,
  )


  def build_multi_geojson(start: tuple[float, float], walks: list[dict]) -> dict:
      features = [{
          "type": "Feature",
          "geometry": {"type": "Point", "coordinates": [start[1], start[0]]},
          "properties": {"type": "start", "name": "Start", "order": 0},
      }]

      seen: set[str] = set()
      for walk in walks:
          for stop in walk["stops"]:
              if stop["name"] not in seen:
                  seen.add(stop["name"])
                  features.append({
                      "type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [stop["lng"], stop["lat"]]},
                      "properties": {"type": "stop", "name": stop["name"]},
                  })

      for i, walk in enumerate(walks):
          if walk["route_coords"]:
              features.append({
                  "type": "Feature",
                  "geometry": {"type": "LineString", "coordinates": walk["route_coords"]},
                  "properties": {
                      "type": "route",
                      "walk_index": i,
                      "n_stops": len(walk["stops"]),
                      "total_km": walk["total_km"],
                  },
              })

      return {"type": "FeatureCollection", "features": features}


  def build_multi_content_page(slug: str, start_label: str, n_runs: int,
                                min_km: float, max_km: float) -> str:
      title = f"Temple Walk Explorer — {slug.replace('-', ' ').title()}"
      return (
          "---\n"
          f'title: "{title}"\n'
          f'description: "{n_runs} walks, {min_km}–{max_km} km, from {start_label}"\n'
          'type: "temple-walk-multi"\n'
          f'geojson: "/temple-walks/{slug}/multi-walk.geojson"\n'
          "---\n"
      )


  def main() -> None:
      parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
      parser.add_argument("--start", required=True, help='"lat,lng" or an address')
      parser.add_argument("--slug", required=True, help="output identifier, shared with generate.py")
      parser.add_argument("--runs", type=int, default=20, help="number of walks to generate (default 20)")
      parser.add_argument("--max-km", type=float, default=10.0, help="walking-distance budget per walk (default 10)")
      args = parser.parse_args()

      slug = slugify(args.slug)
      static_dir = REPO_ROOT / "static/temple-walks" / slug
      content_path = REPO_ROOT / "content/temple-walks" / f"{slug}-multi.md"
      CACHE_DIR.mkdir(parents=True, exist_ok=True)

      print("Phase 1: Resolving start…")
      start = parse_start(args.start)
      if start is None:
          start = geocode_start(args.start)
      print(f"  start: {start[0]:.5f}, {start[1]:.5f}")

      print("\nPhase 2: Overpass temple discovery…")
      overpass_cache = CACHE_DIR / f"{slug}.overpass.json"
      temples = fetch_temples(start[0], start[1], args.max_km * 1000, overpass_cache)
      print(f"  {len(temples)} named Buddhist temples within {args.max_km:.1f} km")
      if not temples:
          sys.exit(f"✗ no named Buddhist temples within {args.max_km:.1f} km of start")

      print(f"\nPhase 3: Generating {args.runs} stochastic walks…")
      fetch_leg = make_leg_fetcher(CACHE_DIR / f"{slug}.routes.json")
      walks = []
      for i in range(args.runs):
          rng = random.Random(i)
          walk = plan_walk_random(start, list(temples), args.max_km, fetch_leg, rng)
          walks.append(walk)
          print(f"  walk {i+1:3d}: {len(walk['stops'])} temples, {walk['total_km']:.2f} km")

      nonempty = [w for w in walks if w["stops"]]
      if not nonempty:
          sys.exit("✗ all walks produced zero stops — check start point and radius")

      km_values = [w["total_km"] for w in nonempty]
      print(f"  {len(nonempty)}/{args.runs} non-empty · {min(km_values):.2f}–{max(km_values):.2f} km range")

      print("\nPhase 4: Writing outputs…")
      fc = build_multi_geojson(start, walks)
      static_dir.mkdir(parents=True, exist_ok=True)
      geojson_path = static_dir / "multi-walk.geojson"
      geojson_path.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
      print(f"  ✓ {geojson_path} — {len(fc['features'])} features")

      if content_path.exists():
          print(f"  = {content_path} exists — left untouched")
      else:
          content_path.parent.mkdir(parents=True, exist_ok=True)
          content_path.write_text(
              build_multi_content_page(
                  slug, args.start, len(nonempty),
                  round(min(km_values), 2), round(max(km_values), 2),
              )
          )
          print(f"  ✓ {content_path}")

      print("\nDone.")


  if __name__ == "__main__":
      main()
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add scripts/temple-walk/multi.py
  git commit -m "feat(temple-walk): add multi.py stochastic walk runner"
  ```

---

### Task 5: Create `layouts/temple-walk-multi/single.html`

**Files:**
- Create: `layouts/temple-walk-multi/single.html`

**Interfaces:**
- Consumes GeoJSON features with `properties.type` in `{"start", "stop", "route"}`
- Reads `{{ .Params.geojson }}` for the GeoJSON URL (set by `multi.py`)
- Reads `{{ os.Getenv "HUGO_STADIA_API_KEY" }}` for the tile API key (same env var as `layouts/temple-walk/single.html`)

- [ ] **Step 1: Create `layouts/temple-walk-multi/single.html`**

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
  .overlay-card h1 { color: #fff; }
  .overlay-card p { color: #999; }
  .site-brand { color: #888 !important; }
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
        <span>Overlapping routes — brighter = more walks pass through</span>
      </div>
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
        const allLatLngs = [];

        // Routes drawn first (below dots)
        data.features
          .filter(f => f.properties.type === 'route')
          .forEach(f => {
            const latLngs = f.geometry.coordinates.map(([lng, lat]) => [lat, lng]);
            L.polyline(latLngs, { color: ROUTE_COLOR, weight: 3, opacity: 0.15 }).addTo(map);
            allLatLngs.push(...latLngs);
          });

        // Stop dots with tooltip
        data.features
          .filter(f => f.properties.type === 'stop')
          .forEach(f => {
            const [lng, lat] = f.geometry.coordinates;
            L.circleMarker([lat, lng], {
              radius: 4,
              color: ROUTE_COLOR,
              fillColor: ROUTE_COLOR,
              fillOpacity: 0.9,
              weight: 1,
            }).bindTooltip(f.properties.name).addTo(map);
          });

        // Start marker
        const startFeature = data.features.find(f => f.properties.type === 'start');
        if (startFeature) {
          const [lng, lat] = startFeature.geometry.coordinates;
          const icon = L.divIcon({
            className: '',
            html: `<div style="width:28px;height:28px;border-radius:50%;background:${START_COLOR};color:#fff;font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,0.6);border:2px solid #fff;">S</div>`,
            iconSize: [28, 28],
            iconAnchor: [14, 14],
          });
          L.marker([lat, lng], { icon }).addTo(map);
        }

        if (allLatLngs.length) {
          map.fitBounds(L.latLngBounds(allLatLngs), { padding: [40, 40] });
        }
      })
      .catch(err => console.error('Failed to load multi-walk.geojson:', err));
  })();
  </script>
  {{ end }}
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add layouts/temple-walk-multi/single.html
  git commit -m "feat(temple-walk): add temple-walk-multi Hugo layout"
  ```

---

### Task 6: Smoke test

**Files:** none created — verification only

- [ ] **Step 1: Run multi.py for rattanakosin**

  This will make real network calls on first run (Overpass API + OSRM). Expected runtime: 1–3 minutes depending on number of OSRM cache misses.

  ```bash
  uv run scripts/temple-walk/multi.py \
    --start "13.7516,100.4927" \
    --slug rattanakosin \
    --runs 20 \
    --max-km 10
  ```

  Expected output (approximate):
  ```
  Phase 1: Resolving start…
    start: 13.75160, 100.49270
  Phase 2: Overpass temple discovery…
    N named Buddhist temples within 10.0 km
  Phase 3: Generating 20 stochastic walks…
    walk   1: K temples, X.XX km
    ...
    walk  20: K temples, X.XX km
    20/20 non-empty · X.XX–Y.YY km range
  Phase 4: Writing outputs…
    ✓ static/temple-walks/rattanakosin/multi-walk.geojson — N features
    ✓ content/temple-walks/rattanakosin-multi.md
  Done.
  ```

- [ ] **Step 2: Verify GeoJSON structure**

  ```bash
  python3 -c "
  import json
  fc = json.load(open('static/temple-walks/rattanakosin/multi-walk.geojson'))
  types = [f['properties']['type'] for f in fc['features']]
  print('start:', types.count('start'))
  print('stops:', types.count('stop'))
  print('routes:', types.count('route'))
  "
  ```

  Expected:
  ```
  start: 1
  stops: <some positive number>
  routes: 20
  ```

- [ ] **Step 3: Verify Hugo content page**

  ```bash
  cat content/temple-walks/rattanakosin-multi.md
  ```

  Expected: valid YAML frontmatter with `type: "temple-walk-multi"` and `geojson:` pointing to `/temple-walks/rattanakosin/multi-walk.geojson`.

- [ ] **Step 4: Start Hugo dev server and visually verify the map**

  ```bash
  hugo server -D
  ```

  Open `http://localhost:1313/temple-walks/rattanakosin-multi/` in the browser.

  Check:
  - Map loads with dark Stadia tile
  - Multiple semi-transparent orange routes visible, overlapping near the start
  - Green "S" marker at the start point
  - Small orange dots at temple locations, showing temple name on hover
  - No numbered badges

- [ ] **Step 5: Commit smoke-test outputs**

  ```bash
  git add static/temple-walks/rattanakosin/multi-walk.geojson content/temple-walks/rattanakosin-multi.md
  git commit -m "feat(temple-walk): add rattanakosin multi-walk explorer (20 walks)"
  ```
