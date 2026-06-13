#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
AI classification pipeline (Phase 1): relevance filter + signal classifier.
Processes news_items rows where pending_classification=1.

Requires a llama.cpp server running locally (default: http://lamai270:8080).
If the server is unreachable, exits cleanly — items remain pending for next run.

Usage:
  uv run scripts/france_project_newsletter/classify.py [--batch-size N] [--llm-url URL]

Env vars:
  LLAMA_CPP_URL  (default: http://lamai270:8080)
"""

import argparse
import json
import pathlib
import re
import sqlite3
from datetime import datetime, timezone

import requests

DB_PATH = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter/state.db"
DEFAULT_LLM_URL = "http://lamai270:8181"

SIGNAL_TYPES = [
    "funding_round", "groundbreaking", "production_start",
    "delay", "regulatory", "partnership", "M&A", "other",
]

FINANCE_SIGNALS = {"funding_round", "groundbreaking", "production_start", "delay", "M&A"}

SYSTEM_PROMPT = """You are an industrial intelligence analyst specializing in French strategic industrial projects.
You will be given a news article title and summary, along with the target company name and its sector.

Your task:
1. Decide if this article is genuinely about the target company's industrial project (not just a passing mention).
2. Classify the primary signal type of the article.

Signal types:
- funding_round: new private investment, equity raise, venture capital, debt financing
- groundbreaking: construction start, first stone ceremony, permitting, site preparation
- production_start: factory opening, first production, commissioning, ramp-up
- delay: project postponed, cancelled, paused, timeline revised negatively
- regulatory: permit obtained/denied, environmental ruling, government approval/rejection
- partnership: joint venture, supply agreement, technology licensing, MOU
- M&A: acquisition, merger, takeover, stake purchase
- other: anything else relevant but not matching above

Rules for relevance:
- The article must be SPECIFICALLY about this company or its named project — not just from the same region or sector.
- A shared département, région, or industry sector is NOT sufficient to be relevant.
- If the article is about a different company that happens to be in the same area, mark relevant=false.

Respond ONLY with a JSON object with keys "relevant" (boolean) and "signal_type" (string). No explanation."""


def extract_json(text: str) -> dict | None:
    """Extract first JSON object from text, handling thinking-model preamble."""
    text = text.strip()
    m = re.search(r"\{[^{}]+\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def check_server(url: str, timeout: int = 5) -> bool:
    try:
        r = requests.get(f"{url}/health", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def classify_item(item: dict, llm_url: str, session: requests.Session) -> dict | None:
    user_msg = (
        f"Company: {item['company']} (sector: {item['category']})\n"
        f"Title: {item['title']}\n"
        f"Summary: {item['summary']}\n\n"
        "Is this article genuinely about this company's industrial project? "
        "What is the primary signal type?"
    )
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.0,
        "max_tokens": 1536,  # Qwen3.5 thinking tokens can be ~1000-1400 before the JSON answer
        "stream": False,
    }
    try:
        resp = session.post(f"{llm_url}/v1/chat/completions", json=payload, timeout=60)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        result = extract_json(content)
        if result is None:
            print(f"  [PARSE ERR] {item['hash']} ({item['company']}): no JSON in {content!r:.80}")
        return result
    except Exception as exc:
        print(f"  [LLM ERR] {item['hash']} ({item['company']}): {exc}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--llm-url", default=None)
    args = parser.parse_args()

    llm_url = (args.llm_url or __import__("os").environ.get("LLAMA_CPP_URL", DEFAULT_LLM_URL)).rstrip("/")

    print(f"Checking llama.cpp server at {llm_url}...")
    if not check_server(llm_url):
        print("  [SKIP] Server unreachable — items remain pending for next run.")
        return

    print("  Server OK.")

    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "SELECT * FROM news_items WHERE pending_classification=1 LIMIT ?",
        (args.batch_size,),
    ).fetchall()

    if not rows:
        print("No items pending classification.")
        con.close()
        return

    print(f"Classifying {len(rows)} items...")
    session = requests.Session()
    now = datetime.now(timezone.utc).isoformat()

    classified = 0
    finance_queued = 0

    for i, row in enumerate(rows, 1):
        item = dict(row)
        title_short = item["title"][:70]
        print(f"\n[{i}/{len(rows)}] {item['company']} ({item['source']})")
        print(f"  title   : {title_short}")

        result = classify_item(item, llm_url, session)

        if result is None:
            print(f"  result  : SKIP (LLM error)")
            continue

        relevant = 1 if result.get("relevant") else 0
        signal_type = result.get("signal_type", "other")
        if signal_type not in SIGNAL_TYPES:
            print(f"  result  : unknown signal_type {signal_type!r} → other")
            signal_type = "other"

        relevance_label = "✓ relevant" if relevant else "✗ filtered"
        print(f"  result  : {relevance_label}  signal={signal_type}")

        con.execute(
            """UPDATE news_items
               SET pending_classification=0, relevant=?, signal_type=?, classified_at=?
               WHERE hash=?""",
            (relevant, signal_type, now, item["hash"]),
        )

        if relevant and signal_type in FINANCE_SIGNALS:
            con.execute(
                """INSERT INTO finance_pending_review
                   (company_id, company, axis, source_hash, source_url, source_title, extracted_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    item["company_id"],
                    item["company"],
                    _signal_to_axis(signal_type),
                    item["hash"],
                    item["url"],
                    item["title"],
                    now,
                ),
            )
            print(f"  finance : queued for review (axis={_signal_to_axis(signal_type)})")
            finance_queued += 1

        classified += 1

    con.commit()
    remaining = con.execute(
        "SELECT COUNT(*) FROM news_items WHERE pending_classification=1"
    ).fetchone()[0]
    con.close()

    print(f"\n{'─'*50}")
    print(f"Classified: {classified}  Finance queued: {finance_queued}  Still pending: {remaining}")


def _signal_to_axis(signal_type: str) -> str:
    return {
        "funding_round": "capex",
        "groundbreaking": "stage",
        "production_start": "stage",
        "delay": "stage",
        "M&A": "capex",
    }.get(signal_type, "capex")


if __name__ == "__main__":
    main()
