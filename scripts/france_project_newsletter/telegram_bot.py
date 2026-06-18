#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["python-telegram-bot>=21.0"]
# ///
"""
GPS Newsletter Telegram bot — runs as a persistent service on lamai270.

Two modes:
  push    — send immediate alert(s) for a specific item hash or all new high-priority items
  summary — send end-of-day summary of all new relevant items
  serve   — long-polling loop to handle inline Approve/Reject callbacks for finance_pending_review

Usage:
  uv run scripts/france_project_newsletter/telegram_bot.py push [--hash HASH]
  uv run scripts/france_project_newsletter/telegram_bot.py summary
  uv run scripts/france_project_newsletter/telegram_bot.py serve

Env vars:
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

import argparse
import asyncio
import json
import os
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter/state.db"
GEOJSON_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/locations.geojson"
COMPANIES_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/companies.json"

# Source types that belong in companies.json (company-level); all others go on the feature.
COMPANY_LEVEL_TYPES = {"company_rss", "linkedin_company_rss", "youtube_channel_rss",
                       "bodacc_rss", "wikipedia_atom"}

HIGH_PRIORITY_SIGNALS = {"funding_round", "groundbreaking", "production_start", "M&A"}

SIGNAL_LABELS = {
    "funding_round":    "💰 Levée de fonds",
    "groundbreaking":   "🏗️ Premier coup de pioche",
    "production_start": "🏭 Démarrage production",
    "delay":            "⏳ Retard",
    "regulatory":       "📋 Réglementaire",
    "partnership":      "🤝 Partenariat",
    "M&A":              "🔀 M&A",
    "other":            "📰 Actualité",
}

CATEGORY_ICONS = {
    "Matériaux critiques":    "🔋",
    "Aéronautique & Défense": "✈️",
    "Data Center & IA":       "🖥️",
    "Recyclage":              "♻️",
    "Énergie & Nucléaire":    "⚡",
    "Industrie":              "🏭",
    "Sidérurgie":             "⚙️",
    "Biocarburants":          "🌿",
    "Électromobilité":        "🚗",
    "Agroalimentaire":        "🌾",
}


def load_env() -> None:
    env_file = pathlib.Path(__file__).parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def get_con() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    return con


def format_alert(item: dict) -> str:
    icon = CATEGORY_ICONS.get(item["category"], "🏭")
    signal = SIGNAL_LABELS.get(item["signal_type"], "📰")
    date = item["date"][:10]
    return (
        f"{icon} <b>{item['company']}</b>\n"
        f"{signal}\n\n"
        f"<b>{item['title']}</b>\n"
        f"{item['summary']}\n\n"
        f"📅 {date} · <a href='{item['url']}'>Lire l'article</a>"
    )


def format_source_review(row: dict) -> str:
    payload = json.loads(row["candidate_payload"]) if row.get("candidate_payload") else {}
    snippet = payload.get("snippet", "")[:120]
    source_url = payload.get("source_url", "")
    scope = "🏢 Entreprise" if row["type"] in COMPANY_LEVEL_TYPES else "📍 Projet"
    return (
        f"🔗 <b>Source à valider</b> — {scope}\n\n"
        f"<b>{row['company_id']}</b>\n"
        f"Type : <code>{row['type']}</code>\n"
        f"URL : <a href='{row['url']}'>{row['url'][:80]}</a>\n\n"
        f"{snippet}\n\n"
        f"Trouvé via : {row['discovered_by']}"
        + (f"\n<a href='{source_url}'>Source de découverte</a>" if source_url else "")
        + f"\nID : {row['id']}"
    )


def write_approved_source(row: dict, con: sqlite3.Connection) -> None:
    """Write an approved source declaration into the appropriate store."""
    src_type = row["type"]
    url = row["url"]
    company_id = row["company_id"]
    payload = json.loads(row["candidate_payload"]) if row.get("candidate_payload") else {}
    now = datetime.now(timezone.utc).isoformat()

    # Profile fields: write to company_enrichment
    if src_type == "profile_linkedin":
        con.execute(
            """INSERT INTO company_enrichment (company_id, linkedin_url, enriched_at)
               VALUES (?, ?, ?)
               ON CONFLICT(company_id) DO UPDATE SET linkedin_url=excluded.linkedin_url,
                 enriched_at=excluded.enriched_at""",
            (company_id, url, now),
        )
        con.commit()
        return

    if src_type == "profile_website":
        desc = payload.get("description_fr")
        emp = payload.get("employee_count_est")
        con.execute(
            """INSERT INTO company_enrichment
               (company_id, website_url, description_fr, employee_count_est,
                website_checked_at, enriched_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(company_id) DO UPDATE SET
                 website_url=excluded.website_url,
                 description_fr=COALESCE(excluded.description_fr, description_fr),
                 employee_count_est=COALESCE(excluded.employee_count_est, employee_count_est),
                 website_checked_at=excluded.website_checked_at,
                 enriched_at=excluded.enriched_at""",
            (company_id, url, desc, emp, now, now),
        )
        con.commit()
        return

    # Feed source declarations: write to companies.json or feeds.sources
    src = {"type": src_type, "url": url}

    if src_type in COMPANY_LEVEL_TYPES:
        companies = json.loads(COMPANIES_PATH.read_text(encoding="utf-8"))
        entry = companies.setdefault(company_id, {"name": company_id, "company_url": "", "sources": []})
        existing_urls = {s["url"] for s in entry.get("sources", [])}
        if url not in existing_urls:
            entry.setdefault("sources", []).append(src)
            COMPANIES_PATH.write_text(json.dumps(companies, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        # Project-level: write to the specific feature's feeds.sources
        data = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))
        target_id = row.get("project_id") or company_id
        for feat in data["features"]:
            if feat.get("id") == target_id:
                feeds = feat["properties"].setdefault("feeds", {})
                sources = feeds.setdefault("sources", [])
                existing_urls = {s["url"] for s in sources}
                if url not in existing_urls:
                    sources.append(src)
                    GEOJSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                break


def format_review(row: dict) -> tuple[str, list]:
    axis_labels = {"capex": "💶 Capex", "subsidy": "🏛️ Subvention", "employment": "👷 Emploi", "stage": "📍 Étape"}
    axis = axis_labels.get(row["axis"], row["axis"])
    val = ""
    if row["value_text"]:
        val = row["value_text"]
    if row["value_num"] is not None:
        val += f" ({row['value_num']:,.0f} €)" if row["axis"] in ("capex", "subsidy") else f" ({row['value_num']:.0f})"

    text = (
        f"🔍 <b>Finance à valider</b>\n\n"
        f"<b>{row['company']}</b> — {axis}\n"
        f"Valeur : {val or 'non extraite'}\n\n"
        f"Source : <a href='{row['source_url']}'>{row['source_title'][:80]}</a>\n"
        f"ID extraction : {row['id']}"
    )
    buttons = [
        [
            {"text": "✅ Approuver", "callback_data": f"approve:{row['id']}"},
            {"text": "❌ Rejeter",   "callback_data": f"reject:{row['id']}"},
        ]
    ]
    return text, buttons


async def cmd_push(token: str, chat_id: str, item_hash: str | None) -> None:
    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
    bot = Bot(token=token)
    con = get_con()

    if item_hash:
        rows = con.execute(
            "SELECT * FROM news_items WHERE hash=?", (item_hash,)
        ).fetchall()
    else:
        # All newly classified high-priority items not yet pushed
        rows = con.execute(
            """SELECT * FROM news_items
               WHERE relevant=1 AND signal_type IN ({}) AND pending_classification=0
               ORDER BY date DESC LIMIT 10""".format(
                ",".join("?" * len(HIGH_PRIORITY_SIGNALS))
            ),
            list(HIGH_PRIORITY_SIGNALS),
        ).fetchall()

    for row in rows:
        item = dict(row)
        await bot.send_message(
            chat_id=chat_id,
            text=format_alert(item),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        print(f"  Pushed: {item['hash']} — {item['company']}")

    # Send pending finance reviews
    pending = con.execute(
        "SELECT * FROM finance_pending_review WHERE status='pending' AND telegram_msg_id IS NULL"
    ).fetchall()
    print(f"  Finance reviews pending: {len(pending)}")
    for row in pending:
        r = dict(row)
        text, _ = format_review(r)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approuver", callback_data=f"approve:{r['id']}"),
            InlineKeyboardButton("❌ Rejeter",   callback_data=f"reject:{r['id']}"),
        ]])
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        con.execute(
            "UPDATE finance_pending_review SET telegram_msg_id=? WHERE id=?",
            (msg.message_id, r["id"]),
        )
        con.commit()
        print(f"  Finance review sent: id={r['id']} ({r['company']})")

    # Send pending source reviews (capped at 10 per push to avoid flooding)
    pending_src = con.execute(
        "SELECT * FROM pending_sources WHERE decision='pending' AND telegram_msg_id IS NULL LIMIT 10"
    ).fetchall()
    print(f"  Source reviews pending: {len(pending_src)}")
    for row in pending_src:
        r = dict(row)
        text = format_source_review(r)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approuver", callback_data=f"approve_src:{r['id']}"),
            InlineKeyboardButton("❌ Rejeter",   callback_data=f"reject_src:{r['id']}"),
        ]])
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        con.execute(
            "UPDATE pending_sources SET telegram_msg_id=? WHERE id=?",
            (msg.message_id, r["id"]),
        )
        con.commit()
        print(f"  Source review sent: id={r['id']} ({r['company_id']} / {r['type']})")

    con.close()


async def cmd_summary(token: str, chat_id: str) -> None:
    from telegram import Bot
    bot = Bot(token=token)
    con = get_con()

    rows = con.execute(
        """SELECT * FROM news_items
           WHERE relevant=1 AND pending_classification=0
             AND signal_type NOT IN ({})
           ORDER BY date DESC LIMIT 20""".format(
            ",".join("?" * len(HIGH_PRIORITY_SIGNALS))
        ),
        list(HIGH_PRIORITY_SIGNALS),
    ).fetchall()

    if not rows:
        print("No non-priority items to summarize.")
        con.close()
        return

    date_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    lines = [f"📊 <b>Résumé quotidien — {date_str}</b>\n"]
    for row in rows:
        item = dict(row)
        icon = CATEGORY_ICONS.get(item["category"], "🏭")
        signal = SIGNAL_LABELS.get(item["signal_type"], "📰")
        lines.append(f"{icon} <b>{item['company']}</b> — {signal}\n{item['title']}\n")

    text = "\n".join(lines)[:4096]
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
    print(f"Summary sent: {len(rows)} items")
    con.close()


def cmd_serve(token: str, chat_id: str) -> None:
    from telegram import Update
    from telegram.ext import Application, CallbackQueryHandler, ContextTypes

    async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if ":" not in data:
            return

        # Route by prefix: approve_src / reject_src → source review; approve / reject → finance
        if data.startswith("approve_src:") or data.startswith("reject_src:"):
            action, src_id_str = data.split(":", 1)
            src_id = int(src_id_str)
            con = get_con()
            row = con.execute("SELECT * FROM pending_sources WHERE id=?", (src_id,)).fetchone()
            if not row:
                await query.edit_message_text("❓ Source introuvable.")
                con.close()
                return
            r = dict(row)
            now = datetime.now(timezone.utc).isoformat()
            if action == "approve_src":
                write_approved_source(r, con)
                con.execute(
                    "UPDATE pending_sources SET decision='approved', decided_at=? WHERE id=?",
                    (now, src_id),
                )
                con.commit()
                await query.edit_message_text(
                    f"✅ Source approuvée — {r['company_id']} · {r['type']}\n{r['url'][:80]}",
                    parse_mode="HTML",
                )
            elif action == "reject_src":
                con.execute(
                    "UPDATE pending_sources SET decision='rejected', decided_at=? WHERE id=?",
                    (now, src_id),
                )
                con.commit()
                await query.edit_message_text(f"❌ Source rejetée — {r['company_id']} · {r['type']}")
            con.close()
            return

        action, review_id_str = data.split(":", 1)
        review_id = int(review_id_str)

        con = get_con()
        row = con.execute(
            "SELECT * FROM finance_pending_review WHERE id=?", (review_id,)
        ).fetchone()

        if not row:
            await query.edit_message_text("❓ Extraction introuvable.")
            con.close()
            return

        r = dict(row)
        now = datetime.now(timezone.utc).isoformat()

        if action == "approve":
            con.execute(
                """INSERT INTO finance_ledger
                   (company_id, axis, value_num, value_text, source_hash, source_label, event_date, recorded_at)
                   VALUES (?, ?, ?, ?, ?, 'AI extraction', NULL, ?)""",
                (r["company_id"], r["axis"], r["value_num"], r["value_text"], r["source_hash"], now),
            )
            con.execute(
                "UPDATE finance_pending_review SET status='approved' WHERE id=?", (review_id,)
            )
            con.commit()
            await query.edit_message_text(
                f"✅ Approuvé — {r['company']} · {r['axis']} · {r['value_text'] or r['value_num']}",
                parse_mode="HTML",
            )

        elif action == "reject":
            con.execute(
                "UPDATE finance_pending_review SET status='rejected' WHERE id=?", (review_id,)
            )
            con.commit()
            await query.edit_message_text(f"❌ Rejeté — {r['company']} · {r['axis']}")

        con.close()

    # run_polling() manages its own event loop — do not wrap in asyncio.run()
    app = Application.builder().token(token).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    print("Bot serving — waiting for callbacks (Ctrl+C to stop)...")
    app.run_polling(allowed_updates=["callback_query"])


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    push_p = sub.add_parser("push")
    push_p.add_argument("--hash", default=None)
    sub.add_parser("summary")
    sub.add_parser("serve")
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        sys.exit("Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")

    if args.cmd == "push":
        asyncio.run(cmd_push(token, chat_id, getattr(args, "hash", None)))
    elif args.cmd == "summary":
        asyncio.run(cmd_summary(token, chat_id))
    elif args.cmd == "serve":
        cmd_serve(token, chat_id)


if __name__ == "__main__":
    main()
