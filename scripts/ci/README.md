# scripts/ci

CI scripts for validation and map preview generation. Both are called by GitHub Actions workflows and are not typically run manually.

---

## `validate_geojson.py`

Validates all `static/**/locations.geojson` files against RFC 7946 and the project schema.

**Run by:** `deploy.yml` (before every build/deploy)

**No external dependencies** — stdlib only.

```bash
uv run scripts/ci/validate_geojson.py
```

Exits 0 on success (warnings allowed), 1 if any errors are found. Errors block deployment.

**Checks performed:**
- Valid JSON + UTF-8 encoding
- Top-level `FeatureCollection` with a `features` array
- Coordinate bounds (WGS84: lon −180..180, lat −90..90)
- Required properties: `name`, `category`
- `coord_source` enum: `google_maps_pin`, `google_places`, `nominatim`, `research`, `estimated`, `on_site_gps`
- `coord_accuracy` enum: `high`, `medium`, `low`
- Unique `id` across all files

Warnings (non-blocking): missing `bbox`, missing `_meta.crs`, missing provenance metadata.

---

## `generate-map-previews.js`

Screenshots Leaflet map pages with Playwright and saves PNG previews.

**Run by:** `map-preview.yml` (on PRs that change GeoJSON/TopoJSON files)

**Prerequisites:**
```bash
npm install                                          # installs Playwright
npx playwright install chromium --with-deps          # installs Chromium
```

**CI/PR mode** — screenshot specific maps by slug:
```bash
node scripts/ci/generate-map-previews.js 711-samui lamai
# → .github/previews/{slug}.png  (committed to the PR branch)
```

**Full build mode** — screenshot all known maps (requires a running dev server):
```bash
npx serve public -l 1414 &
node scripts/ci/generate-map-previews.js
# → public/images/map-previews/{name}.png
```

**Environment variables:**

| Variable | Default | Purpose |
|---|---|---|
| `PREVIEW_BASE_URL` | `http://localhost:1414` | Base URL of the served site |

Exits 0 on success, 1 if any screenshot fails.
