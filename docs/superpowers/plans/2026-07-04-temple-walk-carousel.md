# Temple Walk Photo Carousel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single static photo in each temple walk sidebar card with a Swiper.js carousel showing all available photos with per-photo attribution; cards with one photo keep the current plain `<img>`.

**Architecture:** Two independent changes — (1) `build_geojson` in `lib.py` replaces the single `attribution` string with an `attributions` array mirroring the `photos` array; (2) `layouts/temple-walk/single.html` loads Swiper@11 from CDN and renders a carousel for POIs with 2+ photos.

**Tech Stack:** Python 3 (stdlib only), Swiper.js v11 (CDN), Leaflet.js v1.9.4, Hugo

## Global Constraints

- Always run Python with `uv run` — never `python` or `python3`
- Swiper version: `swiper@11` from `https://cdn.jsdelivr.net/npm/swiper@11/`
- Orange accent color: `#FF6B35`
- No new Python dependencies
- Multi-walk layout (`layouts/temple-walk-multi/single.html`) is out of scope — do not touch it

---

## File Map

| File | Change |
|------|--------|
| `scripts/temple-walk/lib.py` | Replace `attribution` string with `attributions` array in `build_geojson` |
| `scripts/temple-walk/tests/test_generate.py` | Update `build_geojson` tests to assert `attributions` array |
| `layouts/temple-walk/single.html` | Add Swiper CDN, carousel render logic, Swiper init, carousel styles |

---

## Task 1: Update `build_geojson` to emit per-photo attributions array

**Files:**
- Modify: `scripts/temple-walk/lib.py` — `build_geojson` function (~line 181)
- Test: `scripts/temple-walk/tests/test_generate.py` — update three existing `build_geojson` tests

**Interfaces:**
- Produces: GeoJSON stop properties now include `"attributions": ["© A / CC", ...]` instead of `"attribution": "© A / CC"`. The `photos` and `attributions` arrays are always the same length. Both are `[]` when a stop has no photos.

- [ ] **Step 1: Update the failing tests**

In `scripts/temple-walk/tests/test_generate.py`, replace the three tests that reference `attribution`:

```python
def test_build_geojson_stop_properties():
    photos = {"Wat A": [{"url": "http://x/1.jpg", "attribution": "© Alice / CC"}]}
    fc = lib.build_geojson((0.0, 0.0), make_walk(), "testwalk", photos)
    stop = fc["features"][1]
    p = stop["properties"]
    assert p["name"] == "Wat A"
    assert p["order"] == 1
    assert p["slug"] == "wat-a"
    assert p["osm_id"] == "node/Wat A"
    assert p["photos"] == ["/temple-walks/testwalk/photos/wat-a-1.jpg"]
    assert p["attributions"] == ["© Alice / CC"]
    assert p["distance_km"] > 0


def test_build_geojson_stop_without_photos():
    fc = lib.build_geojson((0.0, 0.0), make_walk(), "testwalk", {})
    p = fc["features"][1]["properties"]
    assert p["photos"] == []
    assert p["attributions"] == []


def test_build_geojson_multi_photo_attributions():
    photos = {"Wat A": [
        {"url": "http://x/1.jpg", "attribution": "© Alice / CC"},
        {"url": "http://x/2.jpg", "attribution": "© Bob / CC-BY"},
    ]}
    fc = lib.build_geojson((0.0, 0.0), make_walk(), "testwalk", photos)
    p = fc["features"][1]["properties"]
    assert p["photos"] == [
        "/temple-walks/testwalk/photos/wat-a-1.jpg",
        "/temple-walks/testwalk/photos/wat-a-2.jpg",
    ]
    assert p["attributions"] == ["© Alice / CC", "© Bob / CC-BY"]
```

