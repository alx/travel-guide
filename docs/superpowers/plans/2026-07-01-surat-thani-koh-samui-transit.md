# Surat Thani ↔ Koh Samui Transit Map — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an interactive Leaflet map at slug `surat-thani-koh-samui-transit` showing the 4-leg transit route between Lamai Beach (Koh Samui) and Phunphin train station (Surat Thani), with OSRM-routed polylines for road legs and a straight line for the ferry crossing.

**Architecture:** A Python generator script produces a GeoJSON file with 5 Point features (stops) and 4 LineString features (legs). A custom Hugo layout (adapted from the roadtrip pattern) loads the GeoJSON, renders polylines for LineStrings and emoji-circle markers for Points, with leg-filter tabs that control visibility of both.

**Tech Stack:** Python 3 + urllib (stdlib only), Hugo, Leaflet.js 1.9.4, OpenStreetMap tiles, OSRM public routing API.

## Global Constraints

- Run Python with `uv run`, never `python3` or `python`
- OSRM endpoint: `https://router.project-osrm.org/route/v1/driving/{lng1},{lat1};{lng2},{lat2}?overview=full&geometries=geojson`
- GeoJSON coordinates are `[lng, lat]` (GeoJSON spec), Leaflet uses `[lat, lng]` — convert in layout JS
- All 4 files are new; no existing file is modified
- Slug: `surat-thani-koh-samui-transit`

---

## File Map

| File | Status | Purpose |
|------|--------|---------|
| `scripts/surat-thani-koh-samui-transit/generate.py` | Create | Writes GeoJSON from hardcoded data + OSRM calls |
| `static/surat-thani-koh-samui-transit/locations.geojson` | Generated | Map data consumed by the layout |
| `content/surat-thani-koh-samui-transit/_index.md` | Create | Hugo page (frontmatter only) |
| `layouts/surat-thani-koh-samui-transit/list.html` | Create | Custom Leaflet layout |

---

### Task 1: Generator script + GeoJSON

**Files:**
- Create: `scripts/surat-thani-koh-samui-transit/generate.py`
- Produces: `static/surat-thani-koh-samui-transit/locations.geojson`

**Interfaces:**
- Produces: `locations.geojson` — FeatureCollection with `feature_type: "stop"` Points and `feature_type: "leg"` LineStrings

- [ ] **Step 1: Create the generator script**

Create `scripts/surat-thani-koh-samui-transit/generate.py` with this exact content:

