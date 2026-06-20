#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["python-telegram-bot>=20.0"]
# ///
"""
Persistent Telegram bot for the revente-tickets-fr pipeline.
Run as a service on lamai270:

  uv run scripts/revente-tickets-fr/telegram_bot.py

Requires in .env:
  REVENTE_BOT_TOKEN  — Telegram bot token
  REVENTE_CHAT_ID    — owner personal chat ID
  YOUTUBE_API_KEY    — optional, falls back to scraping
"""
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

REPO_ROOT = Path(__file__).parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "revente-tickets-fr" / "state.db"
GEOCACHE_PATH = Path(__file__).parent / ".geocache.json"
VENUES_CSV = Path(__file__).parent / "venues.csv"
UNMATCHED_PATH = Path(__file__).parent / "unmatched-venues.txt"

_HEADERS = {"User-Agent": "revente-tickets-fr-bot/1.0 (maps.girard-davila.net)"}


# ── Env ───────────────────────────────────────────────────────────────────────

def load_env() -> None:
    for p in (REPO_ROOT / ".env", Path(".env")):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
            return


# ── DB ────────────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# ── Key-value parser ──────────────────────────────────────────────────────────

_ALIASES: dict[str, str] = {
    "venue": "venue", "salle": "venue", "lieu": "venue", "endroit": "venue",
    "artist": "artist", "artiste": "artist", "groupe": "artist", "band": "artist",
    "date": "event_date",
    "tickets": "tickets", "billets": "tickets", "places": "tickets", "nb": "tickets",
    "price": "price_each", "prix": "price_each", "tarif": "price_each", "cout": "price_each",
    "category": "seat_category", "categorie": "seat_category", "catégorie": "seat_category",
    "cat": "seat_category", "type": "seat_category", "placement": "seat_category",
}


def parse_kv(text: str) -> dict:
    result: dict = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower().strip(".")
        val = val.strip()
        canonical = _ALIASES.get(key)
        if not canonical or not val:
            continue
        if canonical == "tickets":
            m = re.search(r"\d+", val)
            result[canonical] = int(m.group()) if m else None
        elif canonical == "price_each":
            m = re.search(r"[\d]+(?:[.,]\d+)?", val.replace(",", "."))
            result[canonical] = float(m.group().replace(",", ".")) if m else None
        else:
            result[canonical] = val
    return result


def format_confirmation(data: dict, listing_title: str) -> str:
    lines = [f"📋 <b>Récapitulatif</b>\n<i>{listing_title[:80]}</i>\n"]
    if data.get("venue"):
        lines.append(f"📍 <b>Salle :</b> {data['venue']}")
    if data.get("artist"):
        lines.append(f"🎤 <b>Artiste :</b> {data['artist']}")
    if data.get("event_date"):
        lines.append(f"📅 <b>Date :</b> {data['event_date']}")
    if data.get("tickets"):
        lines.append(f"🎟️ <b>Billets :</b> {data['tickets']}")
    if data.get("price_each") is not None:
        lines.append(f"💶 <b>Prix :</b> {data['price_each']:.0f}€/place")
    if data.get("seat_category"):
        lines.append(f"💺 <b>Catégorie :</b> {data['seat_category']}")
    lines.append("\n<i>Réponds à ce message : <b>oui</b> pour confirmer, <b>non</b> pour corriger.</i>")
    return "\n".join(lines)


# ── Geocoding ─────────────────────────────────────────────────────────────────

def _load_geocache() -> dict:
    if GEOCACHE_PATH.exists():
        return json.loads(GEOCACHE_PATH.read_text())
    return {}


def _save_geocache(cache: dict) -> None:
    GEOCACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def _load_venue_registry() -> dict[str, dict]:
    if not VENUES_CSV.exists():
        return {}
    registry: dict[str, dict] = {}
    for line in VENUES_CSV.read_text().splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4 and parts[2] and parts[3]:
            try:
                registry[parts[0].lower()] = {"lat": float(parts[2]), "lon": float(parts[3])}
            except ValueError:
                pass
    return registry


