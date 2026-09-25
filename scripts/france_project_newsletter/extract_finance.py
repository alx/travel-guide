#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Extract financial figures from classified news items and write to finance_pending_review.
Runs after classify.py. Requires the llama.cpp server.

Only processes items where:
  - relevant=1
  - signal_type IN (funding_round, groundbreaking, production_start, delay, M&A)
  - not already in finance_pending_review

Usage:
  uv run scripts/france_project_newsletter/extract_finance.py [--llm-url URL]
"""

import argparse
import json
import pathlib
import re
import sqlite3
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import gps_config

DB_PATH = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter/state.db"

FINANCE_SIGNALS = {"funding_round", "groundbreaking", "production_start", "delay", "M&A"}

SYSTEM_PROMPT = """You are a financial analyst extracting structured data from French industrial news articles.

Given a news item about a strategic industrial project, extract:
- axis: the type of financial data (capex=capital investment, subsidy=public grant, employment=jobs, stage=project milestone)
- value_num: numeric value in EUR for capex/subsidy (e.g. 300000000 for 300M€), or job count for employment, or null
- value_text: for stage transitions, the new stage name (announced/permitted/under_construction/operational/delayed/cancelled); for capex/subsidy, a human-readable amount string (e.g. "300M€"); null if not applicable
- event_date: approximate date of the event in YYYY-MM format if mentioned, else null

Be conservative: if no clear financial figure or stage transition is mentioned, set value_num and value_text to null.
Respond ONLY with a JSON object with keys: axis, value_num, value_text, event_date. No explanation."""


def extract_json(text: str) -> dict | None:
    """Extract first JSON object from text, handling thinking-model preamble."""
    text = text.strip()
    m = re.search(r"\{[^{}]+\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def build_validated_url(base_url: str) -> str:
    try:
        if "/../" in base_url or re.search(r"/%2e%2e/", base_url, re.IGNORECASE):
            raise ValueError("Invalid path")
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Invalid protocol")
        if not parsed.hostname:
            raise ValueError("Invalid host")
        allowed_domains = ["example.com"]  # add your allowed domains here
        if parsed.hostname.lower() not in allowed_domains:
            raise ValueError("Invalid host")
        parsed = parsed._replace(path=f"{parsed.path.rstrip('/')}/health")
        return urlunparse(parsed)
    except Exception:
        raise ValueError("Invalid URL")


def check_server(url: str) -> bool:
    try:
        r = requests.get(build_validated_url(url), timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False


def extract_item(item: dict, llm_url: str, session: requests.Session) -> dict | None:
    user_msg = (
        f"Company: {item['company']} (sector: {item['category']})\n"
        f"Signal type: {item['signal_type']}\n"
        f"Title: {item['title']}\n"
        f"Summary: {item['summary']}\n\n"
        "Extract the financial or operational data from this article."
    )
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.0,
        "max_tokens": 1536,  # budget for Qwen3.5 thinking tokens + JSON answer
        "stream": False,
    }
    try:
        resp = session.post(f"{llm_url}/v1/chat/completions", json=payload, timeout=90)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        result = extract_json(content)
        if result is None:
            print(f"  [PARSE ERR] {item['hash']}: no JSON in {content!r:.80}")
        return result
    except Exception as exc:
        print(f"  [LLM ERR] {item['hash']}: {exc}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-url", default=None)
    args = parser.parse_args()

    llm_url = (args.llm_url or gps_config.llm_url()).rstrip("/")

    print(f"Checking llama.cpp server at {llm_url}...")
    if not check_server(llm_url):
        print("  [SKIP] Server unreachable.")
        return

    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row

    # Process stubs inserted by classify.py (value_num and value_text both NULL)
    # as well as any news_items with finance signals not yet queued at all.
    stubs = con.execute(
        """SELECT pr.id, pr.company_id, pr.company, pr.axis, pr.source_hash,
                  pr.source_url, pr.source_title,
                  ni.category, ni.summary, ni.signal_type, ni.title
           FROM finance_pending_review pr
           JOIN news_items ni ON ni.hash = pr.source_hash
           WHERE pr.status = 'pending'
             AND pr.value_num IS NULL AND pr.value_text IS NULL"""
    ).fetchall()

    # Also pick up any finance-signal items not yet in pending_review at all
    already_queued = {
        row[0]
        for row in con.execute("SELECT source_hash FROM finance_pending_review")
    }
    new_rows = con.execute(
        """SELECT * FROM news_items
           WHERE relevant=1 AND signal_type IN ({})
             AND pending_classification=0""".format(
            ",".join("?" * len(FINANCE_SIGNALS))
        ),
        list(FINANCE_SIGNALS),
    ).fetchall()
    new_candidates = [dict(r) for r in new_rows if r["hash"] not in already_queued]

    if not stubs and not new_candidates:
        print("No finance items to extract.")
        con.close()
        return

    print(f"Extracting: {len(stubs)} stubs to fill + {len(new_candidates)} new items")
    session = requests.Session()
    now = datetime.now(timezone.utc).isoformat()
    queued = 0

    # Fill stubs (created by classify.py with no values)
    for stub in stubs:
        s = dict(stub)
        item = {
            "company": s["company"], "category": s["category"],
            "signal_type": s["signal_type"], "title": s["title"],
            "summary": s["summary"], "hash": s["source_hash"],
        }
        print(f"  filling stub: {s['company']} — {s['title'][:60]}")
        result = extract_item(item, llm_url, session)
        if result is None or (result.get("value_num") is None and result.get("value_text") is None):
            print(f"    → no values extracted")
            continue
        con.execute(
            """UPDATE finance_pending_review
               SET axis=?, value_num=?, value_text=?, extracted_at=?
               WHERE id=?""",
            (result.get("axis", s["axis"]), result.get("value_num"),
             result.get("value_text"), now, s["id"]),
        )
        print(f"    → axis={result.get('axis')}  value={result.get('value_text')}  num={result.get('value_num')}")
        queued += 1

    # Insert new items not yet queued at all
    for item in new_candidates:
        result = extract_item(item, llm_url, session)
        if result is None:
            continue
        if result.get("value_num") is None and result.get("value_text") is None:
            continue
        con.execute(
            """INSERT INTO finance_pending_review
               (company_id, company, axis, value_num, value_text,
                source_hash, source_url, source_title, extracted_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                item["company_id"],
                item["company"],
                result.get("axis", "capex"),
                result.get("value_num"),
                result.get("value_text"),
                item["hash"],
                item["url"],
                item["title"],
                now,
            ),
        )
        queued += 1

    con.commit()
    con.close()
    print(f"Finance extractions queued for review: {queued}")


if __name__ == "__main__":
    main()