```python
import json
import os
import urllib.request

STOPS = [
    {
        "name": "Lamai Beach",
        "coords": [100.0608, 9.4737],
        "category": "Beach",
        "icon": "🏖️",
        "leg": 1,
        "notes": "Taxi pickup — Arm Taxi, +66 62 978 3966, 600 THB fixed price.",
    },
    {
        "name": "Nathon Pier",
        "coords": [99.9267, 9.5352],
        "category": "Pier",
        "icon": "⛴️",
        "leg": 2,
        "notes": "Seatran Ferry — every hour. Preferred: 6pm boat (sunset ~6:30–7pm on the water).",
    },
    {
        "name": "Donsak Pier",
        "coords": [99.9573, 9.1903],
        "category": "Pier",
        "icon": "⛴️",
        "leg": 3,
        "notes": "Seatran arrival on mainland. Bus departs 8:15pm toward Surat Thani.",
    },
    {
        "name": "Surat Thani Bus Station",
        "coords": [99.3298, 9.1416],
        "category": "Bus",
        "icon": "🚌",
        "leg": 4,
        "notes": "Combined Seatran boat+bus ticket 380 THB. Journey ~70 min from Donsak.",
    },
    {
        "name": "Phunphin Train Station",
        "coords": [99.1935, 9.1106],
        "category": "Train",
        "icon": "🚂",
        "leg": 4,
        "notes": "Grab from bus station ~160 THB, ~20 min. Hotel: พุนพินสเตชั่น Phunphin Station.",
    },
]

LEGS = [
    {
        "leg": 1,
        "mode": "Taxi",
        "icon": "🚕",
        "color": "#f59e0b",
        "cost_thb": 600,
        "duration": "~30 min",
        "notes": "Arm Taxi — fixed price 600 THB. Call: +66 62 978 3966. Confirm in advance.",
        "routing": "osrm",
        "from_idx": 0,
        "to_idx": 1,
    },
    {
        "leg": 2,
        "mode": "Ferry",
        "icon": "⛴️",
        "color": "#0ea5e9",
        "cost_thb": 380,
        "duration": "~90 min",
        "notes": "Seatran Ferry — combined boat+bus ticket 380 THB. Every hour. Preferred: 6pm boat.",
        "routing": "straight",
        "from_idx": 1,
        "to_idx": 2,
    },
    {
        "leg": 3,
        "mode": "Bus",
        "icon": "🚌",
        "color": "#22c55e",
        "cost_thb": 0,
        "duration": "~70 min",
        "notes": "Included in Seatran combined ticket. Departs Donsak 8:15pm.",
        "routing": "osrm",
        "from_idx": 2,
        "to_idx": 3,
    },
    {
        "leg": 4,
        "mode": "Grab",
        "icon": "🚗",
        "color": "#8b5cf6",
        "cost_thb": 160,
        "duration": "~20 min",
        "notes": "Grab taxi from bus station to Phunphin train station.",
        "routing": "osrm",
        "from_idx": 3,
        "to_idx": 4,
    },
]


def osrm_route(start_coords, end_coords):
    """Fetch driving route from OSRM. Coords are [lng, lat]."""
    lng1, lat1 = start_coords
    lng2, lat2 = end_coords
    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{lng1},{lat1};{lng2},{lat2}"
        f"?overview=full&geometries=geojson"
    )
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read())
    return data["routes"][0]["geometry"]["coordinates"]


features = []

for i, stop in enumerate(STOPS):
    features.append({
        "type": "Feature",
        "id": f"surat-thani-koh-samui-transit-stop-{i + 1:02d}",
        "geometry": {"type": "Point", "coordinates": stop["coords"]},
        "properties": {
            "feature_type": "stop",
            "name": stop["name"],
            "category": stop["category"],
            "icon": stop["icon"],
            "leg": stop["leg"],
            "notes": stop["notes"],
        },
    })

for leg in LEGS:
    start = STOPS[leg["from_idx"]]["coords"]
    end = STOPS[leg["to_idx"]]["coords"]
    if leg["routing"] == "osrm":
        print(f"Leg {leg['leg']}: fetching OSRM route {STOPS[leg['from_idx']]['name']} → {STOPS[leg['to_idx']]['name']}…")
        coords = osrm_route(start, end)
    else:
        coords = [start, end]
    features.append({
        "type": "Feature",
        "id": f"surat-thani-koh-samui-transit-leg-{leg['leg']:02d}",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "feature_type": "leg",
            "leg": leg["leg"],
            "mode": leg["mode"],
            "icon": leg["icon"],
            "color": leg["color"],
            "cost_thb": leg["cost_thb"],
            "duration": leg["duration"],
            "notes": leg["notes"],
        },
    })

geojson = {"type": "FeatureCollection", "features": features}
out_path = "static/surat-thani-koh-samui-transit/locations.geojson"
os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(geojson, f, ensure_ascii=False, indent=2)

print(f"Wrote {len(features)} features ({len(STOPS)} stops + {len(LEGS)} legs) to {out_path}")
```

- [ ] **Step 2: Run the generator**

From the repo root:
```bash
uv run scripts/surat-thani-koh-samui-transit/generate.py
```

Expected output:
```
Leg 1: fetching OSRM route Lamai Beach → Nathon Pier…
Leg 3: fetching OSRM route Donsak Pier → Surat Thani Bus Station…
Leg 4: fetching OSRM route Surat Thani Bus Station → Phunphin Train Station…
Wrote 9 features (5 stops + 4 legs) to static/surat-thani-koh-samui-transit/locations.geojson
```

- [ ] **Step 3: Validate the output**

