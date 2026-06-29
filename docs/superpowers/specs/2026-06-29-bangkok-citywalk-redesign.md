# Bangkok Citywalk — Visual Redesign

**Date:** 2026-06-29  
**Scope:** `scripts/bangkok-citywalk/` — Remotion video renderer + data pipeline  
**Goal:** Align look & feel with lequartier (light map tiles, labled markers, walking rings) and add richer animations (pan-only transitions, pedestrian icon, map-based intro, multi-photo carousel).

---

## 1. Map Tiles & Marker Overhaul

### Tiles
Replace MapTiler `toner-v2` with **OpenFreeMap Positron**:

```
https://tiles.openfreemap.org/styles/positron
```

No API key required. Remove the `maptilerKey` prop from `WalkShowProps`, `SlideShow`, `MapView`, and `render-walk.js`.

### Markers
- **Inactive POIs:** circle radius 10 px, orange fill (`#FF6B35`), 2 px white stroke, opacity 0.7.
- **Active POI:** circle radius 20 px, same orange, 3 px white stroke, full opacity.
- **Name label (all POIs):** MapLibre `symbol` layer below the circle layers.  
  - `text-field`: POI name  
  - `text-anchor: "top"`, `text-offset: [0, 1.2]`  
  - `text-halo-color: "#fff"`, `text-halo-width: 2`  
  - `text-size: 11`  
  - Active label: opacity 1.0. Inactive label: opacity 0.6.

---

## 2. Walking-Distance Rings

Three concentric dashed rings centred on the **active POI**, redrawn on each slide change.

| Ring | Radius | Label |
|------|--------|-------|
| 1 | 400 m | 5 min |
| 2 | 800 m | 10 min |
| 3 | 1 200 m | 15 min |

**Implementation:**
- Rings are 64-point GeoJSON polygons computed via haversine math (no turf dependency).
- Added as a dedicated `rings` GeoJSON source with two layers:
  - `rings-line`: `line` layer, color `#1a6b3c`, `line-dasharray: [3, 2]`, `line-width: 1.5`, no fill.
  - `rings-label`: `symbol` layer, label text = "5 min" / "10 min" / "15 min", placed at the top of each ring (lat offset = radius / 111 320).
- Rings are hidden during the intro phase (no active POI).

---

## 3. Transition Animation

### Pan only (no zoom dip)
Remove the `Math.sin` zoom arc from `MapView`. Between POIs the camera interpolates lat/lng at constant `VENUE_ZOOM` (15) with the existing eased cubic. The intro zoom-in (overview → first POI) is unchanged.

**Before (remove):**
```ts
zoom = VENUE_ZOOM - Math.sin(rawT * Math.PI) * (VENUE_ZOOM - TRANSITION_ZOOM);
```
**After:**
```ts
zoom = VENUE_ZOOM;
```

### Pedestrian icon overlay
An absolutely-positioned React `<div>` sits above the MapLibre canvas (outside the canvas element, in the Remotion layer stack). It is rendered in `SlideShow` over the map region.

- Icon: Font Awesome `fa-person-walking`, 64 px, white fill, `text-shadow: 0 2px 8px rgba(0,0,0,0.6)`.
- Position: horizontally and vertically centred over the map half of the canvas.
- Visibility: computed from `useCurrentFrame` in `SlideShow`. During the first `fps` frames of each slide (the transition window), the icon fades in (frames 0–`fps*0.1`) and fades out (frames `fps*0.85`–`fps`). Outside the transition window it has opacity 0.

---

## 4. Intro Card Redesign

The `Intro` component (black card) is **removed**. The intro phase is map-only — the existing camera motion in `MapView` (overview zoom → first POI) already handles the visual.

A new `IntroOverlay` component replaces `Intro` in `SlideShow`. It is absolutely positioned over the **full canvas** (not just the map half):

- Renders the title: `BANGKOK CITY WALK` (or derived from slides metadata).
- Style: large outlined text (`-webkit-text-stroke: 3px #fff`, `color: rgba(0,0,0,0.75)`), `fontSize: 72px`, `fontWeight: 900`, centred horizontally, positioned at ~30% from top.
- Opacity envelope:
  - Fade in: frames 0 → `fps * 0.3`
  - Hold: `fps * 0.3` → `introFrames * 0.7`
  - Fade out: `introFrames * 0.7` → `introFrames`
- The component unmounts (via `<Sequence>`) when the intro ends.

---

## 5. Bottom Card

### Title
`fontSize`: 32 px → **64 px**. `fontWeight`: 700 → **900**.

### Attribution
Remove the attribution `<div>` from `SlideScene` entirely.

### Multi-photo carousel
`WalkSlide` type changes:

```ts
// Before
photoUrl: string;
attribution: string;

// After
photos: string[];   // 1–5 local photo URLs
```

Inside `SlideScene`, the slide duration is divided equally among `photos.length` photos. Two `<Img>` elements are stacked; the outgoing image fades out and the incoming fades in over 0.3 s (9 frames at 30 fps) at each transition boundary. The order badge stays top-left of the visible photo.

If `photos` is empty or `photos.length === 1`, no cross-fade logic runs (single static image or placeholder).

---

## 6. Data Pipeline (`generate.py`)

### Multi-photo fetch
`fetch_wikimedia_photo` → `fetch_wikimedia_photos(name, max_results=5)`.

- Increases the Wikimedia API `gsrlimit` to 15 to surface more candidates.
- Iterates all returned pages, collects up to `max_results` entries that have a valid `thumburl`.
- Returns `list[tuple[str, str]]` — `(thumb_url, attribution)` pairs.

### Mediacache schema change
```json
{
  "Wat Pho": {
    "photos": [
      {"url": "https://upload.wikimedia.org/...", "attribution": "© Foo / CC BY-SA 4.0"},
      ...
    ]
  }
}
```

### Download
Each photo is saved as `<slug>-1.jpg`, `<slug>-2.jpg`, … up to 5.

### GeoJSON output
Feature properties gain:
```json
"photos": [
  "/bangkok-citywalk/photos/wat-pho-1.jpg",
  "/bangkok-citywalk/photos/wat-pho-2.jpg"
]
```
`"photo"` (singular) property is removed.

### `render-walk.js`
Maps `feature.properties.photos` (array) to `WalkSlide.photos`. Falls back to `[]` if missing.

---

## Files Touched

| File | Change |
|------|--------|
| `remotion/src/types.ts` | `photoUrl/attribution` → `photos: string[]` |
| `remotion/src/MapView.tsx` | OpenFreeMap tiles, remove `maptilerKey`, add rings source/layers, remove zoom dip, add label layers |
| `remotion/src/SlideShow.tsx` | Remove `maptilerKey` prop, add `IntroOverlay`, add pedestrian icon overlay |
| `remotion/src/Intro.tsx` | Delete file |
| `remotion/src/IntroOverlay.tsx` | New component |
| `remotion/src/SlideScene.tsx` | `photos[]` carousel, 2× title, remove attribution |
| `remotion/src/Root.tsx` | Remove `maptilerKey` from props |
| `scripts/bangkok-citywalk/generate.py` | Multi-photo fetch, updated mediacache/GeoJSON schema |
| `scripts/bangkok-citywalk/render-walk.js` | Map `photos[]` to slide props |
