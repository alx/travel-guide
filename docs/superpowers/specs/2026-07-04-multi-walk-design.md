# Multi-Walk Explorer — Design Spec

**Date:** 2026-07-04  
**Goal:** Run 10–100 stochastic temple-walk instances from the same starting point and display all routes overlaid on a single Hugo map page, to visualize the space of possible walks.

---

## Overview

A new `multi.py` script generates N randomised temple-walk routes from a given start, writes a combined GeoJSON, and produces a Hugo content page rendered by a new `temple-walk-multi` layout. The existing `generate.py` is refactored to import shared logic from a new `lib.py` — no duplication, no behaviour change.

---

## File Structure

```
scripts/temple-walk/
  lib.py          ← shared pure + network functions (new)
  generate.py     ← keeps only main(); imports from lib (refactored)
  multi.py        ← stochastic multi-walk runner (new uv script)

layouts/temple-walk-multi/
  single.html     ← Leaflet map: all walks at low opacity (new)

content/temple-walks/
  {slug}-multi.md ← generated per run; type: "temple-walk-multi"

static/temple-walks/{slug}/
  multi-walk.geojson  ← combined output written by multi.py
```

`lib.py` is a plain Python module with no shebang or `__main__`. `generate.py` and `multi.py` each remain self-contained uv scripts (own dependency headers, own `main()`).

---

## Algorithm — Stochastic Walk

`lib.py` adds `plan_walk_random(start, temples, max_km, fetch_leg, rng, k_candidates=3)` alongside the existing deterministic `plan_walk`. The only change from `plan_walk`: instead of `min(routed, key=lambda r: r[2])`, it calls `rng.choice(routed)` — uniform random pick from the k routed candidates.

`multi.py` runs this N times:

```python
for i in range(args.runs):
    rng = random.Random(i)   # seed = run index → reproducible
    walk = plan_walk_random(start, list(temples), max_km, fetch_leg, rng)
    walks.append(walk)
```

- Overpass temple query: fetched once, cached (same file as `generate.py` keyed by slug)
- OSRM leg routes: fetched on demand, cached (same routes cache file as `generate.py`)
- Photos: never fetched — `multi.py` has no photo phase

---

## GeoJSON Output Format

`static/temple-walks/{slug}/multi-walk.geojson` is a single FeatureCollection:

| Feature class | Geometry | Key properties |
|---|---|---|
| Start point | Point | `order: 0`, `type: "start"` |
| Stop dots | Point (deduplicated by name) | `type: "stop"`, `name` |
| Route lines | LineString (one per walk) | `type: "route"`, `walk_index: i`, `n_stops: k`, `total_km: x` |

Stop points are deduplicated across all walks — a temple that appears in 15 of 30 walks produces one dot, not 15.

---

## Hugo Content Page

Generated file: `content/temple-walks/{slug}-multi.md`

```yaml
---
title: "Temple Walk Explorer — {Slug}"
description: "{N} walks, {min_km}–{max_km} km, from {start_label}"
type: "temple-walk-multi"
geojson: "/temple-walks/{slug}/multi-walk.geojson"
---
```

If the file already exists it is left untouched (same convention as `generate.py`).

---

## Hugo Layout — `layouts/temple-walk-multi/single.html`

Inherits the same dark theme and Stadia Maps tile as `layouts/temple-walk/single.html`.

**Map rendering (Leaflet JS):**
- Route LineStrings: `color: #FF6B35`, `opacity: 0.15`, `weight: 3` — overlap accumulates into bright density corridors naturally
- Start point: green filled CircleMarker, radius 8, labelled "S"
- Stop dots: orange filled CircleMarker, radius 4, tooltip shows temple name on hover
- `map.fitBounds` over all route coordinates combined

**Overlay card (compact — no POI sidebar list):**
- Title from `.Title`
- Description: "{N} walks · {min_km}–{max_km} km · from {start}"
- Route legend: coloured line sample + label

---

## CLI

```bash
uv run scripts/temple-walk/multi.py \
  --start "13.7516,100.4927" \
  --slug rattanakosin \
  --runs 30 \
  --max-km 10
```

Arguments:
- `--start` — `"lat,lng"` or address (geocoded via Nominatim), same as `generate.py`
- `--slug` — output identifier; shares Overpass + OSRM caches with `generate.py`
- `--runs` — number of walks to generate (default: 20)
- `--max-km` — walking budget per walk (default: 10)

No `--dry-run` flag: `multi.py` never fetches photos and never writes Hugo content on first run without `--slug` — the network operations (Overpass, OSRM) are always cached after the first call.

---

## Refactor Scope for `generate.py`

All functions below `main()` move to `lib.py`. `generate.py` gains one import line and keeps only `main()`. No behaviour changes, no new arguments. Tests in `tests/test_generate.py` remain valid; they may need their import path updated if they import functions directly.
