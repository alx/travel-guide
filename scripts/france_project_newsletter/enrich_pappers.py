#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Enrich company_enrichment table with Pappers data (SIRENE + bilans).

Pappers API docs: https://api.pappers.fr/documentation
Free tier: 100 requests/month. Rate-limit: 1 req/s.

Usage:
  uv run scripts/france_project_newsletter/enrich_pappers.py [--force]

Env vars:
  PAPPERS_API_KEY

--force re-enriches companies already in company_enrichment.
"""

import argparse
import json
import os
import pathlib
import sqlite3
import time
from datetime import datetime, timezone

import requests

DB_PATH = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter/state.db"
GEOJSON_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/locations.geojson"
BASE_URL = "https://api.pappers.fr/v2"


def load_env() -> None:
    env_file = pathlib.Path(__file__).parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def search_company(name: str, api_key: str, session: requests.Session) -> dict | None:
    """Search Pappers by company name, return first match."""
    try:
        r = session.get(
            f"{BASE_URL}/entreprise",
            params={"api_token": api_key, "q": name, "precision": "standard"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("resultats", [])
        return results[0] if results else None
    except Exception as exc:
        print(f"  [PAPPERS ERR] search '{name}': {exc}")
        return None


def fetch_bilans(siren: str, api_key: str, session: requests.Session) -> dict | None:
    """Fetch the most recent filed bilan for a SIREN."""
    try:
        r = session.get(
            f"{BASE_URL}/entreprise",
            params={"api_token": api_key, "siren": siren, "extrait_inpi": "false"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        bilans = data.get("finances", [])
        return bilans[0] if bilans else None
    except Exception as exc:
        print(f"  [PAPPERS ERR] bilans '{siren}': {exc}")
        return None


def headcount_label(tranche: str | None) -> str | None:
    """Map Pappers tranche_effectif code to readable band."""
    mapping = {
        "NN": None, "00": "0", "01": "1-2", "02": "3-5", "03": "6-9",
        "11": "10-19", "12": "20-49", "21": "50-99", "22": "100-199",
        "31": "200-249", "32": "250-499", "41": "500-999", "42": "1000-1999",
        "51": "2000-4999", "52": "5000-9999", "53": "10000+",
    }
    return mapping.get(tranche or "", tranche)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    load_env()
    api_key = os.environ.get("PAPPERS_API_KEY")
    if not api_key:
        print("[SKIP] PAPPERS_API_KEY not set.")
        return

    features = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))["features"]

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row

    already_done = set()
    if not args.force:
        already_done = {
            row[0]
            for row in con.execute("SELECT company_id FROM company_enrichment")
        }

    session = requests.Session()
    session.headers["Accept"] = "application/json"
    now = datetime.now(timezone.utc).isoformat()

    enriched = 0
    skipped = 0

    for feat in features:
        company_id = feat.get("id", "")
        name = feat["properties"].get("name", "")

        if company_id in already_done:
            skipped += 1
            continue

        print(f"  Enriching {name}...")
        time.sleep(1.1)  # Pappers rate limit: 1 req/s

        match = search_company(name, api_key, session)
        if not match:
            print(f"    [NOT FOUND] {name}")
            continue

        siren = match.get("siren", "")
        siret = match.get("siege", {}).get("siret", "")
        legal_form = match.get("forme_juridique", "")
        headcount = headcount_label(match.get("tranche_effectif"))
        naf = match.get("code_naf", "")
        address_parts = [
            match.get("siege", {}).get("adresse_ligne_1", ""),
            match.get("siege", {}).get("code_postal", ""),
            match.get("siege", {}).get("ville", ""),
        ]
        address = ", ".join(p for p in address_parts if p)

        # Fetch bilans (separate call)
        bilan_year = revenue = net_result = equity = total_assets = None
        if siren:
            time.sleep(1.1)
            bilan = fetch_bilans(siren, api_key, session)
            if bilan:
                bilan_year = bilan.get("annee")
                revenue = bilan.get("chiffre_affaires")
                net_result = bilan.get("resultat")
                equity = bilan.get("capitaux_propres")
                total_assets = bilan.get("total_bilan")

        con.execute(
            """INSERT OR REPLACE INTO company_enrichment
               (company_id, siren, siret, legal_form, headcount_band, naf_code,
                registered_address, bilan_year, revenue_eur, net_result_eur,
                equity_eur, total_assets_eur, enriched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                company_id, siren, siret, legal_form, headcount, naf, address,
                bilan_year, revenue, net_result, equity, total_assets, now,
            ),
        )
        con.commit()
        enriched += 1

    con.close()
    print(f"\nDone: {enriched} enriched, {skipped} skipped (already done).")


if __name__ == "__main__":
    main()
