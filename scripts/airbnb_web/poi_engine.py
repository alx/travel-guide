"""
Thin wrapper around scripts/airbnb_env/airbnb_nearby.py.

Adds the airbnb_env directory to sys.path so we can import the script as a
module, then re-exports the functions the web app needs without duplicating
any logic.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_AIRBNB_ENV = Path(__file__).parent.parent / "airbnb_env"
if str(_AIRBNB_ENV) not in sys.path:
    sys.path.insert(0, str(_AIRBNB_ENV))

import airbnb_nearby as lib  # noqa: E402  (import after sys.path mutation)

_cfg = None


def initialize(config_path: Path | None = None, env_path: Path | None = None) -> object:
    """Load config and wire airbnb_nearby module-level globals. Call once at startup."""
    global _cfg
    from dotenv import load_dotenv

    # Resolve .env: explicit arg → airbnb_env/.env → repo root .env
    if env_path:
        load_dotenv(env_path)
    else:
        for candidate in [_AIRBNB_ENV / ".env", _AIRBNB_ENV.parent.parent / ".env"]:
            if candidate.exists():
                load_dotenv(candidate)
                break

    _cfg = lib.load_config(config_path)

    # Mirror what airbnb_nearby.main() does to wire module-level globals
    lib.CATEGORIES         = _cfg.categories
    lib.CAT_PRIORITY       = _cfg.trim_priority
    lib.DEFAULT_CATEGORIES = _cfg.default_categories
    lib.MAX_PER_CAT        = _cfg.max_per_category
    lib.MIN_RATING         = _cfg.min_rating
    lib.MIN_REVIEWS        = _cfg.min_reviews
    lib.MAX_TOTAL_POIS     = _cfg.max_total_pois
    lib.DEDUP_RADIUS_M     = _cfg.dedup_radius_m
    lib.HARD_DIST_CAP_M    = _cfg.hard_dist_cap_m

    return _cfg


def get_cfg():
    return _cfg


def resolve_coords(
    airbnb_url: str,
    gmaps_url: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> tuple[float, float, str]:
    """Return (lat, lon, confidence). Priority: explicit flags → gmaps URL → scrape."""
    if lat is not None and lon is not None:
        return float(lat), float(lon), "high"
    if gmaps_url:
        rlat, rlon = lib.coords_from_gmaps_url(gmaps_url)
        return rlat, rlon, "high"
    return lib.coords_from_airbnb_url(airbnb_url)


def fetch_all(
    airbnb_url: str,
    lat: float,
    lon: float,
    categories: list[str] | None = None,
    radius: float | None = None,
    progress_cb=None,
) -> tuple[dict, dict, dict, str]:
    """
    Run the full POI pipeline.

    Returns (filtered_results, geojson, location_meta, listing_id).
    progress_cb(pct, msg) is called at key stages if provided.
    """
    cfg = lib.get_config()
    cats   = categories or cfg.default_categories
    radius = radius or cfg.search_radius_m

    def _prog(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)

    _prog(30, "Querying OSM Overpass…")
    osm = lib.query_overpass(cats, lat, lon, radius)

    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    google  = None
    if api_key:
        _prog(60, "Querying Google Places…")
        google = lib.query_google_nearby(api_key, cats, lat, lon, radius)
    else:
        _prog(60, "OSM only (no GOOGLE_MAPS_API_KEY set)")

    _prog(80, "Filtering and deduplicating…")
    merged   = lib.merge_results(osm, google)
    filtered = lib.filter_and_limit(merged, lat, lon)

    _prog(90, "Building GeoJSON…")
    location   = lib.reverse_geocode(lat, lon)
    listing_id = lib.listing_id_from_url(airbnb_url)
    slug       = f"airbnb/{listing_id}"
    geojson    = lib.build_geojson(airbnb_url, lat, lon, filtered, radius, slug,
                                   location=location)

    return filtered, geojson, location, listing_id


# Re-export helpers the routes need directly
build_pr              = lib.build_pr
write_local_hugo_files = lib.write_local_hugo_files
listing_id_from_url   = lib.listing_id_from_url
haversine             = lib.haversine
title_from_airbnb_url = lib.title_from_airbnb_url
photo_from_airbnb_url = lib.photo_from_airbnb_url


def listing_preview(url: str) -> dict:
    """Return {'title': str|None, 'photo_url': str|None}. Best-effort, never raises."""
    try:
        title = lib.title_from_airbnb_url(url)
    except Exception:
        title = None
    try:
        photo_url = lib.photo_from_airbnb_url(url)
    except Exception:
        photo_url = None
    return {"title": title, "photo_url": photo_url}


def apply_status_curation(features: list) -> None:
    """Apply primary/secondary status curation to a list of GeoJSON features in-place.

    Used to upgrade legacy cache entries that pre-date the status field.
    """
    lib._curate_statuses(features)
