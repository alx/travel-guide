#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "python-dotenv"]
# ///
"""
Discover food & drink venues near a location via Google Places API (New),
collect their reviews, keep those within a time window, and output an
interactive HTML map + enriched GeoJSON.

Optionally opens a GitHub PR against alx/travel-guide to publish the result
at maps.girard-davila.net.

Usage (Lamai default):
    uv run --script ingest_places.py

Other location:
    uv run --script ingest_places.py --location "Hoi An, Vietnam" --slug hoian \\
        --lat 15.8801 --lon 108.3380 --radius 2000

With PR:
    uv run --script ingest_places.py --days 30 --pr
"""

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

PLACES_API_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.rating",
    "places.userRatingCount",
    "places.reviews",
    "places.location",
    "places.primaryType",
    "places.regularOpeningHours",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "nextPageToken",
])

UPSTREAM_REPO = "alx/travel-guide"
GITHUB_API = "https://api.github.com"

# Google Places primaryType → (travel-guide category, emoji)
CATEGORY_MAP: dict[str, tuple[str, str]] = {
    "cafe": ("Café", "☕"),
    "coffee_shop": ("Café", "☕"),
    "espresso_bar": ("Café", "☕"),
    "bakery": ("Bakery", "🥐"),
    "breakfast_restaurant": ("Restaurant", "🍳"),
    "brunch_restaurant": ("Restaurant", "🍳"),
    "restaurant": ("Restaurant", "🍽️"),
    "thai_restaurant": ("Restaurant", "🍜"),
    "seafood_restaurant": ("Restaurant", "🦞"),
    "italian_restaurant": ("Restaurant", "🍝"),
    "pizza_restaurant": ("Restaurant", "🍕"),
    "hamburger_restaurant": ("Restaurant", "🍔"),
    "sandwich_shop": ("Restaurant", "🥪"),
    "vegetarian_restaurant": ("Restaurant", "🥗"),
    "vegan_restaurant": ("Restaurant", "🥗"),
    "fast_food_restaurant": ("Restaurant", "🍟"),
    "bar": ("Bar", "🍺"),
    "pub": ("Bar", "🍺"),
    "cocktail_bar": ("Bar", "🍸"),
    "wine_bar": ("Bar", "🍷"),
    "night_club": ("Bar", "🎵"),
}
DEFAULT_CATEGORY = ("Restaurant", "🍽️")


def parse_args():
    p = argparse.ArgumentParser(description="Ingest Google Places reviews → interactive map + optional PR")
    p.add_argument("--location", default="Lamai, Koh Samui, Thailand")
    p.add_argument("--slug", default="lamai", help="Short name for output files and URL path")
    p.add_argument("--lat", type=float, default=9.4775)
    p.add_argument("--lon", type=float, default=100.0454)
    p.add_argument("--radius", type=float, default=3000, help="Search radius in metres")
    p.add_argument("--categories",
                   default="coffee shop,cafe,bakery,breakfast,thai restaurant,seafood restaurant,italian restaurant,pizza,burger,bar,pub,cocktail bar",
                   help="Comma-separated category keywords (each gets its own 60-result API budget)")
    p.add_argument("--days", type=int, default=7,
                   help="Only keep reviews published within this many days")
    p.add_argument("--output-dir", default="./output")
    p.add_argument("--pr", action="store_true",
                   help="Build and open a GitHub PR against alx/travel-guide")
    p.add_argument("--github-token", default=os.getenv("GITHUB_TOKEN", ""),
                   help="GitHub PAT with repo scope (or set GITHUB_TOKEN env var)")
    p.add_argument("--min-reviews", type=int, default=1,
                   help="Min recent reviews for a place to be included in the PR GeoJSON")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Google Places helpers
# ---------------------------------------------------------------------------

