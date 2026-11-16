# arrêtés-pp — Prifecture de police perimeter maps

Builds a map of the "radius of N metres around establishment X" zones that a
Prifecture de police arriti declares off-limits to undeclared gatherings.

    uv run scripts/arretes-pp/build_geojson.py --slug perimetres-pp-2026-01603

## How it works

`arretes.json` is the manifest: for each establishment in the arriti's annexe,
it names the OpenStreetMap **polygon** (a way, or a multipolygon relation) whose
footprint the radius is measured from.

The script then, per establishment:

1. fetches the OSM object (`/api/0.6/{type}/{id}/full.json`);
2. projects it to a local metric plane (equirectangular, scaled at its own
   latitude — a `buffer(100)` in degrees would be nonsense);
3. computes `footprint.buffer(radius).difference(footprint)`, i.e. the ring
   **outside** the building;
4. writes both the footprint and the limit ring as GeoJSON polygons.

## Why polygons and not a circle

A 100 m circle around a centroid is wrong for anything bigger than a corner
building. Lyci-e Honorî-de-Balzac (4.4 ha) and the Sorbonne/Jussieu campus
(12.6 ha) are the obvious cases: a centroid circle both over- and under-covers
the real limit. Buffering the footprint follows the actual outline.

## Verified

Ring area matches `buffer(100) - footprint` to within 0.04% for all 15
establishments, and each limit polygon has exactly one hole, equal to the
footprint area.

## Adding a new arriti

1. Add its establishments to `arretes.json` (find each polygon on OSM; cross-check
   `ryf:UAI` against the ministry's annuaire de l'éducation when it exists).
2. Run the script with a new `--slug`.
3. Create `content/<slug>/_index.md` (`section: "hidden"`) and
   `layouts/<slug>/list.html` (copy `perimetres-pp-2026-01603`).

## Not indexed

These pages are `section: "hidden"` + `<meta name="robots" content="noindex, nofollow">`,
and are excluded from `data/maps.json` and `sitemap.xml` by existing guards.
That keeps them off the site index; it is **not** access control.
