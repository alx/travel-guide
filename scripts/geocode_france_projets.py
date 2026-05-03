# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Geocodes France strategic industrial projects from CSV → GeoJSON.

Usage:
    uv run scripts/geocode_france_projets.py
    uv run scripts/geocode_france_projets.py --output path/to/locations.geojson
    uv run scripts/geocode_france_projets.py --dry-run
"""

import csv
import json
import time
import urllib.parse
import urllib.request
import argparse
import re
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
CSV_PATH = REPO_ROOT / "static/france-grands-projets-strategiques/projets_industriels_france_villes.csv"
DEFAULT_OUTPUT = REPO_ROOT / "static/france-grands-projets-strategiques/locations.geojson"

_HEADERS = {"User-Agent": "maps.girard-davila.net/france-projets"}

# Category detection: first matching rule wins (ordered by specificity)
CATEGORY_RULES = [
    ("Data Center & IA", ["data center", "datacenters", "calcul intensif", "supercalculateurs", "infrastructure dédiée", "drones"]),
    ("Énergie & Nucléaire", ["nucléaire", "réacteur", "photovoltaïque", "hydrogène", "h2 bas carbone", "energy center", "capture et stockage", "charbon végétal", "gigafactory"]),
    ("Recyclage", ["recyclage", "biorecyclage", "recyclé", "recyclée", "recyclés", "valorisation des matériaux", "économie circulaire"]),
    ("Matériaux critiques", ["lithium", "terres rares", "aimants permanents", "matériaux cathodiques", "métaux critiques", "aimants recyclés", "aimants"]),
    ("Aéronautique & Défense", ["aéronautique", "aéro", "lanceurs spatiaux", "lanceurs spaciaux", "freins carbone", "avions", "hélicoptères"]),
    ("Sidérurgie", ["aciérie", "acier", "four électrique", "aluminium", "fer sans haut-fourneau"]),
    ("Biocarburants", ["biocarburant", "biogaz"]),
    ("Électromobilité", ["camions électriques", "véhicules électriques", "batteries de véhicules"]),
    ("Agroalimentaire", ["protéines végétaux", "protéines", "engrais", "abattage", "volaille", "biocarburant"]),
]

CATEGORY_META = {
    "Data Center & IA":     {"color": "#3b82f6", "icon": "🖥️"},
    "Énergie & Nucléaire":  {"color": "#f59e0b", "icon": "⚡"},
    "Recyclage":            {"color": "#22c55e", "icon": "♻️"},
    "Matériaux critiques":  {"color": "#8b5cf6", "icon": "🔋"},
    "Aéronautique & Défense": {"color": "#0369a1", "icon": "✈️"},
    "Sidérurgie":           {"color": "#ef4444", "icon": "⚙️"},
    "Biocarburants":        {"color": "#84cc16", "icon": "🌿"},
    "Électromobilité":      {"color": "#06b6d4", "icon": "🚗"},
    "Agroalimentaire":      {"color": "#f97316", "icon": "🌾"},
    "Industrie":            {"color": "#6366f1", "icon": "🏭"},
}


def detect_category(projet: str) -> str:
    lower = projet.lower()
    for cat, keywords in CATEGORY_RULES:
        if any(kw in lower for kw in keywords):
            return cat
    return "Industrie"


def primary_city(ville: str) -> str:
    """Handle multi-city values like 'Feyzin / Saint-Vulbas' or 'La Maxe et Richemont'."""
    return re.split(r"\s*/\s*|\s+et\s+", ville)[0].strip()


def geocode_nominatim(city: str, department: str, dry_run: bool = False) -> dict | None:
    if dry_run:
        return {"lat": 46.5, "lon": 2.5, "display_name": f"{city}, France", "accuracy": "dry-run"}

    query = f"{city}, {department}, France"
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "limit": 3,
        "countrycodes": "fr",
        "accept-language": "fr",
    })
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            results = json.loads(r.read())
        if results:
            best = results[0]
            return {
                "lat": float(best["lat"]),
                "lon": float(best["lon"]),
                "display_name": best.get("display_name", query)[:100],
                "accuracy": "high",
            }
    except Exception as e:
        print(f"  ⚠ Nominatim error for '{query}': {e}")
    return None


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[àáâãäå]", "a", text)
    text = re.sub(r"[èéêë]", "e", text)
    text = re.sub(r"[ìíîï]", "i", text)
    text = re.sub(r"[òóôõö]", "o", text)
    text = re.sub(r"[ùúûü]", "u", text)
    text = re.sub(r"[ç]", "c", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def build_geojson(features: list) -> dict:
    lons = [f["geometry"]["coordinates"][0] for f in features if f["geometry"]]
    lats = [f["geometry"]["coordinates"][1] for f in features if f["geometry"]]
    bbox = [min(lons), min(lats), max(lons), max(lats)] if lons else []
    return {
        "type": "FeatureCollection",
        "_meta": {
            "crs": "EPSG:4326",
            "generated": str(date.today()),
            "source": "CSV projets_industriels_france_villes + Nominatim",
            "count": len(features),
        },
        "bbox": bbox,
        "features": features,
    }


def main():
    parser = argparse.ArgumentParser(description="Geocode France industrial projects CSV → GeoJSON")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output GeoJSON path")
    parser.add_argument("--dry-run", action="store_true", help="Skip Nominatim requests (test only)")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if any(row.values()):  # skip empty rows
                rows.append(row)

    print(f"📋 {len(rows)} projects to geocode from {CSV_PATH.name}")

    features = []
    for i, row in enumerate(rows, 1):
        region = row["Région"].strip()
        dept = row["Département"].strip()
        entreprise = row["Entreprise"].strip()
        projet = row["Projet"].strip()
        ville_raw = row["Ville"].strip()
        city = primary_city(ville_raw)

        category = detect_category(projet)
        meta = CATEGORY_META[category]
        feat_id = f"france-projet-{i:03d}-{slugify(entreprise)}"

        print(f"[{i:02d}/{len(rows)}] {entreprise} ({city}, {dept}) → {category}", end=" ")

        geo = geocode_nominatim(city, dept, dry_run=args.dry_run)
        if geo:
            print(f"✓ ({geo['lat']:.4f}, {geo['lon']:.4f})")
        else:
            # Fallback: geocode just the city + France
            print(f"⚠ retrying with city only...", end=" ")
            geo = geocode_nominatim(city, "France", dry_run=args.dry_run)
            if geo:
                print(f"✓ fallback ({geo['lat']:.4f}, {geo['lon']:.4f})")
                geo["accuracy"] = "medium"
            else:
                print("✗ failed — skipping coordinates")

        feature = {
            "type": "Feature",
            "id": feat_id,
            "geometry": {
                "type": "Point",
                "coordinates": [geo["lon"], geo["lat"]] if geo else None,
            } if geo else None,
            "properties": {
                "name": entreprise,
                "category": category,
                "icon": meta["icon"],
                "region": region,
                "departement": dept,
                "ville": ville_raw,
                "projet": projet,
                "coord_source": "nominatim" if geo else "unknown",
                "coord_accuracy": geo["accuracy"] if geo else "none",
            },
        }

        if geo:
            features.append(feature)
        else:
            print(f"  → Skipped (no coordinates)")

        if not args.dry_run:
            time.sleep(1.1)  # Nominatim ToS: 1 req/sec max

    geojson = build_geojson(features)
    output_path.write_text(json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(1 for f in features if f["geometry"])
    print(f"\n✅ Written {ok}/{len(rows)} features → {output_path}")
    if len(rows) - ok > 0:
        print(f"   ⚠ {len(rows) - ok} projects could not be geocoded")


if __name__ == "__main__":
    main()