def _geocode_photon(query: str) -> dict | None:
    params = urllib.parse.urlencode({"q": query, "limit": 1})
    try:
        req = urllib.request.Request(f"https://photon.komoot.io/api/?{params}", headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        feats = data.get("features", [])
        if feats:
            c = feats[0]["geometry"]["coordinates"]
            return {"lat": c[1], "lon": c[0], "source": "photon"}
    except Exception:
        pass
    return None


def _geocode_nominatim(query: str) -> dict | None:
    params = urllib.parse.urlencode({
        "q": query, "format": "json", "limit": 1, "accept-language": "fr",
    })
    try:
        req = urllib.request.Request(
            f"https://nominatim.openstreetmap.org/search?{params}", headers=_HEADERS
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            results = json.loads(r.read())
        if results:
            return {"lat": float(results[0]["lat"]), "lon": float(results[0]["lon"]), "source": "nominatim"}
    except Exception:
        pass
    return None


def geocode_venue(venue: str) -> dict | None:
    cache = _load_geocache()
    if venue in cache:
        return cache[venue]

    registry = _load_venue_registry()
    if venue.lower() in registry:
        result = {**registry[venue.lower()], "source": "registry"}
        cache[venue] = result
        _save_geocache(cache)
        return result

    for fn in (_geocode_photon, _geocode_nominatim):
        result = fn(f"{venue} France")
        if result:
            cache[venue] = result
            _save_geocache(cache)
            return result
        time.sleep(1)

    with UNMATCHED_PATH.open("a") as f:
        f.write(f"{venue}\n")
    return None


# ── YouTube ───────────────────────────────────────────────────────────────────

def _yt_api(artist: str, api_key: str) -> str | None:
    params = urllib.parse.urlencode({
        "part": "snippet", "q": artist, "type": "video",
        "maxResults": 1, "key": api_key,
    })
    try:
        req = urllib.request.Request(
            f"https://www.googleapis.com/youtube/v3/search?{params}", headers=_HEADERS
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        items = data.get("items", [])
        if items:
            return items[0]["id"]["videoId"]
    except Exception:
        pass
    return None


def _yt_scrape(artist: str) -> str | None:
    params = urllib.parse.urlencode({"search_query": artist})
    try:
        req = urllib.request.Request(
            f"https://www.youtube.com/results?{params}",
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="ignore")
        m = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
        return m.group(1) if m else None
    except Exception:
        pass
    return None


def get_youtube(artist: str) -> tuple[str, str]:
    """Returns (video_id, search_url). video_id may be empty string."""
    search_url = f"https://www.youtube.com/results?{urllib.parse.urlencode({'search_query': artist})}"
    api_key = os.environ.get("YOUTUBE_API_KEY")
    video_id = (_yt_api(artist, api_key) if api_key else None) or _yt_scrape(artist) or ""
    return video_id, search_url


# ── Bot ───────────────────────────────────────────────────────────────────────

def _lookup_by_msg_id(con: sqlite3.Connection, msg_id: int) -> tuple[str | None, str | None]:
    """Returns (listing_id, context) where context ∈ {notification, confirm, sold_check}."""
    row = con.execute(
        """SELECT id,
                  CASE
                    WHEN telegram_notification_msg_id = :m THEN 'notification'
                    WHEN telegram_confirm_msg_id      = :m THEN 'confirm'
                    WHEN telegram_sold_check_msg_id   = :m THEN 'sold_check'
                  END AS ctx
           FROM listings
           WHERE telegram_notification_msg_id = :m
              OR telegram_confirm_msg_id = :m
              OR telegram_sold_check_msg_id = :m
        """,
        {"m": msg_id},
    ).fetchone()
    return (row["id"], row["ctx"]) if row else (None, None)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    owner_chat_id = int(
        os.environ.get("REVENTE_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "0")
    )
    if message.chat_id != owner_chat_id:
        return

    reply_to = message.reply_to_message
    if not reply_to:
        return

    text = message.text.strip()
    con = get_db()
    listing_id, ctx = _lookup_by_msg_id(con, reply_to.message_id)

    if listing_id is None:
        con.close()
        return

    row = con.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if not row:
        con.close()
        return

    # ── Reply to notification: parse key-value ────────────────────────────
    if ctx == "notification":
        parsed = parse_kv(text)
        if not parsed:
            await message.reply_text(
                "❌ Format non reconnu. Utilise :\n"
                "venue: ...\nartist: ...\ndate: AAAA-MM-JJ\ntickets: N\nprice: XX\ncategory: ..."
            )
            con.close()
            return

        confirmation_text = format_confirmation(parsed, row["title"])
        sent = await message.reply_text(confirmation_text, parse_mode="HTML")

        con.execute(
            """UPDATE listings
               SET status = 'waiting_confirm',
                   pending_structured_json = ?,
                   telegram_confirm_msg_id = ?
               WHERE id = ?""",
            (json.dumps(parsed), sent.message_id, listing_id),
        )
        con.commit()
        con.close()
        return

    # ── Reply to confirmation: oui/non ────────────────────────────────────
    if ctx == "confirm":
        answer = text.lower()
        if answer not in ("oui", "o", "yes", "non", "n", "no"):
            await message.reply_text("Réponds <b>oui</b> ou <b>non</b>.", parse_mode="HTML")
            con.close()
            return

        if answer in ("non", "n", "no"):
            con.execute("UPDATE listings SET status = 'pending' WHERE id = ?", (listing_id,))
            con.commit()
            con.close()
            await message.reply_text(
                "↩️ Annulé. Réponds au <b>message original</b> avec les infos corrigées.",
                parse_mode="HTML",
            )
            return

        # oui → enrich and save
        parsed = json.loads(row["pending_structured_json"] or "{}")
        await message.reply_text("⏳ Géocodage en cours…")

        geo = await asyncio.to_thread(geocode_venue, parsed.get("venue", ""))

        if geo is None:
            await message.reply_text(
                f"⚠️ Impossible de géocoder <b>{parsed.get('venue')}</b>.\n"
                "Ajoute la venue à <code>scripts/revente-tickets-fr/venues.csv</code> "
                "et renvoie le message.",
                parse_mode="HTML",
            )
            con.execute("UPDATE listings SET status = 'pending' WHERE id = ?", (listing_id,))
            con.commit()
            con.close()
            return

        youtube_id, youtube_search = ("", "")
        if parsed.get("artist"):
            await message.reply_text("🎬 Recherche YouTube…")
            youtube_id, youtube_search = await asyncio.to_thread(
                get_youtube, parsed["artist"]
            )

        now = datetime.now(timezone.utc).isoformat()
        con.execute(
            """UPDATE listings SET
                 status = 'available',
                 venue = ?, artist = ?, event_date = ?, tickets = ?,
                 price_each = ?, seat_category = ?,
                 lat = ?, lon = ?, geocode_source = ?,
                 youtube_video_id = ?, youtube_search_url = ?,
                 pending_structured_json = NULL,
                 processed_at = ?
               WHERE id = ?""",
            (
                parsed.get("venue"), parsed.get("artist"), parsed.get("event_date"),
                parsed.get("tickets"), parsed.get("price_each"), parsed.get("seat_category"),
                geo["lat"], geo["lon"], geo["source"],
                youtube_id, youtube_search,
                now, listing_id,
            ),
        )
        con.commit()
        con.close()

        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from generate import generate, git_push_and_deploy
            n = await asyncio.to_thread(generate)
            await asyncio.to_thread(git_push_and_deploy)
            await message.reply_text(
                f"✅ <b>{parsed.get('venue')}</b> ajouté à la carte ({n} annonces).\n"
                f"<a href=\"https://maps.girard-davila.net/revente-tickets-fr/\">Voir la carte</a>",
                parse_mode="HTML",
            )
        except Exception as e:
            await message.reply_text(f"⚠️ Sauvegardé en base mais erreur git/deploy : {e}")
        return

    # ── Reply to sold-check: oui/non ──────────────────────────────────────
    if ctx == "sold_check":
        answer = text.lower()
        if answer in ("oui", "o", "yes"):
            now = datetime.now(timezone.utc).isoformat()
            con.execute(
                "UPDATE listings SET status = 'sold', sold_at = ? WHERE id = ?",
                (now, listing_id),
            )
            con.commit()
            con.close()
            try:
                from generate import generate, git_push_and_deploy
                n = await asyncio.to_thread(generate)
                await asyncio.to_thread(git_push_and_deploy)
                await message.reply_text(f"✅ Marqué vendu. Carte mise à jour ({n} annonces).")
            except Exception as e:
                await message.reply_text(f"⚠️ Marqué vendu mais erreur git : {e}")
        else:
            con.execute(
                "UPDATE listings SET telegram_sold_check_msg_id = NULL WHERE id = ?",
                (listing_id,),
            )
            con.commit()
            con.close()
            await message.reply_text("👍 Conservé comme disponible.")


def main() -> None:
    load_env()
    token = os.environ.get("REVENTE_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("ERROR: REVENTE_BOT_TOKEN required in .env")

    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("revente-tickets-fr bot running…")
    app.run_polling()


if __name__ == "__main__":
    main()
