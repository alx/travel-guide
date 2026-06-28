# Bangkok City Walk — Map + Video Design

**Date:** 2026-06-28
**Status:** approved

## Overview

A curated 10km Bangkok city walk: an interactive Hugo map page showing POI markers and a
walking route, plus a 1080×1920 Remotion video (YouTube Shorts) that flies between POIs
with Wikimedia photos, following the same pattern as `toulouse-distorama`.

---

## Repository structure

```
scripts/bangkok-citywalk/
  venues.csv              ← seed list: name, lat, lng (lat/lng optional — geocoded on first run)
  generate.py             ← pipeline: geocode → OSRM route → Wikimedia photos → GeoJSON
  remotion/
    src/
      index.tsx
      Root.tsx
      SlideShow.tsx       ← adapted from toulouse-distorama
      MapView.tsx         ← adapted: adds route line layer + progressive highlight
      SlideScene.tsx      ← Wikimedia photo + POI name instead of YouTube clip
      types.ts
  render-walk.js          ← CLI entry point (mirrors render-slideshow.js)

static/bangkok-citywalk/
  walk.geojson            ← written by generate.py
  photos/                 ← Wikimedia images cached locally

content/bangkok-citywalk/
  _index.md               ← Hugo frontmatter (title, description, video link)

layouts/bangkok-citywalk/
  list.html               ← MapLibre map: markers + route line
```

---

## Data pipeline (`generate.py`)

Three phases, each result cached to disk for fast re-runs.

### Phase 1 — Geocode POIs
- Read `venues.csv` (columns: `name`, `lat`, `lng`; lat/lng optional)
- For rows missing coordinates, query Nominatim: `"Bangkok Thailand <name>"`
- Write coordinates back to `venues.csv`; cache raw responses in `.geocache.json`

### Phase 2 — Fetch walking route
- Call public OSRM walking API (`/route/v1/foot/`) for each consecutive POI pair
- Stitch resulting LineString geometries into one continuous route
- Cache full route in `.routecache.json`
- Output: one GeoJSON `LineString` feature covering the full walk

### Phase 3 — Fetch Wikimedia photos
- For each POI, query Wikimedia Commons API (`action=query&generator=search`) for best match
- Download image to `static/bangkok-citywalk/photos/<slug>.jpg`
- Record URL + attribution in `.mediacache.json`

### Output: `static/bangkok-citywalk/walk.geojson`
A single `FeatureCollection` containing:
- One `Point` feature per POI: properties `name`, `photo` (local path), `order` (1-based), `attribution`
- One `LineString` feature for the full walking route

---

## Hugo map page (`layouts/bangkok-citywalk/list.html`)

- MapLibre GL map filling the viewport, static Hugo shell, data loaded client-side from `walk.geojson`
- **Route layer:** `line` from the `LineString` feature — dashed walking path, colour `#FF6B35`, 3px, dash `[2, 1]`
- **Marker layer:** numbered `circle` + `symbol` layers (1–N) from `Point` features
- Click a marker → popup with POI name + Wikimedia photo
- Initial camera fitted to route bounding box with padding

---

## Remotion video (`render-walk.js` + `remotion/`)

**Format:** 1080×1920, 30fps

**Structure:**
| Segment | Duration | Map | Bottom half |
|---|---|---|---|
| Intro | 3s | Route overview, full route line visible | Title card "Bangkok City Walk" |
| POI slides × 10 | 10s each | Fly to POI, progressive route highlight | Wikimedia photo + POI name + order badge |
| Outro | 5s | Zoom back to full route overview | Outro card |

**Total:** ~108s (~1m 48s)

### `MapView.tsx` changes from distorama
- Add `route` GeoJSON source with a `line` layer (always visible)
- Route is split into "walked" (highlighted) and "upcoming" (dimmed) segments based on current POI index
- Camera easing: same zoom-out arc as distorama but following actual route geometry for interpolation

### `SlideScene.tsx`
- Full-width `<Img>` (Remotion lazy image) from cached local Wikimedia photo
- POI name in large text overlay; order badge top-left
- No audio by default; `--youtube-url` flag adds background music (same interface as distorama)

### `render-walk.js`
- Reads `static/bangkok-citywalk/walk.geojson`
- Splits features: POI points → slides array, LineString → route prop
- Passes to Remotion `renderMedia`
- Output: `static/bangkok-citywalk/bangkok-citywalk-<hash>.mp4`

---

## Seed POIs (initial `venues.csv`)

10 iconic Bangkok landmarks spanning roughly 10km, ordered for a logical walking flow:

1. Grand Palace
2. Wat Pho
3. Wat Arun
4. Tha Tien Market
5. Flower Market (Pak Khlong Talat)
6. Chinatown (Yaowarat Road)
7. Wat Traimit (Golden Buddha)
8. Samphanthawong Market
9. Lhong 1919
10. Iconsiam

---

## Out of scope

- Audio/music for the video (can be added via `--youtube-url` flag, same as distorama)
- Multiple walk variants or routes
- CI automation / scheduled regeneration
- Mobile-responsive Hugo page enhancements