```bash
uv run - <<'EOF'
import json, sys
data = json.load(open("static/surat-thani-koh-samui-transit/locations.geojson"))
stops = [f for f in data["features"] if f["properties"]["feature_type"] == "stop"]
legs  = [f for f in data["features"] if f["properties"]["feature_type"] == "leg"]
assert len(stops) == 5, f"expected 5 stops, got {len(stops)}"
assert len(legs)  == 4, f"expected 4 legs, got {len(legs)}"
# Road legs must have >2 points (OSRM routing)
for leg_num in [1, 3, 4]:
    leg = next(l for l in legs if l["properties"]["leg"] == leg_num)
    n = len(leg["geometry"]["coordinates"])
    assert n > 2, f"leg {leg_num} expected >2 coords (road routing), got {n}"
# Ferry must be exactly 2 points
ferry = next(l for l in legs if l["properties"]["leg"] == 2)
assert len(ferry["geometry"]["coordinates"]) == 2, "ferry leg must have exactly 2 coords"
print("All assertions passed.")
EOF
```

Expected output: `All assertions passed.`

- [ ] **Step 4: Commit**

```bash
git add scripts/surat-thani-koh-samui-transit/generate.py static/surat-thani-koh-samui-transit/locations.geojson
git commit -m "feat(transit-map): add GeoJSON generator + locations for Samui↔Surat Thani route"
```

---

### Task 2: Hugo frontmatter

**Files:**
- Create: `content/surat-thani-koh-samui-transit/_index.md`

**Interfaces:**
- Consumes: nothing
- Produces: Hugo page at `/surat-thani-koh-samui-transit/` using layout `layouts/surat-thani-koh-samui-transit/list.html`

- [ ] **Step 1: Create the content file**

Create `content/surat-thani-koh-samui-transit/_index.md`:

```markdown
---
title: "Koh Samui ↔ Surat Thani — Transit Route"
description: "4-leg transit map: Lamai → Nathon Pier → Donsak → Surat Thani bus station → Phunphin train station. ~1 140 THB · ~4-5h."
emoji: "⛴️"
section: "travel"
accent_color: "#1a3a5c"
tags: ["🗺️ Route", "🇹🇭 Thailand", "⛴️ Ferry"]
---
```

- [ ] **Step 2: Commit**

```bash
git add content/surat-thani-koh-samui-transit/_index.md
git commit -m "feat(transit-map): add Hugo content page for Samui↔Surat Thani transit map"
```

---

### Task 3: Layout HTML + browser verification

**Files:**
- Create: `layouts/surat-thani-koh-samui-transit/list.html`

**Interfaces:**
- Consumes: `static/surat-thani-koh-samui-transit/locations.geojson` (GeoJSON FeatureCollection)
- Consumes: `LEG_COLORS` = `{1: '#f59e0b', 2: '#0ea5e9', 3: '#22c55e', 4: '#8b5cf6'}`
- Consumes: `LEG_STOPS` = `{1: [0,1], 2: [1,2], 3: [2,3], 4: [3,4]}` (stop array indices per leg)

- [ ] **Step 1: Create the layout file**

Create `layouts/surat-thani-koh-samui-transit/list.html`:

