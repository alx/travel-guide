---
name: maps-create
description: Scaffold a new static POI map. Creates the GeoJSON stub, generate.py enrichment script, and either a Hugo layout (when hugo.toml is present) or a standalone index.html (no Hugo needed). Use when the user says "create a new map", "add a map", or "scaffold a map".
---

# maps-create

Scaffold a new static POI map. Detect the track, gather inputs, resolve the
center, run an interactive query loop to pre-populate locations, then write
all files.

## Step 0 — Detect track

Check for `hugo.toml` at the repo root:

```bash
test -f hugo.toml && echo "hugo" || echo "static"
```

- **Hugo track** (`hugo.toml` exists): generate `content/{slug}/_index.md` + `layouts/{slug}/list.html` (full Hugo integration).
- **Static track** (no `hugo.toml`): generate `static/{slug}/index.html` (standalone, no Hugo required — preview with Python http.server).

Carry the detected track through all subsequent steps.

## Step 1 — Gather inputs

Ask the user in one message:

1. **Slug** — URL-safe identifier, e.g. `yoga-toulouse`. Drives all folder names and `/{slug}/`.
2. **Title** — human-readable, e.g. "Yoga Studios in Toulouse".
3. **Location hint** — free text, e.g. "Toulouse, France". Resolved to map center.
4. **Categories** — name + FA icon class + hex color per category.
   Example: `Studio: fa-spa #7b5ea7, Teacher: fa-person #3b82f6`
   Default if omitted: `Place: fa-location-dot #3b82f6`
5. **Search query** — free text describing what to search for, e.g. `yoga studio`.
   Used to pre-populate `locations.geojson` from available APIs.

## Step 2 — Resolve map center

Call Nominatim with the location hint:

```
GET https://nominatim.openstreetmap.org/search?q={hint}&format=json&limit=1
User-Agent: maps.girard-davila.net/create-map
```

Pick zoom from the result's `type`:
- `country` → 5
- `state` / `region` → 7
- `city` / `town` / `administrative` → 12
- anything else → 13

On failure: use `[0, 0]` zoom 2 and warn the user to fix it in the layout.

## Step 3 — Interactive query refinement loop

Run this loop before writing any files. The goal is to confirm a search query
that returns useful results, which will be pre-populated into `locations.geojson`
and hardcoded into `generate.py`.

### 3a — Detect available API

Read `.env` (if it exists at repo root) and check the environment:
```bash
grep GOOGLE_PLACES_API_KEY .env 2>/dev/null || echo ""
```

- If `GOOGLE_PLACES_API_KEY` is set → use **Google Places API**
- Otherwise → use **Overpass** (free, no key required)

### 3b — Prepare the query

**If Google Places:**
Use the user's search query directly as `textQuery`. No translation needed.

**If Overpass:**
Fetch the OSM tag reference to translate the search query:
```
GET https://maps.girard-davila.net/llms_osm_tags.txt
```
Using the translation examples and tag tables in that file, map the user's
search query to the appropriate Overpass QL body. Use radius search centred on
the map center resolved in Step 2. Default radius: 5000m.

### 3c — Run the query and show results

**Google Places call:**
```
POST https://places.googleapis.com/v1/places:searchText
Headers:
  Content-Type: application/json
  X-Goog-Api-Key: {GOOGLE_PLACES_API_KEY}
  X-Goog-FieldMask: places.id,places.displayName,places.location,places.primaryType,places.formattedAddress,nextPageToken
Body:
{
  "textQuery": "{search_query}",
  "locationBias": {
    "circle": {
      "center": {"latitude": {lat}, "longitude": {lon}},
      "radius": 5000
    }
  },
  "maxResultCount": 20
}
```
Paginate using `nextPageToken` until exhausted. Collect all results.

**Overpass call:**
```
POST https://overpass-api.de/api/interpreter
Body: data={overpass_ql_query}
```
Collect all elements with a non-empty `tags.name`.

**Show results:**
From all collected results, pick 10 at random (use `random.sample` or equivalent).
Display as a compact numbered list:

```
Found {total} places matching "{query}" (showing 10 random):
 1. {name}  —  {address}  —  {source_id}
 2. ...
```

Then ask: **"Confirm this query, or enter a new search query to try again?"**

### 3d — Loop or confirm

- If the user edits the query: go back to 3b with the new query.
- If the user confirms: save `confirmed_query` and `all_features` (the full result set from the confirmed run), and proceed to Step 4.
- If the user skips (types "skip" or similar): proceed with an empty `locations.geojson` and no hardcoded query in `generate.py`.

