# Temple Walk Photo Carousel — Design

**Date:** 2026-07-04
**Status:** approved

## Overview

When a temple walk POI has multiple Wikimedia photos, the sidebar overlay card shows a
Swiper.js carousel instead of a single static image. Each slide displays the photo and its
per-photo attribution caption. Cards with exactly one photo keep the current plain `<img>`
with no carousel controls.

---

## Data Pipeline (`scripts/temple-walk/lib.py`)

### `build_geojson` change

Replace the single `attribution` string property with an `attributions` array that mirrors
the `photos` array 1-to-1:

```python
# before
"attribution": entries[0]["attribution"] if entries else "",

# after
"attributions": [e["attribution"] for e in entries],
```

- `photos` and `attributions` are always the same length.
- When a stop has no photos, both are empty arrays.
- The old `attribution` field is removed from the GeoJSON output.

---

## Hugo Layout (`layouts/temple-walk/single.html`)

### Dependencies

Added to the `head` block:
```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swiper@11/swiper-bundle.min.css">
```

Added before the Leaflet `<script>` in the `scripts` block:
```html
<script src="https://cdn.jsdelivr.net/npm/swiper@11/swiper-bundle.min.js"></script>
```

### Card rendering logic

In the `poi-list` loop (JavaScript), the photo section is rendered conditionally:

- **0 photos**: nothing rendered (unchanged behaviour).
- **1 photo**: plain `<img class="poi-photo">` with no carousel markup (controls not rendered).
- **2+ photos**: a Swiper container structure:
  ```html
  <div class="swiper poi-swiper">
    <div class="swiper-wrapper">
      <div class="swiper-slide">
        <img class="poi-photo" src="..." alt="...">
        <div class="poi-photo-caption">© Artist / License</div>
      </div>
      <!-- one slide per photo -->
    </div>
    <div class="swiper-pagination"></div>
    <div class="swiper-button-prev"></div>
    <div class="swiper-button-next"></div>
  </div>
  ```

### Swiper initialisation

After all cards are appended to `#poi-list`, each `.poi-swiper` element is initialised.
Navigation and pagination selectors are scoped to the individual container element to avoid
cross-card conflicts:
```js
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
```

### Styles

Swiper arrow and dot colors are overridden to match the orange (#FF6B35) theme:
```css
.poi-swiper { width: 100%; margin-bottom: 0.5rem; }
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
```

The `.poi-photo` height remains 140px with `object-fit: cover`.

---

## Out of scope

- Multi-walk layout (`layouts/temple-walk-multi/single.html`) — no POI sidebar cards.
- Auto-play.
- Lightbox / fullscreen view on photo click.
- Backfilling attributions for existing cached GeoJSON files (re-run generate.py to refresh).
