#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "lxml"]
# ///
"""
Enrich company_enrichment and finance_ledger with France 2030 laureate data.

Source: https://www.gouvernement.fr/france-2030 and the open data portal
  https://www.data.gouv.fr/fr/datasets/laureats-france-2030/

The laureate CSV published on data.gouv.fr has columns including:
  nom_du_laureat, montant_aide, appel_a_projets, date_de_decision

This script downloads the latest CSV, matches rows to our company corpus by
name similarity, and writes matches directly to finance_ledger (authoritative
source — no review queue needed).

Usage:
  uv run scripts/france_project_newsletter/enrich_france2030.py [--csv PATH]

--csv PATH: use a local CSV instead of downloading (for offline testing).
"""

import argparse
import csv
import io
import json
import os
import pathlib
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import requests

DB_PATH = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter/state.db"
GEOJSON_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/locations.geojson"

# data.gouv.fr dataset — check for updated resource URL if stale
LAUREATS_CSV_URL = "https://www.data.gouv.fr/fr/datasets/r/laureats-france-2030.csv"


def load_env() -> None:
    env_file = pathlib.Path(__file__).parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def normalize(s: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.split())


def build_validated_url(base_url: str) -> str:
    try:
        # Minimal path validation
        if "/../" in base_url or re.search(r"/%2e%2e/", base_url, re.IGNORECASE):
            raise ValueError("Invalid path")
        
        parsed = urlparse(base_url)
        
        # Protocol + host checks
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Invalid protocol")
        if not parsed.hostname:
            raise ValueError("Invalid host")
        allowed_domains = ["www.data.gouv.fr"]
        if parsed.hostname.lower() not in allowed_domains:
            raise ValueError("Invalid host")
        
        return urlunparse(parsed)
    except Exception:
        raise ValueError("Invalid URL")


def load_csv(path_or_url: str) -> list[dict]:
    if path_or_url.startswith("http"):
        try:
            validated_url = build_validated_url(path_or_url)
            r = requests.get(validated_url, timeout=30)
            r.raise_for_status()
            text = r.text
        except Exception as exc:
            print(f"[ERR] Could not download laureats CSV: {exc}")
            return []
    else:
        text = pathlib.Path(path_or_url).read_text(encoding="utf-8-sig")

    rows = []
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    for row in reader:
        rows.append(row)
    return rows


def match_company(laureate_name: str, features: list[dict]) -> tuple[str, str] | None:
    """Return (company_id, company_name) for the best matching feature, or None."""
    norm_laureate = normalize(laureate_name)
    for feat in features:
        name = feat["properties"].get("name", "")
        norm_name = normalize(name)
        # Check if either is a substring of the other (handles "Safran" matching "Safran Aerosystems")
        if norm_name in norm_laureate or norm_laureate in norm_name:
            return feat.get("id", ""), name
    return None


def parse_amount(s: str) -> float | None:
    """Parse French number format: '12 345 678' or '12345678' → float."""
    s = s.replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    try:
        return float(s)
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=None, help="Path or URL to laureats CSV")
    args = parser.parse_args()

    load_env()

    source = args.csv or LAUREATS_CSV_URL
    print(f"Loading France 2030 laureates from: {source}")
    rows = load_csv(source)
    if not rows:
        print("No rows loaded — check CSV source.")
        return

    print(f"  {len(rows)} laureate rows loaded.")

    features = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))["features"]

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    now = datetime.now(timezone.utc).isoformat()

    matched = 0
    unmatched: list[str] = []

    for row in rows:
        # Column names vary by CSV version — try common variants
        laureate_name = (
            row.get("nom_du_laureat") or row.get("Nom du lauréat") or
            row.get("laureat") or row.get("Lauréat") or ""
        ).strip()
        amount_str = (
            row.get("montant_aide") or row.get("Montant de l'aide") or
            row.get("montant") or ""
        ).strip()
        call = (
            row.get("appel_a_projets") or row.get("Appel à projets") or
            row.get("aap") or ""
        ).strip()
        date_str = (
            row.get("date_de_decision") or row.get("Date de décision") or ""
        ).strip()

        if not laureate_name:
            continue

        result = match_company(laureate_name, features)
        if not result:
            unmatched.append(laureate_name)
            continue

        company_id, company_name = result
        amount = parse_amount(amount_str)

        # Update company_enrichment France 2030 fields
        con.execute(
            """INSERT INTO company_enrichment (company_id, france2030_project, france2030_amount, france2030_call, enriched_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(company_id) DO UPDATE SET
                 france2030_project=excluded.france2030_project,
                 france2030_amount=excluded.france2030_amount,
                 france2030_call=excluded.france2030_call,
                 enriched_at=excluded.enriched_at""",
            (company_id, laureate_name, amount, call, now),
        )

        # Write directly to finance_ledger (authoritative — no review queue)
        if amount is not None:
            con.execute(
                """INSERT INTO finance_ledger
                   (company_id, axis, value_num, value_text, source_hash, source_label, event_date, recorded_at)
                   VALUES (?, 'subsidy', ?, ?, NULL, 'France 2030', ?, ?)""",
                (company_id, amount, f"{amount/1e6:.1f}M€ — {call}", date_str or None, now),
            )

        matched += 1
        print(f"  ✓ {laureate_name} → {company_name} ({amount/1e6:.1f}M€)" if amount else f"  ✓ {laureate_name} → {company_name}")

    con.commit()
    con.close()

    print(f"\nMatched: {matched}  Unmatched: {len(unmatched)}")
    if unmatched:
        print("Unmatched laureates (first 10):")
        for name in unmatched[:10]:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
