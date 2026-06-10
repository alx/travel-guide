#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["tqdm"]
# ///
"""
Fetch YouTube and Bandcamp media for toulouse-distorama artists.

Reads events from distorama.neocities.org/events.json, extracts artist names,
then enriches .mediacache.json with YouTube video IDs and Bandcamp embed URLs.
Run review.py afterwards to validate the results.

Usage:
    uv run scripts/toulouse-distorama/ingest.py
    uv run scripts/toulouse-distorama/ingest.py --dry-run

Requires (in .env or environment):
    YOUTUBE_API_KEY   — YouTube Data API v3 (falls back to scraping if absent)
    SERPAPI_API_KEY   — SerpAPI for Bandcamp search (skipped if absent)
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
MEDIACACHE_PATH = SCRIPT_DIR / ".mediacache.json"
EVENTS_URL = "https://distorama.neocities.org/events.json"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv(REPO_ROOT / ".env")


# ── Cache I/O ─────────────────────────────────────────────────────────────────

def load_mediacache() -> dict:
    if MEDIACACHE_PATH.exists():
        return json.loads(MEDIACACHE_PATH.read_text())
    return {}


def save_mediacache(cache: dict) -> None:
    MEDIACACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


# ── Artist extraction (minimal — mirrors generate.py) ────────────────────────

_NON_ARTIST = re.compile(
    r"(vernissage|soirée|exposition|expo|festival|marché|atelier|conférence|"
    r"projection|distorama|radio|émission|emission|concert|showcase|open\s?mic|"
    r"bal|anniversaire|clôture|ouverture|inauguration)",
    re.IGNORECASE,
)


def _parse_artist(desc: str) -> str | None:
    if _NON_ARTIST.search(desc):
        return None
    artist = re.sub(r"\s*\([^)]*\)\s*$", "", desc).strip()
    return artist if artist else None


def _split_artists(artist: str) -> list[str]:
    parts = [a.strip() for a in artist.split("+") if a.strip()]
    cleaned = [re.sub(r"\s*\([^)]*\)\s*$", "", p).strip() for p in parts]
    return [p for p in cleaned if p]


# ── YouTube ───────────────────────────────────────────────────────────────────

class _YouTubeQuotaExceeded(Exception):
    pass


def _scrape_youtube_video_id(artist: str) -> str:
    """Fallback: scrape first video ID from YouTube search results page."""
    params = urllib.parse.urlencode({"search_query": artist})
    url = f"https://www.youtube.com/results?{params}"
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="replace")
        m = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
        return m.group(1) if m else ""
    except Exception as e:
        print(f"  ⚠ YouTube scrape error for '{artist}': {e}", file=sys.stderr)
    return ""


def _fetch_youtube_video_id(artist: str, api_key: str) -> str:
    """Call YouTube Data API v3. Raises _YouTubeQuotaExceeded when quota is gone."""
    params = urllib.parse.urlencode({
        "part": "id",
        "type": "video",
        "maxResults": 1,
        "q": artist,
        "key": api_key,
    })
    url = f"https://www.googleapis.com/youtube/v3/search?{params}"
    for _ in range(4):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            items = data.get("items", [])
            return items[0]["id"].get("videoId", "") if items else ""
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise _YouTubeQuotaExceeded()
            elif e.code == 403:
                body = e.read().decode("utf-8", errors="replace")
                if "quotaExceeded" in body or "dailyLimitExceeded" in body:
                    raise _YouTubeQuotaExceeded()
                print(f"  ⚠ YouTube API error for '{artist}': {e}", file=sys.stderr)
                return ""
            else:
                print(f"  ⚠ YouTube API error for '{artist}': {e}", file=sys.stderr)
                return ""
        except Exception as e:
            print(f"  ⚠ YouTube API error for '{artist}': {e}", file=sys.stderr)
            return ""
    return ""


# ── Bandcamp ──────────────────────────────────────────────────────────────────

class _SerpAPIQuotaExceeded(Exception):
    pass


def _http_get_html(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ⚠ HTTP error fetching {url}: {e}", file=sys.stderr)
    return ""


def _extract_bandcamp_embed(url: str) -> tuple[str, str]:
    """Given a known Bandcamp artist or album URL, return (url, embed_url)."""
    html = _http_get_html(url)
    if not html:
        return url, ""

    if "/album/" not in url:
        m_album = re.search(r'href="((?:https://[^"]+)?/album/[^"]+)"', html)
        if not m_album:
            return url, ""
        album_path = m_album.group(1)
        if album_path.startswith("/"):
            base = re.match(r"(https://[^/]+)", url)
            album_url = (base.group(1) + album_path) if base else ""
        else:
            album_url = album_path
        if not album_url:
            return url, ""
        html = _http_get_html(album_url)
        if not html:
            return url, ""

    m_id = re.search(r"bandcamp\.com/EmbeddedPlayer/(?:v=2/)?album=(\d+)/", html)
    if not m_id:
        m_id = re.search(r'data-tralbumid="(\d+)"', html)
    if not m_id:
        return url, ""

    embed_url = (
        f"https://bandcamp.com/EmbeddedPlayer/album={m_id.group(1)}"
        f"/size=small/bgcol=111111/linkcol=ffffff/transparent=true/"
    )
    return url, embed_url


def _fetch_bandcamp_via_serp(artist: str, api_key: str) -> tuple[str, str]:
    """Search site:bandcamp.com via SerpAPI. Raises _SerpAPIQuotaExceeded on quota/auth errors."""
    params = urllib.parse.urlencode({
        "q": f"site:bandcamp.com {artist}",
        "api_key": api_key,
        "engine": "google",
        "num": 1,
    })
    url = f"https://serpapi.com/search.json?{params}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 429):
            raise _SerpAPIQuotaExceeded()
        print(f"  ⚠ SerpAPI error for '{artist}': {e}", file=sys.stderr)
        return "", ""
    except Exception as e:
        print(f"  ⚠ SerpAPI error for '{artist}': {e}", file=sys.stderr)
        return "", ""

    if data.get("error"):
        msg = str(data["error"]).lower()
        if any(w in msg for w in ("ran out", "credit", "quota", "upgrade")):
            raise _SerpAPIQuotaExceeded()
        print(f"  ⚠ SerpAPI: {data['error']}", file=sys.stderr)
        return "", ""

    results = data.get("organic_results", [])
    if not results:
        return "", ""

    artist_url = results[0].get("link", "").split("?")[0].split("#")[0]
    if not artist_url or "bandcamp.com" not in artist_url:
        return "", ""

    return _extract_bandcamp_embed(artist_url)


# ── Enrichment loop ───────────────────────────────────────────────────────────

def enrich_artists(artist_dates: dict[str, str], mediacache: dict, api_key: str, dry_run: bool) -> dict:
    """Enrich artists with YouTube video IDs and Bandcamp embed URLs."""
    pending = [
        a for a in sorted(artist_dates, key=lambda a: artist_dates[a], reverse=True)
        if a and (
            a not in mediacache
            or (
                not mediacache[a].get("bandcamp_searched")
                and not mediacache[a].get("bandcamp_validated")
            )
        )
    ]
    if not pending:
        print("  All artists already cached")
        return mediacache

    serp_key = os.environ.get("SERPAPI_API_KEY", "")
    use_yt_scrape = not api_key
    serp_quota_gone = not serp_key

    if use_yt_scrape:
        print("  ⚠ YOUTUBE_API_KEY not set — using scrape for YouTube", file=sys.stderr)
    if serp_quota_gone:
        print("  ⚠ SERPAPI_API_KEY not set — Bandcamp enrichment skipped", file=sys.stderr)

    for artist in tqdm(pending, desc="Enriching artists", unit="artist"):
        already_cached = artist in mediacache
        if dry_run:
            tqdm.write(f"  [dry-run] would enrich: {artist}")
            if not already_cached:
                mediacache[artist] = {"youtube_video_id": "", "bandcamp_url": "", "bandcamp_embed_url": ""}
            continue

        # YouTube — skip if already cached or human-validated
        yt_rejected = mediacache.get(artist, {}).get("youtube_rejected_ids", [])
        if already_cached or mediacache.get(artist, {}).get("youtube_validated"):
            yt_id = mediacache[artist].get("youtube_video_id", "")
        elif use_yt_scrape:
            yt_id = _scrape_youtube_video_id(artist)
        else:
            try:
                yt_id = _fetch_youtube_video_id(artist, api_key)
            except _YouTubeQuotaExceeded:
                tqdm.write("  ⚠ YouTube quota exceeded — switching to scrape", file=sys.stderr)
                use_yt_scrape = True
                yt_id = _scrape_youtube_video_id(artist)
        if yt_id in yt_rejected:
            yt_id = ""
        if yt_id:
            tqdm.write(f"  {artist}: YouTube {yt_id}")
        if not already_cached:
            time.sleep(0.5)

        # Bandcamp via SerpAPI — skip if human already validated
        bc_url, bc_embed = "", ""
        bc_searched = False
        bc_rejected = mediacache.get(artist, {}).get("bandcamp_rejected_urls", [])
        if not serp_quota_gone and not mediacache.get(artist, {}).get("bandcamp_validated"):
            try:
                bc_url, bc_embed = _fetch_bandcamp_via_serp(artist, serp_key)
                bc_searched = True
                if bc_url in bc_rejected:
                    bc_url, bc_embed = "", ""
                if bc_url:
                    tqdm.write(f"  {artist}: Bandcamp {bc_url}")
            except _SerpAPIQuotaExceeded:
                tqdm.write("  ⚠ SerpAPI quota exceeded — skipping Bandcamp for remaining artists", file=sys.stderr)
                serp_quota_gone = True
            if not serp_quota_gone:
                time.sleep(1.1)

        existing = mediacache.get(artist, {})
        mediacache[artist] = {
            "youtube_video_id": yt_id,
            "bandcamp_url": bc_url,
            "bandcamp_embed_url": bc_embed,
            "bandcamp_searched": bc_searched,
            # preserve human-review fields
            "youtube_validated": existing.get("youtube_validated", False),
            "youtube_rejected_ids": existing.get("youtube_rejected_ids", []),
            "bandcamp_validated": existing.get("bandcamp_validated", False),
            "bandcamp_rejected_urls": existing.get("bandcamp_rejected_urls", []),
        }
        save_mediacache(mediacache)

    return mediacache


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Skip network requests and file writes")
    args = parser.parse_args()

    print(f"Fetching {EVENTS_URL}…")
    try:
        req = urllib.request.Request(EVENTS_URL, headers={"User-Agent": "maps.girard-davila.net/toulouse-distorama"})
        with urllib.request.urlopen(req, timeout=15) as r:
            event_data = json.loads(r.read())
        print(f"  {len(event_data)} date entries")
    except Exception as e:
        sys.exit(f"Failed to fetch events.json: {e}")

    print("Collecting artist names…")
    artist_dates: dict[str, str] = {}
    for entry in event_data:
        date_str = entry["date"]
        for ev in entry.get("events", []):
            artist = _parse_artist(ev.get("desc", ""))
            if not artist:
                continue
            for sub in _split_artists(artist):
                if sub not in artist_dates or date_str > artist_dates[sub]:
                    artist_dates[sub] = date_str
    print(f"  {len(artist_dates)} unique artists")

    print("Enriching artists with YouTube + Bandcamp…")
    mediacache = load_mediacache()
    mediacache = enrich_artists(artist_dates, mediacache, os.environ.get("YOUTUBE_API_KEY", ""), args.dry_run)
    print("Done.")


if __name__ == "__main__":
    main()
