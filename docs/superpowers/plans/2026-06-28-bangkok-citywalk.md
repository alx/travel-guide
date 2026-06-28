# Bangkok City Walk — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a curated 10-POI Bangkok city walk with a Leaflet map page (markers + walking route) and a 1080×1920 Remotion video (YouTube Shorts) animating between POIs with Wikimedia photos.

**Architecture:** Python pipeline (`generate.py`) geocodes POIs via Nominatim, fetches pedestrian routing via OSRM, downloads photos via Wikimedia Commons, and writes `walk.geojson`. A Hugo layout (`layouts/bangkok-citywalk/list.html`) renders the interactive Leaflet map. A Remotion composition (`scripts/bangkok-citywalk/remotion/`) renders the video from the same GeoJSON, re-using the MapLibre camera animation pattern from `toulouse-distorama`.

**Tech Stack:** Python 3.11+ (uv inline deps), Nominatim, public OSRM API, Wikimedia Commons API, Hugo, Leaflet 1.9.4, Remotion 4.x, MapLibre GL 4.x, TypeScript, Node.js (CommonJS).

## Global Constraints

- Python scripts: `#!/usr/bin/env -S uv run --script` shebang with `# /// script` inline deps block — always run with `uv run`, never `python3`
- GeoJSON coordinates are always `[lng, lat]` (GeoJSON spec); Leaflet expects `[lat, lng]` — swap on read in the Hugo layout
- Cache files (`.geocache.json`, `.routecache.json`, `.mediacache.json`) load existing data first, then patch — never blank-overwrite on start
- Nominatim ToS: `time.sleep(1.1)` between every request
- OSRM public endpoint: `http://router.project-osrm.org/route/v1/foot/` — no key required
- Wikimedia Commons API: `https://commons.wikimedia.org/w/api.php` — no key required
- Remotion render: always pass `chromiumOptions: {gl: 'swangle'}` for headless/CI
- MAPTILER_API_KEY from `.env` at repo root powers MapLibre tiles inside Remotion
- All new files live under `scripts/bangkok-citywalk/`, `layouts/bangkok-citywalk/`, `content/bangkok-citywalk/`, `static/bangkok-citywalk/`

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `scripts/bangkok-citywalk/venues.csv` | Create | Seed list of 10 POIs |
| `scripts/bangkok-citywalk/.gitignore` | Create | Exclude cache + photo files from git |
| `scripts/bangkok-citywalk/generate.py` | Create | Full pipeline: geocode → route → photos → GeoJSON |
| `content/bangkok-citywalk/_index.md` | Create | Hugo content stub |
| `static/bangkok-citywalk/walk.geojson` | Generated | Output of generate.py |
| `static/bangkok-citywalk/photos/*.jpg` | Generated | Cached Wikimedia photos |
| `layouts/bangkok-citywalk/list.html` | Create | Leaflet map: route + numbered markers + popups |
| `scripts/bangkok-citywalk/remotion/tsconfig.json` | Create | TypeScript config for Remotion |
| `scripts/bangkok-citywalk/remotion/src/types.ts` | Create | `WalkSlide`, `RouteSegment`, `WalkShowProps` |
| `scripts/bangkok-citywalk/remotion/src/index.tsx` | Create | Remotion entry point (re-exports Root) |
| `scripts/bangkok-citywalk/remotion/src/Root.tsx` | Create | Remotion `<Composition>` registration |
| `scripts/bangkok-citywalk/remotion/src/Intro.tsx` | Create | Title card: "Bangkok City Walk" |
| `scripts/bangkok-citywalk/remotion/src/Outro.tsx` | Create | Closing card |
| `scripts/bangkok-citywalk/remotion/src/MapView.tsx` | Create | MapLibre map: route + progressive highlight + camera |
| `scripts/bangkok-citywalk/remotion/src/SlideScene.tsx` | Create | POI card: Wikimedia photo + name + order badge |
| `scripts/bangkok-citywalk/remotion/src/SlideShow.tsx` | Create | Orchestrates Intro/POI slides/Outro + MapView |
| `scripts/bangkok-citywalk/render-walk.js` | Create | CLI: reads walk.geojson → renders MP4 via Remotion |
| `package.json` | Modify | Add `citywalk:render` and `citywalk:studio` scripts |

---

## Task 1: Scaffold + seed venues

**Files:**
- Create: `scripts/bangkok-citywalk/venues.csv`
- Create: `scripts/bangkok-citywalk/.gitignore`
- Create: `content/bangkok-citywalk/_index.md`
- Create: `static/bangkok-citywalk/.gitkeep`

**Interfaces:**
- Produces: `venues.csv` with columns `name,lat,lng` (lat/lng empty initially) consumed by `generate.py`

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p scripts/bangkok-citywalk/remotion/src
mkdir -p static/bangkok-citywalk/photos
touch static/bangkok-citywalk/.gitkeep
touch static/bangkok-citywalk/photos/.gitkeep
```

- [ ] **Step 2: Create `scripts/bangkok-citywalk/venues.csv`**

```csv
name,lat,lng
Grand Palace,,
Wat Pho,,
Wat Arun,,
Tha Tien Market,,
Pak Khlong Talat (Flower Market),,
Yaowarat Road (Chinatown),,
Wat Traimit (Temple of the Golden Buddha),,
Odeon Circle,,
Lhong 1919,,
ICONSIAM,,
```

- [ ] **Step 3: Create `scripts/bangkok-citywalk/.gitignore`**

```
.geocache.json
.routecache.json
.mediacache.json
```

Also add to `static/bangkok-citywalk/.gitignore` so generated photos and GeoJSON are not committed:

Create `static/bangkok-citywalk/.gitignore`:
```
photos/
walk.geojson
```

- [ ] **Step 4: Create `content/bangkok-citywalk/_index.md`**

```markdown
---
title: "Bangkok City Walk"
description: "A 10km walking route through Bangkok's iconic landmarks: Grand Palace, Wat Pho, Chinatown, and more."
type: "bangkok-citywalk"
---
```

- [ ] **Step 5: Commit**

```bash
git add scripts/bangkok-citywalk/ content/bangkok-citywalk/ static/bangkok-citywalk/
git commit -m "feat(bangkok-citywalk): scaffold directories and seed venues"
```

---

## Task 2: Data pipeline (`generate.py`)

**Files:**
- Create: `scripts/bangkok-citywalk/generate.py`

**Interfaces:**
- Consumes: `scripts/bangkok-citywalk/venues.csv` (columns: `name`, `lat`, `lng`)
- Produces:
  - `static/bangkok-citywalk/walk.geojson` — `FeatureCollection` with 10 `Point` features + 1 `LineString` feature
  - `static/bangkok-citywalk/photos/<slug>.jpg` — one JPEG per POI
  - `.geocache.json`, `.routecache.json`, `.mediacache.json` — incremental caches

**GeoJSON contract (consumed by Task 4 layout + Task 5–8 Remotion):**
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {"type": "Point", "coordinates": [100.4913, 13.7500]},
      "properties": {
        "name": "Grand Palace",
        "order": 1,
        "slug": "grand-palace",
        "photo": "/bangkok-citywalk/photos/grand-palace.jpg",
        "attribution": "© Foo / CC BY-SA 4.0"
      }
    },
    {
      "type": "Feature",
      "geometry": {"type": "LineString", "coordinates": [[100.4913, 13.7500], ...]},
      "properties": {
        "type": "route",
        "segment_breaks": [0, 45, 102, 150, 198, 247, 301, 355, 410, 467]
      }
    }
  ]
}
```

