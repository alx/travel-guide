# scripts/google_places_api

Searches food & drink venues near a location via the Google Places API (New), collects recent reviews, and produces a local HTML map and GeoJSON. Optionally opens a GitHub PR to publish the map to the travel-guide site.

## `ingest_places.py`

Self-contained PEP 723 script — dependencies (`requests`, `python-dotenv`) are declared in the file header and installed automatically by `uv run --script`.

### Prerequisites

- `uv` installed
- Google Cloud project with the **Places API (New)** enabled
- `.env` file in the repo root (or pass `--env`):

```
GOOGLE_MAPS_API_KEY=AIza...
GITHUB_TOKEN=ghp_...        # only required with --pr
```

### Usage

```bash
# Default: Lamai, Koh Samui, last 7 days
uv run --script scripts/google_places_api/ingest_places.py

# Different location
uv run --script scripts/google_places_api/ingest_places.py \
  --location "Hoi An, Vietnam" --slug hoian \
  --lat 15.8801 --lon 108.3380 --radius 2000

# Wider review window + open a GitHub PR
uv run --script scripts/google_places_api/ingest_places.py --days 30 --pr
```

### Arguments

| Argument | Default | Description |
|---|---|---|
| `--location` | `Lamai, Koh Samui, Thailand` | Human-readable location name |
| `--slug` | `lamai` | URL path and output file prefix |
| `--lat` / `--lon` | `9.4775` / `100.0454` | Search center coordinates |
| `--radius` | `3000` | Search radius in metres |
| `--categories` | 12 cuisine types | Comma-separated venue categories |
| `--days` | `7` | Review window in days |
| `--output-dir` | `./output` | Directory for local output files |
| `--min-reviews` | `1` | Min recent reviews to include in PR GeoJSON |
| `--pr` | off | Build and open a GitHub PR |
| `--github-token` | `$GITHUB_TOKEN` | GitHub PAT with repo scope |
| `--env` | `.env` | Path to credentials file |

### Outputs

**Always:**
- `{output-dir}/{slug}-{date}_reviewed.geojson` — full review metadata per venue
- `{output-dir}/{slug}-{date}_map.html` — interactive Leaflet map with category/rating filters

**With `--pr`:**
- `static/{slug}/locations.geojson` — minimal PR-ready GeoJSON
- `content/{slug}/_index.md` — Hugo front matter
- `data/maps.json` updated with the new map entry
- GitHub PR opened against `alx/travel-guide`
