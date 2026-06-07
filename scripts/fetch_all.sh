#!/usr/bin/env bash
# fetch_all.sh — Refresh all OSM-sourced map datasets then rebuild the index.
#
# Usage:
#   bash scripts/fetch_all.sh            # run all
#   bash scripts/fetch_all.sh yoga toulouse  # run matching names only
#
# Each fetch script writes GeoJSON to stdout; we pipe it to the static file.
# Progress is printed to stderr so you see it live; errors abort that step
# but continue with the rest.

set -uo pipefail
cd "$(dirname "$0")/.."

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

info()  { echo -e "${GREEN}▶${NC}  $*"; }
warn()  { echo -e "${YELLOW}⚠${NC}  $*"; }
error() { echo -e "${RED}✗${NC}  $*"; }

# Map: script path → output GeoJSON path
declare -A MAPS=(
  ["scripts/yoga_france/fetch_yoga_france.py"]="static/yoga-france/locations.geojson"
  ["scripts/videosurveillance_france/fetch_videosurveillance_france.py"]="static/videosurveillance-france/locations.geojson"
  ["scripts/videoprotection_toulouse/fetch_videoprotection_toulouse.py"]="static/videoprotection-toulouse/locations.geojson"
)

FILTER="${*:-}"  # optional name filter (space-separated substrings)

run_script() {
  local script="$1" output="$2"
  local name
  name="$(basename "$(dirname "$script")")"

  # Apply filter if provided
  if [[ -n "$FILTER" ]]; then
    local match=0
    for f in $FILTER; do
      [[ "$name" == *"$f"* ]] && match=1 && break
    done
    [[ $match -eq 0 ]] && return 0
  fi

  info "Fetching $name → $output"

  local tmp
  tmp="$(mktemp --suffix=.geojson)"
  trap "rm -f '$tmp'" RETURN

  local runner="python3"
  # Use uv if the script has the uv shebang
  if head -2 "$script" | grep -q "uv run"; then
    runner="uv run"
  fi

  local max_attempts=3
  local attempt=0
  local success=0

  while [[ $attempt -lt $max_attempts ]]; do
    attempt=$((attempt + 1))
    [[ $attempt -gt 1 ]] && { warn "  Retrying ($attempt/$max_attempts) after 10s..."; sleep 10; }

    # stdout → tmp file; stderr → terminal so progress messages are visible
    if $runner "$script" > "$tmp"; then
      if python3 -c "import json; json.load(open('$tmp'))" 2>/dev/null; then
        mv "$tmp" "$output"
        local count
        count=$(python3 -c "import json; d=json.load(open('$output')); print(len(d.get('features',[])))")
        info "  ✓ $count features written to $output"
        success=1
        break
      else
        error "  Invalid JSON output — will retry"
      fi
    else
      error "  Script exited with error (attempt $attempt/$max_attempts)"
    fi
  done

  if [[ $success -eq 0 ]]; then
    error "  Gave up after $max_attempts attempts — keeping existing file"
  fi
  return 0
}

for script in "${!MAPS[@]}"; do
  run_script "$script" "${MAPS[$script]}"
done

info "Rebuilding data/maps.json..."
python3 scripts/generate_map_index.py
info "Done."
