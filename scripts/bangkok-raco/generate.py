#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["tqdm"]
# ///
"""
Generate bangkok-raco GeoJSON files and Hugo content stubs.

Usage:
    uv run scripts/bangkok-raco/generate.py
    uv run scripts/bangkok-raco/generate.py --dry-run

Outputs:
    static/bangkok-raco/events/this-week.geojson
    static/bangkok-raco/events/next-week.geojson
    content/bangkok-raco-this-week/_index.md
    content/bangkok-raco-next-week/_index.md
    scripts/bangkok-raco/venues.csv        — auto-updated with new venues
    scripts/bangkok-raco/unmatched-venues.txt
"""

import argparse
import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent

VENUES_CSV = SCRIPT_DIR / "venues.csv"
GEOCACHE_PATH = SCRIPT_DIR / ".geocache.json"
MEDIACACHE_PATH = SCRIPT_DIR / ".mediacache.json"
UNMATCHED_PATH = SCRIPT_DIR / "unmatched-venues.txt"

STATIC_DIR = REPO_ROOT / "static/bangkok-raco"
EVENTS_DIR = STATIC_DIR / "events"
CONTENT_DIR = REPO_ROOT / "content"

RA_AREA_ID = 453  # Bangkok on RA.co (/events/th/bangkok)
RA_GRAPHQL_URL = "https://ra.co/graphql"
RA_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Origin": "https://ra.co",
    "Referer": "https://ra.co/events/th/bangkok",
}
NOMINATIM_HEADERS = {"User-Agent": "maps.girard-davila.net/bangkok-raco"}

CATEGORY_ICONS = {
    "Club": "fa-house-music",
    "Bar": "fa-martini-glass",
    "Outdoor": "fa-tree",
    "Rooftop": "fa-building",
    "Other": "fa-location-dot",
}

RA_QUERY = """query GET_EVENT_LISTINGS($filters: FilterInputDtoInput, $filterOptions: FilterOptionsInputDtoInput, $page: Int, $pageSize: Int, $sort: SortInputDtoInput) {
  eventListings(filters: $filters filterOptions: $filterOptions pageSize: $pageSize page: $page sort: $sort) {
    data {
      id
      listingDate
      event {
        id
        date
        startTime
        endTime
        title
        contentUrl
        images { filename type __typename }
        venue { id name __typename }
        artists { id name __typename }
        __typename
      }
      __typename
    }
    totalResults
    __typename
  }
}"""


# ── RA.co fetch ───────────────────────────────────────────────────────────────

def fetch_ra_events(from_date: date) -> list[dict]:
    page, page_size = 1, 20
    all_listings: list[dict] = []

    while True:
        payload = {
            "operationName": "GET_EVENT_LISTINGS",
            "variables": {
                "filters": {
                    "areas": {"eq": RA_AREA_ID},
                    "listingDate": {"gte": from_date.isoformat()},
                },
                "filterOptions": {"genre": True, "eventType": True},
                "pageSize": page_size,
                "page": page,
                "sort": {
                    "listingDate": {"order": "ASCENDING"},
                    "score": {"order": "DESCENDING"},
                    "titleKeyword": {"order": "ASCENDING"},
                },
            },
            "query": RA_QUERY,
        }
        body = json.dumps(payload).encode()
        req = urllib.request.Request(RA_GRAPHQL_URL, data=body, headers=RA_HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read())
        except Exception as e:
            sys.exit(f"RA.co fetch failed (page {page}): {e}")

        listings = resp["data"]["eventListings"]
        all_listings.extend(listings["data"])
        total = listings["totalResults"]
        fetched = (page - 1) * page_size + len(listings["data"])
        print(f"  Page {page}: {len(listings['data'])} events (total: {total})")
        if fetched >= total:
            break
        page += 1
        time.sleep(0.5)

    return all_listings


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
        "countrycodes": "th",
        "accept-language": "en",
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
    cache = load_geocache()
    updated = False
    pending = [v for v in venues if v.get("address") and v["address"] not in cache]
    for v in tqdm(pending, desc="Geocoding", unit="venue", disable=not pending):
        addr = v["address"]
        if dry_run:
            tqdm.write(f"  [dry-run] would geocode: {addr}")
            continue
        result = geocode_nominatim(addr)
        if result:
            cache[addr] = result
            tqdm.write(f"  → {addr}: {result['lat']:.5f}, {result['lon']:.5f}")
        else:
            tqdm.write(f"  ⚠ Geocode FAILED: {addr}", file=sys.stderr)
        updated = True
        time.sleep(1.1)
    if updated:
        save_geocache(cache)
    return cache


# ── Venue registry ────────────────────────────────────────────────────────────

def load_venues() -> tuple[list[dict], dict[str, dict]]:
    if not VENUES_CSV.exists():
        return [], {}
    venues = []
    with VENUES_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            venues.append(row)
    return venues, {v["name"]: v for v in venues}


def save_venues(venues: list[dict]) -> None:
    fieldnames = ["name", "address", "category", "logo"]
    with VENUES_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(venues)


def merge_venues(existing: list[dict], discovered: set[str]) -> tuple[list[dict], list[str]]:
    existing_names = {v["name"] for v in existing}
    new_names = sorted(discovered - existing_names)
    for name in new_names:
        existing.append({"name": name, "address": "", "category": "Other", "logo": ""})
    return existing, new_names


# ── Media cache ───────────────────────────────────────────────────────────────

def load_mediacache() -> dict:
    if MEDIACACHE_PATH.exists():
        return json.loads(MEDIACACHE_PATH.read_text())
    return {}