## Step 4 — Write files

Never prompt before writing. Files written depend on the track detected in Step 0.

### Both tracks write these two files first

### `static/{slug}/locations.geojson`

If the user confirmed results in Step 3, write all fetched features:

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {"type": "Point", "coordinates": [{lon}, {lat}]},
      "properties": {
        "name": "{name}",
        "category": "{first_category_name}",
        "icon": "{first_category_icon}",
        "address": "{address}",
        "source_id": "{source_id}"
      }
    }
  ]
}
```

`source_id` format:
- Google Places: `google:{place_id}` (e.g. `google:ChIJabc123`)
- Overpass node: `osm:node/{id}`
- Overpass way: `osm:way/{id}`
- Overpass relation: `osm:relation/{id}`

If the user skipped Step 3, write the empty stub:
```json
{"type": "FeatureCollection", "features": []}
```

Each feature should have:
- `geometry.coordinates: [lon, lat]` — or `null` if only an address is known
- `properties.name`
- `properties.category` — must match one of the scaffold categories
- `properties.icon` — FA icon class for the category
- `properties.address` — optional, used by generate.py to geocode missing coordinates
- `properties.source_id` — API origin key; absent on manually-added entries

### `scripts/{slug}/generate.py`

A uv inline-script that:
1. Fetches fresh POI data from the confirmed API using `SEARCH_QUERY`
2. Merges results into `locations.geojson` by `source_id` (manually-added entries without a `source_id` are always preserved)
3. Geocodes manual entries that have an address but no coordinates

If the user skipped Step 3, omit the fetch functions and keep the geocode-only version from the original template.

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["python-dotenv"]
# ///
"""Enrich {slug} GeoJSON: fetch POIs from API and geocode missing coordinates.

Usage:
    uv run scripts/{slug}/generate.py
    uv run scripts/{slug}/generate.py --dry-run

Reads and writes:
    static/{slug}/locations.geojson
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
GEOJSON_PATH = REPO_ROOT / "static/{slug}/locations.geojson"
GEOCACHE_PATH = SCRIPT_DIR / ".geocache.json"
NOMINATIM_HEADERS = {"User-Agent": "maps.girard-davila.net/{slug}"}

# ── Search configuration ───────────────────────────────────────────────────────
SEARCH_QUERY = "{confirmed_query}"
MAP_CENTER = ({lat}, {lon})   # (lat, lon)
SEARCH_RADIUS = 5000          # metres — adjust to taste

# Overpass QL — used when GOOGLE_PLACES_API_KEY is not set
OVERPASS_QUERY = """
[out:json][timeout:25];
(
  {overpass_ql_body}
);
out center;
"""


# ── Google Places ──────────────────────────────────────────────────────────────

def fetch_google_places(api_key: str) -> list[dict]:
    endpoint = "https://places.googleapis.com/v1/places:searchText"
    field_mask = "places.id,places.displayName,places.location,places.formattedAddress,nextPageToken"
    lat, lon = MAP_CENTER
    features = []
    page_token = None

    while True:
        body: dict = {
            "textQuery": SEARCH_QUERY,
            "locationBias": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lon},
                    "radius": float(SEARCH_RADIUS),
                }
            },
            "maxResultCount": 20,
        }
        if page_token:
            body["pageToken"] = page_token

        req = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": field_mask,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                resp = json.loads(r.read())
        except Exception as e:
            print(f"  ⚠ Google Places error: {e}", file=sys.stderr)
            break

        for place in resp.get("places", []):
            loc = place.get("location", {})
            lon_p = loc.get("longitude")
            lat_p = loc.get("latitude")
            if lon_p is None or lat_p is None:
                continue
            name = place.get("displayName", {}).get("text", "")
            source_id = f"google:{place['id']}"
            features.append(_make_feature(name, lon_p, lat_p, source_id, place.get("formattedAddress", "")))

        page_token = resp.get("nextPageToken")
        if not page_token:
            break
        time.sleep(2)

    return features


# ── Overpass ───────────────────────────────────────────────────────────────────

def fetch_overpass() -> list[dict]:
    url = "https://overpass-api.de/api/interpreter"
    data = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "maps.girard-davila.net/{slug}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
    except Exception as e:
        print(f"  ⚠ Overpass error: {e}", file=sys.stderr)
        return []

    features = []
    for el in resp.get("elements", []):
        if el["type"] == "node":
            lon_e, lat_e = el["lon"], el["lat"]
        elif "center" in el:
            lon_e, lat_e = el["center"]["lon"], el["center"]["lat"]
        else:
            continue
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("name:en") or ""
        if not name:
            continue
        source_id = f"osm:{el['type']}/{el['id']}"
        address = ", ".join(filter(None, [
            tags.get("addr:housenumber", ""),
            tags.get("addr:street", ""),
            tags.get("addr:city", ""),
        ]))
        features.append(_make_feature(name, lon_e, lat_e, source_id, address))

    return features


def _make_feature(name: str, lon: float, lat: float, source_id: str, address: str = "") -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "name": name,
            "category": "{first_category_name}",
            "icon": "{first_category_icon}",
            "address": address,
            "source_id": source_id,
        },
    }


# ── Merge ──────────────────────────────────────────────────────────────────────

def merge_features(existing: list[dict], fetched: list[dict]) -> list[dict]:
    manual = [f for f in existing if not (f.get("properties") or {}).get("source_id")]
    by_id = {
        f["properties"]["source_id"]: f
        for f in existing
        if (f.get("properties") or {}).get("source_id")
    }
    for f in fetched:
        sid = f["properties"]["source_id"]
        if sid in by_id:
            by_id[sid]["geometry"] = f["geometry"]
        else:
            by_id[sid] = f
    return manual + list(by_id.values())


# ── Geocode fallback (manual entries only) ─────────────────────────────────────

def load_geocache() -> dict:
    if GEOCACHE_PATH.exists():
        return json.loads(GEOCACHE_PATH.read_text())
    return {}


def save_geocache(cache: dict) -> None:
    GEOCACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def geocode(address: str) -> tuple[float, float] | None:
    params = urllib.parse.urlencode({"q": address, "format": "json", "limit": 1})
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    try:
        req = urllib.request.Request(url, headers=NOMINATIM_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            results = json.loads(r.read())
        if results:
            return float(results[0]["lon"]), float(results[0]["lat"])
    except Exception as e:
        print(f"  ⚠ Nominatim error for '{address}': {e}", file=sys.stderr)
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    fc = json.loads(GEOJSON_PATH.read_text())
    existing = fc.get("features", [])

    # 1. Fetch from API
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if api_key:
        print(f"Fetching via Google Places: {SEARCH_QUERY!r}…")
        fetched = fetch_google_places(api_key)
    else:
        print("No GOOGLE_PLACES_API_KEY — fetching via Overpass…")
        fetched = fetch_overpass()
    print(f"  {len(fetched)} places fetched")

    # 2. Merge (manual entries are always preserved)
    merged = merge_features(existing, fetched)
    manual_count = len([f for f in existing if not (f.get("properties") or {}).get("source_id")])
    added = len(merged) - len(existing)
    print(f"  {added} new, {len(fetched) - added} updated, {manual_count} manual (preserved)")

    # 3. Geocode manual entries missing coordinates
    cache = load_geocache()
    updated_cache = False
    for feature in merged:
        coords = (feature.get("geometry") or {}).get("coordinates")
        address = (feature.get("properties") or {}).get("address", "")
        source_id = (feature.get("properties") or {}).get("source_id")
        if coords or not address or source_id:
            continue
        if address not in cache:
            if args.dry_run:
                print(f"  [dry-run] would geocode: {address}")
                continue
            result = geocode(address)
            if result:
                cache[address] = result
                updated_cache = True
                time.sleep(1.1)
            else:
                print(f"  ⚠ Geocode FAILED: {address}", file=sys.stderr)
                continue
        lon_g, lat_g = cache[address]
        feature["geometry"] = {"type": "Point", "coordinates": [lon_g, lat_g]}

    if updated_cache:
        save_geocache(cache)

    # 4. Write
    if not args.dry_run:
        fc["features"] = merged
        GEOJSON_PATH.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
        print(f"Wrote {len(merged)} features → {GEOJSON_PATH}")
    else:
        print(f"[dry-run] would write {len(merged)} features")


if __name__ == "__main__":
    main()
```

