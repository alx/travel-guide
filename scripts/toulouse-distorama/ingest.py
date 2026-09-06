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
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
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


def _fetch_youtube_candidates(artist: str, api_key: str, n: int = 8) -> tuple[list[dict], str]:
    """Fetch top-N YouTube candidates via Data API v3, each with view count.

    Returns (candidates, official_channel). Candidates are ranked by search
    relevance then annotated with a curation score (see _score_candidate).
    Raises _YouTubeQuotaExceeded on 429 / quota 403.
    """
    params = urllib.parse.urlencode({
        "part": "snippet",
        "type": "video",
        "maxResults": n,
        "q": artist,
        "key": api_key,
    })
    url = f"https://www.googleapis.com/youtube/v3/search?{params}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429 or (e.code == 403 and "quota" in e.read().decode("utf-8", "replace").lower()):
            raise _YouTubeQuotaExceeded()
        print(f"  ⚠ YouTube API error for '{artist}': {e}", file=sys.stderr)
        return [], ""

    items = data.get("items", [])
    ids = [it["id"].get("videoId") for it in items if it.get("id", {}).get("videoId")]
    if not ids:
        return [], ""

    stats: dict[str, dict] = {}
    sp = urllib.parse.urlencode({
        "part": "statistics,snippet",
        "id": ",".join(ids),
        "key": api_key,
    })
    try:
        req = urllib.request.Request(f"https://www.googleapis.com/youtube/v3/videos?{sp}")
        with urllib.request.urlopen(req, timeout=10) as r:
            sdata = json.loads(r.read())
        for it in sdata.get("items", []):
            stats[it["id"]] = it
    except Exception as e:
        print(f"  ⚠ YouTube stats lookup failed for '{artist}': {e}", file=sys.stderr)

    # Pass 1: discover the official channel (channel title == artist name)
    official = ""
    for it in items:
        vid = it["id"].get("videoId", "")
        ch = stats.get(vid, {}).get("snippet", {}).get("channelTitle", "")
        if ch and ch.lower() == artist.lower():
            official = ch
            break

    # Pass 2: score candidates with the official channel in hand
    candidates = []
    for it in items:
        vid = it["id"].get("videoId", "")
        st = stats.get(vid, {})
        title = it.get("snippet", {}).get("title", "")
        channel = st.get("snippet", {}).get("channelTitle", "")
        views = int(st.get("statistics", {}).get("viewCount", 0) or 0)
        live = _live_signal(title, channel, artist, st)
        score = _score_candidate(title, channel, views, artist, official)
        candidates.append({
            "id": vid,
            "title": title,
            "channel": channel,
            "views": views,
            "score": score,
            "live": live,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    return candidates, official


# ── Candidate scoring (conservative curation) ─────────────────────────────────

_LIVE_TERMS = re.compile(
    r"\b(live|concert|set|session|show|showcase|scène|scene|unplugged|"
    r"acoustic|à toulouse|toulouse)\b"
)


def _live_signal(title: str, channel: str, artist: str, st: dict) -> int:
    """1 if the candidate looks like a live/performance video, else 0."""
    t = title.lower()
    art = artist.lower()
    if _LIVE_TERMS.search(t):
        return 1
    # Own channel (title names the artist) — performance-ish
    if channel.lower() == art and art in t:
        return 1
    return 0


def _channel_signal(channel: str, artist: str, official: str) -> float:
    ch = channel.lower()
    art = artist.lower()
    if ch == art or (official and ch == official.lower()):
        return 1.0
    if ch and (art in ch or ch in art):
        return 0.5
    return 0.0


def _score_candidate(title: str, channel: str, views: int, artist: str, official: str) -> float:
    """Deterministic curation score (≈0–7).

    3.0 × live signal + 2.0 × channel signal + log10(views+1)/8 popularity.
    """
    live = _live_signal(title, channel, artist, {})
    ch_sig = _channel_signal(channel, artist, official)
    pop = math.log10(views + 1) / 8.0 if views > 0 else 0.0
    return round(3.0 * live + 2.0 * ch_sig + pop, 2)


def _pick_best(candidates: list[dict], rejected: list[str]) -> tuple[dict | None, bool]:
    """Conservative auto-validation. Returns (best_candidate, auto_validated).

    Auto-validate ONLY when the top candidate is clearly a live/performance
    video, beats the runner-up by a wide margin, and hasn't been rejected.
    """
    ranked = [c for c in candidates if c["id"] not in rejected]
    ranked.sort(key=lambda c: c["score"], reverse=True)
    if not ranked:
        return None, False
    top = ranked[0]
    if top["live"] and top["score"] >= 4.5:
        runner = ranked[1]["score"] if len(ranked) > 1 else 0.0
        if top["score"] >= 1.5 * runner:
            return top, True
    return top, False


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
    data = None
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            if e.code in (401, 429):
                raise _SerpAPIQuotaExceeded()
            print(f"  ⚠ SerpAPI error for '{artist}': {e}", file=sys.stderr)
            return "", ""
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    if data is None:
        print(f"  ⚠ SerpAPI error for '{artist}' (after retries): {last_err}", file=sys.stderr)
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

def _yt_needs_search(m: dict) -> bool:
    """Unvalidated artist needing (re-)search: no candidate, or legacy
    scrape-only entry without a scored candidate list."""
    return not m.get("youtube_validated") and (
        not m.get("youtube_video_id", "") or not m.get("youtube_candidates")
    )


def enrich_artists(artist_dates: dict[str, str], mediacache: dict, api_key: str, dry_run: bool) -> dict:
    """Enrich artists with YouTube video IDs and Bandcamp embed URLs."""
    today_iso = date.today().isoformat()

    def _needs_work(name: str) -> bool:
        m = mediacache.get(name)
        if m is None:
            return True
        if _yt_needs_search(m):
            return True
        if not m.get("bandcamp_searched") and not m.get("bandcamp_validated"):
            return True
        return False

    # Review future shows first, then the backlog. Within each group, soonest
    # date first, then name — so the next upcoming artist is always enriched
    # (and thus reviewable) before past-dated ones.
    future = [a for a in artist_dates
              if artist_dates[a] >= today_iso and _needs_work(a)]
    backlog = [a for a in artist_dates
               if artist_dates[a] < today_iso and _needs_work(a)]
    future.sort(key=lambda a: (artist_dates[a], a))
    backlog.sort(key=lambda a: (artist_dates[a], a))
    pending = future + backlog

    if not pending:
        print("  All artists already cached")
        return mediacache

    serp_key = os.environ.get("SERPAPI_API_KEY", "")
    use_yt_scrape = not api_key
    serp_quota_gone = not serp_key
    yt_quota_gone = False
    auto_validated_run = 0
    review_queued_run = 0

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

        # Work on a copy so new fields (candidates, auto-validation) persist.
        m = dict(mediacache.get(artist, {}))
        yt_rejected = m.get("youtube_rejected_ids", [])
        yt_id = m.get("youtube_video_id", "")
        candidates = m.get("youtube_candidates", [])

        # (Re-)search when unvalidated AND (no current candidate OR never
        # scored via API — legacy scrape-only entries get upgraded too).
        if not m.get("youtube_validated") and (not yt_id or not candidates):
            found = []
            if not (use_yt_scrape or yt_quota_gone):
                try:
                    found, _official = _fetch_youtube_candidates(artist, api_key)
                except _YouTubeQuotaExceeded:
                    tqdm.write("  ⚠ YouTube quota exceeded — falling back to scrape", file=sys.stderr)
                    yt_quota_gone = True
            if found:
                best, auto_ok = _pick_best(found, yt_rejected)
                yt_id = best["id"] if best else ""
                m["youtube_candidates"] = found
                m["youtube_score"] = best["score"] if best else 0
                if auto_ok:
                    m["youtube_validated"] = True
                    m["youtube_auto_validated"] = True
                    auto_validated_run += 1
                else:
                    m["youtube_auto_validated"] = False
                    review_queued_run += 1
                tqdm.write(f"  {artist}: {len(found)} candidates, top={yt_id or '—'}"
                           f" score={best['score'] if best else '—'}"
                           f"{', auto-validated' if auto_ok else ' → review'}")
            else:
                # No API available (no key / quota gone) → scrape fallback
                yt_id = _scrape_youtube_video_id(artist)
                if yt_id:
                    tqdm.write(f"  {artist}: YouTube {yt_id} (scrape)")
            time.sleep(0.5)
            if yt_id in yt_rejected:
                yt_id = ""

        # Bandcamp via SerpAPI — skip if human already validated
        bc_url, bc_embed = "", ""
        bc_searched = False
        bc_rejected = m.get("bandcamp_rejected_urls", [])
        if not serp_quota_gone and not m.get("bandcamp_validated"):
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

        m["youtube_video_id"] = yt_id
        m["bandcamp_url"] = bc_url
        m["bandcamp_embed_url"] = bc_embed
        m["bandcamp_searched"] = bc_searched
        m["event_date"] = artist_dates.get(artist, "")
        mediacache[artist] = m
        save_mediacache(mediacache)

    if auto_validated_run or review_queued_run:
        print(f"  YouTube curation: {auto_validated_run} auto-validated, "
              f"{review_queued_run} queued for review")
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
