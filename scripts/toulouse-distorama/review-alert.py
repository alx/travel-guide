#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""
Alert when upcoming Distorama events have no validated (linked) YouTube video.

Scans the upcoming event window (default: next 14 days) in
distorama.neocities.org/events.json. Any event where NONE of its artists has
a human-validated YouTube video (per .mediacache.json) is reported.

CLI mode: sends one aggregated Discord alert through `hermes send` (deduped —
only sent when the set of pending (date, artist) pairs changes).

    uv run scripts/toulouse-distorama/review-alert.py [--dry-run] [--force] [--window N]

Module mode: `compute_pending()` is reused by distorama_review_bot.py, which
sends per-artist messages supporting 👍/👎 emoji reactions.

Requires: `hermes` CLI on PATH (CLI mode only, except with --dry-run).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
MEDIACACHE_PATH = SCRIPT_DIR / ".mediacache.json"
EVENTS_URL = "https://distorama.neocities.org/events.json"
STATE_PATH = (
    Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    / "travel-guide" / "distorama-review-alert.json"
)

DISCORD_TARGET = "discord:1510444096949325945"  # VIENS tkt — 🤖-nano-closet
USER_AGENT = "maps.girard-davila.net/toulouse-distorama-review-alert"

# ── Event parsing (mirrors generate.py / ingest.py) ─────────────────────────

_NON_ARTIST = re.compile(
    r"(vernissage|soirée|exposition|expo|festival|marché|atelier|conférence|"
    r"projection|distorama|radio|émission|emission|concert|showcase|open\s?mic|"
    r"bal|anniversaire|clôture|ouverture|inauguration)",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"^\d{1,2}h\d{0,2}$")


def parse_artist(desc: str) -> str | None:
    if not desc:
        return None
    if _NON_ARTIST.search(desc):
        return None
    artist = re.sub(r"\s*\([^)]*\)\s*$", "", desc).strip()
    return artist or None


def split_artists(artist: str) -> list[str]:
    parts = [a.strip() for a in artist.split("+") if a.strip()]
    cleaned = [re.sub(r"\s*\([^)]*\)\s*$", "", p).strip() for p in parts]
    return [p for p in cleaned if p]


def parse_details(details: str) -> str | None:
    """Return the raw venue name from a details string, or None."""
    parts = [p.strip() for p in (details or "").split(" - ")]
    if not parts:
        return None
    if _TIME_RE.match(parts[0]):
        return None
    return parts[0] or None


# ── Pending review computation (shared with distorama_review_bot.py) ────────

def artist_status(artist: str, mediacache: dict) -> tuple[str, str, str]:
    """Return (status, url, video_id) for one artist.

    status ∈ {"validated", "has-candidate", "no-candidate", "not-indexed"}
    """
    m = mediacache.get(artist)
    if m is None:
        return "not-indexed", "", ""
    if m.get("youtube_validated"):
        return "validated", "", ""
    rejected = set(m.get("youtube_rejected_ids", []))
    cands = [c for c in m.get("youtube_candidates", [])
             if c.get("url") and c.get("id") not in rejected]
    top = max(cands, key=lambda c: c.get("score", 0), default=None)
    if top is not None:
        return "has-candidate", top["url"], top.get("id", "")
    vid = m.get("youtube_video_id", "")
    if vid and vid not in rejected:
        return "has-candidate", f"https://www.youtube.com/watch?v={vid}", vid
    return "no-candidate", "", ""