def search_places(api_key: str, query: str, lat: float, lon: float, radius: float) -> list[dict]:
    places: dict[str, dict] = {}
    page_token = None

    while True:
        body: dict = {
            "textQuery": query,
            "locationBias": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lon},
                    "radius": radius,
                }
            },
            "maxResultCount": 20,
        }
        if page_token:
            body["pageToken"] = page_token

        resp = requests.post(
            PLACES_API_URL,
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": FIELD_MASK,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=15,
        )

        if not resp.ok:
            print(f"  Warning: API error for '{query}': {resp.status_code} {resp.text[:200]}")
            break

        data = resp.json()
        for place in data.get("places", []):
            pid = place.get("id")
            if pid and pid not in places:
                places[pid] = place

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return list(places.values())


def parse_review_time(publish_time: str | None) -> datetime | None:
    if not publish_time:
        return None
    try:
        return datetime.fromisoformat(publish_time.replace("Z", "+00:00"))
    except Exception:
        return None


def collect_all_reviews(places: list[dict]) -> list[dict]:
    all_reviews = []
    for place in places:
        loc = place.get("location", {})
        place_info = {
            "place_id": place.get("id", ""),
            "place_name": place.get("displayName", {}).get("text", "Unknown"),
            "lat": loc.get("latitude"),
            "lon": loc.get("longitude"),
        }
        for review in place.get("reviews", []):
            publish_time = review.get("publishTime")
            all_reviews.append({
                **place_info,
                "rating": review.get("rating"),
                "text": review.get("text", {}).get("text", ""),
                "author": review.get("authorAttribution", {}).get("displayName", ""),
                "relative_time": review.get("relativePublishTimeDescription", ""),
                "publish_time": publish_time,
                "publish_dt": parse_review_time(publish_time),
            })
    return all_reviews


def opening_hours_text(place: dict) -> str | None:
    periods = place.get("regularOpeningHours", {}).get("weekdayDescriptions", [])
    return periods[0] if periods else None


# ---------------------------------------------------------------------------
# Our enriched GeoJSON (for local HTML map)
# ---------------------------------------------------------------------------

def build_geojson(places: list[dict], top_reviews: list[dict], run_meta: dict) -> dict:
    review_count_map: dict[str, int] = {}
    latest_date_map: dict[str, str] = {}
    reviews_by_place: dict[str, list] = {}

    for r in top_reviews:
        pid = r["place_id"]
        review_count_map[pid] = review_count_map.get(pid, 0) + 1
        if pid not in latest_date_map or (r["publish_time"] or "") > latest_date_map[pid]:
            latest_date_map[pid] = r["publish_time"] or ""
        reviews_by_place.setdefault(pid, []).append({
            "author": r["author"],
            "rating": r["rating"],
            "text": r["text"],
            "relative_time": r["relative_time"],
            "publish_time": r["publish_time"],
        })

    features = []
    for place in places:
        pid = place.get("id", "")
        loc = place.get("location", {})
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        if lat is None or lon is None:
            continue

        recent_revs = reviews_by_place.get(pid, [])
        rated = [r["rating"] for r in recent_revs if r["rating"] is not None]
        avg_recent_rating = round(sum(rated) / len(rated), 2) if rated else None
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "id": pid,
                "name": place.get("displayName", {}).get("text", ""),
                "category": place.get("primaryType", ""),
                "rating": place.get("rating"),
                "userRatingCount": place.get("userRatingCount"),
                "recentReviewCount": review_count_map.get(pid, 0),
                "avgRecentRating": avg_recent_rating,
                "latestReviewDate": latest_date_map.get(pid),
                "hours": opening_hours_text(place),
                "phone": place.get("nationalPhoneNumber"),
                "website": place.get("websiteUri"),
                "reviews": recent_revs,
            },
        })

    return {
        "type": "FeatureCollection",
        "_meta": run_meta,
        "features": features,
    }


# ---------------------------------------------------------------------------
# Travel-guide-compatible GeoJSON (for PR)
# ---------------------------------------------------------------------------