Replace all `{slug}`, `{lat}`, `{lon}`, `{confirmed_query}`, `{first_category_name}`, `{first_category_icon}` with actual values.

For `{overpass_ql_body}`, paste the Overpass body lines constructed in Step 3b (without the outer `[out:json]` wrapper).

If the user skipped Step 3, replace the entire fetch section with a no-op:
```python
fetched = []
```
and remove the `SEARCH_QUERY`, `MAP_CENTER`, `SEARCH_RADIUS`, `OVERPASS_QUERY` constants.

### Hugo track only — `content/{slug}/_index.md`

```markdown
---
title: "{title}"
description: ""
emoji: ""
accent_color: "{first_category_hex}"
---
```

Use the hex color of the first category as `accent_color`.

### Hugo track only — `layouts/{slug}/list.html`

Model after `layouts/yoga-france/list.html` (the canonical minimal static POI layout). Produce:

```html
{{ define "head" }}
{{ partial "map-poi-styles.html" . }}
<style>
  :root { --map-accent: {first_category_hex}; }
  .filter-btn.active { background: var(--map-accent); border-color: var(--map-accent); }
  .poi-card:hover, .poi-card.active { border-color: var(--map-accent); }
  .search-box:focus { border-color: var(--map-accent); }
  .header-btn:hover, .header-btn.active { border-color: var(--map-accent); color: var(--map-accent); background: #f8f4ff; }
  .poi-card .poi-actions a { color: var(--map-accent); }
</style>
{{ end }}

{{ define "main" }}
<div id="map"></div>
<div class="map-overlay">
  <div class="overlay-card">
    <a href="{{ "/" | relURL }}" class="site-brand">🗺️ Maps</a>
    <div class="page-header">
      <h1>{{ with .Params.emoji }}{{ . }} {{ end }}{{ .Title }}</h1>
      {{ with .Description }}<p>{{ . }}</p>{{ end }}
    </div>
    <div class="header-actions">
      <button class="header-btn" id="tb-toggle" onclick="this.classList.toggle('active');document.getElementById('map-toolbar').classList.toggle('tb-visible')">🛠️ Tools</button>
      <button class="header-btn" id="list-toggle" onclick="this.classList.toggle('active');document.querySelector('.overlay-card.scrollable').classList.toggle('mobile-open');this.textContent=document.querySelector('.overlay-card.scrollable').classList.contains('mobile-open')?'📍 Lieux ▾':'📍 Lieux'">📍 Lieux</button>
      <button class="header-btn" id="locate-btn" onclick="toggleLocation()" title="Afficher ma position">📍 Position</button>
    </div>
    {{ partial "map-toolbar.html" . }}
    <input class="search-box" type="text" id="search" placeholder="🔍 Rechercher..." oninput="filterPOIs()">
    <div id="filter-btns"></div>
  </div>
  <div class="overlay-card scrollable">
    <div id="custom-layer-info" style="display:none;" class="custom-layer-badge">
      📂 <span id="custom-layer-name">Custom layer loaded</span>
      <button onclick="removeCustomLayer()" style="margin-left:auto;background:none;border:none;cursor:pointer;color:#92400e;font-size:1rem;line-height:1;">✕</button>
    </div>
    <div id="poi-list"></div>
  </div>
</div>
{{ partial "contact-form.html" . }}
<div class="copy-toast" id="copy-toast"></div>
{{ end }}

{{ define "scripts" }}
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"/>
<script src="https://unpkg.com/topojson-client@3/dist/topojson-client.min.js"></script>
<script>
window.MAP_CONFIG = {
  title: {{ .Title | jsonify }},
  geojsonUrl: '/{slug}/locations.geojson',
  embedPath: '/{slug}/',
  center: [{lat}, {lon}],
  zoom: {zoom},
  useClustering: true
};
window.CATEGORY_COLORS = { {category_colors_js} };
window.CATEGORY_ICONS  = { {category_icons_js} };
</script>
{{ partial "map-poi-helpers.html" . }}
{{ partial "map-geolocation.html" . }}
{{ end }}
```

