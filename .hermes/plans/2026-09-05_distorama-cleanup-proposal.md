# DistoramaMaps Cleanup Implementation Plan (RESUMABLE)

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Restore the full calendar on the DistoramaMaps site, replace the "first YouTube hit + manual review" curation with scored candidate ranking (conservative auto-validate), dedupe venue matching, and fix repo hygiene.

**Architecture:** All changes stay inside `scripts/toulouse-distorama/` + `layouts/toulouse-distorama*/` + `.github/workflows/distorama-update.yml`. The cron `distorama-update.yml` (05:00/17:00 UTC) runs `generate.py` unattended, so **everything generate.py writes must be deterministic and self-contained** — month pages must be produced by generate.py from the feed alone, no manual steps in CI.

**Tech Stack:** Python 3.11+ (uv run), stdlib urllib only (no new deps), Hugo 0.147, Flask (review UI), Leaflet.

---

## User decisions (from clarification, 2026-09-05)

1. **Calendar depth = FULL FEED WINDOW**: rolling month pages for every month that has upcoming dates on the live feed (currently through 2026-09-30; grows as the site publishes further). No per-day pages (month page with day-grouped list is enough — layout already renders a day grid).
2. **Video curation = CONSERVATIVE**: auto-validate only when top candidate is clearly a live/performance video AND beats runner-up by a large margin; everything else goes to review.py. Show score + top-3 candidates in review.py to speed eyeballing.

## Measured baseline (2026-09-05, pre-change)

- Live feed `https://distorama.neocities.org/events.json`: 294 date entries, 943 events, 2025-09-03 → 2026-09-30. Feed is HEALTHY — the "stale" perception comes from the UI, not the data.
- `distorama-update.yml` cron ran fine (GeoJSON regen commits through 05:14 UTC today); "no changes" is correct for the 2-week rolling files.
- **Agenda page is a husk**: live `/toulouse-distorama/agenda/` shows only "Cette semaine" + "Semaine prochaine" cards + a hardcoded `09/2026` nav link that **404s** (verified with curl). The layout `layouts/toulouse-distorama-agenda/list.html` is fully built for month sections + per-day pages, but `generate.py` was stripped down to only emit `this-week.geojson` / `next-week.geojson`.
- `.gitignore` still has the vestigial patterns proving per-day/per-month outputs once existed: `static/toulouse-distorama/events/????-??-??.geojson` and `content/toulouse-distorama-????-??-??/`.
- Media cache: 880 artists | 595 with youtube id | **only 421 validated (48%)** | 58 bandcamp found | 37 bandcamp validated | 243 rejected yt ids.
- **0/8 events in the next two weeks have any media** (all youtube_video_id/bandcamp fields empty in both weekly GeoJSONs).
- `generate.py` run (done 2026-09-05): 108 venues loaded, 819/943 events resolved, **82 unmatched venue names** (see `unmatched-venues.txt`). Duplicates: `L'Impasse`/`L’Impasse`, `La Halle (Rabastens)`/`La Halle [Rabastens]`; typos like `Le Bièreographe`/`Le Biérographe` in CSV; top gaps next 60d: Terreblanque (4), Breughel L'Ancien (4), Dream O'Clock (3).
- Hygiene: `distorama-slideshow.mp4` (29MB, Jun 10) is **tracked in git** although `.gitignore` has `*.mp4`; `static/toulouse-distorama/slideshows/` = 1.2GB on disk (gitignored, fine); `venues.csv` = 108 rows.
- Repo was pulled to `3d143fb` on main; working tree has unrelated modified/untracked files (temple-walks preview, bangkok-raco mediacache) — **do not commit those**.

## Files likely to change

