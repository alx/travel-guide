# Design: `scripts/` sub-project index with screenshots

**Date:** 2026-07-04
**Status:** Approved

## Goal

Make the root `README.md` a real entry point to the sub-projects under `scripts/`.
Every sub-project gets its own README, every sub-project with visual output gets a
screenshot, and the root README indexes them all.

## Current state

- `scripts/` contains 18 sub-directories plus 6 loose utility scripts.
- Only 7 sub-directories have a README: `711_samui`, `airbnb_env`, `ci`,
  `google_places_api`, `reddit`, `temple-walk`, `toulouse-distorama`.
- CI already generates 1200×630 map screenshots (`scripts/ci/generate-map-previews.js`)
  into `.github/previews/` and `public/images/map-previews/` for most map pages these
  scripts feed.

## Decisions

1. **Create READMEs for all 11 missing sub-directories** so every index entry has a
   working README link.
2. **Reuse CI previews as screenshots** — no new screenshot pipeline. Capture fresh
   ones (via the existing Playwright script against a local Hugo build) only for
   map-backed sub-projects with no existing preview.
3. **Screenshots live in `docs/screenshots/`**, one PNG per sub-project, named after
   the sub-project folder (e.g. `temple-walk.png`).
4. **Non-visual sub-projects get no image** — no placeholders, no terminal shots.
   They still appear in the index with description + README link.

## Deliverables

### 1. `docs/screenshots/`

One PNG per sub-project with visual output, copied from `.github/previews/` or
`public/images/map-previews/`, renamed to the sub-project slug:

| Sub-project | Source preview |
|---|---|
| `711_samui` | `711-samui.png` |
| `airbnb_env` | `airbnb-10349749.png` (representative listing map) |
| `airbnb_web` | `airbnb-10349749.png` (same source, copied as `airbnb_web.png`) |
| `bangkok-citywalk` | *capture fresh* (map exists at `static/bangkok-citywalk`) |
| `bangkok-raco` | `bangkok-raco.png` |
| `france_project_newsletter` | `france-grands-projets-strategiques.png` |
| `revente-tickets-fr` | *capture fresh if a map page exists, else skip* |
| `surat-thani-koh-samui-transit` | `surat-thani-koh-samui-transit.png` |
| `temple-walk` | `temple-walks.png` |
| `toulouse-distorama` | `toulouse-distorama.png` |
| `videoprotection_toulouse` | `videoprotection-toulouse.png` |
| `videosurveillance_france` | `videosurveillance-france.png` |
| `yoga_france` | `yoga-france.png` |
| `toulouse_burgers_lookup.py` (loose) | `toulouse-burgers.png` |
| `toulouse_mange_bien_lookup.py` (loose) | `toulouse-mange-bien.png` |

No image: `ci`, `hooks`, `google_places_api`, `raco`, `reddit` (unless a map page for
it is found during implementation).

### 2. Root README — "Sub-projects" section

Inserted after "Creating Maps with Claude Code":

- A table of all 19 sub-directories: thumbnail (HTML `<img width="200">` where a
  screenshot exists), name linking to `scripts/<name>/README.md`, one-line description.
- A second, smaller table for the loose utility scripts (`compact-geojson.py`,
  `fetch_all.sh`, `generate_map_index.py`, `geocode_france_projets.py`,
  `toulouse_burgers_lookup.py`, `toulouse_mange_bien_lookup.py`): description + link
  to the file. No individual READMEs for these — their docstrings document them.
  The two Toulouse lookups get thumbnails since previews exist.

### 3. Sub-project READMEs

Eleven new READMEs, short and uniform:

1. Screenshot at top (`../../docs/screenshots/<slug>.png`) if one exists.
2. What it does (one or two sentences, derived from the script docstrings).
3. How to run (commands from the docstrings; these are `uv run` single-file scripts
   or node scripts).
4. Inputs/outputs (data files read/written, e.g. `static/<map>/locations.geojson`).
5. Link to the live map page it powers, when applicable.

The 7 existing READMEs are only touched to add the screenshot image at the top;
their content is otherwise unchanged.

## Out of scope

- No automation keeping `docs/screenshots/` in sync with CI previews; a manual copy
  is acceptable. (If drift becomes a problem, a follow-up can extend the pre-push
  hook.)
- No changes to the CI preview pipeline itself.
- No READMEs for loose single-file scripts.

## Verification

- A one-off check that every relative link and image path referenced from the root
  README and each `scripts/*/README.md` resolves to an existing file.
- Visual check of the root README table rendering (thumbnail sizes, column layout).
