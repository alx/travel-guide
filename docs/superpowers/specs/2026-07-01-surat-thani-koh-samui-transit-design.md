# Surat Thani ↔ Koh Samui Transit Map — Design Spec

**Date:** 2026-07-01
**Slug:** `surat-thani-koh-samui-transit`
**Source data:** `/home/alx/org/wiki/lamai-to-surat-thani.org`

## Summary

An interactive Leaflet map showing the single 4-leg transit route between Lamai (Koh Samui) and Phunphin train station (Surat Thani), valid in both directions. Styled after the existing roadtrip map pattern.

---

## 1. Files

| Path | Purpose |
|------|---------|
| `content/surat-thani-koh-samui-transit/_index.md` | Hugo page frontmatter |
| `static/surat-thani-koh-samui-transit/locations.geojson` | Map data (generated) |
| `layouts/surat-thani-koh-samui-transit/list.html` | Custom layout (adapted from roadtrip) |
| `scripts/surat-thani-koh-samui-transit/generate.py` | GeoJSON generator |

---

## 2. Route data

Single route, bidirectional. 5 stops, 4 legs.

### Stops (Point features)

| # | Name | Lat | Lng | Leg |
|---|------|-----|-----|-----|
| 1 | Lamai Beach | 9.4737 | 100.0608 | 1 |
| 2 | Nathon Pier | 9.5352 | 99.9267 | 1→2 |
| 3 | Donsak Pier | 9.1903 | 99.9573 | 2→3 |
| 4 | Surat Thani Bus Station | 9.1416 | 99.3298 | 3→4 |
| 5 | Phunphin Train Station | 9.1106 | 99.1935 | 4 |

### Legs (LineString features)

| Leg | Mode | From → To | Cost (THB) | Duration | Line geometry |
|-----|------|-----------|-----------|---------|--------------|
| 1 | 🚕 Taxi | Lamai → Nathon Pier | 600 | ~30 min | OSRM road routing |
| 2 | ⛴️ Ferry (Seatran) | Nathon Pier → Donsak Pier | 380 (incl. bus) | ~90 min | Straight line |
| 3 | 🚌 Bus | Donsak Pier → Surat Thani Bus Stn | (incl.) | ~70 min | OSRM road routing |
| 4 | 🚗 Grab | Surat Thani Bus Stn → Phunphin Stn | ~160 | ~20 min | OSRM road routing |

**Total: ~1140 THB · ~4-5h**

---

## 3. GeoJSON schema

Each **Point** feature:
```json
{
  "type": "Feature",
  "geometry": { "type": "Point", "coordinates": [lng, lat] },
  "properties": {
    "name": "Nathon Pier",
    "category": "Ferry",
    "icon": "⛴️",
    "leg": 2,
    "notes": "Seatran Ferry — every hour",
    "cost_thb": null,
    "duration": null
  }
}
```

Each **LineString** feature:
```json
{
  "type": "Feature",
  "geometry": { "type": "LineString", "coordinates": [[lng1, lat1], ..., [lng2, lat2]] },
  "properties": {
    "leg": 2,
    "mode": "Ferry",
    "icon": "⛴️",
    "color": "#0ea5e9",
    "cost_thb": 380,
    "duration": "~90 min",
    "notes": "Seatran — combined boat+bus ticket 380 THB. Preferred: 6pm boat (sunset ~6:30–7pm)."
  }
}
```

---

## 4. Leg colors

| Leg | Mode | Color |
|-----|------|-------|
| 1 | Taxi | `#f59e0b` (amber) |
| 2 | Ferry | `#0ea5e9` (sky blue) |
| 3 | Bus | `#22c55e` (green) |
| 4 | Grab | `#8b5cf6` (purple) |

---

## 5. Layout (adapted roadtrip pattern)

- **Map**: Leaflet + OpenStreetMap tiles, initial center `[9.35, 100.0]`, zoom 9
- **Sidebar header**: title, description ("Koh Samui ↔ Phunphin — 4 legs · ~4-5h · ~1140 THB"), accent `#1a3a5c`
- **Leg filter tabs**: `All | Leg 1 | Leg 2 | Leg 3 | Leg 4` — active tab colored per leg
- **POI cards**: stop name, leg badge, mode icon, cost + duration, notes
- **Map markers**: emoji circles colored by leg
- **Polylines**: weight 4, opacity 0.8, colored by leg; road legs use OSRM geometry, ferry leg is straight
- **Interaction**: clicking a stop card flies the map to that stop and opens popup; leg tab hides/shows both the polyline and its endpoint markers

---

## 6. Generator script (`generate.py`)

1. Define stop coordinates and leg metadata as Python dicts
2. For legs 1, 3, 4: call OSRM `router.project-osrm.org/route/v1/driving/{lng1},{lat1};{lng2},{lat2}?overview=full&geometries=geojson` — extract `routes[0].geometry.coordinates`
3. For leg 2 (ferry): use 2-point straight line `[[nathon_lng, nathon_lat], [donsak_lng, donsak_lat]]`
4. Write `static/surat-thani-koh-samui-transit/locations.geojson`

No geocaching needed (all coordinates are hardcoded). Script is idempotent.

---

## 7. Hugo frontmatter

```yaml
title: "Koh Samui ↔ Surat Thani — Transit Route"
description: "4-leg transit map: Lamai → Nathon Pier → Donsak → Surat Thani bus station → Phunphin train station. ~1140 THB · ~4-5h."
emoji: "⛴️"
section: "travel"
accent_color: "#1a3a5c"
tags: ["🗺️ Route", "🇹🇭 Thailand", "⛴️ Ferry"]
```