def build_travel_guide_geojson(places: list[dict], reviews_by_place: dict[str, list],
                                review_count_map: dict[str, int], slug: str,
                                min_reviews: int) -> dict:
    features = []
    idx = 0
    for place in places:
        pid = place.get("id", "")
        if review_count_map.get(pid, 0) < min_reviews:
            continue

        loc = place.get("location", {})
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        if lat is None or lon is None:
            continue

        primary_type = place.get("primaryType", "")
        category, icon = CATEGORY_MAP.get(primary_type, DEFAULT_CATEGORY)

        # Best review = highest rated recent one with non-empty text
        revs = reviews_by_place.get(pid, [])
        best = max(
            (r for r in revs if r.get("text")),
            key=lambda r: r.get("rating") or 0,
            default=None,
        )
        notes = ""
        if best:
            text = best["text"].replace("\n", " ").strip()
            notes = "Recently reviewed: " + (text[:197] + "…" if len(text) > 200 else text)

        props: dict = {
            "name": place.get("displayName", {}).get("text", ""),
            "category": category,
            "icon": icon,
            "notes": notes,
            "coord_source": "google_maps_pin",
            "coord_accuracy": "high",
        }
        hours = opening_hours_text(place)
        if hours:
            props["hours"] = hours
        phone = place.get("nationalPhoneNumber")
        if phone:
            props["phone"] = phone
        url = place.get("websiteUri")
        if url:
            props["url"] = url

        features.append({
            "type": "Feature",
            "id": f"{slug}-{idx:03d}",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props,
        })
        idx += 1

    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Hugo content file
# ---------------------------------------------------------------------------

def build_hugo_content(location: str, days: int, n_places: int,
                        tags: list[str]) -> str:
    tags_yaml = json.dumps(tags)
    desc = f"{n_places} food & drink venues reviewed in the last {days} days. Generated from Google Maps reviews."
    return f"""---
title: "{location} — Recently Reviewed Spots"
description: "{desc}"
emoji: "📍"
section: "community"
weight: 50
accent_color: "#1a3a5c"
tags: {tags_yaml}
---
"""


# ---------------------------------------------------------------------------
# GitHub PR helpers
# ---------------------------------------------------------------------------

def gh(method: str, path: str, token: str, **kwargs) -> requests.Response:
    url = f"{GITHUB_API}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    return requests.request(method, url, headers=headers, timeout=20, **kwargs)


def ensure_fork(token: str, login: str) -> str:
    """Return fork full_name, creating it if needed."""
    r = gh("GET", f"/repos/{login}/travel-guide", token)
    if r.status_code == 200:
        return r.json()["full_name"]

    print("  Forking alx/travel-guide…")
    r = gh("POST", f"/repos/{UPSTREAM_REPO}/forks", token, json={"default_branch_only": True})
    r.raise_for_status()
    fork = r.json()["full_name"]

    # Wait until fork is ready
    for _ in range(20):
        time.sleep(3)
        r = gh("GET", f"/repos/{fork}", token)
        if r.status_code == 200:
            return fork
    raise RuntimeError("Fork never became ready")


def get_main_sha(token: str) -> str:
    r = gh("GET", f"/repos/{UPSTREAM_REPO}/git/ref/heads/main", token)
    r.raise_for_status()
    return r.json()["object"]["sha"]


def create_branch(token: str, fork: str, branch: str, sha: str) -> None:
    r = gh("POST", f"/repos/{fork}/git/refs", token,
           json={"ref": f"refs/heads/{branch}", "sha": sha})
    if r.status_code not in (200, 201, 422):  # 422 = already exists
        r.raise_for_status()


def put_file(token: str, fork: str, path: str, content: str, branch: str, message: str) -> None:
    body: dict = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    # Check if file already exists on the fork branch (need sha to update)
    r = gh("GET", f"/repos/{fork}/contents/{path}", token, params={"ref": branch})
    if r.status_code == 200:
        body["sha"] = r.json()["sha"]

    r = gh("PUT", f"/repos/{fork}/contents/{path}", token, json=body)
    r.raise_for_status()


