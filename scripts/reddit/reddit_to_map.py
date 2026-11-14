#!/usr/bin/env python3
"""
reddit_to_map.py — Extract location mentions from a Reddit submission's
comments and publish them as a GeoJSON map on alx/travel-guide.

Usage:
    uv run python scripts/reddit_to_map.py <reddit_url> [options]

Options:
    --name NAME          Human-readable map name (default: derived from post title)
    --slug SLUG          URL slug / directory name (default: derived from post title)
    --bbox LAT_S LON_W LAT_N LON_E   Restrict geocoding to bounding box
    --min-score N        Only include comments with score >= N (default: 1)
    --dry-run            Print GeoJSON without creating a PR

Example:
    uv run python scripts/reddit_to_map.py \\
        https://old.reddit.com/r/kohsamui/comments/1snhkv5/where_do_i_stay_in_koh_samui/

Credentials:
    Set in .env (same format as alx/reddit_painpoints):
        REDDIT_CLIENT_ID=...
        REDDIT_CLIENT_SECRET=...
        REDDIT_USER_AGENT=...
    Reddit posting (required for --reply):
        REDDIT_USERNAME=...
        REDDIT_PASSWORD=...
    Optional geocoding boost:
        LOCATIONIQ_API_KEY=...
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# .env loader (no python-dotenv dependency)
# ---------------------------------------------------------------------------

def load_env(path: Path | None = None) -> None:
    candidates = [path, Path(".env"), Path(__file__).parent.parent / ".env"]
    for p in candidates:
        if p and p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
            return


# ---------------------------------------------------------------------------
# Reddit — PRAW
# ---------------------------------------------------------------------------

def init_reddit(write: bool = False):
    """
    Initialise a PRAW Reddit instance.
    write=True adds username/password for authenticated posting.
    Requires REDDIT_USERNAME + REDDIT_PASSWORD in .env.
    """
    import praw  # installed via uv in rpp venv
    kwargs = dict(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=os.environ.get("REDDIT_USER_AGENT", "reddit-to-map/1.0"),
    )
    if write:
        username = os.environ.get("REDDIT_USERNAME")
        password = os.environ.get("REDDIT_PASSWORD")
        if not username or not password:
            raise RuntimeError(
                "REDDIT_USERNAME and REDDIT_PASSWORD required for posting.\n"
                "Add them to .env and ensure the Reddit app is 'script' type."
            )
        kwargs["username"] = username
        kwargs["password"] = password
    return praw.Reddit(**kwargs)


BASE_URL = "https://maps.girard-davila.net"


def post_reply(submission, map_url: str, map_name: str, feature_count: int) -> str | None:
    """
    Post a top-level comment on the submission with a link to the generated map.
    Returns the permalink of the posted comment, or None on failure.
    """
    body = (
        f"🗺️ I mapped the locations mentioned in this thread — "
        f"**[{map_name}]({map_url})**\n\n"
        f"The map shows **{feature_count} locations** extracted automatically from "
        f"the comments using NLP (spaCy) and geocoded via OpenStreetMap. "
        f"Click any pin to get OsmAnd / Google Maps links.\n\n"
        f"*Built with [maps.girard-davila.net]({BASE_URL}) · "
        f"[source](https://github.com/alx/travel-guide)*"
    )
    try:
        comment = submission.reply(body)
        return f"https://reddit.com{comment.permalink}"
    except Exception as e:
        print(f"  ⚠️  Could not post reply: {e}")
        return None


def fetch_comments(reddit, url: str, min_score: int = 1,
                   submission=None) -> tuple[dict, list[dict]]:
    """Return (post_meta, flat_comments_list). Pass existing submission to avoid re-fetch."""
    if submission is None:
        submission = reddit.submission(url=url)
    submission.comments.replace_more(limit=None)  # expand all MoreComments

    post = {
        "id": submission.id,
        "title": submission.title,
        "url": submission.url,
        "subreddit": str(submission.subreddit),
        "score": submission.score,
        "selftext": submission.selftext or "",
    }

    comments = []

    def _collect(comment_list, depth=0):
        for c in comment_list:
            if not hasattr(c, "body"):
                continue
            if c.body in ("[deleted]", "[removed]"):
                continue
            if c.score >= min_score:
                comments.append({
                    "id": c.id,
                    "body": c.body,
                    "score": c.score,
                    "depth": depth,
                    "author": str(c.author) if c.author else "[deleted]",
                })
            _collect(getattr(c, "replies", []), depth + 1)

    _collect(submission.comments)
    print(f"  Post: '{post['title']}'")
    print(f"  Subreddit: r/{post['subreddit']}")
    print(f"  Comments fetched: {len(comments)} (score >= {min_score})")
    return post, comments


# ---------------------------------------------------------------------------
# NER — spaCy location extraction
# ---------------------------------------------------------------------------

SKIP_ENTITIES = {
    # Generic/non-geographic terms that spaCy often mis-tags
    "google", "airbnb", "agoda", "booking", "tripadvisor", "instagram",
    "the north", "the south", "the east", "the west", "southwest", "northeast",
    "downtown", "uptown", "cbd", "old town", "new town", "city center",
    "a pool", "the beach", "the island", "the coast", "the bay",
    "budget", "luxury", "mid-range", "backpacker", "hostel",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}

def extract_locations(texts: list[str]) -> list[str]:
    """
    Run spaCy NER over all texts, return deduplicated list of GPE/LOC/FAC
    entities likely to be mappable places.
    """
    import spacy
    nlp = spacy.load("en_core_web_sm")

    counter: Counter = Counter()
    for text in texts:
        doc = nlp(text[:5000])  # spaCy token limit safety
        for ent in doc.ents:
            if ent.label_ not in ("GPE", "LOC", "FAC"):
                continue
            name = ent.text.strip()
            if len(name) < 3 or name.lower() in SKIP_ENTITIES:
                continue
            # Skip pure numbers, URLs, @mentions
            if re.match(r"^[\d\W]+$", name) or name.startswith(("http", "@")):
                continue
            counter[name] += 1

    # Return sorted by frequency, most-mentioned first
    return [loc for loc, _ in counter.most_common()]


# ---------------------------------------------------------------------------
# Geocoding — Photon → Nominatim → LocationIQ cascade
# ---------------------------------------------------------------------------

_GEO_HEADERS = {"User-Agent": "maps.girard-davila.net/reddit-to-map"}


def _in_bbox(lon: float, lat: float, bbox: tuple | None) -> bool:
    if not bbox:
        return True
    lat_s, lon_w, lat_n, lon_e = bbox
    return lat_s <= lat <= lat_n and lon_w <= lon <= lon_e


def geocode_photon(query: str, bbox: tuple | None = None) -> dict | None:
    params = {"q": query, "limit": 5}
    url = "https://photon.komoot.io/api/?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers=_GEO_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        for f in data.get("features", []):
            coords = f["geometry"]["coordinates"]  # [lon, lat]
            if _in_bbox(coords[0], coords[1], bbox):
                p = f["properties"]
                return {
                    "lon": coords[0], "lat": coords[1],
                    "display_name": p.get("name", query),
                    "city": p.get("city") or p.get("suburb") or "",
                    "country": p.get("country", ""),
                    "osm_id": str(p.get("osm_id", "")),
                    "osm_type": p.get("osm_type", ""),
                    "source": "photon",
                }
    except Exception:
        pass
    return None


def geocode_nominatim(query: str, bbox: tuple | None = None) -> dict | None:
    params = {
        "q": query, "format": "json", "limit": 5,
        "accept-language": "en",
    }
    if bbox:
        lat_s, lon_w, lat_n, lon_e = bbox
        params["viewbox"] = f"{lon_w},{lat_n},{lon_e},{lat_s}"
        params["bounded"] = "1"
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers=_GEO_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            results = json.loads(r.read())
        if results:
            r = results[0]
            lon, lat = float(r["lon"]), float(r["lat"])
            if _in_bbox(lon, lat, bbox):
                return {
                    "lon": lon, "lat": lat,
                    "display_name": r.get("display_name", query)[:80],
                    "city": "", "country": "",
                    "osm_id": str(r.get("osm_id", "")),
                    "osm_type": r.get("osm_type", ""),
                    "source": "nominatim",
                }
    except Exception:
        pass
    return None


def geocode_locationiq(query: str, bbox: tuple | None = None) -> dict | None:
    key = os.environ.get("LOCATIONIQ_API_KEY")
    if not key:
        return None
    params = {"key": key, "q": query, "format": "json", "limit": 5}
    url = "https://us1.locationiq.com/v1/search?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers=_GEO_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            results = json.loads(r.read())
        if results:
            r = results[0]
            lon, lat = float(r["lon"]), float(r["lat"])
            if _in_bbox(lon, lat, bbox):
                return {
                    "lon": lon, "lat": lat,
                    "display_name": r.get("display_name", query)[:80],
                    "city": "", "country": "",
                    "osm_id": str(r.get("osm_id", "")),
                    "osm_type": r.get("osm_type", ""),
                    "source": "locationiq",
                }
    except Exception:
        pass
    return None


def geocode(query: str, bbox: tuple | None = None, context: str = "") -> dict | None:
    """Try Photon → Nominatim → LocationIQ. Add context string for better results."""
    search = f"{query} {context}".strip() if context else query
    for fn in (geocode_photon, geocode_nominatim, geocode_locationiq):
        result = fn(search, bbox)
        if result:
            result["query"] = query
            return result
        time.sleep(0.5)
    # Try without context as last resort
    if context:
        for fn in (geocode_photon, geocode_nominatim):
            result = fn(query, bbox)
            if result:
                result["query"] = query
                return result
            time.sleep(0.5)
    return None


# ---------------------------------------------------------------------------
# GeoJSON builder
# ---------------------------------------------------------------------------

SOURCE_TO_ACCURACY = {
    "photon": "high",
    "nominatim": "high",
    "locationiq": "high",
}
# Photon is OSM-backed (same data as Nominatim) — map to allowed enum value
SOURCE_TO_COORD_SOURCE = {
    "photon": "nominatim",
    "nominatim": "nominatim",
    "locationiq": "nominatim",
}

CATEGORY_ICONS = {
    "hotel": ("🏨", "Hotel"),
    "resort": ("🏨", "Resort"),
    "hostel": ("🛏️", "Hostel"),
    "villa": ("🏡", "Villa"),
    "guesthouse": ("🏠", "Guesthouse"),
    "beach": ("🏖️", "Beach"),
    "restaurant": ("🍽️", "Restaurant"),
    "bar": ("🍺", "Bar"),
    "cafe": ("☕", "Café"),
    "temple": ("🛕", "Temple"),
    "market": ("🛒", "Market"),
    "pier": ("⚓", "Pier"),
    "waterfall": ("💦", "Waterfall"),
    "viewpoint": ("🔭", "Viewpoint"),
    "island": ("🏝️", "Island"),
    "bay": ("🌊", "Bay"),
    "area": ("📍", "Area"),
    "district": ("📍", "District"),
}


def classify(name: str, geo: dict) -> tuple[str, str]:
    """Return (icon, category) based on name/display_name heuristics."""
    text = (name + " " + geo.get("display_name", "")).lower()
    for kw, (icon, cat) in CATEGORY_ICONS.items():
        if kw in text:
            return icon, cat
    osm_type = geo.get("osm_type", "")
    if osm_type in ("N", "W", "R"):
        return "📍", "Place"
    return "📍", "Location"


def build_geojson(slug: str, geocoded: list[dict], post: dict) -> dict:
    features = []
    for i, g in enumerate(geocoded):
        icon, cat = classify(g["query"], g)
        fid = f"{slug}-{i+1:03d}"
        features.append({
            "type": "Feature",
            "id": fid,
            "geometry": {"type": "Point", "coordinates": [round(g["lon"], 6), round(g["lat"], 6)]},
            "properties": {
                "id": fid,
                "name": g["query"],
                "category": cat,
                "icon": icon,
                "display_name": g.get("display_name", ""),
                "source_post": post["url"],
                "source_subreddit": f"r/{post['subreddit']}",
                "coord_source": SOURCE_TO_COORD_SOURCE.get(g.get("source", ""), "nominatim"),
                "coord_accuracy": SOURCE_TO_ACCURACY.get(g.get("source", ""), "medium"),
                "osm_id": g.get("osm_id", ""),
                "osm_type": g.get("osm_type", ""),
            },
        })

    lons = [f["geometry"]["coordinates"][0] for f in features]
    lats = [f["geometry"]["coordinates"][1] for f in features]

    return {
        "type": "FeatureCollection",
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "_meta": {
            "crs": "EPSG:4326",
            "generated": datetime.now(timezone.utc).isoformat(),
            "source": f"Reddit r/{post['subreddit']} — {post['url']}",
        },
        "features": features,
    }


# ---------------------------------------------------------------------------
# Hugo content + layout
# ---------------------------------------------------------------------------

LAYOUT_TEMPLATE = '''\
{{{{ define "head" }}}}
<style>
  body {{ overflow: hidden; }}
  .site-header, footer {{ display: none !important; }}
  main {{ max-width: 100% !important; padding: 0 !important; margin: 0 !important; }}
  #map {{ position: fixed; inset: 0; z-index: 0; }}

  .map-overlay {{
    position: fixed; left: 1rem; top: 1rem; bottom: 1rem;
    width: 340px; z-index: 100;
    display: flex; flex-direction: column; gap: 0.5rem; pointer-events: none;
  }}
  .map-overlay > * {{ pointer-events: all; }}
  .overlay-card {{
    background: rgba(255,255,255,0.95); backdrop-filter: blur(10px);
    border-radius: 12px; padding: 0.85rem 1rem;
    box-shadow: 0 4px 20px rgba(0,0,0,0.13); border: 1px solid rgba(255,255,255,0.6);
  }}
  .overlay-card.scrollable {{
    flex: 1; min-height: 0; overflow-y: auto;
    display: flex; flex-direction: column; gap: 0.5rem;
  }}
  .overlay-card .page-header h1 {{ font-size: 1.2rem; font-weight: 700; margin-bottom: 0.2rem; }}
  .overlay-card .page-header p {{ font-size: 0.82rem; color: #666; }}
  .site-brand {{ font-size: 0.7rem; font-weight: 600; color: #1a3a5c; text-decoration: none; display: block; margin-bottom: 0.35rem; opacity: 0.65; }}
  .site-brand:hover {{ opacity: 1; }}
  #map-toolbar {{ display: none !important; margin: 0.6rem 0 0 !important; }}
  #map-toolbar.tb-visible {{ display: flex !important; }}
  .header-actions {{ display: flex; gap: 0.4rem; margin-top: 0.5rem; flex-wrap: wrap; }}
  .header-btn {{ padding: 0.2rem 0.6rem; border-radius: 16px; border: 1.5px solid #ddd; background: white; cursor: pointer; font-size: 0.72rem; color: #555; transition: all 0.15s; }}
  .header-btn:hover, .header-btn.active {{ border-color: #1a3a5c; color: #1a3a5c; background: #f0f4ff; }}
  .filter-btn {{ padding: 0.3rem 0.7rem; border-radius: 20px; border: 1.5px solid #ddd; background: white; cursor: pointer; font-size: 0.78rem; font-weight: 500; transition: all 0.15s; }}
  .filter-btn.active {{ background: #1a3a5c; color: white; border-color: #1a3a5c; }}
  #filter-btns {{ display: flex; gap: 0.4rem; flex-wrap: wrap; }}
  .poi-card {{ background: white; border-radius: 10px; padding: 0.75rem 0.85rem; box-shadow: 0 1px 4px rgba(0,0,0,0.08); cursor: pointer; border: 2px solid transparent; transition: all 0.15s; flex-shrink: 0; }}
  .poi-card:hover, .poi-card.active {{ border-color: #1a3a5c; }}
  .poi-card h3 {{ font-size: 0.88rem; font-weight: 600; margin-bottom: 0.2rem; }}
  .poi-card .poi-meta {{ font-size: 0.75rem; color: #888; margin-bottom: 0.35rem; }}
  .poi-card .poi-actions {{ display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.35rem; }}
  .poi-card .poi-actions a {{ font-size: 0.73rem; color: #1a3a5c; text-decoration: none; font-weight: 500; }}
  .cat-badge {{ display: inline-block; font-size: 0.68rem; padding: 0.12rem 0.45rem; border-radius: 20px; font-weight: 600; margin-bottom: 0.25rem; background: #1a3a5c22; color: #1a3a5c; }}
  .search-box {{ width: 100%; padding: 0.45rem 0.7rem; border: 1.5px solid #ddd; border-radius: 8px; font-size: 0.84rem; }}
  .search-box:focus {{ outline: none; border-color: #1a3a5c; }}
  .reddit-source {{ font-size: 0.7rem; color: #888; margin-top: 0.3rem; }}
  .reddit-source a {{ color: #ff4500; text-decoration: none; }}
  .reddit-source a:hover {{ text-decoration: underline; }}

  @media (max-width: 768px) {{
    .map-overlay {{ left: 0; right: 0; bottom: 0; top: auto; width: 100%; flex-direction: column-reverse; gap: 0; }}
    .overlay-card {{ border-radius: 0; }}
    .overlay-card.scrollable {{ max-height: 0; overflow: hidden; padding: 0 !important; transition: max-height 0.3s ease; }}
    .overlay-card.scrollable.mobile-open {{ max-height: 45vh; overflow-y: auto; padding: 0.85rem 1rem !important; }}
  }}
</style>
{{{{ end }}}}

{{{{ define "main" }}}}
<div id="map"></div>
<div class="map-overlay">
  <div class="overlay-card">
    <a href="{{{{ "/" | relURL }}}}" class="site-brand">🗺️ Maps</a>
    <div class="page-header">
      <h1>{EMOJI} {TITLE}</h1>
      <p>{DESCRIPTION}</p>
    </div>
    <div class="reddit-source">
      From <a href="{POST_URL}" target="_blank">r/{SUBREDDIT}</a> · {COMMENT_COUNT} comments analysed
    </div>
    <div class="header-actions">
      <button class="header-btn" id="tb-toggle" onclick="this.classList.toggle(\'active\');document.getElementById(\'map-toolbar\').classList.toggle(\'tb-visible\')">🛠️ Tools</button>
      <button class="header-btn" id="list-toggle" onclick="this.classList.toggle(\'active\');document.querySelector(\'.overlay-card.scrollable\').classList.toggle(\'mobile-open\')">📍 Places</button>
      <button class="header-btn" id="locate-btn" onclick="toggleLocation()" title="My position">📍 Me</button>
    </div>
    {{{{ partial "map-toolbar.html" . }}}}
  </div>
  <div class="overlay-card scrollable">
    <input class="search-box" type="text" id="search" placeholder="🔍 Search locations..." oninput="filterPOIs()">
    <div id="filter-btns"></div>
    <div id="poi-list"></div>
  </div>
</div>
<button class="embed-overlay-toggle" id="overlay-toggle-btn" title="Show list">☰</button>
{{{{ end }}}}

{{{{ define "scripts" }}}}
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
window.MAP_CONFIG = {{
  title: {JSON_TITLE},
  geojsonUrl: '{{{{ "/{SLUG}/locations.geojson" | relURL }}}}',
  embedPath: '/{SLUG}/',
}};
const GEOJSON_URL = window.MAP_CONFIG.geojsonUrl;
const CAT_COLOR = '#1a3a5c';

let map, allPOIs = [], markers = {{}}, activeFilter = 'All', activeCard = null;
Object.defineProperty(window, 'allPOIs', {{ get: () => allPOIs, configurable: true }});
window._mapCategoryColors = {{ 'Place': CAT_COLOR }};
window._mapCategoryIcons  = {{ 'Place': '📍' }};

map = L.map('map').setView([{CENTER_LAT}, {CENTER_LON}], {ZOOM});
window._leafletMap = map;
map.on('click', clearFocus);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '© OpenStreetMap contributors'
}}).addTo(map);

function makeIcon(icon) {{
  return L.divIcon({{
    className: '',
    html: `<div style="background:#1a3a5c;width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:15px;box-shadow:0 2px 6px rgba(0,0,0,0.3);border:2px solid white;">${{icon}}</div>`,
    iconSize: [30,30], iconAnchor: [15,15], popupAnchor: [0,-18]
  }});
}}

function focusPOI(f) {{
  const [lng, lat] = f.geometry.coordinates;
  map.flyTo([lat, lng], 15, {{duration: 0.7}});
  if (markers[f._id]) markers[f._id].openPopup();
  if (activeCard !== null) document.getElementById('card-' + activeCard)?.classList.remove('active');
  activeCard = f._id;
  document.getElementById('card-' + f._id)?.classList.add('active');
  document.getElementById('card-' + f._id)?.scrollIntoView({{behavior:'smooth',block:'nearest'}});
}}

function clearFocus() {{
  if (activeCard === null) return;
  document.getElementById('card-' + activeCard)?.classList.remove('active');
  activeCard = null;
}}

function renderSidebar() {{
  const search = document.getElementById('search').value.toLowerCase();
  const list = document.getElementById('poi-list');
  list.innerHTML = '';
  allPOIs.filter(f => {{
    const p = f.properties;
    const matchCat = activeFilter === 'All' || p.category === activeFilter;
    const matchSearch = !search || p.name.toLowerCase().includes(search);
    return matchCat && matchSearch;
  }}).forEach(f => {{
    const p = f.properties;
    const [lng, lat] = f.geometry.coordinates;
    const card = document.createElement('div');
    card.className = 'poi-card'; card.id = 'card-' + f._id;
    card.innerHTML = `
      <span class="cat-badge">${{p.icon || '📍'}} ${{p.category}}</span>
      <h3>${{p.name}}</h3>
      ${{p.display_name ? `<div class="poi-meta">${{p.display_name}}</div>` : ''}}
      <div class="poi-actions">
        <a href="geo:${{lat}},${{lng}}" title="OsmAnd">🗺️ OsmAnd</a>
        <a href="https://www.google.com/maps/search/?api=1&query=${{lat}},${{lng}}" target="_blank">Google Maps ↗</a>
      </div>`;
    card.onclick = () => focusPOI(f);
    list.appendChild(card);
  }});
}}

function filterPOIs() {{ renderSidebar(); updateMarkers(); }}

function updateMarkers() {{
  const search = document.getElementById('search').value.toLowerCase();
  Object.values(markers).forEach(m => map.removeLayer(m));
  Object.keys(markers).forEach(k => delete markers[k]);
  allPOIs.forEach(f => {{
    const p = f.properties;
    const matchCat = activeFilter === 'All' || p.category === activeFilter;
    const matchSearch = !search || p.name.toLowerCase().includes(search);
    if (!matchCat || !matchSearch) return;
    const [lng, lat] = f.geometry.coordinates;
    const m = L.marker([lat, lng], {{icon: makeIcon(p.icon || '📍')}})
      .addTo(map).bindPopup(`<b>${{p.name}}</b><br><small>${{p.category}}</small><br><em>${{p.display_name || ''}}</em>`);
    m.on('click', () => focusPOI(f));
    markers[f._id] = m;
  }});
}}

function setupFilters(pois) {{
  const cats = ['All', ...new Set(pois.map(f => f.properties.category))];
  const container = document.getElementById('filter-btns');
  container.innerHTML = '';
  cats.forEach(cat => {{
    const btn = document.createElement('button');
    btn.className = 'filter-btn' + (cat === activeFilter ? ' active' : '');
    btn.textContent = cat;
    btn.onclick = () => {{
      activeFilter = cat;
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterPOIs();
    }};
    container.appendChild(btn);
  }});
}}

fetch(GEOJSON_URL)
  .then(r => r.json())
  .then(data => {{
    allPOIs = data.features.map((f, i) => ({{ ...f, _id: i }}));
    window.MAP_CONFIG.geojsonData = data;
    setupFilters(allPOIs);
    renderSidebar();
    updateMarkers();
    if (Object.keys(markers).length > 0) {{
      map.fitBounds(L.featureGroup(Object.values(markers)).getBounds().pad(0.1));
    }}
  }})
  .catch(() => {{
    document.getElementById('poi-list').innerHTML = '<p style="color:#888;font-size:0.85rem;padding:1rem;">No locations loaded.</p>';
  }});

// Embed toggle
(function() {{
  if (!document.documentElement.classList.contains('embed-mode')) return;
  var overlay = document.querySelector('.map-overlay');
  var btn = document.getElementById('overlay-toggle-btn');
  if (!overlay || !btn) return;
  function setOverlay(open) {{
    overlay.style.display = open ? 'flex' : 'none';
    btn.textContent = open ? '✕' : '☰';
  }}
  btn.addEventListener('click', function() {{
    setOverlay(overlay.style.display === 'none' || overlay.style.display === '');
  }});
  setOverlay(new URLSearchParams(location.search).get('overlay') === '1');
}})();
</script>
{{ partial "map-geolocation.html" . }}
{{{{ end }}}}
'''


def slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:50]


def infer_emoji(title: str, subreddit: str) -> str:
    text = (title + " " + subreddit).lower()
    if any(k in text for k in ("samui", "koh", "phuket", "thailand", "thai", "asia")):
        return "🌴"
    if any(k in text for k in ("paris", "france", "europe")):
        return "🗼"
    if any(k in text for k in ("japan", "tokyo", "kyoto")):
        return "⛩️"
    if any(k in text for k in ("food", "eat", "restaurant", "cafe")):
        return "🍽️"
    if any(k in text for k in ("hotel", "stay", "sleep", "accom")):
        return "🏨"
    if any(k in text for k in ("beach", "coast", "sea", "ocean")):
        return "🏖️"
    return "📍"


def write_layout(layout_dir: Path, slug: str, name: str, emoji: str,
                 post: dict, comment_count: int, geocoded: list[dict]) -> None:
    center_lat = sum(g["lat"] for g in geocoded) / len(geocoded)
    center_lon = sum(g["lon"] for g in geocoded) / len(geocoded)

    # Estimate good zoom level from coordinate spread
    lat_spread = max(g["lat"] for g in geocoded) - min(g["lat"] for g in geocoded)
    lon_spread = max(g["lon"] for g in geocoded) - min(g["lon"] for g in geocoded)
    spread = max(lat_spread, lon_spread)
    zoom = 13 if spread < 0.05 else (11 if spread < 0.3 else (9 if spread < 2 else 7))

    description = f"Locations mentioned in r/{post['subreddit']} · {post['title'][:60]}"

    content = LAYOUT_TEMPLATE.format(
        EMOJI=emoji,
        TITLE=name,
        DESCRIPTION=description,
        POST_URL=post["url"],
        SUBREDDIT=post["subreddit"],
        COMMENT_COUNT=comment_count,
        SLUG=slug,
        JSON_TITLE=json.dumps(f"{name} — Maps"),
        CENTER_LAT=round(center_lat, 4),
        CENTER_LON=round(center_lon, 4),
        ZOOM=zoom,
    )

    layout_dir.mkdir(parents=True, exist_ok=True)
    base_real = os.path.realpath(layout_dir.parent)
    target_real = os.path.realpath(layout_dir / "list.html")
    if os.path.commonpath([base_real, target_real]) != base_real:
        raise RuntimeError("Invalid file path")
    (layout_dir / "list.html").write_text(content)


def write_content(content_dir: Path, name: str, description: str) -> None:
    content_dir.mkdir(parents=True, exist_ok=True)
    base_real = os.path.realpath(content_dir.parent)
    target_real = os.path.realpath(content_dir / "_index.md")
    if os.path.commonpath([base_real, target_real]) != base_real:
        raise RuntimeError("Invalid file path")
    (content_dir / "_index.md").write_text(
        f'---\ntitle: "{name}"\ndescription: "{description}"\n---\n'
    )


# ---------------------------------------------------------------------------
# Git + GitHub PR
# ---------------------------------------------------------------------------

def run(cmd: list[str], cwd: Path) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"Command {cmd} failed:\n{result.stderr}")
    return result.stdout.strip()


def create_pr(repo_dir: Path, slug: str, name: str, post: dict,
              feature_count: int, comment_count: int) -> str:
    branch = f"reddit-map/{slug}"
    run(["git", "checkout", "main"], repo_dir)
    run(["git", "pull", "origin", "main"], repo_dir)
    try:
        run(["git", "checkout", "-b", branch], repo_dir)
    except RuntimeError:
        run(["git", "checkout", branch], repo_dir)

    run(["git", "add",
         f"static/{slug}/",
         f"content/{slug}/",
         f"layouts/{slug}/"],
        repo_dir)

    run(["git", "commit", "-m",
         f"feat({slug}): Reddit-sourced location map — {name}\n\n"
         f"Source: {post['url']}\n"
         f"Subreddit: r/{post['subreddit']}\n"
         f"Comments analysed: {comment_count}\n"
         f"Locations geocoded: {feature_count}\n\n"
         "Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"],
        repo_dir)

    run(["git", "push", "-u", "origin", branch], repo_dir)

    pr_body = (
        f"## 🗺️ Reddit → Map: {name}\n\n"
        f"Auto-generated from Reddit comments using `scripts/reddit_to_map.py`.\n\n"
        f"| Field | Value |\n|-------|-------|\n"
        f"| Source | [{post['title'][:60]}]({post['url']}) |\n"
        f"| Subreddit | r/{post['subreddit']} |\n"
        f"| Comments analysed | {comment_count} |\n"
        f"| Locations geocoded | {feature_count} |\n"
        f"| Map path | `/{slug}/` |\n\n"
        "**Geocoding**: Photon (OSM) → Nominatim → LocationIQ cascade, "
        "`coord_accuracy: high` for all results.\n\n"
        "**NER**: spaCy `en_core_web_sm` — GPE, LOC, FAC entities.\n\n"
        "> ⚠️ Review extracted locations for false positives before merging.\n\n"
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    )

    url = subprocess.run(
        ["gh", "pr", "create",
         "--title", f"feat({slug}): Reddit-sourced map — {name}",
         "--body", pr_body,
         "--base", "main",
         "--head", branch],
        capture_output=True, text=True, cwd=repo_dir
    ).stdout.strip()
    return url


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Reddit submission → GeoJSON map PR")
    parser.add_argument("url", help="Reddit submission URL")
    parser.add_argument("--name", help="Map display name")
    parser.add_argument("--slug", help="URL slug / directory name")
    parser.add_argument("--bbox", nargs=4, type=float,
                        metavar=("LAT_S", "LON_W", "LAT_N", "LON_E"),
                        help="Restrict geocoding to bounding box")
    parser.add_argument("--min-score", type=int, default=1,
                        help="Minimum comment score (default: 1)")
    parser.add_argument("--context", default="",
                        help="Geographic context appended to geocode queries, e.g. 'Koh Samui Thailand'")
    parser.add_argument("--env", type=Path, help="Path to .env file")
    parser.add_argument("--repo", type=Path, default=Path(__file__).parent.parent,
                        help="Path to travel-guide repo root")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print GeoJSON; skip git / PR")
    parser.add_argument("--reply", action="store_true",
                        help="Post a reply comment on the submission linking to the map. "
                             "Requires REDDIT_USERNAME + REDDIT_PASSWORD in .env.")
    parser.add_argument("--site-url", default=BASE_URL,
                        help=f"Public base URL for map links (default: {BASE_URL})")
    args = parser.parse_args()

    # Load credentials
    load_env(args.env)
    if not os.environ.get("REDDIT_CLIENT_ID"):
        sys.exit("ERROR: REDDIT_CLIENT_ID not set — add to .env or environment")

    bbox = tuple(args.bbox) if args.bbox else None

    # ── 1. Fetch Reddit data ─────────────────────────────────────────────
    print("\n[1/5] Fetching Reddit submission…")
    reddit = init_reddit(write=args.reply)
    submission = reddit.submission(url=args.url)  # keep ref for --reply
    post, comments = fetch_comments(reddit, args.url, args.min_score, submission=submission)

    # ── 2. Extract location entities ─────────────────────────────────────
    print("\n[2/5] Extracting location entities with spaCy NER…")
    texts = [post["selftext"]] + [c["body"] for c in comments]
    locations = extract_locations(texts)
    print(f"  Unique location candidates: {len(locations)}")
    for loc in locations[:20]:
        print(f"    • {loc}")
    if len(locations) > 20:
        print(f"    … and {len(locations) - 20} more")

    # ── 3. Geocode ───────────────────────────────────────────────────────
    print("\n[3/5] Geocoding locations…")
    geocoded = []
    failed = []
    for loc in locations:
        result = geocode(loc, bbox, context=args.context)
        if result:
            geocoded.append(result)
            src = result["source"]
            print(f"  ✅ [{src:10s}] {loc} → [{result['lat']:.4f}, {result['lon']:.4f}] {result['display_name'][:50]}")
        else:
            failed.append(loc)
            print(f"  ❌ [not found ] {loc}")
        time.sleep(0.8)

    print(f"\n  Geocoded: {len(geocoded)} / {len(locations)}")
    if failed:
        print(f"  Not found: {', '.join(failed[:10])}" + (" …" if len(failed) > 10 else ""))

    # Deduplicate by rounded coordinates (identical pins from false-positive NER hits)
    seen_coords: set = set()
    unique_geocoded = []
    for g in geocoded:
        key = (round(g["lon"], 3), round(g["lat"], 3))
        if key not in seen_coords:
            seen_coords.add(key)
            unique_geocoded.append(g)
        else:
            print(f"  ⚠️  Duplicate coord removed: {g['query']} → {key}")
    geocoded = unique_geocoded
    print(f"  After dedup: {len(geocoded)} unique locations")

    if not geocoded:
        sys.exit("No locations geocoded — nothing to map.")

    # ── 4. Build GeoJSON + files ─────────────────────────────────────────
    print("\n[4/5] Building GeoJSON and Hugo files…")

    # Derive slug + name from post title if not provided
    slug = args.slug or slugify(post["title"])
    name = args.name or post["title"][:60]
    emoji = infer_emoji(post["title"], post["subreddit"])

    geojson = build_geojson(slug, geocoded, post)

    repo = args.repo.resolve()
    static_dir = repo / "static" / slug
    layout_dir = repo / "layouts" / slug
    content_dir = repo / "content" / slug

    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "locations.geojson").write_text(json.dumps(geojson, indent=2))
    print(f"  GeoJSON: {static_dir / 'locations.geojson'} ({len(geocoded)} features)")

    description = f"Locations mentioned in r/{post['subreddit']}: {post['title'][:60]}"
    write_layout(layout_dir, slug, name, emoji, post, len(comments), geocoded)
    write_content(content_dir, name, description)
    print(f"  Layout:  {layout_dir / 'list.html'}")
    print(f"  Content: {content_dir / '_index.md'}")

    if args.dry_run:
        print("\n[dry-run] GeoJSON preview:")
        print(json.dumps(geojson, indent=2)[:2000])
        return

    # ── 5. Validate + PR ─────────────────────────────────────────────────
    print("\n[5/5] Validating GeoJSON and creating PR…")
    validate = repo / "scripts" / "validate_geojson.py"
    if validate.exists():
        result = subprocess.run(
            ["python3", str(validate)], capture_output=True, text=True, cwd=repo
        )
        print(result.stdout.strip())
        if "ERROR" in result.stdout or result.returncode != 0:
            print("⚠️  Validation warnings above — fix before merging.")

    pr_url = create_pr(repo, slug, name, post, len(geocoded), len(comments))
    print(f"\n✅ PR created: {pr_url}")
    print(f"   Map path:  /{slug}/")
    print(f"   Locations: {len(geocoded)} geocoded from {len(comments)} comments")

    # ── 6. (optional) Reply on Reddit ────────────────────────────────────
    if args.reply:
        map_url = f"{args.site_url.rstrip('/')}/{slug}/"
        print(f"\n[6/6] Posting reply on Reddit…")
        print(f"      Map URL: {map_url}")
        permalink = post_reply(submission, map_url, name, len(geocoded))
        if permalink:
            print(f"  ✅ Comment posted: {permalink}")
        else:
            print("  ❌ Reply failed — check credentials or subreddit posting rules.")
    else:
        map_url = f"{args.site_url.rstrip('/')}/{slug}/"
        print(f"\n💡 To auto-reply on Reddit, re-run with --reply")
        print(f"   (set REDDIT_USERNAME + REDDIT_PASSWORD in .env first)")
        print(f"   Map URL will be: {map_url}")


if __name__ == "__main__":
    main()