# ── GeoJSON builders ──────────────────────────────────────────────────────────

def make_event_feature(venue: dict, coords: dict, events: list[dict], mediacache: dict) -> dict:
    enriched = []
    for ev in events:
        media: dict = {}
        for artist_name in ev.get("artists", []):
            m = mediacache.get(artist_name, {})
            if m.get("soundcloud_track_id"):
                media = m
                break
        enriched.append({
            **ev,
            "soundcloud_track_id": media.get("soundcloud_track_id", ""),
            "soundcloud_url": media.get("soundcloud_url", ""),
        })

    icon = CATEGORY_ICONS.get(venue.get("category", "Other"), "fa-location-dot")
    slug = venue["name"].lower().replace(" ", "-").replace("/", "-")
    return {
        "type": "Feature",
        "id": f"bangkok-raco-{slug}",
        "geometry": {"type": "Point", "coordinates": [coords["lon"], coords["lat"]]},
        "properties": {
            "name": venue["name"],
            "category": venue.get("category", "Other"),
            "icon": icon,
            "address": venue.get("address", ""),
            "events": enriched,
        },
    }


def write_geojson(path: Path, features: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fc = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))


# ── Week helpers ──────────────────────────────────────────────────────────────

def week_bounds(ref: date) -> tuple[date, date]:
    monday = ref - timedelta(days=ref.weekday())
    return monday, monday + timedelta(days=6)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    today = date.today()
    this_mon, this_sun = week_bounds(today)
    next_mon, next_sun = week_bounds(today + timedelta(weeks=1))

    # 1. Fetch events (from this Monday so we always have this-week data)
    print(f"Fetching RA.co Bangkok events from {this_mon}…")
    listings = fetch_ra_events(this_mon)
    print(f"  {len(listings)} total listings fetched")

    # 2. Process into {date: {venue_name: [event_record]}}
    by_date: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    discovered_venues: set[str] = set()

    for listing in listings:
        ev = listing.get("event")
        if not ev or not ev.get("venue"):
            continue
        venue_name = ev["venue"]["name"]
        discovered_venues.add(venue_name)
        date_str = (ev.get("date") or listing.get("listingDate") or "")[:10]
        if not date_str:
            continue
        artists = [a["name"] for a in (ev.get("artists") or [])]
        ra_url = f"https://ra.co{ev['contentUrl']}" if ev.get("contentUrl") else ""
        start = ev.get("startTime") or ""
        images = ev.get("images") or []
        poster = next((img["filename"] for img in images if img.get("type") == "FLYERFRONT"), "")
        by_date[date_str][venue_name].append({
            "date": date_str,
            "time": start[11:16] if "T" in start else start[:5],
            "title": ev.get("title", ""),
            "artists": artists,
            "ra_url": ra_url,
            "poster": poster,
        })

    print(f"  {len(discovered_venues)} unique venues discovered")

    # 3. Update venue registry
    print("Updating venue registry…")
    venues, venue_lookup = load_venues()
    venues, new_names = merge_venues(venues, discovered_venues)
    if new_names:
        print(f"  {len(new_names)} new venues: {', '.join(new_names[:5])}{'…' if len(new_names) > 5 else ''}")
        if not args.dry_run:
            save_venues(venues)
            # reload lookup after save
            _, venue_lookup = load_venues()
    else:
        print("  No new venues")

    # 4. Geocode
    print("Geocoding venues…")
    geocache = geocode_venues(venues, dry_run=args.dry_run)

    # 5. Load media
    mediacache = load_mediacache()

    # 6. Build GeoJSONs
    print("Writing event GeoJSONs…")
    for label, start, end in [
        ("this-week", this_mon, this_sun),
        ("next-week", next_mon, next_sun),
    ]:
        week_venues: dict[str, list] = defaultdict(list)
        for date_str, venues_on_day in by_date.items():
            d = date.fromisoformat(date_str)
            if start <= d <= end:
                for vname, evs in venues_on_day.items():
                    week_venues[vname].extend(evs)

        features = []
        unmatched: set[str] = set()
        for vname, evs in week_venues.items():
            venue = venue_lookup.get(vname)
            if not venue:
                unmatched.add(vname)
                continue
            coords = geocache.get(venue.get("address", ""))
            if not coords:
                continue
            features.append(make_event_feature(venue, coords, evs, mediacache))

        if unmatched and not args.dry_run:
            UNMATCHED_PATH.write_text("\n".join(sorted(unmatched)) + "\n")
        if not args.dry_run:
            write_geojson(EVENTS_DIR / f"{label}.geojson", features)
        print(f"  {label}: {len(features)} venues with coordinates ({start} → {end})")

    # 7. Hugo content stubs
    print("Writing Hugo content stubs…")
    for label, start, end in [
        ("this-week", this_mon, this_sun),
        ("next-week", next_mon, next_sun),
    ]:
        stub_path = CONTENT_DIR / f"bangkok-raco-{label}/_index.md"
        if not args.dry_run:
            stub_path.parent.mkdir(parents=True, exist_ok=True)
            window_label = "this week" if label == "this-week" else "next week"
            stub_path.write_text("\n".join([
                "---",
                'title: "Bangkok RA.co"',
                f'description: "Electronic music events in Bangkok {window_label} ({start} – {end})."',
                'type: "bangkok-raco-event"',
                f'raco_window: "{label}"',
                f'geojson_url: "/bangkok-raco/events/{label}.geojson"',
                "---",
                "",
            ]))

    print("Done.")


if __name__ == "__main__":
    main()