Fill in:
- `{first_category_hex}` — hex color of the first category
- `{slug}` — the map slug
- `{lat}`, `{lon}`, `{zoom}` — from Step 2
- `{category_colors_js}` — e.g. `'Studio': '#7b5ea7', 'Teacher': '#3b82f6'`
- `{category_icons_js}` — e.g. `'Studio': 'fa-spa', 'Teacher': 'fa-person'`

### Static track only — `static/{slug}/index.html`

A fully self-contained HTML file. Read each partial from `layouts/partials/`
and inline its content verbatim at the marked positions below.

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"/>
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"/>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    /* ── inline layouts/partials/map-poi-styles.html ── */
    :root { --map-accent: {first_category_hex}; }
    .filter-btn.active { background: var(--map-accent); border-color: var(--map-accent); }
    .poi-card:hover, .poi-card.active { border-color: var(--map-accent); }
    .search-box:focus { border-color: var(--map-accent); }
    .header-btn:hover, .header-btn.active { border-color: var(--map-accent); color: var(--map-accent); background: #f8f4ff; }
    .poi-card .poi-actions a { color: var(--map-accent); }
  </style>
</head>
<body>
<div id="map"></div>
<div class="map-overlay">
  <div class="overlay-card">
    <div class="page-header">
      <h1>{title}</h1>
    </div>
    <div class="header-actions">
      <button class="header-btn" id="tb-toggle" onclick="this.classList.toggle('active');document.getElementById('map-toolbar').classList.toggle('tb-visible')">🛠️ Tools</button>
      <button class="header-btn" id="list-toggle" onclick="this.classList.toggle('active');document.querySelector('.overlay-card.scrollable').classList.toggle('mobile-open');this.textContent=document.querySelector('.overlay-card.scrollable').classList.contains('mobile-open')?'📍 Lieux ▾':'📍 Lieux'">📍 Lieux</button>
      <button class="header-btn" id="locate-btn" onclick="toggleLocation()" title="Afficher ma position">📍 Position</button>
    </div>
    <!-- ── inline layouts/partials/map-toolbar.html ── -->
    <input class="search-box" type="text" id="search" placeholder="🔍 Rechercher..." oninput="filterPOIs()">
    <div id="filter-btns"></div>
  </div>
  <div class="overlay-card scrollable">
    <div id="custom-layer-info" style="display:none;" class="custom-layer-badge">
      📂 <span id="custom-layer-name">Custom layer loaded</span>
      <button onclick="removeCustomLayer()" style="margin-left:auto;background:none;border:none;cursor:pointer;color:#92400e;font-size:1rem;line-height:1;">✕</button>
    </div>
    <div id="poi-list"></div>
  </div>
</div>
<!-- ── inline layouts/partials/contact-form.html ── -->
<div class="copy-toast" id="copy-toast"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<script src="https://unpkg.com/topojson-client@3/dist/topojson-client.min.js"></script>
<script>
window.MAP_CONFIG = {
  title: {title_json},
  geojsonUrl: './locations.geojson',
  embedPath: './',
  center: [{lat}, {lon}],
  zoom: {zoom},
  useClustering: true
};
window.CATEGORY_COLORS = { {category_colors_js} };
window.CATEGORY_ICONS  = { {category_icons_js} };
</script>
<!-- ── inline layouts/partials/map-poi-helpers.html ── -->
<!-- ── inline layouts/partials/map-geolocation.html ── -->
</body>
</html>
```

Fill in:
- `{title}` — human-readable map title
- `{title_json}` — JSON-encoded title literal, e.g. `"Yoga Studios in Toulouse"`
- `{first_category_hex}` — hex color of the first category
- `{lat}`, `{lon}`, `{zoom}` — from Step 2
- `{category_colors_js}` — e.g. `'Studio': '#7b5ea7', 'Teacher': '#3b82f6'`
- `{category_icons_js}` — e.g. `'Studio': 'fa-spa', 'Teacher': 'fa-person'`

At each `<!-- ── inline … ── -->` marker, read the corresponding file and paste
its full contents verbatim (stripping only the outermost `<style>` tags from
`map-poi-styles.html` since they are already inside a `<style>` block above).

## Step 5 — Print summary

**Hugo track:**

```
✓ static/{slug}/locations.geojson   — {n} POIs pre-populated (source: {api_used})
✓ scripts/{slug}/generate.py        — re-run to refresh from {api_used}, merges by source_id
✓ content/{slug}/_index.md
✓ layouts/{slug}/list.html

Next:
1. Edit static/{slug}/locations.geojson to adjust categories or add manual POIs
2. Preview locally: hugo server --disableFastRender
   Then open http://localhost:1313/{slug}/
3. Run /maps-publish {slug} when ready
```

**Static track:**

```
✓ static/{slug}/locations.geojson   — {n} POIs pre-populated (source: {api_used})
✓ scripts/{slug}/generate.py        — re-run to refresh from {api_used}, merges by source_id
✓ static/{slug}/index.html          — standalone map, no Hugo required

Next:
1. Edit static/{slug}/locations.geojson to adjust categories or add manual POIs
2. Preview locally:
   python -m http.server 8000 --directory static/{slug}
   open http://localhost:8000
3. Run /maps-publish {slug} when ready
```

If the user skipped the query step, replace the first two lines in either
summary with:
```
✓ static/{slug}/locations.geojson   — empty stub, add POIs manually
✓ scripts/{slug}/generate.py        — geocodes manual entries with an address
```
