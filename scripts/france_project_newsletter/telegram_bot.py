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
import os
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter/state.db"

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
