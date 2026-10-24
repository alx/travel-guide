# scripts/toulouse_angles_morts

<img src="../../docs/screenshots/toulouse_angles_morts.png" width="600" alt="Angles morts Toulouse map preview">

Finds streets in the **Toulouse hyper-centre** that no surveillance camera can
see, and renders them as a map — the "blind spots" of the videoprotection
network.

Built on the `videoprotection-toulouse` dataset (cameras from OpenStreetMap).
Unlike that map, this one is **unlisted**: it is reachable by direct URL only,
never linked from the site index.

## Method

1. **Zone** — the official Toulouse quarter `admin_level=10`, relation
   **1684025 "Capitole / Arnaud Bernard / Carmes"** (the historical
   hyper-centre). Its boundary is fetched from Overpass and assembled with
   `polygonize`.
2. **Street network** — every `highway=*` way inside the zone bbox, clipped to
   the zone polygon.
3. **Coverage model** (hybrid, stated on the map):
   - camera has `camera:direction` → visibility **cone** (FOV 100°, range 50 m);
   - direction unknown / 360° → full **disk** of 50 m;
   - a street part is *covered* when ≥ 50 % of its ~10 m samples fall inside
     any camera's field of view.
4. **Output** — GeoJSON `LineString` features with `status: uncovered|covered`,
   ready for the map's red/grey styling.

## Run

```bash
uv run scripts/toulouse_angles_morts/generate.py > static/toulouse-angles-morts/locations.geojson
```

Requires network access to Overpass (retries across three mirrors with
exponential backoff). Depends on `shapely` (declared inline, resolved by `uv`).

**Output:** `static/toulouse-angles-morts/locations.geojson`
**Live map (unlisted):** https://maps.girard-davila.net/toulouse-angles-morts/

## Privacy

No new surveillance data is collected. Fields are suppressed exactly as in the
source fetch script (`operator`, `contact:*`, `description`, `camera:direction`,
`height`, `note` are never written). The output is street geometry + coverage
class only.
