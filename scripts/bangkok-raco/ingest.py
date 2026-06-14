#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["tqdm"]
# ///
"""
Fetch YouTube and SoundCloud media for bangkok-raco artists.

Usage:
    uv run scripts/bangkok-raco/ingest.py
    uv run scripts/bangkok-raco/ingest.py --dry-run

Requires (in .env or environment):
    YOUTUBE_API_KEY   — YouTube Data API v3 (falls back to scraping if absent)
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
MEDIACACHE_PATH = SCRIPT_DIR / ".mediacache.json"

RA_AREA_ID = 453
RA_GRAPHQL_URL = "https://ra.co/graphql"
RA_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Origin": "https://ra.co",
    "Referer": "https://ra.co/events/th/bangkok",
}
SC_PROXY_URL = "https://proxy.searchsoundcloud.com/tracks"

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

RA_QUERY = """query GET_EVENT_LISTINGS($filters: FilterInputDtoInput, $filterOptions: FilterOptionsInputDtoInput, $page: Int, $pageSize: Int, $sort: SortInputDtoInput) {
  eventListings(filters: $filters filterOptions: $filterOptions pageSize: $pageSize page: $page sort: $sort) {
    data { event { date artists { name } } }
    totalResults
  }
}"""


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(REPO_ROOT / ".env")


# ── Cache I/O ─────────────────────────────────────────────────────────────────

def load_mediacache() -> dict:
    if MEDIACACHE_PATH.exists():
        return json.loads(MEDIACACHE_PATH.read_text())
    return {}


def save_mediacache(cache: dict) -> None:
    MEDIACACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


# ── RA.co artist fetch ────────────────────────────────────────────────────────

def fetch_artist_dates() -> dict[str, str]:
    """Returns {artist_name: most_recent_event_date} for all Bangkok RA.co artists."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    page, page_size = 1, 100
    artist_dates: dict[str, str] = {}

    while True:
        payload = {
            "operationName": "GET_EVENT_LISTINGS",
            "variables": {
                "filters": {"areas": {"eq": RA_AREA_ID}, "listingDate": {"gte": monday.isoformat()}},
                "filterOptions": {"genre": True, "eventType": True},
                "pageSize": page_size,
                "page": page,
                "sort": {"listingDate": {"order": "ASCENDING"}},
            },
            "query": RA_QUERY,
        }
        body = json.dumps(payload).encode()
        req = urllib.request.Request(RA_GRAPHQL_URL, data=body, headers=RA_HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read())
        except Exception as e:
            sys.exit(f"RA.co fetch failed: {e}")

        listings = resp["data"]["eventListings"]
        for item in listings["data"]:
            ev = item.get("event") or {}
            date_str = (ev.get("date") or "")[:10]
            for a in ev.get("artists") or []:
                name = (a.get("name") or "").strip()
                if name and (name not in artist_dates or date_str > artist_dates[name]):
                    artist_dates[name] = date_str

        fetched = (page - 1) * page_size + len(listings["data"])
        if fetched >= listings["totalResults"]:
            break
        page += 1
        time.sleep(0.3)

    return artist_dates


# ── YouTube ───────────────────────────────────────────────────────────────────

class _YouTubeQuotaExceeded(Exception):
    pass


def _scrape_youtube_video_id(artist: str) -> str:
    params = urllib.parse.urlencode({"search_query": f"{artist} DJ mix"})
    url = f"https://www.youtube.com/results?{params}"
    try:
        req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="replace")
        m = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
        return m.group(1) if m else ""
    except Exception as e:
        print(f"  ⚠ YouTube scrape error for '{artist}': {e}", file=sys.stderr)
    return ""


