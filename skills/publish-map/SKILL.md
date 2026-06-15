---
name: publish-map
description: Run and publish a static POI map scaffolded by create-map. Enriches the GeoJSON via generate.py, then commits and pushes to GitHub Pages (or starts hugo server locally). Use when the user says "publish map", "deploy map", or "run publish-map {slug}".
---

# publish-map

Publish a static POI map once its GeoJSON has at least one feature. Enriches coordinates, commits, and deploys.

## Step 1 — Resolve slug

If the user provided a slug argument, use it. Otherwise ask: "Which map slug should I publish?"

## Step 2 — Guard: check GeoJSON has features

Read `static/{slug}/locations.geojson`. Count the features array length.

If zero: stop and tell the user:
```
static/{slug}/locations.geojson has no features yet.
Add at least one POI, then run /publish-map {slug} again.
```

## Step 3 — Enrich coordinates

Run:
```bash
uv run scripts/{slug}/generate.py
```

This geocodes any feature that has `properties.address` but `geometry: null` or missing coordinates. Features that already have coordinates are untouched.

If `scripts/{slug}/generate.py` does not exist, skip this step silently.

## Step 4 — Detect GitHub remote

```bash
git remote get-url origin 2>/dev/null
```

### If a remote exists → commit and push

Stage the GeoJSON, geocache, and content stub:

```bash
git add static/{slug}/locations.geojson
git add scripts/{slug}/.geocache.json 2>/dev/null || true
git add content/{slug}/
```

Commit:
```bash
git commit -m "feat({slug}): publish map"
```

Push:
```bash
git push
```

Pushing to `main` automatically triggers `deploy.yml` (Hugo build → GitHub Pages). Tell the user:
```
Pushed. GitHub Actions will build and deploy the site.
Map will be live at: https://{github-pages-url}/{slug}/
```

Derive the GitHub Pages URL from `git remote get-url origin`: convert `git@github.com:owner/repo.git` or `https://github.com/owner/repo` to `https://owner.github.io/repo`.

### If no remote → serve locally

```bash
hugo server --disableFastRender
```

Tell the user:
```
No GitHub remote found. Serving locally.
Map is at: http://localhost:1313/{slug}/
```