Note: `test_build_geojson_thai_name_photo_paths_use_osm_id` does not assert on attribution — leave it unchanged.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/alx/code/travel-guide
uv run pytest scripts/temple-walk/tests/test_generate.py -k "build_geojson" -v
```

Expected: `test_build_geojson_stop_properties` FAILS with `KeyError: 'attributions'` (or AssertionError on `attribution`). The new `test_build_geojson_multi_photo_attributions` also FAILS.

- [ ] **Step 3: Update `build_geojson` in `lib.py`**

In `scripts/temple-walk/lib.py`, find `build_geojson` (~line 181). Replace the stop feature properties block:

```python
# BEFORE (inside the for stop in walk["stops"]: loop):
        "properties": {
            "name": stop["name"],
            "order": stop["order"],
            "slug": tslug,
            "osm_id": stop["osm_id"],
            "distance_km": stop["distance_km"],
            "photos": [f"/temple-walks/{slug}/photos/{tslug}-{j+1}.jpg" for j in range(len(entries))],
            "attribution": entries[0]["attribution"] if entries else "",
        },

# AFTER:
        "properties": {
            "name": stop["name"],
            "order": stop["order"],
            "slug": tslug,
            "osm_id": stop["osm_id"],
            "distance_km": stop["distance_km"],
            "photos": [f"/temple-walks/{slug}/photos/{tslug}-{j+1}.jpg" for j in range(len(entries))],
            "attributions": [e["attribution"] for e in entries],
        },
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/alx/code/travel-guide
uv run pytest scripts/temple-walk/tests/test_generate.py -v
```

Expected: all tests PASS, including the three updated/new `build_geojson` tests.

- [ ] **Step 5: Run the full test suite**

```bash
cd /home/alx/code/travel-guide
uv run pytest scripts/temple-walk/tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/temple-walk/lib.py scripts/temple-walk/tests/test_generate.py
git commit -m "feat(temple-walk): store per-photo attributions array in GeoJSON"
```

---

## Task 2: Add Swiper photo carousel to the temple walk sidebar

**Files:**
- Modify: `layouts/temple-walk/single.html` — three sections: `head` (CDN links + CSS), `scripts` (Swiper CDN JS + carousel render logic + init)

**Interfaces:**
- Consumes: GeoJSON POI properties with `photos: string[]` and `attributions: string[]` (from Task 1). Falls back to `[]` for both if absent (handles pre-Task-1 cached GeoJSONs gracefully).

- [ ] **Step 1: Add Swiper CSS to the `head` block and carousel styles**

In `layouts/temple-walk/single.html`, find the `{{ define "head" }}` block. After the existing `{{ partial "map-poi-styles.html" . }}` line, add the Swiper stylesheet. Then inside the `<style>` block, append the carousel styles after the existing `.poi-photo` rule:

The full updated `head` block should look like this:

```html
{{ define "head" }}
{{ partial "map-poi-styles.html" . }}
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swiper@11/swiper-bundle.min.css">
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
.poi-swiper { width: 100%; margin-bottom: 0.5rem; border-radius: 6px; overflow: hidden; }
.poi-swiper .swiper-button-next,
.poi-swiper .swiper-button-prev { color: #FF6B35; }
.poi-swiper .swiper-pagination-bullet-active { background: #FF6B35; }
.poi-photo-caption {
  position: absolute; bottom: 0; left: 0; right: 0;
  background: rgba(0,0,0,0.55);
  color: #aaa; font-size: 0.65rem;
  padding: 0.2rem 0.4rem;
  border-radius: 0 0 6px 6px;
}
</style>
{{ end }}
```

- [ ] **Step 2: Add Swiper JS CDN and update card rendering + init in the `scripts` block**

Replace the entire `{{ define "scripts" }}` block with the following. Key changes:
- Swiper CDN `<script>` added before the Leaflet script
- `photoHtml(p)` helper function replaces the inline `photo` variable
- `.attributions` read from properties (falls back to `[]`)
- `attribution` in `.poi-meta` replaced with `attributions[0]` for single-photo case
- Swiper init loop added after the `points.forEach` card-building loop

```html
{{ define "scripts" }}
<script src="https://cdn.jsdelivr.net/npm/swiper@11/swiper-bundle.min.js"></script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
(function () {
  const GEOJSON_URL = '{{ .Params.geojson | relURL }}';
  const ROUTE_COLOR = '#FF6B35';
  const START_COLOR = '#2ecc71';
  const TILE_URL = 'https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png?api_key={{ os.Getenv "HUGO_STADIA_API_KEY" }}';

  const map = L.map('map', { maxZoom: 18 }).setView([13.736717, 100.523186], 13);
  L.tileLayer(TILE_URL, {
    attribution: '© <a href="https://stadiamaps.com/">Stadia Maps</a>, © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 18,
  }).addTo(map);

  function photoHtml(p) {
    const photos = p.photos || [];
    const attributions = p.attributions || [];
    if (photos.length === 0) return '';
    if (photos.length === 1) {
      return `<img class="poi-photo" src="${photos[0]}" alt="${p.name}" loading="lazy">`;
    }
    const slides = photos.map((src, i) => `
      <div class="swiper-slide" style="position:relative;">
        <img class="poi-photo" src="${src}" alt="${p.name}" loading="lazy">
        ${attributions[i] ? `<div class="poi-photo-caption">${attributions[i]}</div>` : ''}
      </div>`).join('');
    return `
      <div class="swiper poi-swiper">
        <div class="swiper-wrapper">${slides}</div>
        <div class="swiper-pagination"></div>
        <div class="swiper-button-prev"></div>
        <div class="swiper-button-next"></div>
      </div>`;
  }

  fetch(GEOJSON_URL)
    .then(r => r.json())
    .then(data => {
      const points = data.features
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

      // Draw markers: green "S" for the start (order 0), numbered orange for temples
      points.forEach(f => {
        const [lng, lat] = f.geometry.coordinates;
        const p = f.properties;
        const isStart = p.order === 0;
        const bg = isStart ? START_COLOR : ROUTE_COLOR;
        const label = isStart ? 'S' : p.order;
        const icon = L.divIcon({
          className: '',
          html: `<div style="width:28px;height:28px;border-radius:50%;background:${bg};color:#fff;font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,0.6);border:2px solid #fff;">${label}</div>`,
          iconSize: [28, 28],
          iconAnchor: [14, 14],
        });
        const marker = L.marker([lat, lng], { icon });
        marker.addTo(map);
        if (!isStart) marker.on('click', () => focusPOI(f));
      });

      // Render sidebar list (temples only, not the start point)
      const list = document.getElementById('poi-list');
      points.filter(f => f.properties.order > 0).forEach(f => {
        const p = f.properties;
        const attributions = p.attributions || [];
        const card = document.createElement('div');
        card.className = 'poi-card';
        card.id = `card-${p.order}`;
        card.style.cssText = 'border-radius:8px;padding:0.75rem;margin-bottom:0.4rem;';
        card.innerHTML = `
          ${photoHtml(p)}
          <div style="display:flex;align-items:center;">
            <span class="walk-badge">${p.order}</span>
            <h3>${p.name}</h3>
          </div>
          <div class="poi-meta">${p.distance_km} km from start${attributions[0] ? ` · ${attributions[0]}` : ''}</div>
        `;
        card.addEventListener('click', () => focusPOI(f));
        list.appendChild(card);
      });

      // Init Swiper on each multi-photo card (scoped selectors prevent cross-card conflicts)
      document.querySelectorAll('.poi-swiper').forEach(el => {
        new Swiper(el, {
          navigation: {
            nextEl: el.querySelector('.swiper-button-next'),
            prevEl: el.querySelector('.swiper-button-prev'),
          },
          pagination: { el: el.querySelector('.swiper-pagination'), clickable: true },
          loop: false,
        });
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

- [ ] **Step 3: Rebuild the Hugo site and start the dev server**

```bash
cd /home/alx/code/travel-guide
hugo server --disableFastRender
```

- [ ] **Step 4: Smoke test in the browser**

Open `http://localhost:1313/temple-walks/ccplace-temple-walk/` (or `rattanakosin`).

Verify:
- A POI with 2+ photos shows a Swiper carousel with orange prev/next arrows and orange active dot
- Clicking prev/next navigates photos; attribution caption updates per slide
- A POI with 1 photo shows a plain `<img>` with no arrows/dots
- Clicking a sidebar card still flies the map to the temple
- Clicking a map marker still highlights the sidebar card and scrolls to it

- [ ] **Step 5: Commit**

```bash
git add layouts/temple-walk/single.html
git commit -m "feat(temple-walk): add Swiper carousel for multi-photo POI cards"
```
