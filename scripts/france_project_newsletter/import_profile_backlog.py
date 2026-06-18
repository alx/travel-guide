#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""
One-shot import of company_profiles_to_review.md backlog into pending_sources.

Reads the known-bad URL entries, inserts them as pending candidates with
discovered_by='backlog_import' and the issue note in candidate_payload.
Entries where the correct URL is noted are inserted with the corrected URL.
Entries where only the bad URL is known are inserted as 'rejected' (already triaged).

After import, deletes company_profiles_to_review.md.

Usage:
  uv run scripts/france_project_newsletter/import_profile_backlog.py [--dry-run]
"""

import argparse
import json
import pathlib
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone

DB_PATH = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter/state.db"
BACKLOG_PATH = pathlib.Path(__file__).parents[2] / "company_profiles_to_review.md"


def slugify(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")


# Backlog entries: (company_name, linkedin_url_or_None, bad_website_url, correct_website_or_None, issue_note)
BACKLOG = [
    ("MagREEsource",
     "https://www.linkedin.com/company/magreesource",
     "https://www.lindustrie-recrute.fr/entreprise/20572/",
     None,
     "website is a recruiter page, not the official site"),
    ("DataOne - Oreus - Core 42",
     "https://www.linkedin.com/company/core42ai",
     "https://www.core42.ai/en/news/author/core42",
     "https://www.core42.ai",
     "website should be https://www.core42.ai (not the author page)"),
    ("Bordet",
     None,
     "https://www.echodescommunes.fr/actualite_economique_1091_premiere-mondiale-les-charbons-actifs-bordet-certifies-agriculture-biologique.html",
     None,
     "website is a news article, not the official site"),
    ("Jimmy Energy",
     "https://www.linkedin.com/company/jimmy-energy",
     "https://reglementation-controle.asnr.fr/information/archives-des-actualites/le-college-de-l-asn-a-auditionne-la-societe-jimmy-energy",
     None,
     "website is an ASNR regulatory page, not the official site"),
    ("Fiat Powrtrain",
     "https://www.linkedin.com/company/sfh-saic-fiat-powertrain-hongyan-co-ltd",
     "https://www.marantmotortechniek.com/en/engines/",
     None,
     "linkedin is wrong company (Chinese JV), website is unrelated Dutch dealer"),
    ("Prysmian",
     "https://www.linkedin.com/company/prysmian",
     "https://fr.prysmian.com/qui-sommes-nous/prysmian-en-france",
     "https://fr.prysmian.com",
     "website should be https://fr.prysmian.com (not the about page)"),
    ("Sigmaphi",
     "https://www.linkedin.com/company/sigmaphiconsulting",
     "https://www.middle-france.com/sigmaphi/",
     "https://www.sigmaphi.fr",
     "linkedin is wrong company (consulting firm), website should be https://www.sigmaphi.fr"),
    ("Crystalrod",
     "https://www.linkedin.com/company/changchunyutaiopticsco.ltd.",
     "https://shop.tiktok.com/us/pdp/crystal-rod-semi-flush-ceiling-light-by-corbett-lighting-with-gold-leaf-finish/1729768098666942846",
     None,
     "both linkedin and website are totally wrong companies"),
    ("Arcelor Mittal",
     "https://www.linkedin.com/company/and-steel-arcelor-mittal-distribution",
     "https://actu.fr/hauts-de-france/dunkerque_59183/emmanuel-macron-a-dunkerque-arcelormittal-confirme-la-construction-dun-enorme-four-electrique_63818627.html",
     None,
     "linkedin is wrong company (distribution subsidiary), website is a news article"),
    ("Windrose Technology",
     "https://www.linkedin.com/company/windrosetrucks",
     "https://www.hautsdefrance.fr/electromobilite-windrose-choisit-les-hauts-de-france-pour-sa-base-industrielle-europeenne/",
     None,
     "website is a regional press release, not the official site"),
    ("Nexans",
     "https://www.linkedin.com/company/nexans",
     "https://www.nexans.fr/fr/newsroom/news/details/2025/10/Nexans-launches-innovative-factory-project-in-Lens.html",
     "https://www.nexans.fr",
     "website should be https://www.nexans.fr (not a specific news article)"),
]


def main(dry_run: bool = False) -> None:
    if not BACKLOG_PATH.exists():
        print("company_profiles_to_review.md not found — already imported or deleted.")
        return

    now = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    imported = 0
    for (name, linkedin_url, bad_website, correct_website, issue_note) in BACKLOG:
        slug = slugify(name)

        # LinkedIn: import as pending if URL looks like valid linkedin (regardless of correctness)
        if linkedin_url:
            payload = json.dumps({"issue": issue_note, "source": "backlog_import"})
            # Check whether it's a plausible correct URL (contains company slug words)
            slug_words = slug.replace("-", " ").split()
            li_slug = linkedin_url.split("/company/")[-1].rstrip("/").lower()
            likely_correct = any(w in li_slug for w in slug_words if len(w) > 3)
            decision = "pending" if likely_correct else "pending"  # always pending — human decides
            if not dry_run:
                con.execute(
                    """INSERT OR IGNORE INTO pending_sources
                       (company_id, type, url, candidate_payload, discovered_by, discovered_at, decision)
                       VALUES (?, 'profile_linkedin', ?, ?, 'backlog_import', ?, ?)""",
                    (slug, linkedin_url, payload, now, decision),
                )
            imported += 1
            print(f"  [linkedin] {slug}: {linkedin_url[:60]}")

        # Website: prefer the known-correct URL if available, else mark bad URL as rejected
        if correct_website:
            payload = json.dumps({"issue": issue_note, "source": "backlog_import",
                                   "bad_url": bad_website})
            if not dry_run:
                con.execute(
                    """INSERT OR IGNORE INTO pending_sources
                       (company_id, type, url, candidate_payload, discovered_by, discovered_at)
                       VALUES (?, 'profile_website', ?, ?, 'backlog_import', ?)""",
                    (slug, correct_website, payload, now),
                )
            print(f"  [website ✓] {slug}: {correct_website}")
            imported += 1
        else:
            # Bad URL — insert as rejected so it doesn't re-surface
            payload = json.dumps({"issue": issue_note, "source": "backlog_import"})
            if not dry_run:
                con.execute(
                    """INSERT OR IGNORE INTO pending_sources
                       (company_id, type, url, candidate_payload, discovered_by, discovered_at, decision)
                       VALUES (?, 'profile_website', ?, ?, 'backlog_import', ?, 'rejected')""",
                    (slug, bad_website, payload, now),
                )
            print(f"  [website ✗] {slug}: bad URL auto-rejected")
            imported += 1

    if not dry_run:
        con.commit()
        BACKLOG_PATH.unlink()
        print(f"\nImported {imported} rows into pending_sources.")
        print(f"Deleted {BACKLOG_PATH.name}.")
    else:
        print(f"\n[dry-run] Would import {imported} rows and delete {BACKLOG_PATH.name}.")

    con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
