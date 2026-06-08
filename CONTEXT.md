# Domain Glossary

## Core concepts

**Map**
An interactive Leaflet.js map rendered as a Hugo page, backed by a GeoJSON file at `static/{slug}/locations.geojson`. Each map has a slug, a content file at `content/{slug}/_index.md`, and optionally a custom layout at `layouts/{slug}/list.html`.

**Slug**
The URL-safe identifier for a map (e.g. `distorama`, `videoprotection-toulouse`). Used as the folder name under both `content/` and `static/`. Drives the public URL (`/{slug}/`).

**POI** (Point of Interest)
A single location rendered as a marker on a map. Each POI has a name, coordinates, category, and icon.

**Category**
A named group of POIs sharing an icon, color, and filter chip. Defined per-map. Categories drive both map markers and the filter UI.

**FA icon layer**
Font Awesome-based circular marker rendering (`makeIcon()`), as opposed to emoji icons stored in the GeoJSON `properties.icon` field. Defined in `layouts/partials/map-fa-icons.html` (shared partial). Maps that use FA icons include `CATEGORY_FA_ICONS` and `CATEGORY_COLORS` mappings in their layout template.

## Data pipeline

**Location list**
A structured input of named places with addresses (but not necessarily coordinates). The raw material for a map. May be a CSV file or scraped from a website.

**Data directory**
A per-map folder under `scripts/{slug}/` containing the source data (or scraper) and geocache for that map. Convention: `scripts/{slug}/scrape.py` or `scripts/{slug}/addresses.csv`.

**Geocoding**
Resolving a street address to lat/lon coordinates. Primary: OSM Nominatim (free, rate-limited to 1 req/s). Fallback: Google Geocoding API (requires `GOOGLE_API_KEY`).

**Geocache**
A local JSON file at `scripts/{slug}/.geocache.json` storing previously resolved address→coordinates mappings. Prevents redundant API calls on re-runs. Keyed by the raw address string.
