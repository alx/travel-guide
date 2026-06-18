#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""
Migrate locations.geojson to the v2 schema (ADR-0006) and generate companies.json.

Changes:
  - Adds properties.company_id (slug) to every project feature
  - Converts feeds scalar block → feeds.sources typed list (project-level sources only)
  - Extracts company-level sources (company_rss) into companies.json
  - Fixes the france-projet-029 ID collision: Eclairion → france-projet-028-eclairion
  - Preserves changedetection_uuid / rss_uuid on features (migrated to SQLite in Slice 2)
  - Preserves feeds.keywords (project-level context)

Idempotent: safe to re-run. Detects already-migrated features by the presence of
feeds.sources and skips them.

Usage:
  uv run scripts/france_project_newsletter/migrate_feeds_v2.py [--dry-run]
"""

import argparse
import json
import pathlib
import re
import unicodedata

GEOJSON_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/locations.geojson"
COMPANIES_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/companies.json"


def slugify(name: str) -> str:
    """Convert a company display name to a URL-safe slug."""
    # Normalise accents
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    # Lowercase
    name = name.lower()
    # Replace non-alphanumeric runs with a hyphen
    name = re.sub(r"[^a-z0-9]+", "-", name)
    # Strip leading/trailing hyphens
    name = name.strip("-")
    return name


def migrate(dry_run: bool = False) -> None:
    data = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))
    features = data["features"]

    # --- Pass 1: collect company-level data from all features ---
    # Keyed by slug. First feature wins for name/company_url/company_rss.
    companies: dict[str, dict] = {}

    for feat in features:
        name = feat["properties"].get("name", "")
        slug = slugify(name)
        if slug not in companies:
            feeds = feat["properties"].get("feeds", {})
            companies[slug] = {
                "name": name,
                "company_url": feeds.get("company_url") or "",
                "sources": [],
            }
            company_rss = feeds.get("company_rss")
            if company_rss:
                companies[slug]["sources"].append({"type": "company_rss", "url": company_rss})

    # --- Pass 2: migrate each feature ---
    migrated = 0
    skipped = 0
    id_collision_fixed = False

    for feat in features:
        props = feat["properties"]
        feeds = props.get("feeds", {})

        # Fix the france-projet-029 ID collision (Eclairion duplicated Holosolis's ID)
        if feat.get("id") == "france-projet-029-eclairion":
            feat["id"] = "france-projet-028-eclairion"
            id_collision_fixed = True

        # Add company_id
        name = props.get("name", "")
        slug = slugify(name)
        props["company_id"] = slug

        # Already migrated?
        if "sources" in feeds:
            skipped += 1
            continue

        # Build project-level sources list
        sources = []

        # changedetection source
        cd_url = feeds.get("changedetection_url")
        if cd_url:
            sources.append({"type": "changedetection", "url": cd_url})

        # sector_rss sources
        for rss_url in feeds.get("sector_rss", []):
            sources.append({"type": "sector_rss", "url": rss_url})

        # Replace feeds block: keep keywords + UUIDs, add sources, remove company-level fields
        new_feeds: dict = {"sources": sources}

        # Preserve project-level state fields (moved to SQLite in Slice 2, kept for now)
        if feeds.get("changedetection_uuid"):
            new_feeds["changedetection_uuid"] = feeds["changedetection_uuid"]
        if feeds.get("rss_uuid"):
            new_feeds["rss_uuid"] = feeds["rss_uuid"]

        # Preserve keywords (project-level context for sector RSS matching)
        if feeds.get("keywords"):
            new_feeds["keywords"] = feeds["keywords"]

        props["feeds"] = new_feeds
        migrated += 1

    # Update meta count (stays the same)
    data["_meta"]["count"] = len(features)

    # --- Build final companies.json ---
    # Preserve existing entries if companies.json already exists
    existing_companies: dict = {}
    if COMPANIES_PATH.exists():
        existing_companies = json.loads(COMPANIES_PATH.read_text(encoding="utf-8"))

    merged_companies: dict = {}
    for slug, entry in companies.items():
        if slug in existing_companies:
            # Merge: keep existing but add any missing source URLs
            merged = dict(existing_companies[slug])
            existing_urls = {s["url"] for s in merged.get("sources", [])}
            for src in entry["sources"]:
                if src["url"] not in existing_urls:
                    merged.setdefault("sources", []).append(src)
            merged_companies[slug] = merged
        else:
            merged_companies[slug] = entry

    if not dry_run:
        GEOJSON_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        COMPANIES_PATH.write_text(
            json.dumps(merged_companies, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Written: {GEOJSON_PATH}")
        print(f"Written: {COMPANIES_PATH}")
    else:
        print("[dry-run] Would write GeoJSON and companies.json")

    print(f"\nFeatures migrated:  {migrated}")
    print(f"Features skipped:   {skipped} (already had feeds.sources)")
    print(f"ID collision fixed: {'yes' if id_collision_fixed else 'no (already fixed)'}")
    print(f"Companies:          {len(merged_companies)} unique entries")

    if dry_run:
        # Print a sample
        sample_slug = list(merged_companies.keys())[0]
        print(f"\nSample company ({sample_slug}):")
        print(json.dumps(merged_companies[sample_slug], ensure_ascii=False, indent=2))

        sample_feat = features[0]
        print(f"\nSample feature ({sample_feat.get('id')}):")
        print(json.dumps({
            "id": sample_feat.get("id"),
            "company_id": sample_feat["properties"].get("company_id"),
            "feeds": sample_feat["properties"].get("feeds"),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)
