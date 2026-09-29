#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["discord.py>=2.4"]
# ///
"""
DistoraMaps review bot — Discord service (runs on lamai270).

Long-running systemd user service. Every day (default 09:00 local, plus once
at startup):

1. Runs `uv run scripts/toulouse-distorama/ingest.py` to enrich new artists.
2. Computes upcoming events (default next 14 days) where NO artist has a
   validated YouTube video (review-alert.compute_pending).
3. Sends ONE Discord message per pending artist that has a candidate video,
   in the alert channel. Each message embeds the YouTube candidate:
       👍 / ✅  → validate the artist↔video link (youtube_validated=true)
       👎 / ❌  → reject it (added to youtube_rejected_ids, re-searched next
                 ingest, artist re-alerted with the new candidate)
   Validations are committed to git (best effort push) so the GitHub
   distorama-update workflow picks them up.

Only reactions from DISCORD_ALLOWED_USERS on the bot's own alert messages are
honored. Per-artist messages are throttled to REVIEW_ALERT_MAX_PER_RUN per
cycle (default 20, soonest dates first).

Env (from travel-guide/.env or environment):
    DISCORD_BOT_TOKEN            required
    DISCORD_ALERT_CHANNEL_ID     default 1510444096949325945 (nano-closet)
    DISCORD_ALLOWED_USERS        comma-separated Discord user IDs
    REVIEW_ALERT_WINDOW          days ahead to scan (default 14)
    REVIEW_ALERT_MAX_PER_RUN     max per-artist messages per cycle (20)
    REVIEW_ALERT_HOUR            local hour for the daily cycle (9)
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import discord

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
MEDIACACHE_PATH = SCRIPT_DIR / ".mediacache.json"
STATE_PATH = (
    Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    / "travel-guide" / "distorama-review-bot.json"
)
GIT_PUSH = os.environ.get("DISTORAMA_BOT_PUSH", "1") != "0"

# review-alert.py has a hyphen — load it explicitly by path.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "review_alert", SCRIPT_DIR / "review-alert.py"
)
review_alert = importlib.util.module_from_spec(_spec)
sys.modules["review_alert"] = review_alert
_spec.loader.exec_module(review_alert)
compute_pending = review_alert.compute_pending
fetch_events = review_alert.fetch_events
load_mediacache = review_alert.load_mediacache


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

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("DISCORD_ALERT_CHANNEL_ID", "1510444096949325945"))
ALLOWED_USERS = {
    u.strip()
    for u in os.environ.get("DISCORD_ALLOWED_USERS", "").replace(";", ",").split(",")
    if u.strip()
}
WINDOW_DAYS = int(os.environ.get("REVIEW_ALERT_WINDOW", "14"))
MAX_PER_RUN = int(os.environ.get("REVIEW_ALERT_MAX_PER_RUN", "20"))
CYCLE_HOUR = int(os.environ.get("REVIEW_ALERT_HOUR", "9"))

APPROVE_EMOJI = {"✅", "👍"}
REJECT_EMOJI = {"❌", "👎"}


# ── Persistence ──────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"alerted": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE_PATH)


def save_mediacache(cache: dict) -> None:
    tmp = MEDIACACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    os.replace(tmp, MEDIACACHE_PATH)


def commit_mediacache(reason: str) -> None:
    """Best-effort commit+push of .mediacache.json (so the GitHub workflow
    embeds the validated media in the next regeneration)."""
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120
        )
    git("add", str(MEDIACACHE_PATH.relative_to(REPO_ROOT)))
    if git("diff", "--cached", "--quiet").returncode == 0:
        return
    msg = f"chore(toulouse-distorama): {reason} [review-bot]"
    r = git("commit", "-m", msg)
    if r.returncode != 0:
        print(f"⚠ git commit failed: {r.stderr.strip()}")
        return
    if not GIT_PUSH:
        return
    r = git("push")
    if r.returncode != 0:
        # Non-fast-forward (CI committed GeoJSON while we were busy): rebase
        # our one commit on top of origin/main and push again.
        p = git("pull", "--rebase", "--autostash")
        if p.returncode != 0:
            print(f"⚠ git pull --rebase failed: {p.stderr.strip()}")
            return
        r = git("push")
        if r.returncode != 0:
            print(f"⚠ git push failed (will retry next change): {r.stderr.strip()}")


# ── Daily cycle ──────────────────────────────────────────────────────────────

UPCOMING_MARKER = "upcoming artists done"
INGEST_TIMEOUT = 3600  # seconds


def _find_uv() -> str | None:
    import shutil as _shutil
    return next((p for p in filter(None, (os.environ.get("UV_BIN"), "uv")) if _shutil.which(p)), None)


async def run_ingest_stream(client: discord.Client) -> None:
    """Run ingest.py with output streamed straight into the journal.

    Publishes the alerts as soon as all upcoming (future-dated) artists are
    enriched — ingest emits a marker at the future→backlog boundary; the
    backlog keeps processing in the background afterwards.
    """
    uv = _find_uv()
    if not uv:
        print("⚠ uv not found on PATH — skipping ingest (using existing cache)", flush=True)
        await send_alerts(client)
        return

    print("Running ingest.py …", flush=True)
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        uv, "run", str(SCRIPT_DIR / "ingest.py"),
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    trigger = asyncio.Event()

    async def stream() -> None:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip()
            print(line, flush=True)
            if not trigger.is_set() and UPCOMING_MARKER in line:
                print("→ upcoming artists enriched — publishing alerts now", flush=True)
                trigger.set()

    stream_task = asyncio.create_task(stream())
    trigger_task = asyncio.create_task(trigger.wait())
    exit_task = asyncio.create_task(proc.wait())
    await asyncio.wait({trigger_task, exit_task}, return_when=asyncio.FIRST_COMPLETED)
    trigger_task.cancel()

    await send_alerts(client)

    if not exit_task.done():
        budget = max(INGEST_TIMEOUT - (time.monotonic() - t0), 0)
        try:
            await asyncio.wait_for(asyncio.gather(stream_task, exit_task), timeout=budget)
        except (asyncio.TimeoutError, TimeoutError):
            proc.kill()
            await exit_task
            print("⚠ ingest.py timed out — killed", flush=True)
    rc = exit_task.result() if exit_task.done() else -9
    print(f"ingest.py exited {rc} after {time.monotonic() - t0:.0f}s", flush=True)


client = discord.Client(intents=discord.Intents.default())


@client.event
async def on_ready() -> None:
    print(f"Logged in as {client.user} (id={client.user.id})")
    asyncio.create_task(cycle_loop())


async def cycle_loop() -> None:
    await asyncio.sleep(15)
    while True:
        try:
            await run_cycle(client)
        except Exception as e:
            print(f"⚠ cycle error: {e}")
        now = datetime.now()
        nxt = now.replace(hour=CYCLE_HOUR, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())


async def run_cycle(client: discord.Client) -> None:
    print(f"[cycle] start ({datetime.now().isoformat(timespec='seconds')})")
    await run_ingest_stream(client)


async def send_alerts(client: discord.Client) -> None:
    """Compute pending review artists and post per-artist alert messages."""
    try:
        event_data = await asyncio.to_thread(fetch_events)
    except Exception as e:
        print(f"⚠ events.json fetch failed: {e}")
        return

    mediacache = load_mediacache()
    state = load_state()
    alerted: dict[str, dict] = state.setdefault("alerted", {})
    pending = compute_pending(event_data, mediacache, date.today(), WINDOW_DAYS)
    pending_keys = {f"{r['date']}|{r['artist']}" for r in pending}

    stale = [k for k in list(alerted) if k not in pending_keys]
    for k in stale:
        del alerted[k]

    candidates = [r for r in pending if r["status"] == "has-candidate"]
    fresh = [r for r in candidates if f"{r['date']}|{r['artist']}" not in alerted]
    non_reactable = [r for r in pending if r["status"] != "has-candidate"]

    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"⚠ channel {CHANNEL_ID} not visible — cycle aborted")
        return

    sent = 0
    for row in fresh[:MAX_PER_RUN]:
        msg = (
            f"**{row['artist']}** · {row['venue']} · {row['date']}\n"
            f"{row['url']}\n\n"
            f"👍 link · 👎 reject"
        )
        try:
            m = await channel.send(msg)
        except Exception as e:
            print(f"⚠ send failed for {row['artist']}: {e}")
            continue
        alerted[f"{row['date']}|{row['artist']}"] = {
            "message_id": m.id,
            "video_id": row["video_id"],
            "url": row["url"],
        }
        sent += 1
        await asyncio.sleep(1.0)

    if sent:
        print(f"[cycle] sent {sent} per-artist message(s)")
        save_state(state)

    if non_reactable or len(fresh) > MAX_PER_RUN:
        extras = len(fresh) - min(len(fresh), MAX_PER_RUN)
        parts = []
        if non_reactable:
            names = ", ".join(r["artist"] for r in non_reactable[:8])
            more = f" +{len(non_reactable) - 8}" if len(non_reactable) > 8 else ""
            parts.append(f"⚠ {len(non_reactable)} pending artist(s) have no candidate yet: {names}{more}")
        if extras > 0:
            parts.append(f"… {extras} more pending artist(s) will be alerted in later runs")
        if parts:
            try:
                await channel.send(" — ".join(parts))
            except Exception:
                pass

    state["last_cycle"] = datetime.now().isoformat(timespec="seconds")
    save_state(state)
    print(f"[cycle] done — pending={len(pending)} sent={sent}")


# ── Reactions ────────────────────────────────────────────────────────────────

def _find_by_message(state: dict, message_id: int) -> tuple[str, dict] | None:
    for key, meta in state.get("alerted", {}).items():
        if meta.get("message_id") == message_id:
            return key, meta
    return None


async def handle_reaction(payload) -> None:  # discord.ReactionEvent (no public alias)
    if payload.user_id == client.user.id:
        return
    if ALLOWED_USERS and str(payload.user_id) not in ALLOWED_USERS:
        return
    emoji = payload.emoji
    name = emoji.name if emoji else None
    if name in APPROVE_EMOJI:
        decision = "approve"
    elif name in REJECT_EMOJI:
        decision = "reject"
    else:
        return

    state = load_state()
    found = _find_by_message(state, payload.message_id)
    if not found:
        return  # not one of our alert messages
    key, meta = found
    date_str, artist = key.split("|", 1)

    channel = client.get_channel(CHANNEL_ID)
    try:
        m = await channel.fetch_message(payload.message_id)
    except discord.HTTPException as e:
        print(f"⚠ cannot fetch message {payload.message_id}: {e}")
        return

    print(f"[reaction] {decision} {artist!r} → {meta.get('video_id')} ({payload.event_type})")

    cache = load_mediacache()
    entry = cache.get(artist, {})
    if decision == "approve":
        entry["youtube_video_id"] = meta.get("video_id", entry.get("youtube_video_id", ""))
        entry["youtube_validated"] = True
        entry["youtube_auto_validated"] = False
        entry["youtube_validated_via"] = "discord-reaction"
        entry["youtube_validated_at"] = date.today().isoformat()
        entry["event_date"] = date_str
        cache[artist] = entry
        save_mediacache(cache)
        commit_mediacache(f"validate {artist} YouTube link")
        suffix = "— ✅ linked"
    else:
        vid = meta.get("video_id", "")
        rejected = entry.get("youtube_rejected_ids", [])
        if vid and vid not in rejected:
            rejected.append(vid)
            entry["youtube_rejected_ids"] = rejected
        if entry.get("youtube_video_id") == vid:
            entry["youtube_video_id"] = ""  # don't leave a rejected id as the current one
        entry["youtube_validated"] = False
        cache[artist] = entry
        save_mediacache(cache)
        commit_mediacache(f"reject {artist} YouTube candidate")
        # Forget the alert so the artist is re-alerted after the next ingest
        # re-searches (rejected ids are excluded from future candidates).
        del state["alerted"][key]
        suffix = "— ❌ rejected (will re-search)"

    save_state(state)
    try:
        await m.edit(content=m.content + f"\n{suffix}")
    except discord.HTTPException:
        pass
    try:
        # Message.remove_reaction only reads member.id (Snowflake abc).
        user = discord.Object(id=payload.user_id)
        await m.remove_reaction(payload.emoji, user)
    except (discord.HTTPException, discord.DiscordException):
        pass


@client.event
async def on_raw_reaction_add(payload) -> None:  # discord.ReactionEvent (no public alias)
    if payload.message_id is None:
        return
    try:
        await handle_reaction(payload)
    except Exception as e:
        print(f"⚠ reaction handler error: {e}")


if __name__ == "__main__":
    if not TOKEN:
        sys.exit("DISCORD_BOT_TOKEN missing (set in travel-guide/.env or environment)")
    client.run(TOKEN, log_handler=None, reconnect=True)
