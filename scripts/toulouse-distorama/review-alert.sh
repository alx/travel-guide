#!/usr/bin/env bash
# Daily driver: enrich new artists, then alert if upcoming events have no
# linked (validated) YouTube video. Runs from crontab — see travel-guide
# scripts/toulouse-distorama/README.md.
set -uo pipefail

export PATH="/home/alx/.local/bin:$PATH"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

echo "[$(ts)] === Distorama review alert run ==="

# 1) Enrich new artists from fresh events (incremental; cached).
if uv run scripts/toulouse-distorama/ingest.py; then
  echo "[$(ts)] ingest: ok"
else
  echo "[$(ts)] ⚠ ingest failed — alert will use existing .mediacache.json"
fi

# 2) Alert on upcoming events without a linked YouTube video (deduped).
if uv run scripts/toulouse-distorama/review-alert.py; then
  echo "[$(ts)] review-alert: ok"
else
  echo "[$(ts)] ⚠ review-alert failed (exit $?)"
fi

echo "[$(ts)] === done ==="
