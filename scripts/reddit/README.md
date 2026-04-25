# scripts/reddit

Converts a Reddit submission's comments into an interactive GeoJSON map. Extracts location mentions via NLP (spaCy NER), geocodes them, generates Hugo layout/content files, and opens a GitHub PR.

## `reddit_to_map.py`

### Prerequisites

- `uv` installed
- `git` and `gh` (GitHub CLI) installed and authenticated
- spaCy English model (downloaded automatically on first run)
- `.env` file in the repo root (or pass `--env`):

```
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USER_AGENT=reddit-to-map/1.0 by u/your_username

# Only required with --reply:
REDDIT_USERNAME=your_username
REDDIT_PASSWORD=your_password

# Optional — improves geocoding reliability:
LOCATIONIQ_API_KEY=pk.your_key_here
```

Reddit API credentials: create a "script" app at https://www.reddit.com/prefs/apps.

### Usage

```bash
# Basic — fetch, geocode, and open a GitHub PR
uv run scripts/reddit/reddit_to_map.py \
  https://old.reddit.com/r/kohsamui/comments/1snhkv5/where_do_i_stay_in_koh_samui/

# With name override and geographic context to improve geocoding accuracy
uv run scripts/reddit/reddit_to_map.py <url> \
  --name "Koh Samui Accommodation Guide" \
  --context "Koh Samui Thailand" \
  --slug koh-samui-2024

# Preview GeoJSON without creating a PR
uv run scripts/reddit/reddit_to_map.py <url> --dry-run

# Also post a reply on the Reddit thread with the map link
uv run scripts/reddit/reddit_to_map.py <url> --reply
```

### Arguments

| Argument | Default | Description |
|---|---|---|
| `url` | required | Reddit submission URL |
| `--name` | derived from title | Human-readable map name (max 60 chars) |
| `--slug` | derived from title | URL slug (max 50 chars, URL-safe) |
| `--bbox S W N E` | none | Restrict geocoding to a bounding box |
| `--context` | empty | Text appended to geocode queries (e.g. `"Koh Samui Thailand"`) |
| `--min-score` | `1` | Minimum comment score to include |
| `--dry-run` | off | Print GeoJSON to stdout; skip git/PR |
| `--reply` | off | Post a reply on the Reddit thread with the map URL |
| `--site-url` | `https://maps.girard-davila.net` | Base URL for map links |
| `--env` | `.env` | Path to credentials file |
| `--repo` | parent dir | Path to travel-guide repo root |

### Outputs

**Without `--dry-run`:**
- `static/{slug}/locations.geojson` — GeoJSON FeatureCollection
- `layouts/{slug}/list.html` — Hugo layout with Leaflet map + sidebar
- `content/{slug}/_index.md` — Hugo front matter
- GitHub PR on branch `reddit-map/{slug}`

**Geocoding pipeline:** Photon (Komoot) → Nominatim → LocationIQ (if key provided). Each service has a 10s timeout; 0.5s delay between requests for rate limiting.
