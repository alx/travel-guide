# scripts/ Sub-project Index with Screenshots — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the root README into an entry point for all 18 sub-projects under `scripts/`, each with its own README and, where visual output exists, a screenshot in `docs/screenshots/`.

**Architecture:** Docs-only change. Screenshots are copies of existing CI-generated previews (`.github/previews/`, `public/images/map-previews/`), plus two fresh captures via the existing Playwright script. Eleven new sub-project READMEs, screenshot images added atop the 7 existing ones, and a new "Sub-projects" section in the root README. Verified with a link/image-path checker.

**Tech Stack:** Markdown, bash, Hugo (local build), Playwright via `scripts/ci/generate-map-previews.js`.

**Spec:** `docs/superpowers/specs/2026-07-04-scripts-subproject-index-design.md`

## Global Constraints

- Screenshots live in `docs/screenshots/<subproject-slug>.png` — named exactly after the `scripts/` folder (or loose-script basename without `.py`), so `711_samui.png`, `temple-walk.png`, `toulouse_burgers_lookup.png`.
- Non-visual sub-projects (`ci`, `hooks`, `google_places_api`, `raco`, `reddit`) get **no** image and **no** placeholder.
- The 7 existing READMEs (`711_samui`, `airbnb_env`, `ci`, `google_places_api`, `reddit`, `temple-walk`, `toulouse-distorama`) are only touched to add a screenshot `<img>` after the H1 — content otherwise unchanged. `ci`, `google_places_api`, `reddit` have no screenshot, so they are not modified at all.
- Image reference from a sub-project README: `../../docs/screenshots/<slug>.png`. From the root README: `docs/screenshots/<slug>.png`.
- Live site base URL: `https://maps.girard-davila.net`.
- Loose utility scripts get root-README table entries only — no individual READMEs.
- Commit after each task with a `docs:` prefix message ending in `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Populate `docs/screenshots/` from existing previews

**Files:**
- Create: `docs/screenshots/*.png` (13 files, copied)

**Interfaces:**
- Produces: the 13 PNGs listed below, referenced by every later task.

- [ ] **Step 1: Copy and rename existing previews**

Run from the repo root:

```bash
mkdir -p docs/screenshots
cp .github/previews/711-samui.png                           docs/screenshots/711_samui.png
cp .github/previews/airbnb-10349749.png                     docs/screenshots/airbnb_env.png
cp .github/previews/airbnb-10349749.png                     docs/screenshots/airbnb_web.png
cp public/images/map-previews/bangkok-raco.png              docs/screenshots/bangkok-raco.png
cp .github/previews/france-grands-projets-strategiques.png  docs/screenshots/france_project_newsletter.png
cp .github/previews/surat-thani-koh-samui-transit.png       docs/screenshots/surat-thani-koh-samui-transit.png
cp .github/previews/temple-walks.png                        docs/screenshots/temple-walk.png
cp .github/previews/toulouse-distorama.png                  docs/screenshots/toulouse-distorama.png
cp .github/previews/videoprotection-toulouse.png            docs/screenshots/videoprotection_toulouse.png
cp .github/previews/videosurveillance-france.png            docs/screenshots/videosurveillance_france.png
cp .github/previews/yoga-france.png                         docs/screenshots/yoga_france.png
cp .github/previews/toulouse-burgers.png                    docs/screenshots/toulouse_burgers_lookup.png
cp .github/previews/toulouse-mange-bien.png                 docs/screenshots/toulouse_mange_bien_lookup.png
```

Note: `bangkok-raco.png` comes from `public/images/map-previews/` (gitignored Hugo output that exists locally). If it is missing, add `bangkok-raco-this-week` to the Task 2 capture list instead.

- [ ] **Step 2: Verify 13 files exist**

Run: `ls docs/screenshots/ | wc -l`
Expected: `13`

- [ ] **Step 3: Commit**

```bash
git add docs/screenshots/
git commit -m "docs(screenshots): add per-subproject screenshots from CI previews"
```

---

### Task 2: Capture missing previews (bangkok-citywalk, revente-tickets-fr)

**Files:**
- Create: `docs/screenshots/bangkok-citywalk.png`, `docs/screenshots/revente-tickets-fr.png`

**Interfaces:**
- Consumes: `scripts/ci/generate-map-previews.js` (existing; writes `.github/previews/<slug>.png`, needs a server on `PREVIEW_BASE_URL`, default `http://localhost:1414`).
- Produces: the two PNGs above, referenced by Tasks 4, 5, and 8.

- [ ] **Step 1: Build the site and start a local server**

```bash
hugo --minify
npx serve public -l 1414 &
sleep 2
curl -sf http://localhost:1414/bangkok-citywalk/ > /dev/null && echo OK
```

Expected: `OK`. If `npx playwright` has never run here: `npm install && npx playwright install chromium --with-deps` first.

- [ ] **Step 2: Capture the two screenshots**

```bash
node scripts/ci/generate-map-previews.js bangkok-citywalk revente-tickets-fr
```

Expected: exits 0, writes `.github/previews/bangkok-citywalk.png` and `.github/previews/revente-tickets-fr.png`. Then stop the server (`kill %1`).

If a capture fails after one retry, skip that sub-project's image (spec allows imageless entries) and remove its `<img>` from later tasks — do not block on it.

- [ ] **Step 3: Copy into docs/screenshots/**

```bash
cp .github/previews/bangkok-citywalk.png    docs/screenshots/bangkok-citywalk.png
cp .github/previews/revente-tickets-fr.png  docs/screenshots/revente-tickets-fr.png
```

- [ ] **Step 4: Commit**

```bash
git add docs/screenshots/ .github/previews/bangkok-citywalk.png .github/previews/revente-tickets-fr.png
git commit -m "docs(screenshots): capture bangkok-citywalk and revente-tickets-fr previews"
```

---

### Task 3: READMEs for the three OSM fetchers

**Files:**
- Create: `scripts/videoprotection_toulouse/README.md`, `scripts/videosurveillance_france/README.md`, `scripts/yoga_france/README.md`

- [ ] **Step 1: Write `scripts/videoprotection_toulouse/README.md`**

````markdown
# scripts/videoprotection_toulouse

<img src="../../docs/screenshots/videoprotection_toulouse.png" width="600" alt="Videoprotection Toulouse map preview">

Fetches surveillance cameras and speed radars in Toulouse from OpenStreetMap
(Overpass API) and outputs GeoJSON.

## Run

```bash
uv run scripts/videoprotection_toulouse/fetch_videoprotection_toulouse.py > static/videoprotection-toulouse/locations.geojson
```

No external dependencies (stdlib only). Also run automatically by
`scripts/fetch_all.sh`.

**Output:** `static/videoprotection-toulouse/locations.geojson`
**Live map:** https://maps.girard-davila.net/videoprotection-toulouse/
````

- [ ] **Step 2: Write `scripts/videosurveillance_france/README.md`**

````markdown
# scripts/videosurveillance_france

<img src="../../docs/screenshots/videosurveillance_france.png" width="600" alt="Videosurveillance France map preview">

Fetches surveillance cameras and speed radars across France from OpenStreetMap
(Overpass API) and outputs GeoJSON.

## Run

```bash
uv run scripts/videosurveillance_france/fetch_videosurveillance_france.py > static/videosurveillance-france/locations.geojson
```

No external dependencies (stdlib only). Also run automatically by
`scripts/fetch_all.sh`.

**Output:** `static/videosurveillance-france/locations.geojson`
**Live map:** https://maps.girard-davila.net/videosurveillance-france/
````

- [ ] **Step 3: Write `scripts/yoga_france/README.md`**

````markdown
# scripts/yoga_france

<img src="../../docs/screenshots/yoga_france.png" width="600" alt="Yoga France map preview">

Fetches all yoga places in France from OpenStreetMap (Overpass API) and
outputs GeoJSON.

## Run

```bash
uv run scripts/yoga_france/fetch_yoga_france.py > static/yoga-france/locations.geojson
```

No external dependencies (stdlib only). Also run automatically by
`scripts/fetch_all.sh`.

**Output:** `static/yoga-france/locations.geojson`
**Live map:** https://maps.girard-davila.net/yoga-france/
````

- [ ] **Step 4: Verify image paths resolve**

Run: `for f in videoprotection_toulouse videosurveillance_france yoga_france; do test -f docs/screenshots/$f.png && echo "$f OK"; done`
Expected: three `OK` lines.

- [ ] **Step 5: Commit**

```bash
git add scripts/videoprotection_toulouse/README.md scripts/videosurveillance_france/README.md scripts/yoga_france/README.md
git commit -m "docs(scripts): add READMEs for OSM fetcher subprojects"
```

---

### Task 4: READMEs for the Bangkok trio (bangkok-citywalk, bangkok-raco, raco)

**Files:**
- Create: `scripts/bangkok-citywalk/README.md`, `scripts/bangkok-raco/README.md`, `scripts/raco/README.md`

- [ ] **Step 1: Write `scripts/bangkok-citywalk/README.md`**

````markdown
# scripts/bangkok-citywalk

<img src="../../docs/screenshots/bangkok-citywalk.png" width="600" alt="Bangkok citywalk map preview">

Generates the Bangkok city-walk map: reads `venues.csv`, geocodes venues
(cached in `.geocache.json`), downloads photos from Wikimedia Commons, and
writes GeoJSON. A Remotion project (`remotion/`, `render-walk.js`) renders a
video version of the walk.

## Run

```bash
uv run scripts/bangkok-citywalk/generate.py            # full run
uv run scripts/bangkok-citywalk/generate.py --dry-run  # preview without writing
```

**Input:** `scripts/bangkok-citywalk/venues.csv`
**Output:** `static/bangkok-citywalk/` GeoJSON + photos
**Live map:** https://maps.girard-davila.net/bangkok-citywalk/
````

- [ ] **Step 2: Write `scripts/bangkok-raco/README.md`**

````markdown
# scripts/bangkok-raco

<img src="../../docs/screenshots/bangkok-raco.png" width="600" alt="Bangkok RA.co events map preview">

Pipeline for the Bangkok RA.co (Resident Advisor) weekly event maps.

## Scripts

- `generate.py` — builds this-week / next-week GeoJSON and Hugo content stubs
  from RA.co event data; auto-updates `venues.csv` and reports
  `unmatched-venues.txt`.
- `ingest.py` — fetches YouTube and SoundCloud media for event artists
  (needs `YOUTUBE_API_KEY` in `.env`, falls back to scraping).
- `geocode.py` — geocodes venues.
- `curation_web.py` — local Flask UI (http://localhost:5020/) to curate
  artists' SoundCloud tracks.

## Run

```bash
uv run scripts/bangkok-raco/generate.py [--dry-run]
uv run scripts/bangkok-raco/ingest.py [--dry-run]
uv run scripts/bangkok-raco/curation_web.py
```

**Outputs:** `static/bangkok-raco/events/{this,next}-week.geojson`,
`content/bangkok-raco-{this,next}-week/_index.md`
**Live maps:** https://maps.girard-davila.net/bangkok-raco-this-week/ ·
https://maps.girard-davila.net/bangkok-raco-next-week/
**Upstream data:** produced by [`scripts/raco`](../raco/README.md).
````

- [ ] **Step 3: Write `scripts/raco/README.md`**

````markdown
# scripts/raco

Raw RA.co (Resident Advisor) data collection. No visual output — feeds
[`scripts/bangkok-raco`](../bangkok-raco/README.md).

## Scripts

- `fetch_ra_events.js` — GraphQL area scanner: scans a range of RA area IDs,
  fetches all events for active areas, saves per-area JSON under `data/`.
  Confirmed event fields come from `.field-probe-state.json` (regenerate with
  `--probe`).
- `event_listener.js` — Playwright network sniffer (non-headless) that watches
  ra.co traffic for a target string; used to discover API fields.

## Run

```bash
node scripts/raco/fetch_ra_events.js           # scan areas, save JSON
node scripts/raco/fetch_ra_events.js --probe   # regenerate field probe state
node scripts/raco/event_listener.js            # interactive API exploration
```

**Output:** `scripts/raco/data/*.json`, `scripts/raco/schema-summary.json`
````

- [ ] **Step 4: Commit**

```bash
git add scripts/bangkok-citywalk/README.md scripts/bangkok-raco/README.md scripts/raco/README.md
git commit -m "docs(scripts): add READMEs for bangkok-citywalk, bangkok-raco, raco"
```

---

### Task 5: READMEs for revente-tickets-fr and surat-thani-koh-samui-transit

**Files:**
- Create: `scripts/revente-tickets-fr/README.md`, `scripts/surat-thani-koh-samui-transit/README.md`

- [ ] **Step 1: Write `scripts/revente-tickets-fr/README.md`**

````markdown
# scripts/revente-tickets-fr

<img src="../../docs/screenshots/revente-tickets-fr.png" width="600" alt="Revente tickets FR map preview">

Ticket-resale tracker for r/ReventeTicketsFR: fetches VENTE posts from Reddit,
notifies via Telegram, tracks sold signals, and publishes confirmed listings
to a map.

## Scripts

- `init_db.py` — creates the SQLite state DB (`data/revente-tickets-fr/state.db`).
- `fetch_posts.py` — cron job: pulls new posts via Reddit API (praw), sends
  Telegram notifications, polls listings for sold signals.
- `telegram_bot.py` — persistent bot service: confirm/reject listings from chat.
- `generate.py` — rebuilds the map GeoJSON from SQLite state (also called by
  the bot after each confirmation).

## Run

```bash
uv run scripts/revente-tickets-fr/init_db.py       # once
uv run scripts/revente-tickets-fr/fetch_posts.py   # cron (daily)
uv run scripts/revente-tickets-fr/telegram_bot.py  # long-running service
uv run scripts/revente-tickets-fr/generate.py      # rebuild GeoJSON manually
```

Requires in `.env`: `REVENTE_BOT_TOKEN`, `REVENTE_CHAT_ID`,
`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`,
optional `YOUTUBE_API_KEY`.

**Output:** `static/revente-tickets-fr/` GeoJSON
**Live map:** https://maps.girard-davila.net/revente-tickets-fr/
````

- [ ] **Step 2: Write `scripts/surat-thani-koh-samui-transit/README.md`**

````markdown
# scripts/surat-thani-koh-samui-transit

<img src="../../docs/screenshots/surat-thani-koh-samui-transit.png" width="600" alt="Surat Thani – Koh Samui transit map preview">

Generates the Surat Thani ↔ Koh Samui transit itinerary map from a hardcoded
list of stops (taxi pickup, ferry piers, train station) with per-leg notes,
contacts, and booking links.

## Run

```bash
uv run scripts/surat-thani-koh-samui-transit/generate.py
```

No external dependencies (stdlib only). Edit the `STOPS` list in
`generate.py` to change the itinerary, then re-run.

**Output:** `static/surat-thani-koh-samui-transit/locations.geojson`
**Live map:** https://maps.girard-davila.net/surat-thani-koh-samui-transit/
````

- [ ] **Step 3: Commit**

```bash
git add scripts/revente-tickets-fr/README.md scripts/surat-thani-koh-samui-transit/README.md
git commit -m "docs(scripts): add READMEs for revente-tickets-fr and transit map"
```

---

### Task 6: READMEs for airbnb_web, france_project_newsletter, hooks

**Files:**
- Create: `scripts/airbnb_web/README.md`, `scripts/france_project_newsletter/README.md`, `scripts/hooks/README.md`

- [ ] **Step 1: Write `scripts/airbnb_web/README.md`**

````markdown
# scripts/airbnb_web

<img src="../../docs/screenshots/airbnb_web.png" width="600" alt="Airbnb neighbourhood map preview">

Flask web app that builds an Airbnb neighbourhood map through a wizard: paste
a listing URL, it scrapes the location, finds nearby family-friendly POIs
(Overpass), and generates the map page. Web-app sibling of the
[`scripts/airbnb_env`](../airbnb_env/README.md) CLI.

## Run

```bash
uv run scripts/airbnb_web/app.py                     # dev server

uv run gunicorn -k gthread --threads 4 --workers 1 \
    --bind 127.0.0.1:5010 'app:create_app()'         # prod
```

**Modules:** `app.py` (Flask app factory), `routes/wizard.py` (wizard flow),
`poi_engine.py` (Overpass POI search), `cache.py` + `cache/` (listing cache).

**Output:** `static/airbnb/<listing-id>/` map pages
**Live example:** https://maps.girard-davila.net/airbnb/
````

- [ ] **Step 2: Write `scripts/france_project_newsletter/README.md`**

````markdown
# scripts/france_project_newsletter

<img src="../../docs/screenshots/france_project_newsletter.png" width="600" alt="France grands projets map preview">

Newsletter pipeline monitoring France's strategic industrial projects
(France 2030). Discovers news sources per company, classifies items with a
local LLM, and sends digests — while enriching the
[grands projets map](https://maps.girard-davila.net/france-grands-projets-strategiques/).

## Pipeline

1. `init_db.py` — create the SQLite DB.
2. `enrich_france2030.py`, `enrich_pappers.py`, `enrich_web.py`,
   `enrich_geojson.py` — enrich company/project metadata and the map GeoJSON.
3. `discover_sources.py --type {bodacc_rss,google_news_query_rss,youtube_channel_rss,linkedin_company_rss,all}`
   — propose RSS sources, reviewed via the Telegram bot before activation.
4. `fetch_digest.py` → `classify.py` — fetch feed items, then LLM relevance
   filter + signal classifier (llama.cpp server, see `config.toml`; degrades
   gracefully if unreachable).
5. `extract_finance.py`, `send_mailchimp.py` — extract financial signals,
   send the newsletter.
6. `telegram_bot.py` — human review loop; `run_pipeline.sh` — orchestrates a
   full run (see `systemd/` for service units, `docker/rsshub.yml` for the
   RSS bridge).

## Configuration

`config.toml` — LLM server URL, changedetection.io instance, etc.
Infra: llama.cpp on `fami`, changedetection.io + cron on `lamai270`.

**Output:** `static/france-grands-projets-strategiques/locations.geojson`
enrichment + Mailchimp newsletter
**Live map:** https://maps.girard-davila.net/france-grands-projets-strategiques/
````

- [ ] **Step 3: Write `scripts/hooks/README.md`**

````markdown
# scripts/hooks

Git hooks for this repository. No visual output.

## `pre-push`

Ensures og:image previews exist for all maps whose GeoJSON changed in the
pushed range: builds the site with Hugo (port 1414), screenshots missing
previews via `scripts/ci/generate-map-previews.js`, and blocks the push if
previews can't be produced.

## Install

```bash
cp scripts/hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push
```
````

- [ ] **Step 4: Commit**

```bash
git add scripts/airbnb_web/README.md scripts/france_project_newsletter/README.md scripts/hooks/README.md
git commit -m "docs(scripts): add READMEs for airbnb_web, france_project_newsletter, hooks"
```

---

### Task 7: Add screenshots atop the existing READMEs that have one

**Files:**
- Modify: `scripts/711_samui/README.md`, `scripts/airbnb_env/README.md`, `scripts/temple-walk/README.md`, `scripts/toulouse-distorama/README.md`

(`ci`, `google_places_api`, `reddit` have no screenshot → untouched.)

- [ ] **Step 1: Insert the image line after each H1**

In each file, immediately after the first line (`# …` heading) insert a blank
line and the image tag. Only this line differs per file:

- `scripts/711_samui/README.md`:
  `<img src="../../docs/screenshots/711_samui.png" width="600" alt="7-Eleven Koh Samui map preview">`
- `scripts/airbnb_env/README.md`:
  `<img src="../../docs/screenshots/airbnb_env.png" width="600" alt="Airbnb nearby POIs map preview">`
- `scripts/temple-walk/README.md`:
  `<img src="../../docs/screenshots/temple-walk.png" width="600" alt="Temple walk map preview">`
- `scripts/toulouse-distorama/README.md`:
  `<img src="../../docs/screenshots/toulouse-distorama.png" width="600" alt="Toulouse Distorama map preview">`

Resulting top of each file (example for 711_samui):

````markdown
# scripts/711_samui

<img src="../../docs/screenshots/711_samui.png" width="600" alt="7-Eleven Koh Samui map preview">

Fetches all 7-Eleven stores on Koh Samui from the Google Places API (New) and outputs GeoJSON.
````

- [ ] **Step 2: Verify no other lines changed**

Run: `git diff --stat scripts/*/README.md`
Expected: exactly 4 files, `+2` lines each, `-0`.

- [ ] **Step 3: Commit**

```bash
git add scripts/711_samui/README.md scripts/airbnb_env/README.md scripts/temple-walk/README.md scripts/toulouse-distorama/README.md
git commit -m "docs(scripts): add screenshots to existing subproject READMEs"
```

---

### Task 8: Root README "Sub-projects" section

**Files:**
- Modify: `README.md` (insert new section between "Creating Maps with Claude Code" and "Development")

- [ ] **Step 1: Insert the section**

Insert the following between the "Creating Maps with Claude Code" section and
the "## Development" heading:

````markdown
## Sub-projects

Data pipelines and tools under [`scripts/`](scripts/). Each links to its own README.

| Preview | Project | Description |
|---|---|---|
| <img src="docs/screenshots/711_samui.png" width="200"> | [711_samui](scripts/711_samui/README.md) | 7-Eleven stores on Koh Samui via Google Places API → GeoJSON |
| <img src="docs/screenshots/airbnb_env.png" width="200"> | [airbnb_env](scripts/airbnb_env/README.md) | CLI: family-friendly POIs near an Airbnb listing |
| <img src="docs/screenshots/airbnb_web.png" width="200"> | [airbnb_web](scripts/airbnb_web/README.md) | Flask wizard that turns an Airbnb listing into a neighbourhood map |
| <img src="docs/screenshots/bangkok-citywalk.png" width="200"> | [bangkok-citywalk](scripts/bangkok-citywalk/README.md) | Bangkok city-walk map with Wikimedia photos + Remotion video |
| <img src="docs/screenshots/bangkok-raco.png" width="200"> | [bangkok-raco](scripts/bangkok-raco/README.md) | Weekly Bangkok RA.co event maps with artist media curation |
| | [ci](scripts/ci/README.md) | GeoJSON validation + map preview screenshots for CI |
| <img src="docs/screenshots/france_project_newsletter.png" width="200"> | [france_project_newsletter](scripts/france_project_newsletter/README.md) | Newsletter pipeline monitoring France's strategic industrial projects |
| | [google_places_api](scripts/google_places_api/README.md) | Food & drink venue ingester → HTML map, GeoJSON, optional PR |
| | [hooks](scripts/hooks/README.md) | Git pre-push hook ensuring og:image previews exist |
| | [raco](scripts/raco/README.md) | RA.co GraphQL area scanner (raw event data) |
| | [reddit](scripts/reddit/README.md) | Reddit thread → GeoJSON map via NER geocoding |
| <img src="docs/screenshots/revente-tickets-fr.png" width="200"> | [revente-tickets-fr](scripts/revente-tickets-fr/README.md) | r/ReventeTicketsFR ticket-resale tracker: Reddit → Telegram → map |
| <img src="docs/screenshots/surat-thani-koh-samui-transit.png" width="200"> | [surat-thani-koh-samui-transit](scripts/surat-thani-koh-samui-transit/README.md) | Surat Thani ↔ Koh Samui transit itinerary map |
| <img src="docs/screenshots/temple-walk.png" width="200"> | [temple-walk](scripts/temple-walk/README.md) | Self-guided temple walking tours (Overpass + OSRM + Wikimedia) |
| <img src="docs/screenshots/toulouse-distorama.png" width="200"> | [toulouse-distorama](scripts/toulouse-distorama/README.md) | Toulouse underground events pipeline with rendered videos |
| <img src="docs/screenshots/videoprotection_toulouse.png" width="200"> | [videoprotection_toulouse](scripts/videoprotection_toulouse/README.md) | Toulouse CCTV & radar map from OpenStreetMap |
| <img src="docs/screenshots/videosurveillance_france.png" width="200"> | [videosurveillance_france](scripts/videosurveillance_france/README.md) | France-wide CCTV & radar map from OpenStreetMap |
| <img src="docs/screenshots/yoga_france.png" width="200"> | [yoga_france](scripts/yoga_france/README.md) | Yoga places in France from OpenStreetMap |

### Utility scripts

| Preview | Script | Description |
|---|---|---|
| | [compact-geojson.py](scripts/compact-geojson.py) | Converts `locations.geojson` to compact columnar `locations.min.json` |
| | [fetch_all.sh](scripts/fetch_all.sh) | Refreshes all OSM-sourced datasets, then rebuilds the map index |
| | [generate_map_index.py](scripts/generate_map_index.py) | Scans content + GeoJSON stats into `data/maps.json` for the homepage |
| | [geocode_france_projets.py](scripts/geocode_france_projets.py) | Geocodes France strategic industrial projects CSV → GeoJSON |
| <img src="docs/screenshots/toulouse_burgers_lookup.png" width="200"> | [toulouse_burgers_lookup.py](scripts/toulouse_burgers_lookup.py) | Google Places lookup → [toulouse-burgers](https://maps.girard-davila.net/toulouse-burgers/) map |
| <img src="docs/screenshots/toulouse_mange_bien_lookup.png" width="200"> | [toulouse_mange_bien_lookup.py](scripts/toulouse_mange_bien_lookup.py) | Google Places lookup → [toulouse-mange-bien](https://maps.girard-davila.net/toulouse-mange-bien/) map |
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): add sub-projects index with screenshots"
```