def compute_pending(event_data: list, mediacache: dict, today: date, window_days: int) -> list[dict]:
    """Return one row per pending (date, artist):
    {date, artist, venue, status, url, video_id}, sorted by date then artist.

    An artist is pending when its event (within [today, today+window-1]) has
    NO artist with a validated YouTube video.
    """
    start_iso = today.isoformat()
    end_iso = (today + timedelta(days=window_days - 1)).isoformat()

    rows: list[dict] = []
    for entry in event_data:
        date_str = entry.get("date", "")
        if not (start_iso <= date_str <= end_iso):
            continue
        for ev in entry.get("events", []):
            artist = parse_artist(ev.get("desc", ""))
            if not artist:
                continue  # non-artist event — nothing to link
            subs = split_artists(artist)
            statuses = [artist_status(a, mediacache) for a in subs]
            if any(s == "validated" for s, _, _ in statuses):
                continue  # at least one artist is linked → event is fine
            venue = parse_details(ev.get("details", "")) or "?"
            for a, (s, url, vid) in zip(subs, statuses):
                rows.append({"date": date_str, "artist": a, "venue": venue,
                             "status": s, "url": url, "video_id": vid})
    rows.sort(key=lambda r: (r["date"], r["artist"].lower()))
    return rows


def load_mediacache() -> dict:
    if MEDIACACHE_PATH.exists():
        for _ in range(3):
            try:
                return json.loads(MEDIACACHE_PATH.read_text())
            except json.JSONDecodeError:
                time.sleep(0.5)  # writer mid-flush — retry
        return {}
    return {}


def fetch_events() -> list:
    req = urllib.request.Request(EVENTS_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


# ── CLI: aggregated hermes-send alert ────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the alert without sending or updating state")
    parser.add_argument("--force", action="store_true",
                        help="Send even if the pending set is unchanged")
    parser.add_argument("--window", type=int, default=14,
                        help="Days ahead to scan (default: 14)")
    args = parser.parse_args()

    print(f"Fetching {EVENTS_URL}…")
    try:
        event_data = fetch_events()
    except Exception as e:
        sys.exit(f"Failed to fetch events.json: {e}")

    mediacache = load_mediacache()
    today = date.today()
    pending = compute_pending(event_data, mediacache, today, args.window)
    if not pending:
        print("No upcoming events need YouTube review — nothing to do.")
        return

    start_iso = today.isoformat()
    end_iso = (today + timedelta(days=args.window - 1)).isoformat()
    by_date: dict[str, list[dict]] = {}
    for row in pending:
        by_date.setdefault(row["date"], []).append(row)

    key = sorted(f"{r['date']}|{r['artist']}" for r in pending)

    state = {}
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text())

    n_events = len(by_date)
    n_artists = len(pending)
    if not args.force and state.get("key") == key:
        print(f"Pending set unchanged since last alert ({n_artists} artist(s)) — skipping send.")
        return

    lines = [
        "**🎧 DistoraMaps — YouTube review needed**",
        f"*{n_events} upcoming event(s), {n_artists} artist(s) without a linked video"
        f" (window: {start_iso} → {end_iso})*",
        "",
    ]
    for date_str, rows in sorted(by_date.items()):
        lines.append(f"**{date_str}**")
        for row in rows:
            if row["status"] == "has-candidate":
                lines.append(f"- {row['artist']} · {row['venue']} — {row['url']}")
            elif row["status"] == "no-candidate":
                lines.append(f"- {row['artist']} · {row['venue']} — *no YouTube candidate yet*")
            else:
                lines.append(f"- {row['artist']} · {row['venue']} — *not indexed (run ingest.py)*")
        lines.append("")
    lines.append("_Review: `uv run scripts/toulouse-distorama/review.py` → http://localhost:5020_")
    message = "\n".join(lines)

    print("\n" + "=" * 60 + "\n" + message + "\n" + "=" * 60)

    if args.dry_run:
        print("\n[dry-run] not sending, state not updated.")
        return

    hermes = shutil.which("hermes")
    if not hermes:
        sys.exit("ERROR: `hermes` not found on PATH — cannot deliver Discord alert.")

    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, prefix="distorama-review-alert-"
    ) as f:
        f.write(message)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [hermes, "send", "--to", DISCORD_TARGET, "--file", tmp_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            sys.exit(f"hermes send failed (exit {result.returncode}):\n{result.stderr.strip()}")
        print("Discord alert sent.")
    finally:
        os.unlink(tmp_path)

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({
        "key": key,
        "sent_at": today.isoformat(),
        "n_artists": n_artists,
    }, indent=2))
    print(f"State saved to {STATE_PATH}")


if __name__ == "__main__":
    main()
