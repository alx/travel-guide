# toulouse-distorama scripts

Data pipeline for the Distorama underground map of Toulouse.
Source: [distorama.neocities.org](https://distorama.neocities.org)

## Workflow

```
ingest.py → review.py → generate.py
```

### 1. Venue classification (one-time / on new unmatched venues)

```sh
uv run scripts/toulouse-distorama/classify.py
```

Reads `unmatched-venues.txt` (produced by `generate.py`) and lets you interactively assign each raw venue name to a known entry in `venues.csv`, or mark it as a non-match. Updates `venues.csv` and `venues_nomatch.csv` in place.

Run this whenever `generate.py` reports new unmatched venue names.

---

### 2. Media ingest

```sh
uv run scripts/toulouse-distorama/ingest.py
```

Fetches `events.json` from distorama.neocities.org, extracts artist names, then searches YouTube and Bandcamp for each artist not yet in `.mediacache.json`.

Results are written incrementally to `.mediacache.json` as each artist is processed.

**Environment variables** (set in `.env` at the repo root):

| Variable | Required | Description |
|---|---|---|
| `YOUTUBE_API_KEY` | No | YouTube Data API v3. Falls back to page scraping if absent. |
| `SERPAPI_API_KEY` | No | SerpAPI for Bandcamp search. Bandcamp enrichment is skipped if absent. |

---

### 3. Media review

```sh
uv run scripts/toulouse-distorama/review.py
# → http://localhost:5020
```

Browser UI for validating the YouTube and Bandcamp results found by `ingest.py`. Opens one artist at a time; only media approved here will appear on the maps.

**Keyboard shortcuts:**

| Key | Action |
|---|---|
| `1` | Approve YouTube |
| `2` | Reject YouTube |
| `4` | Approve Bandcamp |
| `5` | Reject Bandcamp |
| `← →` or `Space` | Navigate |

**Filter buttons** (top of page):

- **Pending** — artists with at least one unvalidated result (default)
- **Has media** — all artists with a YouTube ID or Bandcamp URL
- **All** — every entry in `.mediacache.json`

Rejected IDs/URLs are stored in `youtube_rejected_ids` / `bandcamp_rejected_urls` so `ingest.py` never re-proposes them.

---

### 4. GeoJSON generation

```sh
uv run scripts/toulouse-distorama/generate.py
uv run scripts/toulouse-distorama/generate.py --dry-run   # no writes
```

Fetches `events.json`, resolves venues, and writes all GeoJSON files and Hugo content stubs. Reads `.mediacache.json` but never modifies it — only **validated** media (approved in `review.py`) is embedded in the output.

**Outputs:**

| Path | Description |
|---|---|
| `static/toulouse-distorama/locations.geojson` | All venues (static map) |
| `static/toulouse-distorama/events/YYYY-MM-DD.geojson` | Per-day event maps |
| `static/toulouse-distorama/events/YYYY-MM.geojson` | Per-month rollups |
| `static/toulouse-distorama/events/this-week.geojson` | Current ISO week |
| `static/toulouse-distorama/events/next-week.geojson` | Next ISO week |
| `content/toulouse-distorama-*/` | Hugo content stubs |
| `scripts/toulouse-distorama/unmatched-venues.txt` | Venue names not found in `venues.csv` |

---

## Data files

| File | Description |
|---|---|
| `venues.csv` | Registry of known venues: name, address, category, logo, URL |
| `venues_nomatch.csv` | Venue names confirmed as non-matches (excluded from maps) |
| `unmatched-venues.txt` | Raw venue names from latest `events.json` not yet classified |
| `.geocache.json` | Address → coordinates cache (Nominatim) |
| `.mediacache.json` | Artist → YouTube/Bandcamp cache, including validation state |

## Typical update cycle

```sh
# Pull fresh events and enrich new artists
uv run scripts/toulouse-distorama/ingest.py

# Validate new media results in the browser
uv run scripts/toulouse-distorama/review.py

# Regenerate all GeoJSONs and Hugo stubs
uv run scripts/toulouse-distorama/generate.py

# If generate.py reported new unmatched venues, classify them
uv run scripts/toulouse-distorama/classify.py
```