---

### Task 9: Verify all links and image paths

**Files:**
- None created (throwaway check script in `$CLAUDE_JOB_DIR/tmp` or a heredoc).

- [ ] **Step 1: Run the link/image checker**

```bash
python3 - <<'EOF'
import re, sys
from pathlib import Path

root = Path('.')
files = [Path('README.md')] + sorted(root.glob('scripts/*/README.md'))
errors = []
for f in files:
    text = f.read_text(encoding='utf-8')
    refs  = re.findall(r'\]\(([^)#]+)\)', text)          # markdown links
    refs += re.findall(r'src="([^"]+)"', text)            # img tags
    for ref in refs:
        if ref.startswith(('http://', 'https://', 'mailto:')):
            continue
        target = (f.parent / ref).resolve()
        if not target.exists():
            errors.append(f'{f}: broken ref {ref}')
for e in errors:
    print(e)
sys.exit(1 if errors else 0)
EOF
echo "exit: $?"
```

Expected: no output, `exit: 0`.

- [ ] **Step 2: Fix any broken refs found, re-run until clean**

- [ ] **Step 3: Verify only intended files changed, then final check of git log**

Run: `git status --short` (expect clean or only pre-existing unrelated changes: `scripts/raco/fetch_ra_events.js`, `scripts/raco/data/`, `scripts/raco/schema-summary.json`) and `git log --oneline -8` (expect the commits from Tasks 1–8).

---

## Notes for the implementer

- The spec's counts say "19 sub-directories / 12 new READMEs"; the accurate
  numbers are 18 sub-directories and 11 new READMEs (this plan is the source
  of truth for the lists).
- `git status` already shows unrelated dirty files under `scripts/raco/` —
  leave them alone; never `git add -A`.