`segment_breaks[i]` is the index in `geometry.coordinates` where leg i starts (POI i → POI i+1). Last break is the final coordinate index (len - 1). Length = N (number of POIs), not N-1; the final entry is the terminator.

- [ ] **Step 1: Create `scripts/bangkok-citywalk/generate.py`**

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["tqdm"]
# ///
"""
Generate bangkok-citywalk GeoJSON and download Wikimedia photos.

Usage:
    uv run scripts/bangkok-citywalk/generate.py
    uv run scripts/bangkok-citywalk/generate.py --dry-run
"""

import argparse
import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent

VENUES_CSV = SCRIPT_DIR / "venues.csv"
GEOCACHE_PATH = SCRIPT_DIR / ".geocache.json"
ROUTECACHE_PATH = SCRIPT_DIR / ".routecache.json"
MEDIACACHE_PATH = SCRIPT_DIR / ".mediacache.json"

STATIC_DIR = REPO_ROOT / "static/bangkok-citywalk"
PHOTOS_DIR = STATIC_DIR / "photos"
GEOJSON_OUT = STATIC_DIR / "walk.geojson"

NOMINATIM_HEADERS = {"User-Agent": "maps.girard-davila.net/bangkok-citywalk"}
WIKIMEDIA_HEADERS = {"User-Agent": "maps.girard-davila.net/bangkok-citywalk (girard.davila@gmail.com)"}
OSRM_BASE = "http://router.project-osrm.org/route/v1/foot"


# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower()
    for src, dst in [("é","e"),("è","e"),("ê","e"),("à","a"),("â","a"),("ù","u"),("û","u"),("î","i"),("ô","o"),("ç","c")]:
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


# ── Phase 1: Geocode ──────────────────────────────────────────────────────────