```html
{{ define "head" }}
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  body { overflow: hidden; }
  .site-header, footer { display: none !important; }
  main { max-width: 100% !important; padding: 0 !important; margin: 0 !important; }
  #map { position: fixed; inset: 0; z-index: 0; }

  .map-overlay {
    position: fixed; left: 1rem; top: 1rem; bottom: 1rem;
    width: 340px; z-index: 100;
    display: flex; flex-direction: column; gap: 0.5rem; pointer-events: none;
  }
  .map-overlay > * { pointer-events: all; }
  .overlay-card {
    background: rgba(255,255,255,0.95); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
    border-radius: 12px; padding: 0.85rem 1rem;
    box-shadow: 0 4px 20px rgba(0,0,0,0.13); border: 1px solid rgba(255,255,255,0.6);
  }
  .overlay-card.scrollable {
    flex: 1; min-height: 0; overflow-y: auto;
    display: flex; flex-direction: column; gap: 0.5rem;
  }
  .page-header h1 { font-size: 1.1rem; font-weight: 700; margin-bottom: 0.2rem; }
  .page-header p  { font-size: 0.8rem; color: #666; }
  .site-brand { font-size: 0.7rem; font-weight: 600; color: #1a3a5c; text-decoration: none; display: block; margin-bottom: 0.35rem; opacity: 0.65; }
  .site-brand:hover { opacity: 1; }

  .header-actions { display: flex; gap: 0.4rem; margin-top: 0.5rem; flex-wrap: wrap; }
  .header-btn { padding: 0.2rem 0.6rem; border-radius: 16px; border: 1.5px solid #ddd; background: white; cursor: pointer; font-size: 0.72rem; color: #555; transition: all 0.15s; }
  .header-btn:hover { border-color: #1a3a5c; color: #1a3a5c; }
  #list-toggle { display: none; }

  .leg-tabs { display: flex; gap: 0.35rem; flex-wrap: wrap; margin-bottom: 0.25rem; }
  .leg-tab {
    padding: 0.25rem 0.65rem; border-radius: 20px; border: 1.5px solid #ddd;
    background: white; cursor: pointer; font-size: 0.74rem; font-weight: 500; transition: all 0.15s;
  }

  .poi-card { background: white; border-radius: 10px; padding: 0.75rem 0.85rem; cursor: pointer; border: 2px solid transparent; transition: all 0.15s; flex-shrink: 0; }
  .poi-card:hover, .poi-card.active { border-color: #1a3a5c; }
  .poi-card h3 { font-size: 0.88rem; font-weight: 600; margin-bottom: 0.15rem; }
  .poi-card .poi-notes { font-size: 0.78rem; color: #555; line-height: 1.4; margin-top: 0.25rem; }
  .poi-card .poi-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.35rem; }
  .poi-card .poi-actions a { font-size: 0.72rem; color: #1a3a5c; text-decoration: none; font-weight: 500; }
  .cat-badge { display: inline-block; font-size: 0.68rem; padding: 0.12rem 0.45rem; border-radius: 20px; font-weight: 600; margin-bottom: 0.2rem; }
  .leg-badge { display: inline-block; font-size: 0.65rem; padding: 0.1rem 0.4rem; border-radius: 20px; font-weight: 700; margin-left: 0.3rem; color: white; vertical-align: middle; }

  @media (max-width: 768px) {
    #list-toggle { display: inline-block; }
    .map-overlay { left: 0; right: 0; bottom: 0; top: auto; width: 100%; flex-direction: column-reverse; gap: 0; }
    .overlay-card { border-radius: 0; }
    .overlay-card.scrollable { max-height: 0; overflow: hidden; padding: 0 !important; transition: max-height 0.3s ease, padding 0.15s ease; }
    .overlay-card.scrollable.mobile-open { max-height: 45vh; overflow-y: auto; padding: 0.85rem 1rem !important; margin-bottom: 0.5rem; }
  }
</style>
{{ end }}

{{ define "main" }}
<div id="map"></div>
<div class="map-overlay">
  <div class="overlay-card">
    <a href="{{ "/" | relURL }}" class="site-brand">🗺️ Maps</a>
    <div class="page-header">
      <h1>⛴️ Koh Samui ↔ Surat Thani</h1>
      <p>4 legs · ~4-5h · ~1 140 THB</p>
    </div>
    <div class="header-actions">
      <button class="header-btn" id="list-toggle"
        onclick="this.classList.toggle('active');document.querySelector('.overlay-card.scrollable').classList.toggle('mobile-open')">
        📍 Stops
      </button>
    </div>
  </div>
  <div class="overlay-card scrollable">
    <div class="leg-tabs" id="leg-tabs"></div>
    <div id="poi-list"></div>
  </div>
</div>
{{ end }}

{{ define "scripts" }}
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
// Leg number → hex color
const LEG_COLORS = { 1: '#f59e0b', 2: '#0ea5e9', 3: '#22c55e', 4: '#8b5cf6' };
// Leg number → [startStopIdx, endStopIdx] (0-based index into stops array, ordered by GeoJSON)
const LEG_STOPS = { 1: [0, 1], 2: [1, 2], 3: [2, 3], 4: [3, 4] };

let map, stops = [], legs = [], markers = {}, polylines = {}, activeCard = null, activeLeg = 'all';

map = L.map('map').setView([9.35, 100.0], 9);
window._leafletMap = map;
map.on('click', clearFocus);
map.on('dragstart', clearFocus);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '© OpenStreetMap contributors'
}).addTo(map);

function makeIcon(p) {
  const color = LEG_COLORS[p.leg] || '#1a3a5c';
  return L.divIcon({
    className: '',
    html: `<div style="background:${color};width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:16px;box-shadow:0 2px 6px rgba(0,0,0,0.3);border:2px solid white;">${p.icon}</div>`,
    iconSize: [32, 32], iconAnchor: [16, 16], popupAnchor: [0, -18]
  });
}

function focusStop(i) {
  const [lng, lat] = stops[i].geometry.coordinates;
  map.flyTo([lat, lng], 13, { duration: 0.7 });
  if (markers[i]) markers[i].openPopup();
  if (activeCard !== null) document.getElementById('stop-card-' + activeCard)?.classList.remove('active');
  activeCard = i;
  document.getElementById('stop-card-' + i)?.classList.add('active');
  document.getElementById('stop-card-' + i)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function clearFocus() {
  if (activeCard === null) return;
  document.getElementById('stop-card-' + activeCard)?.classList.remove('active');
  activeCard = null;
}

function renderSidebar() {
  const list = document.getElementById('poi-list');
  list.innerHTML = '';
  const indices = activeLeg === 'all'
    ? stops.map((_, i) => i)
    : (LEG_STOPS[activeLeg] || []);
  indices.forEach(i => {
    const p = stops[i].properties;
    const [lng, lat] = stops[i].geometry.coordinates;
    const color = LEG_COLORS[p.leg] || '#1a3a5c';
    const card = document.createElement('div');
    card.className = 'poi-card'; card.id = 'stop-card-' + i;
    card.innerHTML = `
      <span class="cat-badge" style="background:${color}22;color:${color};">${p.icon} ${p.category}</span>
      <span class="leg-badge" style="background:${color};">Leg ${p.leg}</span>
      <h3>${p.name}</h3>
      ${p.notes ? `<div class="poi-notes">${p.notes}</div>` : ''}
      <div class="poi-actions">
        <a href="geo:${lat},${lng}">🗺️ OsmAnd</a>
        <a href="https://www.google.com/maps/search/?api=1&query=${lat},${lng}" target="_blank">Google Maps ↗</a>
      </div>`;
    card.onclick = () => focusStop(i);
    list.appendChild(card);
  });
}

function filterLeg(leg) {
  activeLeg = leg;
  legs.forEach(f => {
    const poly = polylines[f.properties.leg];
    if (!poly) return;
    const show = activeLeg === 'all' || f.properties.leg === activeLeg;
    show ? (map.hasLayer(poly) || poly.addTo(map)) : map.removeLayer(poly);
  });
  stops.forEach((_, i) => {
    const m = markers[i];
    if (!m) return;
    const show = activeLeg === 'all' ||
      (LEG_STOPS[activeLeg] && LEG_STOPS[activeLeg].includes(i));
    show ? (map.hasLayer(m) || m.addTo(map)) : map.removeLayer(m);
  });
  renderSidebar();
  updateTabStyles();
}

function updateTabStyles() {
  const tabs = [...document.querySelectorAll('.leg-tab')];
  tabs.forEach(btn => { btn.style.background = ''; btn.style.color = ''; btn.style.borderColor = '#ddd'; });
  if (activeLeg === 'all') {
    tabs[0].style.background = '#1a3a5c'; tabs[0].style.color = 'white'; tabs[0].style.borderColor = '#1a3a5c';
  } else {
    const idx = Number(activeLeg); // tabs[0]=All, tabs[1]=Leg1, tabs[2]=Leg2, etc.
    if (tabs[idx]) {
      tabs[idx].style.background = LEG_COLORS[activeLeg];
      tabs[idx].style.color = 'white';
      tabs[idx].style.borderColor = LEG_COLORS[activeLeg];
    }
  }
}

function setupLegTabs() {
  const container = document.getElementById('leg-tabs');
  const allBtn = document.createElement('button');
  allBtn.className = 'leg-tab';
  allBtn.textContent = 'All';
  allBtn.onclick = () => filterLeg('all');
  container.appendChild(allBtn);
  legs.forEach(f => {
    const p = f.properties;
    const btn = document.createElement('button');
    btn.className = 'leg-tab';
    btn.textContent = `${p.icon} Leg ${p.leg}`;
    btn.onclick = () => filterLeg(p.leg);
    container.appendChild(btn);
  });
  updateTabStyles();
}

fetch('{{ "/surat-thani-koh-samui-transit/locations.geojson" | relURL }}')
  .then(r => r.json())
  .then(data => {
    stops = data.features
      .filter(f => f.geometry.type === 'Point')
      .sort((a, b) => a.properties.leg - b.properties.leg || 0);
    legs = data.features
      .filter(f => f.geometry.type === 'LineString')
      .sort((a, b) => a.properties.leg - b.properties.leg);

    // Render polylines
    legs.forEach(f => {
      const p = f.properties;
      const coords = f.geometry.coordinates.map(([lng, lat]) => [lat, lng]);
      const poly = L.polyline(coords, { color: p.color, weight: 4, opacity: 0.8 })
        .addTo(map)
        .bindPopup(`<b>${p.icon} ${p.mode}</b><br>${p.duration}${p.cost_thb ? ' · ' + p.cost_thb + ' THB' : ' (included)'}<br><em style="font-size:0.8rem;">${p.notes}</em>`);
      polylines[p.leg] = poly;
    });

    // Render stop markers
    stops.forEach((f, i) => {
      const p = f.properties;
      const [lng, lat] = f.geometry.coordinates;
      const color = LEG_COLORS[p.leg] || '#1a3a5c';
      const m = L.marker([lat, lng], { icon: makeIcon(p) })
        .addTo(map)
        .bindPopup(`<b>${p.name}</b><br><small style="color:${color};font-weight:600;">${p.icon} ${p.category}</small>${p.notes ? '<br><em style="font-size:0.8rem;">' + p.notes + '</em>' : ''}`);
      m.on('click', () => focusStop(i));
      markers[i] = m;
    });

    setupLegTabs();
    renderSidebar();

    // Fit to all stop markers
    const group = L.featureGroup(Object.values(markers));
    if (group.getLayers().length > 0) map.fitBounds(group.getBounds().pad(0.12));
  });
</script>
{{ end }}
```

- [ ] **Step 2: Start Hugo dev server and open the map**

```bash
hugo server -D
```

Open `http://localhost:1313/surat-thani-koh-samui-transit/` in a browser.

Verify visually:
1. Map shows Gulf of Thailand with Koh Samui on the right and mainland coast on the left
2. 4 colored polylines connect the stops: amber (taxi on Samui), blue (ferry across the gulf), green (bus on mainland), purple (Grab to station)
3. 5 emoji-circle markers appear at each stop in their leg color
4. Sidebar shows 5 stop cards with name, leg badge, notes, OsmAnd + Google Maps links
5. Clicking "Leg 2" tab shows only Nathon Pier + Donsak Pier markers and the blue ferry line; all others are hidden
6. Clicking "All" restores all markers and polylines
7. Clicking a stop card flies the map to that stop and highlights the card
8. Clicking a polyline on the map opens a popup with mode, duration, cost

- [ ] **Step 3: Commit**

```bash
git add layouts/surat-thani-koh-samui-transit/list.html
git commit -m "feat(transit-map): add Leaflet layout for Samui↔Surat Thani transit map"
```