| File | Change |
|---|---|
| `scripts/toulouse-distorama/generate.py` | A: emit per-month GeoJSON + month Hugo stubs; C: aliases column in venues.csv |
| `scripts/toulouse-distorama/ingest.py` | B1: scored YouTube candidates (top-N + scoring + conservative auto-validate); B2: re-search rejected artists |
| `scripts/toulouse-distorama/review.py` | B1: show score + top-3 candidates |
| `scripts/toulouse-distorama/venues.csv` | C: add `aliases` column; dedupe rows |
| `scripts/toulouse-distorama/classify.py` | C: normalize curly apostrophes/brackets |
| `layouts/toulouse-distorama-agenda/list.html` | A: month link uses `relURL` of generated month page; drop broken hardcoded `09/2026` behavior if no month pages exist |
| `.github/workflows/distorama-update.yml` | A: stage month outputs (`git add static/toulouse-distorama/events/*.geojson`, month content dirs) |
| `git` | D: `git rm --cached distorama-slideshow.mp4` (keep on disk) |
| `scripts/toulouse-distorama/README.md` | Docs for new behavior |

---

## Task 1 (A) — Per-month GeoJSON + month pages in generate.py

**Objective:** Every month on the feed with upcoming events gets `static/toulouse-distorama/events/YYYY-MM.geojson` and `content/toulouse-distorama-YYYY-MM/_index.md` (type `toulouse-distorama-event`, `distorama_window: "YYYY-MM"`, `geojson_url: "/toulouse-distorama/events/YYYY-MM.geojson"`). This activates the existing month sections in the agenda layout and fixes the 404 month link.

**Files:**
- Modify: `scripts/toulouse-distorama/generate.py` (main() section 7, lines ~444-471, and section 10 stubs, lines ~473-519)
- Modify: `.github/workflows/distorama-update.yml` (commit step, lines ~37-43)
- Modify: `scripts/toulouse-distorama/README.md` (outputs table)

Implementation notes:
- Reuse the existing `make_event_feature()` + `write_geojson()`; build a `month_venues: dict[month_str, dict[venue_name, events]]` in the same loop as weekly files.
- Month GeoJSON features = all events in that month grouped per venue (same shape as weekly files → the event layout JS already filters past dates client-side, and month pages will naturally show full-month cards).
- Month stub `_index.md`: `title: "DistoraMaps"`, description in French like the weekly ones (`"Concerts et événements underground à Toulouse septembre 2026"`), `type: "toulouse-distorama-event"`, `distorama_window: "2026-09"`, `geojson_url: "/toulouse-distorama/events/2026-09.geojson"`.
- Event layout check: `layouts/toulouse-distorama-event/list.html` computes `$isDay := eq (len .Params.distorama_window) 10` — a 7-char window shows the "Ce soir" button; that's acceptable for months (verify visually with `hugo serve`).
- Agenda layout month-section code already exists and expects `distorama_window` ≥ 7 chars → month pages will light up automatically. The hardcoded `{{ printf "/toulouse-distorama-%s/" $thisMonth }}` nav link will start resolving once the month stub exists for the current month.
- **Cron staging**: update distorama-update.yml commit step to `git add static/toulouse-distorama/` (already does — month geojsons land there) and the content find to include `toulouse-distorama-????-??` dirs. Current find excludes `toulouse-distorama-????-??-??` but ADDs `! -name 'toulouse-distorama-????-??-??'` only for EXCLUDE — verify the `git add` list actually picks up `content/toulouse-distorama-2026-09/` (currently the find pattern `-type d` with `! -name '...-??-??'` — month dirs match `toulouse-distorama*` so they get added; verify with a dry run).
- Cleanup: when the feed no longer contains a month's events (all dates passed), delete its geojson + content dir in generate.py (deterministic prune; prevents the `content/toulouse-distorama-????-??-??/` gitignored per-day dirs from accumulating — same logic can prune per-day dirs if ever reintroduced).