def geocode_nominatim(name: str) -> tuple[float, float] | None:
    params = urllib.parse.urlencode({
        "q": f"{name} Bangkok Thailand",
        "format": "json",
        "limit": 1,
        "countrycodes": "th",
        "accept-language": "en",
    })
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    try:
        req = urllib.request.Request(url, headers=NOMINATIM_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            results = json.loads(r.read())
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        print(f"  ⚠ Nominatim error for '{name}': {e}", file=sys.stderr)
    return None


def geocode_venues(venues: list[dict], dry_run: bool) -> list[dict]:
    """Fill in lat/lng for venues missing coordinates. Mutates venues in place."""
    cache = load_json(GEOCACHE_PATH)
    updated = False

    for v in tqdm(venues, desc="Geocoding", unit="venue"):
        if v["lat"] and v["lng"]:
            continue
        if v["name"] in cache:
            v["lat"], v["lng"] = cache[v["name"]]["lat"], cache[v["name"]]["lng"]
            continue
        if dry_run:
            tqdm.write(f"  [dry-run] would geocode: {v['name']}")
            continue
        result = geocode_nominatim(v["name"])
        if result:
            lat, lng = result
            v["lat"], v["lng"] = lat, lng
            cache[v["name"]] = {"lat": lat, "lng": lng}
            tqdm.write(f"  → {v['name']}: {lat:.5f}, {lng:.5f}")
            updated = True
        else:
            print(f"  ✗ Geocode FAILED: {v['name']}", file=sys.stderr)
        time.sleep(1.1)

    if updated:
        save_json(GEOCACHE_PATH, cache)

    # Write back to venues.csv with filled coordinates
    if updated and not dry_run:
        fieldnames = ["name", "lat", "lng"]
        with VENUES_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(venues)
        print("  ✓ venues.csv updated with coordinates")

    return venues


# ── Phase 2: OSRM walking route ───────────────────────────────────────────────

def fetch_osrm_leg(lng1: float, lat1: float, lng2: float, lat2: float) -> list[list[float]] | None:
    """Returns list of [lng, lat] coordinates for one walking leg, or None on failure."""
    url = f"{OSRM_BASE}/{lng1},{lat1};{lng2},{lat2}?overview=full&geometries=geojson"
    try:
        req = urllib.request.Request(url, headers=NOMINATIM_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return data["routes"][0]["geometry"]["coordinates"]
    except Exception as e:
        print(f"  ⚠ OSRM error ({lng1},{lat1}) → ({lng2},{lat2}): {e}", file=sys.stderr)
        return None


def build_route(venues: list[dict], dry_run: bool) -> tuple[list[list[float]], list[int]]:
    """
    Fetch walking route between consecutive venues.
    Returns (all_coords, segment_breaks) where segment_breaks[i] is the index
    in all_coords where leg i starts. len(segment_breaks) == len(venues).
    """
    cache = load_json(ROUTECACHE_PATH)
    updated = False

    all_coords: list[list[float]] = []
    segment_breaks: list[int] = []

    for i in tqdm(range(len(venues) - 1), desc="Routing", unit="leg"):
        v1, v2 = venues[i], venues[i + 1]
        if not (v1["lat"] and v1["lng"] and v2["lat"] and v2["lng"]):
            print(f"  ⚠ Skipping leg {i}: missing coordinates", file=sys.stderr)
            continue

        cache_key = f"{v1['name']}→{v2['name']}"
        if cache_key in cache:
            leg_coords = cache[cache_key]
        elif dry_run:
            tqdm.write(f"  [dry-run] would fetch route: {cache_key}")
            leg_coords = []
        else:
            leg_coords = fetch_osrm_leg(
                float(v1["lng"]), float(v1["lat"]),
                float(v2["lng"]), float(v2["lat"]),
            )
            if leg_coords is None:
                leg_coords = []
            else:
                cache[cache_key] = leg_coords
                updated = True
            time.sleep(0.5)

        segment_breaks.append(len(all_coords))
        # Avoid duplicating the junction point between legs
        if all_coords and leg_coords:
            leg_coords = leg_coords[1:]
        all_coords.extend(leg_coords)

    # Final segment_break terminator
    segment_breaks.append(len(all_coords) - 1 if all_coords else 0)

    if updated:
        save_json(ROUTECACHE_PATH, cache)

    return all_coords, segment_breaks


# ── Phase 3: Wikimedia photos ─────────────────────────────────────────────────

def fetch_wikimedia_photo(name: str) -> tuple[str, str] | None:
    """
    Returns (thumb_url, attribution) for the best Wikimedia Commons result,
    or None if nothing found. Attribution format: '© Artist / License'.
    """
    params = urllib.parse.urlencode({
        "action": "query",
        "generator": "search",
        "gsnamespace": 6,
        "gsearch": f"{name} Bangkok",
        "gsrlimit": 5,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 1080,
        "format": "json",
    })
    url = f"https://commons.wikimedia.org/w/api.php?{params}"
    try:
        req = urllib.request.Request(url, headers=WIKIMEDIA_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            ii = page.get("imageinfo", [{}])[0]
            thumb = ii.get("thumburl", "")
            if not thumb:
                continue
            meta = ii.get("extmetadata", {})
            artist = meta.get("Artist", {}).get("value", "")
            # Strip HTML tags from artist field
            artist = re.sub(r"<[^>]+>", "", artist).strip()
            license_name = meta.get("LicenseShortName", {}).get("value", "")
            attribution = f"© {artist} / {license_name}" if artist else license_name
            return thumb, attribution
    except Exception as e:
        print(f"  ⚠ Wikimedia error for '{name}': {e}", file=sys.stderr)
    return None


def fetch_photos(venues: list[dict], dry_run: bool) -> None:
    """Download one Wikimedia photo per venue into PHOTOS_DIR. Updates .mediacache.json."""
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    cache = load_json(MEDIACACHE_PATH)
    updated = False

    for v in tqdm(venues, desc="Wikimedia photos", unit="venue"):
        name = v["name"]
        slug = slugify(name)
        dest = PHOTOS_DIR / f"{slug}.jpg"

        if name in cache and dest.exists():
            continue

        if dry_run:
            tqdm.write(f"  [dry-run] would fetch photo: {name}")
            continue

        result = fetch_wikimedia_photo(name)
        if result is None:
            print(f"  ⚠ No photo found for: {name}", file=sys.stderr)
            cache[name] = {"thumb_url": "", "attribution": ""}
            updated = True
            continue

        thumb_url, attribution = result
        try:
            urllib.request.urlretrieve(thumb_url, str(dest))
            cache[name] = {"thumb_url": thumb_url, "attribution": attribution}
            updated = True
            tqdm.write(f"  → {name}: {dest.name} ({attribution[:60]})")
        except Exception as e:
            print(f"  ⚠ Download failed for '{name}': {e}", file=sys.stderr)

        time.sleep(0.5)

    if updated:
        save_json(MEDIACACHE_PATH, cache)


# ── Phase 4: Write GeoJSON ────────────────────────────────────────────────────

def write_geojson(venues: list[dict], route_coords: list[list[float]], segment_breaks: list[int]) -> None:
    mediacache = load_json(MEDIACACHE_PATH)
    features = []

    for i, v in enumerate(venues):
        if not (v["lat"] and v["lng"]):
            continue
        slug = slugify(v["name"])
        attribution = mediacache.get(v["name"], {}).get("attribution", "")
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(v["lng"]), float(v["lat"])]},
            "properties": {
                "name": v["name"],
                "order": i + 1,
                "slug": slug,
                "photo": f"/bangkok-citywalk/photos/{slug}.jpg",
                "attribution": attribution,
            },
        })

    if route_coords:
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": route_coords},
            "properties": {
                "type": "route",
                "segment_breaks": segment_breaks,
            },
        })

    fc = {"type": "FeatureCollection", "features": features}
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    GEOJSON_OUT.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    print(f"✓ {GEOJSON_OUT} — {len(features)} features")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Skip network requests and file writes")
    args = parser.parse_args()

    # Load venues
    venues = []
    with VENUES_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            venues.append(row)
    print(f"Loaded {len(venues)} venues")

    # Phase 1: Geocode
    print("\nPhase 1: Geocoding…")
    venues = geocode_venues(venues, args.dry_run)

    # Phase 2: Route
    print("\nPhase 2: Walking route (OSRM)…")
    route_coords, segment_breaks = build_route(venues, args.dry_run)
    if route_coords:
        total_m = sum(
            ((route_coords[i+1][0]-route_coords[i][0])**2 + (route_coords[i+1][1]-route_coords[i][1])**2)**0.5 * 111_000
            for i in range(len(route_coords)-1)
        )
        print(f"  {len(route_coords)} route points, ~{total_m/1000:.1f} km")

    # Phase 3: Photos
    print("\nPhase 3: Wikimedia photos…")
    fetch_photos(venues, args.dry_run)

    # Phase 4: Write GeoJSON
    if not args.dry_run:
        print("\nPhase 4: Writing GeoJSON…")
        write_geojson(venues, route_coords, segment_breaks)

    print("\nDone.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run with `--dry-run` to verify the script loads**

```bash
uv run scripts/bangkok-citywalk/generate.py --dry-run
```

Expected output (no network calls):
```
Loaded 10 venues
Phase 1: Geocoding…
Geocoding:   0%|...
  [dry-run] would geocode: Grand Palace
...
Phase 3: Wikimedia photos…
  [dry-run] would fetch photo: Grand Palace
...
Done.
```

If `ModuleNotFoundError: tqdm` appears — wait, `tqdm` is in the inline deps; `uv run` installs it automatically.

- [ ] **Step 3: Run for real (network calls, ~3 min)**

```bash
uv run scripts/bangkok-citywalk/generate.py
```

Expected: 10 photos downloaded to `static/bangkok-citywalk/photos/`, `walk.geojson` created.

- [ ] **Step 4: Validate the GeoJSON**

```bash
python3 -c "
import json
data = json.load(open('static/bangkok-citywalk/walk.geojson'))
pois = [f for f in data['features'] if f['geometry']['type'] == 'Point']
route = [f for f in data['features'] if f['geometry']['type'] == 'LineString']
assert len(pois) == 10, f'Expected 10 POIs, got {len(pois)}'
assert len(route) == 1, f'Expected 1 route, got {len(route)}'
assert 'segment_breaks' in route[0]['properties']
for p in pois:
    pr = p['properties']
    assert pr['order'] >= 1
    assert pr['slug']
    assert pr['photo'].startswith('/bangkok-citywalk/photos/')
print('✓ GeoJSON valid:', len(pois), 'POIs,', len(route[0]['geometry']['coordinates']), 'route points')
"
```

- [ ] **Step 5: Commit**

```bash
git add scripts/bangkok-citywalk/generate.py
git commit -m "feat(bangkok-citywalk): add generate.py pipeline (geocode + OSRM + Wikimedia)"
```

---

## Task 3: Hugo map layout

**Files:**
- Create: `layouts/bangkok-citywalk/list.html`

**Interfaces:**
- Consumes: `static/bangkok-citywalk/walk.geojson` (Point features + LineString route)
- Consumes: partials `map-poi-styles.html`, `map-geolocation.html` (already in `layouts/partials/`)
- Produces: interactive Leaflet map page at `/bangkok-citywalk/`

- [ ] **Step 1: Create `layouts/bangkok-citywalk/list.html`**

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
      <h1 style="font-size:1.2rem;font-weight:700;margin:0 0 0.25rem;">Bangkok City Walk</h1>
      <p style="font-size:0.8rem;margin:0;">10km · 10 landmarks · Grand Palace → ICONSIAM</p>
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
  const GEOJSON_URL = '{{ "/bangkok-citywalk/walk.geojson" | relURL }}';
  const ROUTE_COLOR = '#FF6B35';
  const TILE_URL = 'https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png?api_key={{ os.Getenv "HUGO_STADIA_API_KEY" }}';

  const map = L.map('map', { maxZoom: 18 }).setView([13.736717, 100.523186], 13);
  L.tileLayer(TILE_URL, {
    attribution: '© <a href="https://stadiamaps.com/">Stadia Maps</a>, © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 18,
  }).addTo(map);

  fetch(GEOJSON_URL)
    .then(r => r.json())
    .then(data => {
      const pois = data.features
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

      // Draw numbered markers
      pois.forEach(f => {
        const [lng, lat] = f.geometry.coordinates;
        const p = f.properties;
        const icon = L.divIcon({
          className: '',
          html: `<div style="width:28px;height:28px;border-radius:50%;background:#FF6B35;color:#fff;font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,0.6);border:2px solid #fff;">${p.order}</div>`,
          iconSize: [28, 28],
          iconAnchor: [14, 14],
        });
        const marker = L.marker([lat, lng], { icon });
        marker.addTo(map);
        marker.on('click', () => focusPOI(f));
      });

      // Render sidebar list
      const list = document.getElementById('poi-list');
      pois.forEach(f => {
        const p = f.properties;
        const card = document.createElement('div');
        card.className = 'poi-card';
        card.id = `card-${p.order}`;
        card.style.cssText = 'border-radius:8px;padding:0.75rem;margin-bottom:0.4rem;';
        card.innerHTML = `
          ${p.photo ? `<img class="poi-photo" src="${p.photo}" alt="${p.name}" loading="lazy">` : ''}
          <div style="display:flex;align-items:center;">
            <span class="walk-badge">${p.order}</span>
            <h3>${p.name}</h3>
          </div>
          ${p.attribution ? `<div class="poi-meta">${p.attribution}</div>` : ''}
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

- [ ] **Step 2: Verify the page builds**

```bash
hugo --minify 2>&1 | grep -E "(ERROR|WARN|bangkok)"
```

Expected: no ERROR lines. May see a warning about missing `walk.geojson` if `static/bangkok-citywalk/walk.geojson` doesn't exist yet — that's OK, it's generated separately.

- [ ] **Step 3: Start Hugo dev server and open the page**

```bash
hugo server -D &
# open http://localhost:1313/bangkok-citywalk/
```

Verify: map renders, route polyline visible in orange, 10 numbered markers, clicking a marker scrolls the sidebar.

- [ ] **Step 4: Commit**

```bash
git add layouts/bangkok-citywalk/list.html
git commit -m "feat(bangkok-citywalk): add Leaflet map layout with route + numbered markers"
```

---

## Task 4: Remotion types, Root, Intro, Outro

**Files:**
- Create: `scripts/bangkok-citywalk/remotion/tsconfig.json`
- Create: `scripts/bangkok-citywalk/remotion/src/types.ts`
- Create: `scripts/bangkok-citywalk/remotion/src/index.tsx`
- Create: `scripts/bangkok-citywalk/remotion/src/Root.tsx`
- Create: `scripts/bangkok-citywalk/remotion/src/Intro.tsx`
- Create: `scripts/bangkok-citywalk/remotion/src/Outro.tsx`

**Interfaces:**
- Produces:
  - `WalkSlide` — consumed by `MapView`, `SlideScene`, `SlideShow`
  - `RouteSegment` — consumed by `MapView`, `SlideShow`, `render-walk.js`
  - `WalkShowProps` — consumed by `Root`, `SlideShow`, `render-walk.js`

- [ ] **Step 1: Create `scripts/bangkok-citywalk/remotion/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM"],
    "module": "ESNext",
    "moduleResolution": "node",
    "jsx": "react-jsx",
    "strict": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "skipLibCheck": true
  },
  "include": ["src"]
}
```

- [ ] **Step 2: Create `scripts/bangkok-citywalk/remotion/src/types.ts`**

```typescript
export interface WalkSlide {
  name: string;
  order: number;           // 1-based POI index
  photoUrl: string;        // http://localhost:<port>/photos/<slug>.jpg
  attribution: string;
  coordinates: [number, number]; // [lng, lat]
}

export interface RouteSegment {
  coords: [number, number][]; // [lng, lat] pairs for one POI-to-POI leg
}

export interface WalkShowProps {
  slides: WalkSlide[];
  route: RouteSegment[];     // length = slides.length - 1 (one per consecutive POI pair)
  introDur: number;          // seconds, default 3
  outroDur: number;          // seconds, default 5
  slideDur: number;          // seconds per POI slide, default 10
  maptilerKey: string;
}
```

- [ ] **Step 3: Create `scripts/bangkok-citywalk/remotion/src/Intro.tsx`**

```tsx
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {WalkSlide} from './types';

const MONO: React.CSSProperties = {fontFamily: "'Courier New', Courier, monospace"};

interface Props {
  slides: WalkSlide[];
}

export const Intro: React.FC<Props> = ({slides}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const opacity = interpolate(frame, [0, Math.round(0.5 * fps)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        background: '#0a0a14',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        padding: '0 48px',
        opacity,
      }}
    >
      <div style={{...MONO, fontSize: 16, color: '#FF6B35', textTransform: 'uppercase', letterSpacing: '0.15em', marginBottom: 16}}>
        City Walk
      </div>
      <div style={{...MONO, fontSize: 52, fontWeight: 700, color: '#fff', letterSpacing: '0.02em', marginBottom: 12}}>
        BANGKOK
      </div>
      <div style={{...MONO, fontSize: 18, color: '#666', marginBottom: 32}}>
        {slides.length} landmarks · ~10 km
      </div>
      <div style={{width: 48, height: 3, background: '#FF6B35', borderRadius: 2}} />
    </AbsoluteFill>
  );
};
```

- [ ] **Step 4: Create `scripts/bangkok-citywalk/remotion/src/Outro.tsx`**

```tsx
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

const MONO: React.CSSProperties = {fontFamily: "'Courier New', Courier, monospace"};

export const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();

  const opacity = interpolate(
    frame,
    [0, Math.round(0.5 * fps), durationInFrames - Math.round(fps), durationInFrames],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  return (
    <AbsoluteFill
      style={{
        background: '#0a0a14',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        opacity,
      }}
    >
      <div style={{...MONO, fontSize: 52, fontWeight: 700, color: '#fff', letterSpacing: '0.02em', marginBottom: 16}}>
        BANGKOK
      </div>
      <div style={{width: 48, height: 3, background: '#FF6B35', borderRadius: 2, marginBottom: 16}} />
      <div style={{...MONO, fontSize: 16, color: '#555', letterSpacing: '0.08em'}}>
        maps.girard-davila.net
      </div>
    </AbsoluteFill>
  );
};
```

- [ ] **Step 5: Create `scripts/bangkok-citywalk/remotion/src/Root.tsx`**

```tsx
import {Composition} from 'remotion';
import {SlideShow} from './SlideShow';
import {WalkShowProps} from './types';

const DEFAULT_PROPS: WalkShowProps = {
  slides: [],
  route: [],
  introDur: 3,
  outroDur: 5,
  slideDur: 10,
  maptilerKey: '',
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="BangkokCityWalk"
      component={SlideShow as unknown as React.ComponentType<Record<string, unknown>>}
      durationInFrames={30 * 30}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={DEFAULT_PROPS}
      calculateMetadata={async ({props}) => {
        const {slides, introDur, outroDur, slideDur} = props as unknown as WalkShowProps;
        const fps = 30;
        const durationInFrames = Math.round(fps * (introDur + slides.length * slideDur + outroDur));
        return {durationInFrames, props};
      }}
    />
  );
};
```

- [ ] **Step 6: Create `scripts/bangkok-citywalk/remotion/src/index.tsx`**

```tsx
import {registerRoot} from 'remotion';
import {RemotionRoot} from './Root';
registerRoot(RemotionRoot);
```

- [ ] **Step 7: Commit**

```bash
git add scripts/bangkok-citywalk/remotion/
git commit -m "feat(bangkok-citywalk): add Remotion scaffold (types, Root, Intro, Outro)"
```

---

## Task 5: Remotion `MapView.tsx`

**Files:**
- Create: `scripts/bangkok-citywalk/remotion/src/MapView.tsx`

**Interfaces:**
- Consumes:
  - `WalkSlide[]` — for POI marker positions and active index
  - `RouteSegment[]` — for drawing the route (one entry per POI-to-POI leg)
  - `introDur: number`, `slideDur: number`, `maptilerKey: string`
- Produces: MapLibre GL map component (1080×960px) with:
  - Route drawn as two layers: walked (highlighted `#FF6B35`) and upcoming (dim `#555`)
  - Numbered POI markers; active marker enlarged
  - Camera: overview during intro, then fly to each POI per slide

- [ ] **Step 1: Create `scripts/bangkok-citywalk/remotion/src/MapView.tsx`**

```tsx
import {useEffect, useRef, useState} from 'react';
import {
  continueRender,
  delayRender,
  Easing,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import {RouteSegment, WalkSlide} from './types';

export const MAP_HEIGHT = 960;
const VENUE_ZOOM = 15;
const OVERVIEW_ZOOM = 12;
const TRANSITION_ZOOM = 13;
const PADDING_BOTTOM = 480;
const MAP_PADDING = {top: 0, right: 0, bottom: PADDING_BOTTOM, left: 0};

// Bangkok city centre fallback
const BANGKOK_CENTER: [number, number] = [100.5018, 13.7563];

interface Props {
  slides: WalkSlide[];
  route: RouteSegment[];
  introDur: number;
  slideDur: number;
  maptilerKey: string;
}

function centroid(coords: [number, number][]): [number, number] {
  const lng = coords.reduce((s, c) => s + c[0], 0) / coords.length;
  const lat = coords.reduce((s, c) => s + c[1], 0) / coords.length;
  return [lng, lat];
}

function buildMarkerGeojson(
  slides: WalkSlide[],
  activeIdx: number,
): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: slides.map((s, i) => ({
      type: 'Feature',
      geometry: {type: 'Point', coordinates: s.coordinates},
      properties: {index: i, active: i === activeIdx, order: s.order},
    })),
  };
}

function buildRouteGeojson(segments: RouteSegment[], upTo: number): GeoJSON.FeatureCollection {
  // Stitch legs 0..upTo-1 into one walked MultiLineString
  // Remaining legs as another (upcoming)
  const walked = segments.slice(0, Math.max(0, upTo)).map(s => s.coords);
  const upcoming = segments.slice(Math.max(0, upTo)).map(s => s.coords);
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: {type: 'MultiLineString', coordinates: walked},
        properties: {role: 'walked'},
      },
      {
        type: 'Feature',
        geometry: {type: 'MultiLineString', coordinates: upcoming},
        properties: {role: 'upcoming'},
      },
    ],
  };
}

export const MapView: React.FC<Props> = ({slides, route, introDur, slideDur, maptilerKey}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const [map, setMap] = useState<maplibregl.Map | null>(null);
  const [loadHandle] = useState(() => delayRender('Loading MapLibre map'));

  const coords = slides.map(s => s.coordinates);
  const N = slides.length;
  const overviewCenter = N > 0 ? centroid(coords) : BANGKOK_CENTER;

  useEffect(() => {
    if (!containerRef.current || N === 0) {
      continueRender(loadHandle);
      return;
    }

    const mapInstance = new maplibregl.Map({
      container: containerRef.current,
      style: `https://api.maptiler.com/maps/toner-v2/style.json?key=${maptilerKey}`,
      center: overviewCenter,
      zoom: OVERVIEW_ZOOM,
      interactive: false,
      attributionControl: false,
      fadeDuration: 0,
      canvasContextAttributes: {preserveDrawingBuffer: true},
    } as maplibregl.MapOptions);

    mapInstance.once('idle', () => {
      // Route source (walked + upcoming MultiLineStrings)
      mapInstance.addSource('route', {
        type: 'geojson',
        data: buildRouteGeojson(route, 0),
      });
      mapInstance.addLayer({
        id: 'route-upcoming',
        type: 'line',
        source: 'route',
        filter: ['==', ['get', 'role'], 'upcoming'],
        paint: {'line-color': '#444', 'line-width': 2, 'line-dasharray': [3, 2]},
      });
      mapInstance.addLayer({
        id: 'route-walked',
        type: 'line',
        source: 'route',
        filter: ['==', ['get', 'role'], 'walked'],
        paint: {'line-color': '#FF6B35', 'line-width': 3},
      });

      // POI markers source
      mapInstance.addSource('markers', {
        type: 'geojson',
        data: buildMarkerGeojson(slides, -1),
      });
      // Inactive markers: small circle
      mapInstance.addLayer({
        id: 'markers-base',
        type: 'circle',
        source: 'markers',
        filter: ['!=', ['get', 'active'], true],
        paint: {
          'circle-radius': 6,
          'circle-color': '#FF6B35',
          'circle-stroke-width': 2,
          'circle-stroke-color': '#fff',
          'circle-opacity': 0.7,
        },
      });
      // Active marker: large circle
      mapInstance.addLayer({
        id: 'markers-active',
        type: 'circle',
        source: 'markers',
        filter: ['==', ['get', 'active'], true],
        paint: {
          'circle-radius': 20,
          'circle-color': '#FF6B35',
          'circle-stroke-width': 3,
          'circle-stroke-color': '#fff',
        },
      });
      // Order number on active marker
      mapInstance.addLayer({
        id: 'markers-label',
        type: 'symbol',
        source: 'markers',
        filter: ['==', ['get', 'active'], true],
        layout: {
          'text-field': ['to-string', ['get', 'order']],
          'text-size': 14,
          'text-anchor': 'center',
          'text-allow-overlap': true,
          'text-font': ['Noto Sans Bold'],
        },
        paint: {'text-color': '#fff'},
      });

      mapInstance.setPadding(MAP_PADDING);
      setMap(mapInstance);
      continueRender(loadHandle);
    });

    return () => mapInstance.remove();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!map || N === 0) return;

    const handle = delayRender('Moving camera');
    const TRANSITION_FRAMES = fps;
    const introFrames = Math.round(introDur * fps);
    const slideDurFrames = Math.round(slideDur * fps);

    let lng: number;
    let lat: number;
    let zoom: number;
    let activeIdx: number;
    let routeUpTo: number;

    if (frame < introFrames) {
      [lng, lat] = overviewCenter;
      zoom = OVERVIEW_ZOOM;
      activeIdx = -1;
      routeUpTo = 0;
    } else {
      const idx = Math.min(Math.floor((frame - introFrames) / slideDurFrames), N - 1);
      activeIdx = idx;
      routeUpTo = idx; // legs 0..idx-1 are "walked"
      const localFrame = frame - introFrames - idx * slideDurFrames;
      const rawT = Math.min(localFrame / TRANSITION_FRAMES, 1);
      const easedT = Easing.inOut(Easing.cubic)(rawT);

      if (idx === 0) {
        lng = overviewCenter[0] + (coords[0][0] - overviewCenter[0]) * easedT;
        lat = overviewCenter[1] + (coords[0][1] - overviewCenter[1]) * easedT;
        zoom = OVERVIEW_ZOOM + (VENUE_ZOOM - OVERVIEW_ZOOM) * easedT;
      } else {
        const prev = idx - 1;
        lng = coords[prev][0] + (coords[idx][0] - coords[prev][0]) * easedT;
        lat = coords[prev][1] + (coords[idx][1] - coords[prev][1]) * easedT;
        zoom = VENUE_ZOOM - Math.sin(rawT * Math.PI) * (VENUE_ZOOM - TRANSITION_ZOOM);
      }
    }

    map.jumpTo({center: [lng, lat], zoom, padding: MAP_PADDING});

    const markerSource = map.getSource('markers') as maplibregl.GeoJSONSource;
    markerSource.setData(buildMarkerGeojson(slides, activeIdx));

    const routeSource = map.getSource('route') as maplibregl.GeoJSONSource;
    routeSource.setData(buildRouteGeojson(route, routeUpTo));

    const onIdle = () => continueRender(handle);
    map.once('idle', onIdle);
    map.triggerRepaint();

    return () => {
      map.off('idle', onIdle);
      continueRender(handle);
    };
  }, [frame, map]);

  return <div ref={containerRef} style={{width: 1080, height: MAP_HEIGHT}} />;
};
```

- [ ] **Step 2: TypeScript check (catches import/type errors before runtime)**

```bash
cd scripts/bangkok-citywalk/remotion && npx tsc --noEmit
```

Expected: no errors. If `maplibre-gl` types are missing, they come from `@types/maplibre-gl` — but maplibre-gl 4.x ships its own types so this should pass.

- [ ] **Step 3: Commit**

```bash
cd ../../../
git add scripts/bangkok-citywalk/remotion/src/MapView.tsx
git commit -m "feat(bangkok-citywalk): add Remotion MapView with route + progressive highlight"
```

---

## Task 6: Remotion `SlideScene.tsx`

**Files:**
- Create: `scripts/bangkok-citywalk/remotion/src/SlideScene.tsx`

**Interfaces:**
- Consumes: `WalkSlide` — `name`, `order`, `photoUrl`, `attribution`, `slideDur`
- Produces: Bottom-half POI card (960px height) with photo, order badge, name, attribution

- [ ] **Step 1: Create `scripts/bangkok-citywalk/remotion/src/SlideScene.tsx`**

```tsx
import {AbsoluteFill, Img, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {WalkSlide} from './types';

export const PHOTO_HEIGHT = 600;
export const CARD_HEIGHT = 360;

const MONO: React.CSSProperties = {fontFamily: "'Courier New', Courier, monospace"};

interface Props {
  slide: WalkSlide;
  slideDur: number;
}

export const SlideScene: React.FC<Props> = ({slide, slideDur}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const slideDurFrames = Math.round(slideDur * fps);
  const fadeInFrames = Math.round(0.4 * fps);
  const fadeOutFrames = Math.round(1.5 * fps);

  const opacity = interpolate(
    frame,
    [0, fadeInFrames, slideDurFrames - fadeOutFrames, slideDurFrames],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  return (
    <AbsoluteFill style={{opacity}}>
      {/* Photo */}
      <div style={{position: 'absolute', top: 0, left: 0, right: 0, height: PHOTO_HEIGHT, background: '#111', overflow: 'hidden'}}>
        {slide.photoUrl ? (
          <Img
            src={slide.photoUrl}
            style={{width: '100%', height: '100%', objectFit: 'cover'}}
          />
        ) : (
          <div style={{width: '100%', height: '100%', background: '#1a1a2e', display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
            <span style={{...MONO, color: '#333', fontSize: 48}}>🗺</span>
          </div>
        )}
        {/* Order badge overlay */}
        <div
          style={{
            position: 'absolute', top: 20, left: 20,
            width: 44, height: 44, borderRadius: '50%',
            background: '#FF6B35', color: '#fff',
            fontSize: 20, fontWeight: 700,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: "'Courier New', Courier, monospace",
            boxShadow: '0 2px 12px rgba(0,0,0,0.6)',
          }}
        >
          {slide.order}
        </div>
      </div>

      {/* Info card */}
      <div
        style={{
          position: 'absolute',
          top: PHOTO_HEIGHT,
          left: 0, right: 0,
          height: CARD_HEIGHT,
          background: '#0a0a14',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          padding: '0 48px',
        }}
      >
        <div style={{width: 32, height: 3, background: '#FF6B35', borderRadius: 2, marginBottom: 20}} />
        <div
          style={{
            ...MONO,
            fontSize: 32,
            fontWeight: 700,
            color: '#fff',
            lineHeight: 1.2,
            marginBottom: 16,
          }}
        >
          {slide.name}
        </div>
        {slide.attribution && (
          <div style={{...MONO, fontSize: 13, color: '#444', lineHeight: 1.4}}>
            {slide.attribution}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};
```

- [ ] **Step 2: TypeScript check**

```bash
cd scripts/bangkok-citywalk/remotion && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd ../../../
git add scripts/bangkok-citywalk/remotion/src/SlideScene.tsx
git commit -m "feat(bangkok-citywalk): add Remotion SlideScene (Wikimedia photo + POI card)"
```

---

## Task 7: Remotion `SlideShow.tsx`

**Files:**
- Create: `scripts/bangkok-citywalk/remotion/src/SlideShow.tsx`

**Interfaces:**
- Consumes: `WalkShowProps` (all props)
- Consumes: `MapView` (top 960px), `Intro`/`SlideScene`/`Outro` (bottom 960px)
- Produces: Full 1080×1920 composition wired together

- [ ] **Step 1: Create `scripts/bangkok-citywalk/remotion/src/SlideShow.tsx`**

```tsx
import {AbsoluteFill, Sequence} from 'remotion';
import {Intro} from './Intro';
import {MAP_HEIGHT, MapView} from './MapView';
import {Outro} from './Outro';
import {SlideScene} from './SlideScene';
import {WalkShowProps} from './types';

const BOTTOM_HEIGHT = 960;

export const SlideShow: React.FC<WalkShowProps> = ({slides, route, introDur, outroDur, slideDur, maptilerKey}) => {
  const fps = 30;
  const introFrames = Math.round(introDur * fps);
  const slideDurFrames = Math.round(slideDur * fps);
  const outroFrames = Math.round(outroDur * fps);
  const outroFrom = introFrames + slides.length * slideDurFrames;

  return (
    <AbsoluteFill style={{background: '#0a0a14'}}>
      {/* Top half: continuous MapLibre map */}
      <div style={{position: 'absolute', top: 0, left: 0, right: 0, height: MAP_HEIGHT, overflow: 'hidden'}}>
        <MapView
          slides={slides}
          route={route}
          introDur={introDur}
          slideDur={slideDur}
          maptilerKey={maptilerKey}
        />
      </div>

      {/* Bottom half: intro / POI slides / outro */}
      <div style={{position: 'absolute', top: MAP_HEIGHT, left: 0, right: 0, height: BOTTOM_HEIGHT}}>
        {introFrames > 0 && (
          <Sequence from={0} durationInFrames={introFrames}>
            <Intro slides={slides} />
          </Sequence>
        )}

        {slides.map((slide, i) => (
          <Sequence
            key={`${slide.slug ?? slide.order}-${i}`}
            from={introFrames + i * slideDurFrames}
            durationInFrames={slideDurFrames}
          >
            <SlideScene slide={slide} slideDur={slideDur} />
          </Sequence>
        ))}

        {outroFrames > 0 && (
          <Sequence from={outroFrom} durationInFrames={outroFrames}>
            <Outro />
          </Sequence>
        )}
      </div>
    </AbsoluteFill>
  );
};
```

Note: `slide.slug` is not in `WalkSlide` — use `slide.order` as the key. Update the key expression:
```tsx
key={`poi-${slide.order}`}
```

- [ ] **Step 2: TypeScript check (whole composition)**

```bash
cd scripts/bangkok-citywalk/remotion && npx tsc --noEmit
```

Expected: no errors. If `slug` is flagged, the key is already fixed above — use `slide.order`.

- [ ] **Step 3: Preview in Remotion Studio**

Temporarily add a `citywalk:studio` script or run directly:

```bash
node -e "
const {execSync} = require('child_process');
execSync('npx remotion studio scripts/bangkok-citywalk/remotion/src/index.tsx', {stdio:'inherit'});
"
```

Or after adding the npm script in Task 8: `npm run citywalk:studio`. Open the studio, verify composition `BangkokCityWalk` appears with the default empty props.

- [ ] **Step 4: Commit**

```bash
cd ../../../
git add scripts/bangkok-citywalk/remotion/src/SlideShow.tsx
git commit -m "feat(bangkok-citywalk): add Remotion SlideShow composition"
```

---

## Task 8: `render-walk.js` + npm scripts

**Files:**
- Create: `scripts/bangkok-citywalk/render-walk.js`
- Modify: `package.json`

**Interfaces:**
- Consumes: `static/bangkok-citywalk/walk.geojson` (from `generate.py`)
- Consumes: `static/bangkok-citywalk/photos/*.jpg` (served over local HTTP)
- Consumes: `MAPTILER_API_KEY` from `.env`
- Produces: `static/bangkok-citywalk/bangkok-citywalk-<hash>.mp4`

- [ ] **Step 1: Create `scripts/bangkok-citywalk/render-walk.js`**

```javascript
#!/usr/bin/env node
// Renders the Bangkok city walk video via Remotion.
//
// Usage:
//   node scripts/bangkok-citywalk/render-walk.js [output.mp4]
//
// Options:
//   --intro-dur <s>   Intro duration in seconds (default: 3)
//   --outro-dur <s>   Outro duration in seconds (default: 5)
//   --slide-dur <s>   Per-POI duration in seconds (default: 10)

const {bundle}      = require('@remotion/bundler');
const {renderMedia, selectComposition} = require('@remotion/renderer');
const fs            = require('fs');
const http          = require('http');
const path          = require('path');
const crypto        = require('crypto');

const ROOT        = path.resolve(__dirname, '../..');
const GEOJSON     = path.join(ROOT, 'static/bangkok-citywalk/walk.geojson');
const PHOTOS_DIR  = path.join(ROOT, 'static/bangkok-citywalk/photos');
const OUTPUT_DIR  = path.join(ROOT, 'static/bangkok-citywalk');
const ENTRY_POINT = path.join(__dirname, 'remotion/src/index.tsx');

// ── CLI parsing ───────────────────────────────────────────────────────────────
const args = process.argv.slice(2);

function getFlag(flag) {
  const i = args.indexOf(flag);
  return i !== -1 ? args[i + 1] : null;
}

const CLI_INTRO_DUR = getFlag('--intro-dur');
const CLI_OUTRO_DUR = getFlag('--outro-dur');
const CLI_SLIDE_DUR = getFlag('--slide-dur');
const CLI_OUTPUT    = args.find(a => !a.startsWith('--') && a.endsWith('.mp4')) || null;

const INTRO_DUR = parseFloat(CLI_INTRO_DUR ?? 3);
const OUTRO_DUR = parseFloat(CLI_OUTRO_DUR ?? 5);
const SLIDE_DUR = parseFloat(CLI_SLIDE_DUR ?? 10);

const HASH       = crypto.randomBytes(4).toString('hex');
const OUTPUT_MP4 = CLI_OUTPUT || path.join(OUTPUT_DIR, `bangkok-citywalk-${HASH}.mp4`);

// ── Env ───────────────────────────────────────────────────────────────────────
function loadEnv() {
  const env = {...process.env};
  const envFile = path.join(ROOT, '.env');
  if (fs.existsSync(envFile)) {
    fs.readFileSync(envFile, 'utf8').split('\n').forEach(line => {
      const m = line.match(/^([^#=\s][^=]*)=(.*)$/);
      if (m) env[m[1].trim()] = m[2].trim().replace(/^["']|["']$/g, '');
    });
  }
  return env;
}

// ── Photo HTTP server ─────────────────────────────────────────────────────────
function startPhotoServer(dir) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const filePath = path.join(dir, decodeURIComponent(req.url.slice(1)));
      fs.readFile(filePath, (err, data) => {
        if (err) { res.writeHead(404); res.end(); return; }
        res.writeHead(200, {'Content-Type': 'image/jpeg'});
        res.end(data);
      });
    });
    server.listen(0, '127.0.0.1', () => resolve({server, port: server.address().port}));
    server.on('error', reject);
  });
}

// ── GeoJSON → Remotion props ──────────────────────────────────────────────────
function parseGeoJSON(photoBaseUrl) {
  const data = JSON.parse(fs.readFileSync(GEOJSON, 'utf8'));
  const poiFeatures = data.features
    .filter(f => f.geometry.type === 'Point')
    .sort((a, b) => a.properties.order - b.properties.order);
  const routeFeature = data.features.find(f => f.geometry.type === 'LineString');

  const slides = poiFeatures.map(f => {
    const p = f.properties;
    const slug = p.slug;
    const photoPath = path.join(PHOTOS_DIR, `${slug}.jpg`);
    const photoUrl = fs.existsSync(photoPath)
      ? `${photoBaseUrl}/${slug}.jpg`
      : '';
    return {
      name: p.name,
      order: p.order,
      photoUrl,
      attribution: p.attribution || '',
      coordinates: f.geometry.coordinates, // [lng, lat]
    };
  });

  let routeSegments = [];
  if (routeFeature) {
    const allCoords = routeFeature.geometry.coordinates;
    const breaks = routeFeature.properties.segment_breaks;
    // Build one RouteSegment per consecutive POI pair
    for (let i = 0; i < breaks.length - 1; i++) {
      const start = breaks[i];
      const end = (i + 1 < breaks.length) ? breaks[i + 1] : allCoords.length - 1;
      routeSegments.push({coords: allCoords.slice(start, end + 1)});
    }
  }

  return {slides, routeSegments};
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  fs.mkdirSync(OUTPUT_DIR, {recursive: true});

  if (!fs.existsSync(GEOJSON)) {
    console.error(`✗ ${GEOJSON} not found — run generate.py first`);
    process.exit(1);
  }

  const env = loadEnv();
  const maptilerKey = env.MAPTILER_API_KEY || '';
  if (!maptilerKey) console.warn('⚠ MAPTILER_API_KEY not set — map tiles may fail');

  // Serve photos over HTTP so Remotion's Chromium can load them
  const {server: photoServer, port: photoPort} = await startPhotoServer(PHOTOS_DIR);
  const photoBaseUrl = `http://127.0.0.1:${photoPort}`;

  const {slides, routeSegments} = parseGeoJSON(photoBaseUrl);
  console.log(`${slides.length} POI slides, ${routeSegments.length} route segments`);

  const inputProps = {
    slides,
    route: routeSegments,
    introDur: INTRO_DUR,
    outroDur: OUTRO_DUR,
    slideDur: SLIDE_DUR,
    maptilerKey,
  };

  const totalSec = INTRO_DUR + slides.length * SLIDE_DUR + OUTRO_DUR;
  console.log(`Total duration: ${totalSec.toFixed(0)}s (${(totalSec/60).toFixed(1)} min) @ 30fps`);

  console.log('\nBundling Remotion composition…');
  const serveUrl = await bundle({entryPoint: ENTRY_POINT});

  console.log('Selecting composition…');
  const composition = await selectComposition({
    serveUrl,
    id: 'BangkokCityWalk',
    inputProps,
  });

  console.log(`\nRendering → ${OUTPUT_MP4}`);
  await renderMedia({
    composition,
    serveUrl,
    codec: 'h264',
    outputLocation: OUTPUT_MP4,
    inputProps,
    chromiumOptions: {gl: 'swangle'},
    videoBitrate: '8M',
    onProgress: ({progress}) => {
      process.stdout.write(`  ${Math.round(progress * 100)}%\r`);
    },
  });

  photoServer.close();
  console.log(`\n✓ ${OUTPUT_MP4}`);
}

main().catch(err => { console.error(err); process.exit(1); });
```

- [ ] **Step 2: Update `package.json`**

Add these two scripts to the `"scripts"` block:

```json
"citywalk:render": "node scripts/bangkok-citywalk/render-walk.js",
"citywalk:studio": "npx remotion studio scripts/bangkok-citywalk/remotion/src/index.tsx"
```

Full `"scripts"` block after edit:
```json
"scripts": {
  "generate-previews": "node scripts/generate-map-previews.js",
  "distorama:render": "node scripts/toulouse-distorama/render-slideshow.js",
  "distorama:studio": "npx remotion studio scripts/toulouse-distorama/remotion/src/index.tsx",
  "citywalk:render": "node scripts/bangkok-citywalk/render-walk.js",
  "citywalk:studio": "npx remotion studio scripts/bangkok-citywalk/remotion/src/index.tsx"
}
```

- [ ] **Step 3: Preview in Remotion Studio to verify the composition loads**

```bash
npm run citywalk:studio
```

Open the browser tab that Remotion Studio launches (usually `http://localhost:3000`). Verify:
- Composition `BangkokCityWalk` is listed
- Scrubbing the timeline shows the map (may show blank tiles without a valid MAPTILER_API_KEY)

- [ ] **Step 4: Render the video**

Make sure `generate.py` has been run and `walk.geojson` + photos exist first:

```bash
ls static/bangkok-citywalk/walk.geojson static/bangkok-citywalk/photos/*.jpg | wc -l
# should print 11 (1 geojson + 10 photos)

npm run citywalk:render
```

Expected output:
```
10 POI slides, 9 route segments
Total duration: 113s (1.9 min) @ 30fps

Bundling Remotion composition…
Selecting composition…

Rendering → static/bangkok-citywalk/bangkok-citywalk-<hash>.mp4
  100%
✓ static/bangkok-citywalk/bangkok-citywalk-<hash>.mp4
```

- [ ] **Step 5: Verify the output**

```bash
# Check file exists and is non-empty
ls -lh static/bangkok-citywalk/bangkok-citywalk-*.mp4

# Inspect duration with ffprobe (if installed)
ffprobe -v quiet -show_entries format=duration -of csv=p=0 static/bangkok-citywalk/bangkok-citywalk-*.mp4
# Expected: ~113.000000
```

Open the MP4 in a video player and verify:
- Intro card shows "BANGKOK / City Walk"
- Each POI slide shows the Wikimedia photo in top portion, POI name + order badge below
- Map camera flies between POIs, route draws progressively
- Outro card shows "BANGKOK / maps.girard-davila.net"

- [ ] **Step 6: Commit**

```bash
git add scripts/bangkok-citywalk/render-walk.js package.json
git commit -m "feat(bangkok-citywalk): add render-walk.js and npm scripts"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Seed POI list (venues.csv) — Task 1
- ✅ Geocode via Nominatim — Task 2 Phase 1
- ✅ OSRM walking route cached as GeoJSON — Task 2 Phase 2
- ✅ Wikimedia Commons photo fetch + local cache — Task 2 Phase 3
- ✅ `walk.geojson` FeatureCollection (Point × N + LineString) — Task 2 Phase 4
- ✅ Hugo map page (Leaflet, route line, numbered markers, click popups) — Task 3
- ✅ Remotion 1080×1920 Shorts format — Tasks 4–8
- ✅ Intro: route overview + title card — Tasks 4 (Intro.tsx) + 7 (SlideShow)
- ✅ 10 POI slides: Wikimedia photo + name + order badge — Task 6
- ✅ MapView: route progressive highlight (walked orange / upcoming dim) — Task 5
- ✅ MapView: camera flies POI-to-POI with zoom arc — Task 5
- ✅ Outro card — Task 4 (Outro.tsx)
- ✅ render-walk.js CLI — Task 8
- ✅ npm scripts — Task 8

**Placeholder scan:** No TBDs or incomplete code blocks found.

**Type consistency check:**
- `WalkSlide.coordinates: [number, number]` — used as `[lng, lat]` in MapView ✅
- `RouteSegment.coords: [number, number][]` — consumed by `buildRouteGeojson` in MapView ✅
- `WalkShowProps.route: RouteSegment[]` — passed from SlideShow → MapView ✅
- `segment_breaks` in GeoJSON → split into `RouteSegment[]` in render-walk.js ✅
- `slug` key in SlideScene note: `WalkSlide` has no `slug` field — SlideShow.tsx key uses `slide.order` ✅
