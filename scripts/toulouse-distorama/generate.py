#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Generate all toulouse-distorama GeoJSON files and Hugo content stubs.

Usage:
    uv run scripts/toulouse-distorama/generate.py
    uv run scripts/toulouse-distorama/generate.py --dry-run

Outputs:
    static/toulouse-distorama/locations.geojson        — venues map
    static/toulouse-distorama/events/YYYY-MM-DD.geojson
    static/toulouse-distorama/events/YYYY-MM.geojson
    static/toulouse-distorama/events/this-week.geojson
    static/toulouse-distorama/events/next-week.geojson
    content/toulouse-distorama-*/                      — Hugo content stubs
    scripts/toulouse-distorama/unmatched-venues.txt    — for manual classification
"""

import argparse
import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent

VENUES_CSV = SCRIPT_DIR / "venues.csv"
GEOCACHE_PATH = SCRIPT_DIR / ".geocache.json"
UNMATCHED_PATH = SCRIPT_DIR / "unmatched-venues.txt"

EVENTS_URL = "https://distorama.neocities.org/events.json"
STATIC_DIR = REPO_ROOT / "static/toulouse-distorama"
EVENTS_DIR = STATIC_DIR / "events"
CONTENT_DIR = REPO_ROOT / "content"

NOMINATIM_HEADERS = {"User-Agent": "maps.girard-davila.net/toulouse-distorama"}

CATEGORY_ICONS = {
    "Concert Bar": "🎵",
    "Record Shop": "💿",
    "Cinema": "🎬",
    "Radio": "📻",
    "Studio": "🎙️",
    "Boutique": "🛍️",
    "Merch/Print": "🖨️",
}

FRENCH_MONTHS = {
    1: "janvier", 2: "février", 3: "mars", 4: "avril",
    5: "mai", 6: "juin", 7: "juillet", 8: "août",
    9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre",
}

FRENCH_DAYS = {
    0: "lundi", 1: "mardi", 2: "mercredi", 3: "jeudi",
    4: "vendredi", 5: "samedi", 6: "dimanche",
}

TIME_RE = re.compile(r"^\d{1,2}h\d{0,2}$")


# ── Geocoding ─────────────────────────────────────────────────────────────────

def load_geocache() -> dict:
    if GEOCACHE_PATH.exists():
        return json.loads(GEOCACHE_PATH.read_text())
    return {}


def save_geocache(cache: dict) -> None:
    GEOCACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def geocode_nominatim(address: str) -> dict | None:
    params = urllib.parse.urlencode({
        "q": address,
        "format": "json",
        "limit": 1,
        "countrycodes": "fr",
        "accept-language": "fr",
    })
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    try:
        req = urllib.request.Request(url, headers=NOMINATIM_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            results = json.loads(r.read())
        if results:
            best = results[0]
            return {"lat": float(best["lat"]), "lon": float(best["lon"])}
    except Exception as e:
        print(f"  ⚠ Nominatim error for '{address}': {e}", file=sys.stderr)
    return None


def geocode_venues(venues: list[dict], dry_run: bool) -> dict:
    """Returns {address: {lat, lon}} for all venues that have addresses."""
    cache = load_geocache()
    updated = False

    for v in venues:
        addr = v["address"]
        if not addr or addr in cache:
            continue
        if dry_run:
            print(f"  [dry-run] would geocode: {addr}")
            continue
        print(f"  Geocoding: {addr}")
        result = geocode_nominatim(addr)
        if result:
            cache[addr] = result
            print(f"    → {result['lat']:.5f}, {result['lon']:.5f}")
        else:
            print(f"    → FAILED", file=sys.stderr)
        updated = True
        time.sleep(1.1)  # Nominatim ToS: max 1 req/s

    if updated:
        save_geocache(cache)
    return cache


# ── Venue registry ─────────────────────────────────────────────────────────────

def load_venues() -> tuple[list[dict], dict[str, dict]]:
    """Returns (venues_list, lookup_by_normalized_name)."""
    venues = []
    with VENUES_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            venues.append(row)

    lookup = {}
    for v in venues:
        key = normalize_venue_name(v["name"])
        lookup[key] = v
    return venues, lookup


def normalize_venue_name(name: str) -> str:
    name = name.lower().strip()
    # Strip leading articles for fuzzy matching
    for prefix in ("le ", "la ", "l'", "les ", "au ", "aux ", "the "):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name


def resolve_venue(raw_name: str, lookup: dict[str, dict]) -> dict | None:
    """Try exact match, then article-stripped match."""
    if not raw_name:
        return None
    # Exact normalized match
    key = normalize_venue_name(raw_name)
    if key in lookup:
        return lookup[key]
    # Try matching the raw normalized name directly (without stripping articles)
    raw_key = raw_name.lower().strip()
    for k, v in lookup.items():
        if k == raw_key or v["name"].lower().strip() == raw_key:
            return v
    return None


# ── Event parsing ──────────────────────────────────────────────────────────────

def parse_details(details: str) -> tuple[str | None, str | None, str | None]:
    """Returns (venue_name, time, price) from a details string like 'Bistrot Ducoin - 19h30 - Prix Libre'."""
    parts = [p.strip() for p in details.split(" - ")]
    if not parts:
        return None, None, None

    # If first part looks like a time, there's no venue
    if TIME_RE.match(parts[0]):
        return None, parts[0], parts[1] if len(parts) > 1 else None

    venue = parts[0]
    time_val = parts[1] if len(parts) > 1 else None
    price = parts[2] if len(parts) > 2 else None

    # Sanity-check: time should match HHhMM pattern
    if time_val and not TIME_RE.match(time_val):
        # Could be price without time, or malformed — keep venue, drop time
        price = time_val
        time_val = None

    return venue, time_val, price


NON_ARTIST_PATTERNS = re.compile(
    r"(vernissage|soirée|exposition|expo|festival|marché|atelier|conférence|"
    r"projection|distorama|radio|émission|emission|concert|showcase|open\s?mic|"
    r"bal|anniversaire|clôture|ouverture|inauguration)",
    re.IGNORECASE,
)


def parse_artist(desc: str) -> str | None:
    """Extract artist name from desc, stripping genre annotations. Returns None for non-artist events."""
    if NON_ARTIST_PATTERNS.search(desc):
        return None
    # Strip trailing parenthetical genre annotation like (pop rk cover)
    artist = re.sub(r"\s*\([^)]*\)\s*$", "", desc).strip()
    return artist if artist else None


# ── GeoJSON builders ───────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower()
    for src, dst in [("é","e"),("è","e"),("ê","e"),("à","a"),("â","a"),("ù","u"),("û","u"),("î","i"),("ô","o"),("ç","c")]:
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def make_event_feature(venue: dict, coords: dict, events_at_venue: list[dict]) -> dict:
    return {
        "type": "Feature",
        "id": f"toulouse-distorama-{slugify(venue['name'])}",
        "geometry": {"type": "Point", "coordinates": [coords["lon"], coords["lat"]]},
        "properties": {
            "name": venue["display_name"],
            "category": venue["category"],
            "icon": CATEGORY_ICONS.get(venue["category"], "🎵"),
            "address": venue["address"],
            "logo": venue["logo"],
            "events": events_at_venue,
        },
    }


def make_venue_feature(venue: dict, coords: dict) -> dict:
    return {
        "type": "Feature",
        "id": f"toulouse-distorama-{slugify(venue['name'])}",
        "geometry": {"type": "Point", "coordinates": [coords["lon"], coords["lat"]]},
        "properties": {
            "name": venue["display_name"],
            "category": venue["category"],
            "icon": CATEGORY_ICONS.get(venue["category"], "🎵"),
            "address": venue["address"],
            "logo": venue["logo"],
            "url": venue["url"],
            "coord_source": "nominatim",
            "coord_accuracy": "high",
        },
    }


def write_geojson(path: Path, features: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fc = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))


# ── Week helpers ───────────────────────────────────────────────────────────────

def week_bounds(ref: date) -> tuple[date, date]:
    """Return (monday, sunday) of the ISO week containing ref."""
    monday = ref - timedelta(days=ref.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


# ── Hugo content stubs ─────────────────────────────────────────────────────────

def fr_date(d: date) -> str:
    return f"{FRENCH_DAYS[d.weekday()]} {d.day} {FRENCH_MONTHS[d.month]} {d.year}"


def fr_month(year: int, month: int) -> str:
    return f"{FRENCH_MONTHS[month]} {year}"


def write_stub(path: Path, frontmatter: dict) -> None:
    """Write a Hugo _index.md stub if it doesn't already exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    lines = ["---"]
    for k, v in frontmatter.items():
        lines.append(f'{k}: "{v}"')
    lines += ["---", ""]
    path.write_text("\n".join(lines))


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Skip Nominatim requests and file writes")
    args = parser.parse_args()

    # 1. Load venues
    print("Loading venue registry…")
    venues, venue_lookup = load_venues()
    print(f"  {len(venues)} venues loaded")

    # 2. Geocode
    print("Geocoding venues…")
    geocache = geocode_venues(venues, dry_run=args.dry_run)

    # 3. Build venues GeoJSON
    print("Building venues GeoJSON…")
    venue_features = []
    for v in venues:
        if not v["address"]:
            continue
        coords = geocache.get(v["address"])
        if not coords:
            print(f"  ⚠ No coordinates for {v['name']}", file=sys.stderr)
            continue
        venue_features.append(make_venue_feature(v, coords))

    if not args.dry_run:
        write_geojson(STATIC_DIR / "locations.geojson", venue_features)
        print(f"  ✓ Written {len(venue_features)} venue features")

    # 4. Fetch events
    print(f"Fetching {EVENTS_URL}…")
    try:
        req = urllib.request.Request(EVENTS_URL, headers=NOMINATIM_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            event_data = json.loads(r.read())
        print(f"  {len(event_data)} date entries")
    except Exception as e:
        sys.exit(f"Failed to fetch events.json: {e}")

    # 5. Process events → {date_str: {venue_name: [event_dict]}}
    print("Processing events…")
    by_date: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    unmatched: set[str] = set()
    total_events = 0
    resolved_events = 0

    for entry in event_data:
        date_str = entry["date"]
        for ev in entry.get("events", []):
            total_events += 1
            raw_venue, time_val, price = parse_details(ev.get("details", ""))
            if not raw_venue:
                continue  # radio broadcast or no venue
            venue = resolve_venue(raw_venue, venue_lookup)
            if not venue:
                unmatched.add(raw_venue)
                continue
            coords = geocache.get(venue["address"])
            if not coords:
                continue
            artist = parse_artist(ev.get("desc", ""))
            event_record = {
                "time": time_val or "",
                "price": price or "",
                "desc": ev.get("desc", ""),
                "artist": artist or "",
            }
            by_date[date_str][venue["name"]].append(event_record)
            resolved_events += 1

    print(f"  {resolved_events}/{total_events} events resolved to known venues")
    print(f"  {len(unmatched)} unmatched venue names")

    # 6. Write unmatched
    if unmatched:
        sorted_unmatched = sorted(unmatched)
        if not args.dry_run:
            UNMATCHED_PATH.write_text("\n".join(sorted_unmatched) + "\n")
        print(f"  → {UNMATCHED_PATH.name}: {', '.join(sorted_unmatched[:5])}{'…' if len(sorted_unmatched) > 5 else ''}")

    # 7. Build per-day and per-month event GeoJSONs
    print("Writing event GeoJSONs…")
    all_dates = sorted(by_date.keys())
    by_month: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for date_str, venues_on_day in by_date.items():
        month_str = date_str[:7]  # YYYY-MM
        features = []
        for venue_name, events in venues_on_day.items():
            venue = venue_lookup.get(normalize_venue_name(venue_name))
            if not venue:
                continue
            coords = geocache.get(venue["address"])
            if not coords:
                continue
            features.append(make_event_feature(venue, coords, events))
            # Accumulate for monthly rollup
            by_month[month_str][venue_name].extend(events)

        if not args.dry_run:
            write_geojson(EVENTS_DIR / f"{date_str}.geojson", features)

    # 8. Per-month GeoJSONs
    for month_str, venues_in_month in by_month.items():
        features = []
        # De-duplicate events per venue (same desc+time may repeat across days)
        for venue_name, events in venues_in_month.items():
            venue = venue_lookup.get(normalize_venue_name(venue_name))
            if not venue:
                continue
            coords = geocache.get(venue["address"])
            if not coords:
                continue
            features.append(make_event_feature(venue, coords, events))
        if not args.dry_run:
            write_geojson(EVENTS_DIR / f"{month_str}.geojson", features)

    # 9. This-week and next-week GeoJSONs
    today = date.today()
    this_mon, this_sun = week_bounds(today)
    next_mon, next_sun = week_bounds(today + timedelta(weeks=1))

    for label, start, end in [
        ("this-week", this_mon, this_sun),
        ("next-week", next_mon, next_sun),
    ]:
        week_venues: dict[str, list] = defaultdict(list)
        for date_str, venues_on_day in by_date.items():
            d = date.fromisoformat(date_str)
            if start <= d <= end:
                for venue_name, events in venues_on_day.items():
                    week_venues[venue_name].extend(events)
        features = []
        for venue_name, events in week_venues.items():
            venue = venue_lookup.get(normalize_venue_name(venue_name))
            if not venue:
                continue
            coords = geocache.get(venue["address"])
            if not coords:
                continue
            features.append(make_event_feature(venue, coords, events))
        if not args.dry_run:
            write_geojson(EVENTS_DIR / f"{label}.geojson", features)
        print(f"  {label}: {len(features)} venues ({start} → {end})")

    # 10. Hugo content stubs
    print("Writing Hugo content stubs…")
    stubs_written = 0

    # Venues map
    venues_stub = CONTENT_DIR / "toulouse-distorama/_index.md"
    if not args.dry_run:
        write_stub(venues_stub, {
            "title": "Distorama — Toulouse underground",
            "description": "La carte des lieux underground toulousains : bars concerts, disquaires, cinémas, radios, studios.",
            "type": "toulouse-distorama",
            "accent_color": "#ffffff",
            "section": "community",
        })

    # Agenda index
    agenda_stub = CONTENT_DIR / "toulouse-distorama/agenda/_index.md"
    if not args.dry_run:
        write_stub(agenda_stub, {
            "title": "Distorama — Agenda",
            "description": "Tous les événements underground à Toulouse, par date.",
            "type": "toulouse-distorama-agenda",
        })

    # this-week / next-week (always overwrite — title stays generic)
    for label, start, end in [
        ("this-week", this_mon, this_sun),
        ("next-week", next_mon, next_sun),
    ]:
        stub_path = CONTENT_DIR / f"toulouse-distorama-{label}/_index.md"
        if not args.dry_run:
            stub_path.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                "---",
                f'title: "Distorama — {"Cette semaine" if label == "this-week" else "Semaine prochaine"}"',
                f'description: "Concerts et événements underground à Toulouse {"cette semaine" if label == "this-week" else "la semaine prochaine"} ({fr_date(start)} – {fr_date(end)})."',
                'type: "toulouse-distorama-event"',
                f'distorama_window: "{label}"',
                f'geojson_url: "/toulouse-distorama/events/{label}.geojson"',
                "---",
                "",
            ]
            stub_path.write_text("\n".join(lines))
        stubs_written += 1

    # Per-month stubs
    for month_str in by_month:
        year, month = int(month_str[:4]), int(month_str[5:7])
        stub_path = CONTENT_DIR / f"toulouse-distorama-{month_str}/_index.md"
        if not args.dry_run:
            write_stub(stub_path, {
                "title": f"Distorama — {fr_month(year, month).capitalize()}",
                "description": f"Concerts et événements underground à Toulouse en {fr_month(year, month)}.",
                "type": "toulouse-distorama-event",
                "distorama_window": month_str,
                "geojson_url": f"/toulouse-distorama/events/{month_str}.geojson",
            })
        stubs_written += 1

    # Per-day stubs
    for date_str in all_dates:
        d = date.fromisoformat(date_str)
        stub_path = CONTENT_DIR / f"toulouse-distorama-{date_str}/_index.md"
        if not args.dry_run:
            write_stub(stub_path, {
                "title": f"Distorama — {fr_date(d).capitalize()}",
                "description": f"Concerts et événements underground à Toulouse le {fr_date(d)}.",
                "type": "toulouse-distorama-event",
                "distorama_window": date_str,
                "geojson_url": f"/toulouse-distorama/events/{date_str}.geojson",
            })
        stubs_written += 1

    print(f"  ✓ {stubs_written} stubs")
    print("Done.")


if __name__ == "__main__":
    main()
