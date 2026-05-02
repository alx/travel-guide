#!/usr/bin/env python3
"""
generate_map_index.py — CI/CD map auto-indexer
Scans content/*/  _index.md for map metadata + static/*/locations.geojson for POI stats.
Writes data/maps.json consumed by layouts/index.html via {{ .Site.Data.maps }}.

Usage:
  python3 scripts/generate_map_index.py [--repo-root PATH]
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Simple inline YAML front-matter parser ────────────────────────────────────
# We only need scalar strings, ints, and flat lists — no full PyYAML required.

_SCALAR_RE = re.compile(r'^([a-zA-Z_]+):\s+"?(.*?)"?\s*$')
_LIST_ITEM_RE = re.compile(r'^\s+-\s+"?(.*?)"?\s*$')


def parse_frontmatter(text: str) -> dict:
    """Extract YAML front-matter block and parse it into a dict."""
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}

    fm: dict = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # List field: key starts on its own line, items follow
        list_key_m = re.match(r'^([a-zA-Z_]+):\s*$', line)
        if list_key_m:
            key = list_key_m.group(1)
            items = []
            i += 1
            while i < len(lines) and _LIST_ITEM_RE.match(lines[i]):
                items.append(_LIST_ITEM_RE.match(lines[i]).group(1))
                i += 1
            fm[key] = items
            continue

        # Inline list: key: ["a", "b"]
        inline_list_m = re.match(r'^([a-zA-Z_]+):\s*\[(.*)\]\s*$', line)
        if inline_list_m:
            key = inline_list_m.group(1)
            raw = inline_list_m.group(2)
            items = [v.strip().strip('"').strip("'") for v in raw.split(',') if v.strip()]
            fm[key] = items
            i += 1
            continue

        # Scalar: key: value  or  key: "value"
        scalar_m = re.match(r'^([a-zA-Z_]+):\s*"?(.*?)"?\s*$', line)
        if scalar_m:
            key, val = scalar_m.group(1), scalar_m.group(2)
            # Coerce numeric strings
            if val.lstrip('-').isdigit():
                fm[key] = int(val)
            elif val.lower() in ('true', 'false'):
                fm[key] = val.lower() == 'true'
            else:
                fm[key] = val
        i += 1

    return fm


# ── GeoJSON stats ─────────────────────────────────────────────────────────────

def geojson_stats(geojson_path: Path) -> dict:
    """Return {poi_count, categories} from a GeoJSON FeatureCollection."""
    try:
        with open(geojson_path, encoding="utf-8") as f:
            data = json.load(f)
        features = data.get("features", [])
        poi_count = len(features)
        cats = sorted({
            feat.get("properties", {}).get("category", "")
            for feat in features
            if feat.get("properties", {}).get("category")
        })
        return {"poi_count": poi_count, "categories": cats}
    except Exception as e:
        print(f"  ⚠  Could not read {geojson_path}: {e}", file=sys.stderr)
        return {"poi_count": 0, "categories": []}


# ── Main ─────────────────────────────────────────────────────────────────────

SKIP_SLUGS = {"docs"}  # content/ sub-dirs that are not maps


def main():
    # Find repo root (script lives in scripts/)
    if len(sys.argv) > 2 and sys.argv[1] == "--repo-root":
        repo_root = Path(sys.argv[2])
    else:
        repo_root = Path(__file__).parent.parent

    content_dir = repo_root / "content"
    static_dir = repo_root / "static"
    data_dir = repo_root / "data"
    data_dir.mkdir(exist_ok=True)

    maps = []

    for section_dir in sorted(content_dir.iterdir()):
        if not section_dir.is_dir():
            continue
        slug = section_dir.name
        if slug in SKIP_SLUGS:
            continue

        index_md = section_dir / "_index.md"
        if not index_md.exists():
            continue

        fm = parse_frontmatter(index_md.read_text(encoding="utf-8"))

        # Skip hidden or draft sections
        if fm.get("draft") or fm.get("section") == "hidden":
            continue

        # Required: title
        title = fm.get("title", slug)
        description = fm.get("description", "")
        section = fm.get("section", "curated")
        weight = fm.get("weight", 50)
        emoji = fm.get("emoji", "🗺️")
        accent_color = fm.get("accent_color", "#1a3a5c")
        tags = fm.get("tags", [])

        # GeoJSON stats (optional)
        geojson_path = static_dir / slug / "locations.geojson"
        geo = geojson_stats(geojson_path) if geojson_path.exists() else {"poi_count": 0, "categories": []}

        entry = {
            "slug": slug,
            "url": f"/{slug}/",
            "title": title,
            "description": description,
            "emoji": emoji,
            "section": section,
            "weight": weight,
            "accent_color": accent_color,
            "tags": tags,
            "poi_count": geo["poi_count"],
            "categories": geo["categories"],
            "has_geojson": geojson_path.exists(),
        }
        maps.append(entry)
        print(f"  ✓ [{section:10}] {slug} — {geo['poi_count']} POIs")

    # Sort: by section order then weight
    SECTION_ORDER = {"curated": 0, "community": 1, "airbnb": 2, "demo": 3}
    maps.sort(key=lambda m: (SECTION_ORDER.get(m["section"], 99), m["weight"]))

    output = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "map_count": len(maps),
            "generator": "scripts/generate_map_index.py",
        },
        "maps": maps,
    }

    out_path = data_dir / "maps.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅  Written {len(maps)} maps → {out_path}")


if __name__ == "__main__":
    main()