def _fetch_youtube_video_id(artist: str, api_key: str) -> str:
    params = urllib.parse.urlencode({
        "part": "id", "type": "video", "maxResults": 1,
        "q": f"{artist} DJ mix", "key": api_key,
    })
    url = f"https://www.googleapis.com/youtube/v3/search?{params}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        items = data.get("items", [])
        return items[0]["id"].get("videoId", "") if items else ""
    except urllib.error.HTTPError as e:
        if e.code in (429, 403):
            raise _YouTubeQuotaExceeded()
        print(f"  ⚠ YouTube API error for '{artist}': {e}", file=sys.stderr)
    except Exception as e:
        print(f"  ⚠ YouTube error for '{artist}': {e}", file=sys.stderr)
    return ""


# ── SoundCloud ────────────────────────────────────────────────────────────────

def _fetch_soundcloud_track(artist: str) -> tuple[str, str]:
    """Returns (track_id, permalink_url) for the first result, or ('', '')."""
    params = urllib.parse.urlencode({"q": artist})
    url = f"{SC_PROXY_URL}?{params}"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "maps.girard-davila.net/bangkok-raco",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        tracks = data.get("collection", [])
        if not tracks:
            return "", ""
        t = tracks[0]
        return str(t["id"]), t.get("permalink_url", "")
    except Exception as e:
        print(f"  ⚠ SoundCloud error for '{artist}': {e}", file=sys.stderr)
    return "", ""


# ── Enrichment loop ───────────────────────────────────────────────────────────

def enrich_artists(artist_dates: dict[str, str], mediacache: dict, api_key: str, dry_run: bool) -> dict:
    pending = [
        a for a in sorted(artist_dates, key=lambda a: artist_dates[a], reverse=True)
        if a and a not in mediacache
    ]
    if not pending:
        print("  All artists already cached")
        return mediacache

    use_yt_scrape = not api_key
    if use_yt_scrape:
        print("  ⚠ YOUTUBE_API_KEY not set — using scrape fallback", file=sys.stderr)

    for artist in tqdm(pending, desc="Enriching artists", unit="artist"):
        if dry_run:
            tqdm.write(f"  [dry-run] would enrich: {artist}")
            mediacache[artist] = {"youtube_video_id": "", "soundcloud_track_id": "", "soundcloud_url": ""}
            continue

        # YouTube
        try:
            if use_yt_scrape:
                yt_id = _scrape_youtube_video_id(artist)
            else:
                yt_id = _fetch_youtube_video_id(artist, api_key)
        except _YouTubeQuotaExceeded:
            tqdm.write("  ⚠ YouTube quota exceeded — switching to scrape", file=sys.stderr)
            use_yt_scrape = True
            yt_id = _scrape_youtube_video_id(artist)
        if yt_id:
            tqdm.write(f"  {artist}: YouTube {yt_id}")
        time.sleep(0.5)

        # SoundCloud
        sc_id, sc_url = _fetch_soundcloud_track(artist)
        if sc_id:
            tqdm.write(f"  {artist}: SoundCloud {sc_url}")
        time.sleep(1.0)

        existing = mediacache.get(artist, {})
        mediacache[artist] = {
            "youtube_video_id": yt_id,
            "soundcloud_track_id": sc_id,
            "soundcloud_url": sc_url,
            "youtube_validated": existing.get("youtube_validated", False),
            "youtube_rejected_ids": existing.get("youtube_rejected_ids", []),
            "soundcloud_validated": existing.get("soundcloud_validated", False),
            "soundcloud_rejected_ids": existing.get("soundcloud_rejected_ids", []),
        }
        save_mediacache(mediacache)

    return mediacache


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Fetching artist list from RA.co Bangkok…")
    artist_dates = fetch_artist_dates()
    print(f"  {len(artist_dates)} unique artists")

    print("Enriching artists with YouTube + SoundCloud…")
    mediacache = load_mediacache()
    enrich_artists(artist_dates, mediacache, os.environ.get("YOUTUBE_API_KEY", ""), args.dry_run)
    print("Done.")


if __name__ == "__main__":
    main()
