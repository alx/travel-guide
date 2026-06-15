---
name: create-map
description: Scaffold a new static POI map in the alx/travel-guide Hugo site. Creates the GeoJSON stub, generate.py enrichment script, Hugo content stub, and custom layout. Use when the user says "create a new map", "add a map", or "scaffold a map".
---

# create-map

Scaffold a new static POI map. Gather inputs, resolve the center, write all files.

## Step 1 — Gather inputs

Ask the user in one message:

1. **Slug** — URL-safe identifier, e.g. `yoga-toulouse`. Drives all folder names and `/{slug}/`.
2. **Title** — human-readable, e.g. "Yoga Studios in Toulouse".
3. **Location hint** — free text, e.g. "Toulouse, France". Resolved to map center.
4. **Categories** — name + FA icon class + hex color per category.
   Example: `Studio: fa-spa #7b5ea7, Teacher: fa-person #3b82f6`
   Default if omitted: `Place: fa-location-dot #3b82f6`

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

## Step 3 — Write files

Write all four files. Never prompt before writing.

### `static/{slug}/locations.geojson`

```json
{
  "type": "FeatureCollection",
  "features": []
}
```

This is the source of truth. The user adds POIs here directly. Each feature should have:
- `geometry.coordinates: [lon, lat]` — or `null` if only an address is known (generate.py will fill it)
- `properties.name`
- `properties.category` — must match one of the scaffold categories
- `properties.icon` — FA icon class for the category
- `properties.address` — optional, used by generate.py to geocode missing coordinates

### `scripts/{slug}/generate.py`

A uv inline-script that enriches the GeoJSON: reads `static/{slug}/locations.geojson`, geocodes any feature whose coordinates are `null` but has a non-empty `properties.address`, writes the updated GeoJSON back in place.

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Enrich {slug} GeoJSON: geocode features with a known address but missing coordinates.

Usage:
    uv run scripts/{slug}/generate.py
    uv run scripts/{slug}/generate.py --dry-run

Reads and writes:
    static/{slug}/locations.geojson
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
GEOJSON_PATH = REPO_ROOT / "static/{slug}/locations.geojson"
GEOCACHE_PATH = SCRIPT_DIR / ".geocache.json"
NOMINATIM_HEADERS = {"User-Agent": "maps.girard-davila.net/{slug}"}


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    fc = json.loads(GEOJSON_PATH.read_text())
    cache = load_geocache()
    updated_cache = False
    enriched = 0

    for feature in fc["features"]:
        coords = (feature.get("geometry") or {}).get("coordinates")
        address = (feature.get("properties") or {}).get("address", "")
        if coords or not address:
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

        lon, lat = cache[address]
        feature["geometry"] = {"type": "Point", "coordinates": [lon, lat]}
        print(f"  → {address}: {lat:.5f}, {lon:.5f}")
        enriched += 1

    if updated_cache:
        save_geocache(cache)

    if not args.dry_run and enriched:
        GEOJSON_PATH.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
        print(f"Enriched {enriched} feature(s) — wrote {GEOJSON_PATH}")
    elif not enriched:
        print("Nothing to enrich.")


if __name__ == "__main__":
    main()
```

Replace `{slug}` with the actual slug throughout.

### `content/{slug}/_index.md`

```markdown
---
title: "{title}"
description: ""
emoji: ""
accent_color: "{first_category_hex}"
---
```

Use the hex color of the first category as `accent_color`.

### `layouts/{slug}/list.html`

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

## Step 4 — Print summary

```
✓ static/{slug}/locations.geojson   — add POIs here (name, category, address or coordinates)
✓ scripts/{slug}/generate.py        — run to geocode features with address but no coordinates
✓ content/{slug}/_index.md
✓ layouts/{slug}/list.html

Next:
1. Edit static/{slug}/locations.geojson and add your POIs
2. Run /publish-map {slug} when ready
```
