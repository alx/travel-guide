#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["tqdm"]
# ///
"""
Generate a temple walk GeoJSON + Hugo page from a starting point.

Finds named Buddhist temples via the Overpass API, chains them greedily with
OSRM walking legs, and stops before the cumulative walking distance exceeds
--max-km (default 10). Output follows the bangkok-citywalk GeoJSON schema.

Usage:
    uv run scripts/temple-walk/generate.py --start "13.7516,100.4927" --slug rattanakosin
    uv run scripts/temple-walk/generate.py --start "Democracy Monument, Bangkok" --slug democracy --max-km 8
    uv run scripts/temple-walk/generate.py --start "13.7516,100.4927" --slug rattanakosin --dry-run
"""

import argparse
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
CACHE_DIR = SCRIPT_DIR / "cache"

USER_AGENT = {"User-Agent": "maps.girard-davila.net/temple-walk (girard.davila@gmail.com)"}
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSRM_BASE = "http://router.project-osrm.org/route/v1/foot"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"


# ── Pure helpers ──────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower()
    for src, dst in [("é","e"),("è","e"),("ê","e"),("à","a"),("â","a"),("ù","u"),("û","u"),("î","i"),("ô","o"),("ç","c")]:
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_start(value: str) -> tuple[float, float] | None:
    """Return (lat, lng) if value parses as "lat,lng", else None (treat as address)."""
    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        lat, lng = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def parse_overpass_elements(elements: list[dict]) -> list[dict]:
    """
    Extract named temples with coordinates from Overpass `out center` elements.
    Dedupes by name, preferring ways/relations over nodes (richer geometry).
    """
    temples: dict[str, dict] = {}
    for el in elements:
        name = el.get("tags", {}).get("name")
        if not name:
            continue
        if el["type"] == "node":
            lat, lng = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            lat, lng = center.get("lat"), center.get("lon")
        if lat is None or lng is None:
            continue
        existing = temples.get(name)
        if existing is None or (existing["osm_type"] == "node" and el["type"] != "node"):
            temples[name] = {
                "name": name,
                "lat": lat,
                "lng": lng,
                "osm_type": el["type"],
                "osm_id": f"{el['type']}/{el['id']}",
            }
    return list(temples.values())


if __name__ == "__main__":
    pass  # main() arrives in Task 5