**Verification:**
1. `uv run scripts/toulouse-distorama/generate.py --dry-run` → expect "2026-09" month written (plus 2025 months in the past are pruned/never written).
2. Full run → `static/toulouse-distorama/events/2026-09.geojson` exists with all Sept events; `content/toulouse-distorama-2026-09/_index.md` exists.
3. `hugo build` (local) → `public/toulouse-distorama-2026-09/index.html` exists; agenda page HTML now contains a `month-section` for `2026-09` with a working link.
4. User runs `hugo serve` themselves before committing (user preference: they test via local hugo serve; don't commit/push unless asked).

**Commit (after user approval):** `feat(toulouse-distorama): month pages + monthly GeoJSONs from full feed window`

---

## Task 2 (B2) — Re-search artists whose YouTube id was rejected

**Objective:** Kill the "poisoned artist" bug: currently `enrich_artists()` skips any artist `already_cached` for YouTube, so an artist whose only candidate was rejected stays empty forever.

**Files:**
- Modify: `scripts/toulouse-distorama/ingest.py` — `enrich_artists()` (lines ~246-331), pending-selection (lines ~248-257) and the per-artist YouTube branch (lines ~279-297)

Implementation notes:
- New pending condition for YouTube: artist is pending if `youtube_validated` is falsy AND (not in cache, OR has no `youtube_video_id`). I.e. unvalidated artists without a current candidate get (re)searched on every ingest run.
- Rejected ids are already filtered (`if yt_id in yt_rejected: yt_id = ""`) — with top-N search from Task 3 this naturally walks to the next candidate. With the old single-result search it just retries; keep a per-artist `youtube_attempts` counter? NO (YAGNI) — the rejected-list check already prevents re-proposing the same id; if search keeps returning the same rejected id the field stays "" and the artist stays pending (visible in review.py, acceptable).
- Bandcamp branch unchanged (only 58 entries, works).

**Verification:**
1. `uv run scripts/toulouse-distorama/ingest.py --dry-run` → prints would-enrich list; confirm rejected-id artists appear again.
2. Real run on a few artists (rate-limited): pick one artist with a rejected id, confirm a different id lands in the cache.

**Commit:** `fix(toulouse-distorama): re-search artists whose youtube candidate was rejected`

---

## Task 3 (B1) — Scored YouTube candidate ranking + conservative auto-validate

**Objective:** Replace "first YouTube search hit" with top-N scored candidates. Auto-validate ONLY the conservative case; everything ambiguous → review.py with score + top-3 visible.

**Files:**
- Modify: `scripts/toulouse-distorama/ingest.py` (YouTube section lines ~99-150 + enrich loop)
- Modify: `scripts/toulouse-distorama/review.py` (template + approve actions)
- Modify: `scripts/toulouse-distorama/.mediacache.json` schema (additive, backward compatible)

### 3a. Fetch top-N candidates

- `_fetch_youtube_video_id` → `_fetch_youtube_candidates(artist, api_key, n=8)` via `search?part=snippet` + `videos` lookup for `statistics.viewCount` + channel info. One `search` (call) + one `videos?part=statistics,snippet&id=...` (call) = 2 API units… actually `search` = 100 units per call with `part` — use `part=snippet` only (100 units). Budget check: 100 units/artist × pending artists vs 10k/day quota = ~100 artists/day. **Pending set is ~460 unvalidated artists** → ~5 days of full refresh at daily cron, fine. Add `order=relevance` (default) and also try `order=viewCount`? NO (YAGNI) — relevance top-8 is enough; scoring uses viewCount from the second call.
- Fallback scrape path (no API key): keep as-is (single first hit), no scoring — mark `curation: "scrape"`.

### 3b. Scoring function (per candidate)

```
score = 3.0 * live_signal + 2.0 * channel_signal + 1.0 * log10(views + 1) / 8
```
- `live_signal`: 1.0 if title (lowercased) contains any of: live, concert, set, session, show, scène, scene, unplugged, acoustic, ft., feat., with, à toulouse, toulouse — and 0.0 otherwise. (Distro = live underground scene; prefer live footage.)
- `channel_signal`: 1.0 if channel title (lowercased) contains the artist name (or vice versa), 0.5 if channel is verified, else 0.
- Deterministic; store the full ranked list.

### 3c. mediacache schema (additive)

```json
"artist": {
  "youtube_video_id": "...",          // best candidate (existing field)
  "youtube_validated": true/false,    // existing
  "youtube_rejected_ids": [...],      // existing
  "youtube_candidates": [             // NEW
    {"id": "...", "title": "...", "channel": "...", "views": 12345,
     "score": 4.7, "url": "https://www.youtube.com/watch?v=..."}
  ],
  "youtube_auto_validated": true,     // NEW: true when auto-validated by scoring
  "youtube_score": 4.7,               // NEW: convenience denormalized top score
  "bandcamp_url": ..., "bandcamp_embed_url": ..., "bandcamp_validated": ...,
  "bandcamp_rejected_urls": [...]
}
```

### 3d. Conservative auto-validation rule

Auto-validate (set `youtube_validated: true`, `youtube_auto_validated: true`) ONLY when ALL hold:
1. top candidate score ≥ 4.5, AND
2. top score ≥ 1.5 × runner-up score (clear winner), AND
3. top candidate has `live_signal == 1` (it's a live/performance video), AND
4. top id not in `youtube_rejected_ids`.
Otherwise: store top candidate as `youtube_video_id` unvalidated → review.py.

### 3e. review.py UI

- Show under the YouTube panel: top-3 candidates list (thumbnail link or title + channel + views + score, clickable to preview via `?v=` swap on the embed — simplest: each candidate row is a link `youtube.com/watch?v=ID` target=_blank, and a small "use this" button that POSTs `modify_yt` with that id — the `modify_yt` action ALREADY EXISTS and sets validated, so no new endpoint needed).
- Badge: "auto-validated" (yellow) when `youtube_auto_validated` so the user can override.
- Existing actions unchanged.

**Verification:**
1. Unit-ish: run ingest for ~10 known-good artists + 3 known-ambiguous ones (from the rejected-id list); print the candidate table (title/channel/views/score) for inspection.
2. Count expected: how many of ~460 pending auto-validate vs queue for review (print a summary line: `auto-validated: N, queued for review: M`).
3. Review UI: `uv run scripts/toulouse-distorama/review.py` → verify candidate list renders, "use this" works, auto badge shows.
4. `generate.py` → weekly GeoJSONs now carry `youtube_video_id` for validated artists (0 → expect some).

**Commit:** `feat(toulouse-distorama): scored youtube curation with conservative auto-validation`

---

## Task 4 (C) — Venue dedupe: aliases + normalize

**Objective:** Collapse the 82 unmatched names (4 obvious duplicate groups, ~10 typos, recurring real venues) so resolve rate goes up.

**Files:**
- Modify: `scripts/toulouse-distorama/venues.csv` — add `aliases` column (comma-free: use `|`-separated), fill for known dupes (`L'Impasse`, `La Halle`, `Le Biérographe`, plus the recurring real venues Terreblanque/Breughel L'Ancien/Dream O'Clock IF they are real venues — classify.py interactive flow decides; do NOT guess addresses — geocache/Nominatim handles new addresses automatically in generate.py).
- Modify: `scripts/toulouse-distorama/generate.py` — `normalize_venue_name()` + `resolve_venue()`: also try each alias normalized (strip contents of `(...)`/`[...]` parens before comparing, so `La Halle (Rabastens)` == `La Halle [Rabastens]` == `La Halle` when alias exists; curly `'` → `'` already handled).
- Modify: `scripts/toulouse-distorama/classify.py` — same normalization so the interactive classifier sees the same keys.

**Verification:**
1. Re-run `uv run scripts/toulouse-distorama/generate.py` → resolved count up from 819, unmatched down from 82; print both before/after.
2. New venues need no manual geocoding (Nominatim cache grows; 1.1s/req).

**Commit:** `feat(toulouse-distorama): venue aliases + paren/bracket normalization`

---

## Task 5 (D) — Hygiene

1. `git rm --cached distorama-slideshow.mp4` (file stays on disk; `*.mp4` already gitignored so it won't come back). Commit separately.
2. README.md: update outputs table (month files), ingest scoring summary, review shortcuts (no key changes), "keep latest N" note for `static/toulouse-distorama/slideshows/` (1.2GB on disk) — document manual cleanup `ls -t ... | tail -n +3 | xargs rm` style, do NOT script it.
3. Leave unrelated dirty files alone (temple-walks preview, bangkok-raco mediacache).

---

## Execution order & status

| # | Task | Status |
|---|---|---|
| 1 | A: month pages | **DONE** — `YYYY-MM.geojson` + month `_index.md` per feed month, stale-month pruning, `.gitignore` line removed; `hugo --minify` builds `/toulouse-distorama-2026-09/` and the agenda links it |
| 2 | B2: re-search rejected | **DONE** — `_yt_needs_search()` re-searches unvalidated artists with empty id or no scored candidates; rejected ids excluded; dry-run shows 42 queued |
| 3 | B1: scored curation | **DONE** — top-8 scored candidates, conservative auto-validate (score ≥ 4.5, margin ≥ 1.5, live signal), review.py candidate list + `useCandidate()` + `auto` badge; scoring unit-tested |
| 4 | C: venue aliases | **DONE** — venues.csv 108→78 rows with `aliases` column (all 30 dropped names preserved), fixed normalizer in both scripts, alias-aware lookup; live-feed regression 819→827 resolved, 0 regressions, 7 improved |
| 5 | D: hygiene | **DONE** — `distorama-slideshow.mp4` confirmed never tracked (0 tracked .mp4s; no `git rm` needed); README updated (scoring rules, review UI, aliases, slideshows cleanup) |

Already done this session (2026-09-05):
- [x] `git pull` (repo at `3d143fb`; unblocked by moving stale untracked preview png — origin's version is in place)
- [x] Full audit + measured numbers (this file, "Measured baseline")
- [x] `uv run scripts/toulouse-distorama/generate.py` run cleanly (819/943 resolved, 82 unmatched → `unmatched-venues.txt` updated, currently `git status: M scripts/toulouse-distorama/unmatched-venues.txt` — this is an expected diff, fine to commit with Task 4)
- [x] User decisions recorded (full feed window + conservative auto-validation)

## Risks / open questions

- **Hugo month-page URL**: `/toulouse-distorama-2026-09/` — confirm no clash with existing `toulouse-distorama-{label}` dirs (none exist besides this-week/next-week/slideshow).
- **YouTube API budget**: 2 calls/artist (100+2 units… actually search w/o snippet=100? verify: search = 100 units ALWAYS; videos = 1 unit per call with ≤50 ids). ~460 pending × 101 units ≈ 46k units = needs ~4.6 days at 10k/day default quota. If quota is larger (many projects get 10k), full backfill in one `ingest.py` run may 429 → the existing `_YouTubeQuotaExceeded` → scrape fallback kicks in (acceptable) or split with `--limit N`? **Add nothing**; observe first run.
- **Scrape fallback artists get no score** → they stay manual forever; acceptable, documented.
- **Auto-validation false positives**: the ≥4.5 + 1.5× margin + live-signal triple gate should keep this rare; review.py badge lets the user audit (`auto-validated` filter could be added later — YAGNI for now, "All" filter + badge suffices).
- User must `hugo serve` locally to eyeball month pages + agenda before we commit/push (user preference).

## Verification of the whole change (before any commit)

1. `uv run scripts/toulouse-distorama/generate.py` → clean run, printed summary shows month files + improved resolve count.
2. `hugo --minify` (or user's `hugo serve`) → agenda page shows month section with working link; `/toulouse-distorama-2026-09/` renders the month map.
3. `uv run scripts/toulouse-distorama/review.py` → new UI works.
4. `git status` → only the intended paths dirty (plus pre-existing unrelated ones left alone).