def get_maps_json(token: str) -> tuple[dict, str]:
    """Fetch data/maps.json from upstream, return (parsed dict, file sha)."""
    r = gh("GET", f"/repos/{UPSTREAM_REPO}/contents/data/maps.json", token)
    r.raise_for_status()
    data = r.json()
    content = base64.b64decode(data["content"]).decode()
    return json.loads(content), data["sha"]


def build_pr(token: str, slug: str, location: str, days: int,
             tg_geojson: dict, hugo_md: str, run_date: str,
             top_ranked: list[tuple[str, int, float | None]]) -> str:
    print("\n🔧 Building GitHub PR…")

    r = gh("GET", "/user", token)
    r.raise_for_status()
    login = r.json()["login"]
    print(f"  Authenticated as: {login}")

    upstream_owner = UPSTREAM_REPO.split("/")[0]
    if login == upstream_owner:
        # User owns the upstream — create branch directly, no fork needed
        working_repo = UPSTREAM_REPO
        print(f"  Working directly on upstream: {working_repo}")
    else:
        working_repo = ensure_fork(token, login)
        print(f"  Fork: {working_repo}")

    main_sha = get_main_sha(token)
    branch = f"ingest/{slug}"
    create_branch(token, working_repo, branch, main_sha)
    print(f"  Branch: {branch}")

    # Commit GeoJSON
    geojson_path = f"static/{slug}/locations.geojson"
    put_file(token, working_repo, geojson_path,
             json.dumps(tg_geojson, ensure_ascii=False, indent=2),
             branch, f"feat({slug}): add locations.geojson from Google Maps reviews")
    print(f"  ✓ {geojson_path}")

    # Commit Hugo content
    content_path = f"content/{slug}/_index.md"
    put_file(token, working_repo, content_path, hugo_md, branch,
             f"feat({slug}): add Hugo content file")
    print(f"  ✓ {content_path}")

    # Update data/maps.json
    maps_doc, maps_sha = get_maps_json(token)
    n_places = len(tg_geojson["features"])
    cats = sorted({f["properties"]["category"] for f in tg_geojson["features"]})
    new_entry = {
        "slug": slug,
        "url": f"/{slug}/",
        "title": f"{location} — Recently Reviewed",
        "description": f"{n_places} venues reviewed in last {days} days",
        "emoji": "📍",
        "section": "community",
        "weight": 50,
        "accent_color": "#1a3a5c",
        "tags": cats,
        "poi_count": n_places,
        "categories": cats,
        "has_geojson": True,
    }
    maps_list = [e for e in maps_doc.get("maps", []) if e.get("slug") != slug]
    maps_list.append(new_entry)
    maps_doc["maps"] = maps_list
    maps_doc["_meta"]["map_count"] = len(maps_list)
    maps_doc["_meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()

    maps_body: dict = {
        "message": f"feat({slug}): add maps.json entry",
        "content": base64.b64encode(json.dumps(maps_doc, ensure_ascii=False, indent=2).encode()).decode(),
        "branch": branch,
        "sha": maps_sha,
    }
    r = gh("PUT", f"/repos/{working_repo}/contents/data/maps.json", token, json=maps_body)
    r.raise_for_status()
    print("  ✓ data/maps.json")

    # Build PR body
    table_rows = "\n".join(
        f"| {name} | {count} | {f'{rating:.1f}★' if rating else '—'} |"
        for name, count, rating in top_ranked[:10]
    )
    pr_body = f"""## {location} — recently reviewed food & drink

**Generated:** {run_date}
**Review window:** last {days} days
**Venues with recent reviews:** {n_places}

### Top venues by recent review count

| Venue | Reviews (last {days}d) | Overall rating |
|-------|----------------------|----------------|
{table_rows}

### Legend
- **Marker color** = average rating of reviews in the last {days} days: 🟢 ≥4★ · 🟡 3–4★ · 🔴 <3★
- **Marker size** = number of recent reviews (larger = more reviews)
- Only venues with at least one review in the last {days} days are shown

"""

    head = branch if login == upstream_owner else f"{login}:{branch}"
    r = gh("POST", f"/repos/{UPSTREAM_REPO}/pulls", token, json={
        "title": f"feat({slug}): add {n_places} recently reviewed venues",
        "head": head,
        "base": "main",
        "body": pr_body,
    })
    r.raise_for_status()
    pr_url = r.json()["html_url"]
    return pr_url


# ---------------------------------------------------------------------------
# HTML map
# ---------------------------------------------------------------------------

def build_html(geojson: dict, run_meta: dict) -> str:
    geojson_str = json.dumps(geojson, ensure_ascii=False)
    location = run_meta["location"]
    run_date = run_meta["run_date"]
    total_places = run_meta["total_places"]
    days = run_meta["days_window"]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{location} — Recent Reviews Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; display: flex; flex-direction: column; height: 100vh; }}
  #header {{ background: #1a1a2e; color: #eee; padding: 10px 16px; display: flex; align-items: center; gap: 12px; flex-shrink: 0; }}
  #header h1 {{ font-size: 1rem; font-weight: 600; }}
  #header span {{ font-size: 0.78rem; color: #aaa; margin-left: auto; }}
  #controls {{ background: #f5f5f5; border-bottom: 1px solid #ddd; padding: 8px 12px; display: flex; gap: 8px; flex-wrap: wrap; flex-shrink: 0; align-items: center; }}
  #controls button {{ border: 1px solid #bbb; background: #fff; border-radius: 20px; padding: 4px 14px; font-size: 0.82rem; cursor: pointer; transition: all .15s; }}
  #controls button.active {{ background: #1a1a2e; color: #fff; border-color: #1a1a2e; }}
  #controls label {{ font-size: 0.82rem; color: #555; margin-left: 8px; }}
  #controls input[type=range] {{ width: 90px; vertical-align: middle; }}
  #main {{ display: flex; flex: 1; overflow: hidden; }}
  #map {{ flex: 1; }}
  #panel {{ width: 300px; background: #fff; border-left: 1px solid #ddd; overflow-y: auto; flex-shrink: 0; }}
  #panel h2 {{ font-size: 0.85rem; font-weight: 600; padding: 10px 14px; border-bottom: 1px solid #eee; background: #fafafa; color: #333; position: sticky; top: 0; }}
  .place-item {{ padding: 10px 14px; border-bottom: 1px solid #f0f0f0; cursor: pointer; }}
  .place-item:hover {{ background: #f9f9f9; }}
  .place-item .place-name {{ font-weight: 600; font-size: 0.88rem; }}
  .place-item .place-meta {{ font-size: 0.76rem; color: #666; margin-top: 2px; }}
  .place-item .recent-badge {{ display: inline-block; background: #e8f5e9; color: #2e7d32; border-radius: 10px; padding: 1px 8px; font-size: 0.72rem; margin-left: 6px; }}
  .popup-name {{ font-weight: 700; font-size: 1rem; margin-bottom: 4px; }}
  .popup-meta {{ font-size: 0.78rem; color: #555; margin-bottom: 4px; }}
  .popup-review {{ background: #f9f9f9; border-left: 3px solid #ccc; padding: 5px 8px; margin: 5px 0; font-size: 0.78rem; }}
  .popup-review.high {{ border-color: #4caf50; }}
  .popup-review .rev-author {{ font-weight: 600; color: #333; }}
  .popup-review .rev-date {{ color: #888; font-size: 0.72rem; }}
  .popup-review .rev-text {{ margin-top: 2px; color: #444; }}
  #legend {{ position: absolute; bottom: 24px; left: 12px; z-index: 1000; background: rgba(255,255,255,.95); border-radius: 8px; padding: 10px 14px; font-size: 0.78rem; box-shadow: 0 1px 6px rgba(0,0,0,.2); min-width: 190px; }}
  #legend h3 {{ font-size: 0.82rem; font-weight: 700; margin-bottom: 6px; color: #111; }}
  #legend .leg-note {{ font-size: 0.72rem; color: #666; margin-bottom: 8px; border-bottom: 1px solid #eee; padding-bottom: 6px; }}
  .leg-section {{ font-size: 0.72rem; font-weight: 600; color: #888; text-transform: uppercase; letter-spacing: .04em; margin: 6px 0 3px; }}
  .leg-row {{ display: flex; align-items: center; gap: 7px; margin: 3px 0; font-size: 0.78rem; }}
  .leg-dot {{ border-radius: 50%; display: inline-block; flex-shrink: 0; }}
  @media (max-width: 600px) {{ #panel {{ display: none; }} }}
</style>
</head>
<body>
<div id="header">
  <h1>📍 {location}</h1>
  <span>{total_places} venues searched · last {days} days · {run_date}</span>
</div>
<div id="controls">
  <button class="active" data-cat="all">All</button>
  <button data-cat="cafe">Café</button>
  <button data-cat="restaurant">Restaurant</button>
  <button data-cat="bar">Bar</button>
  <label>Min recent rating: <span id="rating-val">0</span>★
    <input type="range" id="rating-filter" min="0" max="5" step="0.5" value="0">
  </label>
</div>
<div id="main">
  <div id="map"></div>
  <div id="panel">
    <h2>Most recently reviewed</h2>
    <div id="place-list"></div>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const GEOJSON = {geojson_str};

const map = L.map('map');
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors © <a href="https://carto.com/">CARTO</a>',
  subdomains: 'abcd',
  maxZoom: 20,
}}).addTo(map);

function tierColor(props) {{
  const r = props.avgRecentRating;
  if (r == null) return '#90a4ae';
  if (r >= 4.0) return '#43a047';
  if (r >= 3.0) return '#fdd835';
  return '#e53935';
}}

function starStr(r) {{
  if (r == null) return '';
  return '★'.repeat(Math.round(r)) + '☆'.repeat(5 - Math.round(r)) + ' ' + r.toFixed(1);
}}

function truncate(s, n) {{
  return s && s.length > n ? s.slice(0, n) + '…' : (s || '');
}}

function makeMarker(feature) {{
  const [lon, lat] = feature.geometry.coordinates;
  const p = feature.properties;
  const color = tierColor(p);
  const circle = L.circleMarker([lat, lon], {{
    radius: Math.min(7 + p.recentReviewCount * 2, 18),
    color: '#fff',
    weight: 1.5,
    fillColor: color,
    fillOpacity: 0.88,
  }});

  let html = `<div class="popup-name">${{p.name}}</div>`;
  html += `<div class="popup-meta">${{p.category || ''}}`;
  if (p.rating) html += ` · ${{starStr(p.rating)}} (${{p.userRatingCount || 0}} total)`;
  html += `</div>`;
  const recAvg = p.avgRecentRating != null ? ` · avg ${{p.avgRecentRating.toFixed(1)}}★` : '';
  html += `<div class="popup-meta"><b>${{p.recentReviewCount}} review${{p.recentReviewCount > 1 ? 's' : ''}} in last {days}d</b>${{recAvg}}</div>`;
  if (p.hours) html += `<div class="popup-meta">🕐 ${{p.hours}}</div>`;
  if (p.phone) html += `<div class="popup-meta">📞 ${{p.phone}}</div>`;
  if (p.website) html += `<div class="popup-meta"><a href="${{p.website}}" target="_blank">🌐 Website</a></div>`;

  for (const r of (p.reviews || []).slice(0, 3)) {{
    const cls = r.rating >= 4 ? 'high' : '';
    html += `<div class="popup-review ${{cls}}">`;
    html += `<span class="rev-author">${{r.author}}</span> <span class="rev-date">${{r.relative_time}}</span>`;
    if (r.rating) html += ` ${{starStr(r.rating)}}`;
    if (r.text) html += `<div class="rev-text">${{truncate(r.text, 140)}}</div>`;
    html += `</div>`;
  }}

  circle.bindPopup(html, {{ maxWidth: 290 }});
  circle._featureProps = p;
  return circle;
}};

const markers = [];
const layerGroup = L.layerGroup().addTo(map);

for (const f of GEOJSON.features) {{
  if (!f.properties.recentReviewCount) continue;
  const m = makeMarker(f);
  markers.push(m);
  layerGroup.addLayer(m);
}}

if (markers.length) {{
  map.fitBounds(L.featureGroup(markers).getBounds().pad(0.15));
}}

// Legend
const legend = L.control({{position: 'bottomleft'}});
legend.onAdd = () => {{
  const d = L.DomUtil.create('div', '');
  d.id = 'legend';
  d.innerHTML = `
    <h3>📍 {location}</h3>
    <div class="leg-note">Only venues reviewed in the last <b>{days} days</b></div>
    <div class="leg-section">Marker color — avg recent rating</div>
    <div class="leg-row"><span class="leg-dot" style="background:#43a047;width:12px;height:12px"></span> ≥4★ — highly rated recently</div>
    <div class="leg-row"><span class="leg-dot" style="background:#fdd835;width:12px;height:12px"></span> 3–4★ — mixed recent reviews</div>
    <div class="leg-row"><span class="leg-dot" style="background:#e53935;width:12px;height:12px"></span> &lt;3★ — poor recent reviews</div>
    <div class="leg-section" style="margin-top:8px">Marker size — review count</div>
    <div class="leg-row"><span class="leg-dot" style="background:#888;width:8px;height:8px"></span> 1 recent review</div>
    <div class="leg-row"><span class="leg-dot" style="background:#888;width:14px;height:14px"></span> 3+ recent reviews</div>
  `;
  return d;
}};
legend.addTo(map);

// Filters
let activeCat = 'all';
let minRating = 0;

function applyFilters() {{
  layerGroup.clearLayers();
  for (const m of markers) {{
    const p = m._featureProps;
    const catMatch = activeCat === 'all' || (p.category || '').includes(activeCat);
    const ratingMatch = p.avgRecentRating == null || p.avgRecentRating >= minRating;
    if (catMatch && ratingMatch) layerGroup.addLayer(m);
  }}
}}

document.querySelectorAll('#controls button').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('#controls button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeCat = btn.dataset.cat;
    applyFilters();
  }});
}});

const ratingInput = document.getElementById('rating-filter');
const ratingVal = document.getElementById('rating-val');
ratingInput.addEventListener('input', () => {{
  minRating = parseFloat(ratingInput.value);
  ratingVal.textContent = minRating;
  applyFilters();
}});

// Side panel
const sorted = [...GEOJSON.features]
  .filter(f => f.properties.recentReviewCount > 0)
  .sort((a, b) => {{
    const da = a.properties.latestReviewDate || '';
    const db = b.properties.latestReviewDate || '';
    return db.localeCompare(da);
  }});

const list = document.getElementById('place-list');
for (const f of sorted) {{
  const p = f.properties;
  const div = document.createElement('div');
  div.className = 'place-item';
  div.innerHTML = `<div class="place-name">${{p.name}}<span class="recent-badge">${{p.recentReviewCount}} recent</span></div>
    <div class="place-meta">${{p.category || ''}}${{p.avgRecentRating ? ' · ' + p.avgRecentRating.toFixed(1) + '★ recent' : ''}}</div>`;
  div.addEventListener('click', () => {{
    const [lon, lat] = f.geometry.coordinates;
    map.setView([lat, lon], 17);
    const m = markers.find(mk => mk._featureProps.id === p.id);
    if (m) m.openPopup();
  }});
  list.appendChild(div);
}}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def stars(rating: float | None) -> str:
    if rating is None:
        return ""
    return f"{'★' * round(rating)}{'☆' * (5 - round(rating))} {rating:.1f}"


def main():
    args = parse_args()
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        print("Error: GOOGLE_MAPS_API_KEY not set in environment or .env file")
        sys.exit(1)

    if args.pr and not args.github_token:
        print("Error: --pr requires a GitHub token (--github-token or GITHUB_TOKEN env var)")
        sys.exit(1)

    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Discover places
    print(f"\n🔍 Searching for places near {args.location} (radius {args.radius:.0f}m)...")
    all_places: dict[str, dict] = {}
    for cat in categories:
        query = f"{cat} {args.location}"
        print(f"  → {query}")
        found = search_places(api_key, query, args.lat, args.lon, args.radius)
        for p in found:
            all_places[p["id"]] = p
        print(f"     {len(found)} found ({len(all_places)} unique total)")

    places = list(all_places.values())
    print(f"\n✅ {len(places)} unique venues discovered")

    # 2. Collect & filter reviews by date window
    print(f"\n📝 Collecting reviews (last {args.days} days)...")
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=args.days)

    all_reviews = collect_all_reviews(places)
    all_reviews = [r for r in all_reviews if r["publish_dt"] is not None]
    total_reviews = len(all_reviews)

    top_reviews = [r for r in all_reviews if r["publish_dt"] >= cutoff_dt]
    top_reviews.sort(key=lambda r: r["publish_dt"], reverse=True)

    print(f"  {total_reviews} reviews collected across {len(places)} venues")
    print(f"  {len(top_reviews)} reviews within last {args.days} days (since {cutoff_dt.strftime('%Y-%m-%d')})")

    # 3. Build review index
    review_count_map: dict[str, int] = {}
    latest_date_map: dict[str, str] = {}
    reviews_by_place: dict[str, list] = {}
    for r in top_reviews:
        pid = r["place_id"]
        review_count_map[pid] = review_count_map.get(pid, 0) + 1
        if pid not in latest_date_map or (r["publish_time"] or "") > latest_date_map[pid]:
            latest_date_map[pid] = r["publish_time"] or ""
        reviews_by_place.setdefault(pid, []).append(r)

    # 4. Print ranking
    place_map = {p["id"]: p for p in places}
    ranked = sorted(review_count_map.items(), key=lambda x: x[1], reverse=True)[:10]
    top_ranked = []
    print(f"\n🏆 Top venues by recent reviews:")
    for pid, count in ranked:
        p = place_map.get(pid, {})
        name = p.get("displayName", {}).get("text", pid)
        rating = p.get("rating")
        print(f"  {count:2d} reviews  {stars(rating)}  {name}")
        top_ranked.append((name, count, rating))

    # 5. Build outputs
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = f"{args.slug}-{run_date}"
    run_meta = {
        "run_date": run_date,
        "location": args.location,
        "lat": args.lat,
        "lon": args.lon,
        "radius_m": args.radius,
        "categories": categories,
        "total_places": len(places),
        "total_reviews_collected": total_reviews,
        "reviews_in_window": len(top_reviews),
        "days_window": args.days,
        "cutoff_date": cutoff_dt.isoformat(),
    }

    geojson = build_geojson(places, top_reviews, run_meta)

    geojson_path = out_dir / f"{slug}_reviewed.geojson"
    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    print(f"\n💾 GeoJSON saved: {geojson_path}")

    html_path = out_dir / f"{slug}_map.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(build_html(geojson, run_meta))
    print(f"🗺️  Map saved:    {html_path}")
    print(f"\nOpen in browser: file://{html_path.resolve()}")

    # 6. Build PR
    if args.pr:
        tg_geojson = build_travel_guide_geojson(
            places, reviews_by_place, review_count_map, slug, args.min_reviews
        )
        n_places = len(tg_geojson["features"])
        print(f"\n📦 Travel-guide GeoJSON: {n_places} venues (min {args.min_reviews} recent review)")

        tags = sorted({f["properties"]["category"] for f in tg_geojson["features"]})
        hugo_md = build_hugo_content(args.location, args.days, n_places, tags)

        pr_url = build_pr(
            args.github_token, slug, args.location, args.days,
            tg_geojson, hugo_md, run_date, top_ranked,
        )
        print(f"\n✅ PR opened: {pr_url}")


if __name__ == "__main__":
    main()
