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
