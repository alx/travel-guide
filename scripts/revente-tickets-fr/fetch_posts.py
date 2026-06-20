#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["praw"]
# ///
"""
Fetch new VENTE posts from r/ReventeTicketsFR and send Telegram notifications.
Also polls available listings for sold signals (VENDU in title, post removed, vouch comments).

Cron on lamai270:
  0 8 * * * cd /path/to/travel-guide && uv run scripts/revente-tickets-fr/fetch_posts.py >> logs/revente-fetch.log 2>&1

Requires in .env:
  REVENTE_BOT_TOKEN    — Telegram bot token
  REVENTE_CHAT_ID      — personal chat ID
  REDDIT_CLIENT_ID     — Reddit OAuth app client ID
  REDDIT_CLIENT_SECRET — Reddit OAuth app client secret
  REDDIT_USER_AGENT    — e.g. "revente-tickets-fr/1.0 by /u/yourname"
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "revente-tickets-fr" / "state.db"


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


# ── Telegram ──────────────────────────────────────────────────────────────────

def tg_send(token: str, chat_id: str, text: str) -> int:
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data["result"]["message_id"]


# ── Reddit (PRAW) ─────────────────────────────────────────────────────────────

def init_reddit():
    import praw
    return praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=os.environ.get("REDDIT_USER_AGENT", "revente-tickets-fr/1.0"),
    )


def fetch_vente_posts(reddit) -> list[dict]:
    sub = reddit.subreddit("ReventeTicketsFR")
    results = sub.search(
        'flair:"🎟️ VENTE"',
        sort="new",
        time_filter="day",
        limit=100,
    )
    posts = []
    for p in results:
        posts.append({
            "id": p.id,
            "title": p.title,
            "selftext": (p.selftext or "").strip(),
            "url": f"https://www.reddit.com{p.permalink}",
            "created_utc": int(p.created_utc),
        })
    return posts


def check_sold_signals(reddit, post_id: str) -> list[str]:
    """Return list of sold signals: title_vendu, post_removed, vouch_comment."""
    signals = []
    try:
        submission = reddit.submission(id=post_id)
        title = submission.title.lower()
        selftext = (submission.selftext or "").lower()
        removed = (
            bool(submission.removed_by_category)
            or selftext in ("[removed]", "[deleted]")
        )

        vendu_kw = ("vendu", "sold", "vente terminée", "[v]", "✅", "transaction ok")
        if any(kw in title for kw in vendu_kw):
            signals.append("title_vendu")
        if removed:
            signals.append("post_removed")

        submission.comments.replace_more(limit=0)
        vouch_kw = ("vouch", "transaction réussie", "transaction ok", "vendu", "+1")
        for comment in list(submission.comments)[:20]:
            body = (comment.body or "").lower()
            if any(kw in body for kw in vouch_kw):
                signals.append("vouch_comment")
                break

    except Exception as e:
        print(f"  ⚠ Could not fetch post {post_id}: {e}", file=sys.stderr)
    return signals


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    load_env()
    token = os.environ.get("REVENTE_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("REVENTE_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        sys.exit("ERROR: REVENTE_BOT_TOKEN and REVENTE_CHAT_ID required in .env")

    if not os.environ.get("REDDIT_CLIENT_ID"):
        sys.exit("ERROR: REDDIT_CLIENT_ID not set in .env")

    reddit = init_reddit()

    sys.path.insert(0, str(Path(__file__).parent))
    from init_db import init
    con = init(DB_PATH)

    # ── 1. New posts ──────────────────────────────────────────────────────
    print("Fetching new VENTE posts…")
    try:
        posts = fetch_vente_posts(reddit)
    except Exception as e:
        sys.exit(f"ERROR: could not fetch Reddit posts: {e}")

    new_count = 0
    for post in posts:
        if con.execute("SELECT 1 FROM listings WHERE id = ?", (post["id"],)).fetchone():
            continue

        con.execute(
            "INSERT INTO listings (id, reddit_url, title, selftext, created_utc, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (post["id"], post["url"], post["title"], post["selftext"], post["created_utc"]),
        )
        con.commit()

        preview = post["selftext"][:400].strip()
        text = (
            f"🎟️ <b>Nouveau post VENTE</b>\n\n"
            f"<b>{post['title']}</b>\n"
            + (f"\n{preview}\n" if preview else "")
            + f"\n<a href=\"{post['url']}\">Voir sur Reddit</a>\n\n"
            f"<i>Réponds à ce message :</i>\n"
            f"venue: ...\n"
            f"artist: ...\n"
            f"date: AAAA-MM-JJ\n"
            f"tickets: N\n"
            f"price: XX\n"
            f"category: Fosse / Assis / ..."
        )
        try:
            msg_id = tg_send(token, chat_id, text)
            con.execute(
                "UPDATE listings SET telegram_notification_msg_id = ? WHERE id = ?",
                (msg_id, post["id"]),
            )
            con.commit()
            print(f"  ✅ {post['title'][:70]} (msg_id={msg_id})")
            new_count += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"  ⚠ Telegram failed for {post['id']}: {e}", file=sys.stderr)

    print(f"New posts: {new_count}")

    # ── 2. Sold detection on available listings ───────────────────────────
    print("Checking available listings for sold signals…")
    available = con.execute(
        "SELECT id, title, reddit_url FROM listings WHERE status = 'available'"
    ).fetchall()

    for row in available:
        signals = check_sold_signals(reddit, row["id"])
        if not signals:
            time.sleep(0.5)
            continue
        print(f"  Signals for {row['id']}: {signals}")
        text = (
            f"❓ <b>Post vendu ?</b>\n\n"
            f"<b>{row['title']}</b>\n"
            f"<a href=\"{row['reddit_url']}\">Voir le post</a>\n\n"
            f"Signaux : {', '.join(signals)}\n\n"
            f"<i>Réponds à ce message : <b>oui</b> (marquer vendu) / <b>non</b> (garder disponible)</i>"
        )
        try:
            msg_id = tg_send(token, chat_id, text)
            con.execute(
                "UPDATE listings SET telegram_sold_check_msg_id = ? WHERE id = ?",
                (msg_id, row["id"]),
            )
            con.commit()
            time.sleep(0.5)
        except Exception as e:
            print(f"  ⚠ Telegram failed for sold check {row['id']}: {e}", file=sys.stderr)

    con.close()
    print("Done.")


if __name__ == "__main__":
    main()
