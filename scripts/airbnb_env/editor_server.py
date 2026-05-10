#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["flask"]
# ///
"""
Flask editor server for Airbnb neighbourhood maps.

Run from project root:
    uv run --script scripts/airbnb_env/editor_server.py

Opens at http://127.0.0.1:5001/
"""

import argparse
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, request

REPO_ROOT = Path(__file__).parent.parent.parent
MAPS_JSON = REPO_ROOT / "data" / "maps.json"
AIRBNB_DIR = REPO_ROOT / "static" / "airbnb"

app = Flask(__name__)


# ─── Data helpers ────────────────────────────────────────────────────────────

def get_listing_ids() -> list[str]:
    if not AIRBNB_DIR.exists():
        return []
    return sorted(
        d.name for d in AIRBNB_DIR.iterdir()
        if d.is_dir() and (d / "locations.geojson").exists()
    )


def load_maps_json() -> dict:
    try:
        return json.loads(MAPS_JSON.read_text("utf-8"))
    except Exception:
        return {"_meta": {}, "maps": []}


def save_maps_json(doc: dict) -> None:
    content = json.dumps(doc, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=MAPS_JSON.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        shutil.move(tmp, MAPS_JSON)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def find_map_entry(maps: list[dict], listing_id: str) -> dict | None:
    exact = f"airbnb/{listing_id}"
    for m in maps:
        if m.get("slug") == exact:
            return m
    for m in maps:
        if listing_id in m.get("slug", "") or listing_id in m.get("url", ""):
            return m
    return None


def save_geojson(listing_id: str, new_features: list) -> dict:
    path = AIRBNB_DIR / listing_id / "locations.geojson"
    doc = json.loads(path.read_text("utf-8"))
    doc["features"] = new_features
    poi_count = len(new_features)
    categories = sorted({
        f["properties"].get("category", "Other")
        for f in new_features
        if isinstance(f.get("properties"), dict)
    })
    content = json.dumps(doc, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        shutil.move(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return {"poi_count": poi_count, "categories": categories}


def update_maps_json_counts(listing_id: str, poi_count: int, categories: list) -> None:
    doc = load_maps_json()
    entry = find_map_entry(doc.get("maps", []), listing_id)
    if entry:
        entry["poi_count"] = poi_count
        entry["categories"] = categories
        if "_meta" in doc:
            doc["_meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
        save_maps_json(doc)


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        shutil.move(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_content_frontmatter(listing_id: str, fields: dict) -> None:
    """Update editable fields in content/airbnb/{listing_id}/_index.md front matter."""
    path = REPO_ROOT / "content" / "airbnb" / listing_id / "_index.md"
    if not path.exists():
        return
    text = path.read_text("utf-8")

    def replace_field(src: str, key: str, value) -> str:
        if isinstance(value, str):
            escaped = value.replace('"', '\\"')
            replacement = f'{key}: "{escaped}"'
        elif isinstance(value, list):
            items = ", ".join(f'"{str(v).replace(chr(34), chr(92)+chr(34))}"' for v in value)
            replacement = f"{key}: [{items}]"
        else:
            replacement = f"{key}: {value}"
        return re.sub(rf'^{re.escape(key)}:.*$', replacement, src, flags=re.MULTILINE)

    for k, v in fields.items():
        if k in {"title", "description", "emoji", "accent_color", "tags", "weight"}:
            text = replace_field(text, k, v)

    _atomic_write(path, text)


def update_layout_map_config(listing_id: str, title: str) -> None:
    """Update MAP_CONFIG.title in layouts/airbnb/{listing_id}.html."""
    path = REPO_ROOT / "layouts" / "airbnb" / f"{listing_id}.html"
    if not path.exists():
        return
    text = path.read_text("utf-8")
    escaped = title.replace("'", "\\'")
    text = re.sub(
        r"(title:\s*')[^']*(')",
        rf"\g<1>{escaped}\g<2>",
        text,
    )
    _atomic_write(path, text)


# ─── Index page ──────────────────────────────────────────────────────────────

HTML_INDEX = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Airbnb Map Editor</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=DM+Sans:opsz,wght@9..40,400;9..40,600;9..40,700&display=swap');
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0d0f14; --surf: #13161f; --surf2: #1a1e2a;
  --border: #1f2435; --border2: #2a3048;
  --text: #d8dce8; --muted: #6b7090;
  --accent: #5d7cef; --green: #22c55e;
}
body { background: var(--bg); color: var(--text); font-family: 'DM Sans', sans-serif; min-height: 100vh; }
.topbar {
  position: sticky; top: 0;
  background: var(--surf); border-bottom: 1px solid var(--border);
  padding: 0 20px; height: 48px; display: flex; align-items: center; gap: 12px;
  z-index: 10;
}
.topbar-title { font-size: 14px; font-weight: 700; }
.topbar-sub { font-size: 12px; color: var(--muted); font-family: 'IBM Plex Mono', monospace; }
.main { max-width: 900px; margin: 0 auto; padding: 32px 20px; }
h1 { font-size: 22px; margin-bottom: 6px; }
.sub { color: var(--muted); font-size: 14px; margin-bottom: 28px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
.card {
  background: var(--surf); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px; display: flex; flex-direction: column; gap: 10px;
  transition: border-color .15s;
}
.card:hover { border-color: var(--accent); }
.card-top { display: flex; align-items: flex-start; gap: 10px; }
.card-emoji { font-size: 26px; flex-shrink: 0; }
.card-info { flex: 1; min-width: 0; }
.card-id { font-size: 11px; font-family: 'IBM Plex Mono', monospace; color: var(--muted); }
.card-title { font-size: 14px; font-weight: 600; margin-top: 2px; line-height: 1.3; }
.card-desc { font-size: 12px; color: var(--muted); line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.badges { display: flex; gap: 6px; flex-wrap: wrap; }
.badge {
  padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600;
  background: var(--surf2); border: 1px solid var(--border2); color: var(--muted);
}
.badge.green { background: rgba(34,197,94,.1); border-color: rgba(34,197,94,.3); color: var(--green); }
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  padding: 7px 14px; border-radius: 7px; border: none; cursor: pointer;
  font-size: 12px; font-weight: 600; font-family: inherit; text-decoration: none;
  transition: all .15s; white-space: nowrap;
}
.btn-accent { background: var(--accent); color: #fff; width: 100%; }
.btn-accent:hover { background: #6d8ef5; }
.empty { text-align: center; padding: 60px 20px; color: var(--muted); font-size: 14px; }
</style>
</head>
<body>
<div class="topbar">
  <span class="topbar-title">Airbnb Map Editor</span>
  <span class="topbar-sub">__REPO_ROOT__</span>
</div>
<div class="main">
  <h1>Maps</h1>
  <p class="sub">__LISTING_COUNT__ listing(s) found in static/airbnb/</p>
  __CARDS__
</div>
</body>
</html>"""


def render_index() -> str:
    ids = get_listing_ids()
    doc = load_maps_json()
    maps = doc.get("maps", [])

    if not ids:
        cards = '<div class="empty">No listings found in <code>static/airbnb/</code>.</div>'
    else:
        card_html = []
        for lid in ids:
            entry = find_map_entry(maps, lid)
            title = entry.get("title", "Untitled") if entry else "No maps.json entry"
            desc = entry.get("description", "") if entry else ""
            emoji = entry.get("emoji", "🏠") if entry else "🏠"
            poi_count = entry.get("poi_count", "?") if entry else "?"
            has_entry = "yes" if entry else "no"
            entry_cls = "green" if entry else ""
            card_html.append(f"""
<div class="card">
  <div class="card-top">
    <div class="card-emoji">{emoji}</div>
    <div class="card-info">
      <div class="card-id">{lid}</div>
      <div class="card-title">{title}</div>
    </div>
  </div>
  <div class="card-desc">{desc}</div>
  <div class="badges">
    <span class="badge">{poi_count} spots</span>
    <span class="badge {entry_cls}">maps.json: {has_entry}</span>
  </div>
  <a class="btn btn-accent" href="/edit/{lid}">✏️ Open Editor</a>
</div>""")
        cards = f'<div class="grid">{"".join(card_html)}</div>'

    return (HTML_INDEX
            .replace("__REPO_ROOT__", str(REPO_ROOT))
            .replace("__LISTING_COUNT__", str(len(ids)))
            .replace("__CARDS__", cards))


# ─── Editor page ─────────────────────────────────────────────────────────────

HTML_EDITOR = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Edit __LISTING_ID__ — Airbnb Map Editor</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,600;0,9..40,700;1,9..40,400&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:      #0d0f14;
  --surf:    #13161f;
  --surf2:   #1a1e2a;
  --border:  #1f2435;
  --border2: #2a3048;
  --text:    #d8dce8;
  --muted:   #6b7090;
  --accent:  #5d7cef;
  --green:   #22c55e;
  --orange:  #f97316;
  --red:     #ef4444;
  --airbnb:  #FF5A5F;
  --topbar:  48px;
  --tabbar:  56px;
}

html, body { height: 100%; overflow: hidden; font-family: 'DM Sans', sans-serif; background: var(--bg); color: var(--text); }

.topbar {
  position: fixed; top: 0; left: 0; right: 0; height: var(--topbar);
  display: flex; align-items: center; gap: 10px; padding: 0 12px;
  background: var(--surf); border-bottom: 1px solid var(--border);
  z-index: 200;
}
.topbar-back { color: var(--accent); font-size: 13px; text-decoration: none; white-space: nowrap; }
.topbar-sep  { color: var(--border2); flex-shrink: 0; }
.topbar-title { font-size: 13px; color: var(--muted); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: 'IBM Plex Mono', monospace; }
.topbar-actions { display: flex; gap: 6px; flex-shrink: 0; }

.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  padding: 6px 12px; border-radius: 7px; border: none; cursor: pointer;
  font-size: 12px; font-weight: 600; font-family: inherit; transition: all .15s; white-space: nowrap;
}
.btn-ghost   { background: transparent; color: var(--muted); border: 1px solid var(--border2); }
.btn-ghost:hover { color: var(--text); border-color: var(--accent); }
.btn-accent  { background: var(--accent); color: #fff; }
.btn-accent:hover { background: #6d8ef5; }
.btn-green   { background: var(--green); color: #fff; }
.btn-green:hover { background: #16a34a; }
.btn-red     { background: transparent; color: var(--red); border: 1px solid #ef444433; }
.btn-red:hover { background: #ef44441a; }
.btn-sm      { padding: 4px 9px; font-size: 11px; }
.btn-icon    { padding: 6px; border-radius: 7px; font-size: 16px; background: transparent; border: 1px solid var(--border2); color: var(--muted); cursor: pointer; transition: all .15s; }
.btn-icon:hover { color: var(--text); border-color: var(--accent); }

.layout {
  position: fixed;
  top: var(--topbar); left: 0; right: 0; bottom: 0;
  display: flex;
}

.sidebar {
  width: 320px; flex-shrink: 0;
  display: flex; flex-direction: column;
  background: var(--surf); border-right: 1px solid var(--border);
  overflow: hidden;
}
.sidebar-head {
  padding: 12px 14px 8px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.sidebar-head h2 { font-size: 13px; font-weight: 700; margin-bottom: 2px; }
.poi-count { font-size: 11px; color: var(--muted); }
.poi-count strong { color: var(--accent); }

.searchbox {
  display: flex; align-items: center; gap: 8px;
  margin-top: 8px; padding: 0 10px;
  background: var(--bg); border: 1px solid var(--border2); border-radius: 7px;
}
.searchbox input {
  flex: 1; padding: 7px 0; background: none; border: none; outline: none;
  font-size: 13px; color: var(--text); font-family: inherit;
}
.searchbox input::placeholder { color: var(--muted); }

.cat-bar {
  display: flex; gap: 5px; flex-wrap: wrap;
  padding: 8px 12px; border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.cat-pill {
  padding: 3px 9px; border-radius: 20px; cursor: pointer;
  font-size: 11px; font-weight: 600;
  background: transparent; border: 1px solid var(--border2); color: var(--muted);
  transition: all .15s; white-space: nowrap;
}
.cat-pill.active { color: #fff; border-color: transparent; }

.poi-list { flex: 1; overflow-y: auto; padding: 6px; }
.poi-list::-webkit-scrollbar { width: 3px; }
.poi-list::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 2px; }

.poi-item {
  display: flex; align-items: center; gap: 9px;
  padding: 8px 9px; border-radius: 8px; cursor: pointer;
  transition: background .12s; border: 1px solid transparent; margin-bottom: 1px;
}
.poi-item:hover { background: var(--bg); }
.poi-item.active { background: var(--bg); border-color: var(--accent); }
.poi-emoji { font-size: 17px; flex-shrink: 0; width: 26px; text-align: center; }
.poi-info { flex: 1; min-width: 0; }
.poi-name { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.poi-cat  { font-size: 11px; color: var(--muted); margin-top: 1px; }
.poi-acts { display: flex; gap: 3px; opacity: 0; transition: opacity .12s; flex-shrink: 0; }
.poi-item:hover .poi-acts, .poi-item.active .poi-acts { opacity: 1; }

.add-btn {
  margin: 6px; padding: 9px; width: calc(100% - 12px);
  border: 1px dashed var(--border2); border-radius: 8px;
  background: none; cursor: pointer; color: var(--accent);
  font-size: 13px; font-weight: 600; font-family: inherit;
  transition: all .15s;
}
.add-btn:hover { background: rgba(93,124,239,.08); border-color: var(--accent); }

.map-wrap { flex: 1; position: relative; overflow: hidden; }
#map { width: 100%; height: 100%; }

.drawer {
  position: absolute; right: 0; top: 0; bottom: 0;
  width: 360px; max-width: 100%;
  background: var(--surf); border-left: 1px solid var(--border);
  display: flex; flex-direction: column;
  transform: translateX(100%);
  transition: transform .25s cubic-bezier(.4,0,.2,1);
  z-index: 100;
}
.drawer.open { transform: translateX(0); }

.drawer-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 14px; border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.drawer-head h3 { font-size: 14px; font-weight: 700; }
.drawer-body { flex: 1; overflow-y: auto; padding: 14px; }
.drawer-body::-webkit-scrollbar { width: 3px; }
.drawer-body::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 2px; }
.drawer-foot {
  padding: 10px 14px; border-top: 1px solid var(--border);
  display: flex; gap: 7px; justify-content: flex-end; flex-shrink: 0;
}

.map-hint {
  position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%);
  padding: 7px 16px; border-radius: 20px;
  background: rgba(13,15,20,.92); border: 1px solid var(--border2);
  font-size: 12px; color: var(--muted); pointer-events: none;
  z-index: 50; opacity: 0; transition: opacity .2s; white-space: nowrap;
}
.map-hint.show { opacity: 1; }

.fab {
  display: none;
  position: absolute; right: 16px; bottom: calc(var(--tabbar) + 16px);
  width: 52px; height: 52px; border-radius: 50%;
  background: var(--accent); color: #fff; font-size: 24px;
  border: none; cursor: pointer;
  box-shadow: 0 4px 20px rgba(93,124,239,.5);
  z-index: 150; transition: transform .15s, box-shadow .15s;
}
.fab:hover { transform: scale(1.05); box-shadow: 0 6px 24px rgba(93,124,239,.6); }

@media (max-width: 767px) {
  :root { --topbar: 44px; }
  .topbar { padding: 0 10px; gap: 8px; }
  .topbar-back { font-size: 12px; }
  .topbar-title { font-size: 11px; }
  .btn { font-size: 11px; padding: 5px 9px; }
  .sidebar { display: none; }
  .layout { bottom: var(--tabbar); }
  .map-wrap { flex: 1; }
  .fab { display: flex; align-items: center; justify-content: center; }
  .drawer {
    position: fixed;
    top: auto; right: 0; left: 0; bottom: 0;
    width: 100%; height: 85vh;
    border-left: none; border-top: 1px solid var(--border);
    border-radius: 16px 16px 0 0;
    transform: translateY(100%);
  }
  .drawer.open { transform: translateY(0); }
  .drawer-head::before {
    content: '';
    position: absolute; top: 8px; left: 50%; transform: translateX(-50%);
    width: 36px; height: 4px; border-radius: 2px; background: var(--border2);
  }
  .drawer-head { position: relative; padding-top: 20px; }
  .drawer-backdrop {
    display: none;
    position: fixed; inset: 0;
    background: rgba(0,0,0,.5);
    z-index: 99;
  }
  .drawer-backdrop.show { display: block; }
  .map-hint { bottom: calc(var(--tabbar) + 70px); }
}

.tabbar {
  display: none;
  position: fixed; bottom: 0; left: 0; right: 0;
  height: var(--tabbar);
  background: var(--surf); border-top: 1px solid var(--border);
  z-index: 200;
}
.tabbar-inner { display: flex; height: 100%; }
.tab {
  flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 2px; cursor: pointer; border: none; background: none; color: var(--muted);
  font-size: 10px; font-weight: 600; font-family: inherit; transition: color .15s;
  padding-bottom: env(safe-area-inset-bottom);
}
.tab .tab-icon { font-size: 18px; line-height: 1; }
.tab.active { color: var(--accent); }

@media (max-width: 767px) {
  .tabbar { display: flex; }
}

.spot-panel {
  display: none;
  position: fixed; bottom: var(--tabbar); left: 0; right: 0;
  height: 60vh;
  background: var(--surf); border-top: 1px solid var(--border);
  z-index: 180; flex-direction: column;
  transform: translateY(100%);
  transition: transform .25s cubic-bezier(.4,0,.2,1);
}
.spot-panel.open { transform: translateY(0); }

@media (max-width: 767px) { .spot-panel { display: flex; } }

.spot-panel-head { padding: 12px 14px 8px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.spot-panel-head h2 { font-size: 14px; font-weight: 700; }
.spot-panel-search {
  display: flex; align-items: center; gap: 8px;
  margin-top: 8px; padding: 0 10px;
  background: var(--bg); border: 1px solid var(--border2); border-radius: 7px;
}
.spot-panel-search input {
  flex: 1; padding: 7px 0; background: none; border: none; outline: none;
  font-size: 13px; color: var(--text); font-family: inherit;
}
.spot-panel-search input::placeholder { color: var(--muted); }
.spot-panel-cats {
  display: flex; gap: 5px; overflow-x: auto; padding: 8px 12px;
  border-bottom: 1px solid var(--border); flex-shrink: 0; -webkit-overflow-scrolling: touch;
}
.spot-panel-cats::-webkit-scrollbar { display: none; }
.spot-panel-list { flex: 1; overflow-y: auto; padding: 6px; -webkit-overflow-scrolling: touch; }

.field { margin-bottom: 13px; }
.field label {
  display: block; font-size: 10px; font-weight: 700;
  color: var(--muted); letter-spacing: .07em; text-transform: uppercase; margin-bottom: 4px;
}
.field input, .field select, .field textarea {
  width: 100%; padding: 8px 10px;
  background: var(--bg); border: 1px solid var(--border2); border-radius: 7px;
  color: var(--text); font-size: 13px; font-family: inherit; outline: none;
  transition: border-color .15s; -webkit-appearance: none;
}
.field input[type="color"] { padding: 4px 6px; height: 36px; cursor: pointer; }
.field input:focus, .field select:focus, .field textarea:focus { border-color: var(--accent); }
.field textarea { resize: vertical; min-height: 64px; line-height: 1.5; }
.field select { background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%236b7090' fill='none' stroke-width='1.5'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 10px center; padding-right: 28px; }
.field select option { background: var(--surf); }
.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.coord-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.coord-hint { font-size: 11px; color: var(--muted); margin-top: 4px; line-height: 1.4; }
.field-hint { font-size: 11px; color: var(--accent); font-weight: 600; }

.toast {
  position: fixed; bottom: calc(var(--tabbar) + 16px); left: 50%; transform: translateX(-50%);
  padding: 9px 18px; border-radius: 8px;
  background: var(--green); color: #fff; font-size: 13px; font-weight: 600;
  z-index: 2000; pointer-events: none; opacity: 0; transition: opacity .2s; white-space: nowrap;
}
@media (min-width: 768px) { .toast { bottom: 24px; } }
.toast.err { background: var(--red); }
.toast.show { opacity: 1; }

.leaflet-popup-content-wrapper {
  background: #1e2330; border: 1px solid var(--border2);
  color: var(--text); border-radius: 10px; box-shadow: 0 4px 20px rgba(0,0,0,.4);
}
.leaflet-popup-tip { background: #1e2330; }
.leaflet-popup-content { margin: 10px 12px; }
.pop-name { font-weight: 700; font-size: 14px; margin-bottom: 3px; }
.pop-cat  { font-size: 11px; color: var(--muted); margin-bottom: 5px; }
.pop-note { font-size: 12px; line-height: 1.45; }
.pop-hours { font-size: 11px; color: var(--accent); margin-top: 4px; }

.temp-pulse {
  width: 16px; height: 16px; border-radius: 50%;
  border: 2px solid var(--accent); background: rgba(93,124,239,.25);
  animation: pulse .9s ease-out infinite;
}
@keyframes pulse { to { transform: scale(2.2); opacity: 0; } }
</style>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin=""/>
</head>
<body>

<header class="topbar">
  <a class="topbar-back" href="/">← Maps</a>
  <span class="topbar-sep">›</span>
  <span class="topbar-title">airbnb/__LISTING_ID__/edit</span>
  <div class="topbar-actions">
    <button class="btn btn-ghost btn-sm" onclick="App.openMetaDrawer()" title="Edit map metadata">🗺️ Map Info</button>
    <button class="btn btn-ghost btn-sm" onclick="App.importJSON()" title="Import GeoJSON">📂</button>
    <button class="btn btn-accent btn-sm" onclick="App.saveToServer()">💾 Save all</button>
  </div>
</header>

<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-head">
      <h2>Spots <span class="poi-count">— <strong id="d-count">0</strong></span></h2>
      <div class="searchbox">
        <span style="color:var(--muted)">🔍</span>
        <input type="text" id="d-search" placeholder="Search…" oninput="App.filter()">
      </div>
    </div>
    <div class="cat-bar" id="d-cats"></div>
    <div class="poi-list" id="d-list"></div>
    <button class="add-btn" onclick="App.startAdd()">＋ Add a spot</button>
  </aside>

  <div class="map-wrap">
    <div id="map"></div>
    <div class="map-hint" id="map-hint">Tap the map to place the spot</div>
    <button class="fab" onclick="App.startAdd()" title="Add spot">＋</button>
  </div>
</div>

<div class="spot-panel" id="spot-panel">
  <div class="spot-panel-head">
    <h2>Spots — <strong id="m-count">0</strong></h2>
    <div class="spot-panel-search">
      <span style="color:var(--muted)">🔍</span>
      <input type="text" id="m-search" placeholder="Search…" oninput="App.filter()">
    </div>
  </div>
  <div class="spot-panel-cats" id="m-cats"></div>
  <div class="spot-panel-list" id="m-list"></div>
</div>

<nav class="tabbar">
  <div class="tabbar-inner">
    <button class="tab active" id="tab-map" onclick="App.showTab('map')">
      <span class="tab-icon">🗺️</span>Map
    </button>
    <button class="tab" id="tab-spots" onclick="App.showTab('spots')">
      <span class="tab-icon">📍</span>Spots
    </button>
    <button class="tab" id="tab-save" onclick="App.saveToServer()">
      <span class="tab-icon">💾</span>Save
    </button>
  </div>
</nav>

<!-- POI drawer -->
<div class="drawer-backdrop" id="backdrop" onclick="App.closeDrawer()"></div>
<div class="drawer" id="drawer">
  <div class="drawer-head">
    <h3 id="drawer-title">Add Spot</h3>
    <button class="btn-icon btn-sm" onclick="App.closeDrawer()">✕</button>
  </div>
  <div class="drawer-body">
    <input type="hidden" id="f-id">

    <div class="field">
      <label>Name *</label>
      <input type="text" id="f-name" placeholder="e.g. Big C Supermarket" autocomplete="off">
    </div>

    <div class="field-row">
      <div class="field">
        <label>Category *</label>
        <select id="f-cat" onchange="App.onCatChange()">
          <option value="Supermarket">🛒 Supermarket</option>
          <option value="Park">🌳 Park</option>
          <option value="Playground">🛝 Playground</option>
          <option value="Transit">🚌 Transit</option>
          <option value="Activity">🎠 Activity</option>
          <option value="Restaurant">🍽️ Restaurant</option>
          <option value="Café">☕ Café</option>
          <option value="Beach">🏖️ Beach</option>
          <option value="Other">📍 Other</option>
        </select>
      </div>
      <div class="field">
        <label>Icon</label>
        <input type="text" id="f-icon" placeholder="🛒" maxlength="4">
      </div>
    </div>

    <div class="field">
      <label>
        Coordinates *
        <span class="field-hint" id="coord-hint-label"> — tap the map to place</span>
      </label>
      <div class="coord-row">
        <input type="number" id="f-lat" placeholder="Lat" step="0.000001" inputmode="decimal">
        <input type="number" id="f-lon" placeholder="Lon" step="0.000001" inputmode="decimal">
      </div>
      <p class="coord-hint">e.g. 9.4703, 100.0491 — or tap the map</p>
    </div>

    <div class="field">
      <label>Address</label>
      <input type="text" id="f-addr" placeholder="Street or area" autocomplete="off">
    </div>

    <div class="field">
      <label>Why we picked it</label>
      <textarea id="f-notes" placeholder="What makes this spot worth visiting?"></textarea>
    </div>

    <div class="field-row">
      <div class="field">
        <label>Hours</label>
        <input type="text" id="f-hours" placeholder="09:00–20:00">
      </div>
      <div class="field">
        <label>Price</label>
        <input type="text" id="f-price" placeholder="Free / €5">
      </div>
    </div>

    <div class="field">
      <label>Website</label>
      <input type="url" id="f-url" placeholder="https://…" inputmode="url">
    </div>

    <div class="field">
      <label>Phone</label>
      <input type="tel" id="f-phone" placeholder="+66 77 …" inputmode="tel">
    </div>
  </div>
  <div class="drawer-foot">
    <button class="btn btn-red btn-sm" id="del-btn" onclick="App.deletePOI()" style="display:none;margin-right:auto">🗑</button>
    <button class="btn btn-ghost btn-sm" onclick="App.closeDrawer()">Cancel</button>
    <button class="btn btn-green" onclick="App.save()">💾 Save spot</button>
  </div>
</div>

<!-- Map metadata drawer -->
<div class="drawer" id="meta-drawer">
  <div class="drawer-head">
    <h3>Map Metadata</h3>
    <button class="btn-icon btn-sm" onclick="App.closeMetaDrawer()">✕</button>
  </div>
  <div class="drawer-body">
    <div class="field">
      <label>Title</label>
      <input type="text" id="m-title" placeholder="Map title" autocomplete="off">
    </div>
    <div class="field">
      <label>Description</label>
      <textarea id="m-desc" rows="3" placeholder="Short description of the map…"></textarea>
    </div>
    <div class="field-row">
      <div class="field">
        <label>Emoji</label>
        <input type="text" id="m-emoji" maxlength="4" placeholder="🏠">
      </div>
      <div class="field">
        <label>Accent color</label>
        <input type="color" id="m-accent" value="#1a6b3c">
      </div>
    </div>
    <div class="field">
      <label>Tags (one per line)</label>
      <textarea id="m-tags" rows="5" placeholder="🛒 Supermarket&#10;🌳 Park&#10;🛝 Playground"></textarea>
    </div>
    <div class="field">
      <label>Weight (sort order)</label>
      <input type="number" id="m-weight" min="0" max="999" placeholder="55">
    </div>
    <p id="meta-status" style="font-size:11px;color:var(--muted);margin-top:4px"></p>
  </div>
  <div class="drawer-foot">
    <button class="btn btn-ghost btn-sm" onclick="App.closeMetaDrawer()">Cancel</button>
    <button class="btn btn-green" onclick="App.saveMeta()">💾 Save metadata</button>
  </div>
</div>

<div class="toast" id="toast"></div>
<input type="file" id="file-import" accept=".geojson,.json" style="display:none" onchange="App.handleImport(event)">

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
<script>
const AIRBNB_ID  = "__LISTING_ID__";
const GEOJSON_URL = "/api/maps/__LISTING_ID__";

const CAT_ICONS  = { Supermarket:'🧺', Park:'🌳', Playground:'🛝', Transit:'🚌', Activity:'🎠', Restaurant:'🍽️', 'Café':'☕', Beach:'🏖️', Other:'📍' };
const CAT_COLORS = { Supermarket:'#22c55e', Park:'#fef9c3', Playground:'#fb923c', Transit:'#38bdf8', Activity:'#a78bfa', Restaurant:'#f97316', 'Café':'#92400e', Beach:'#0ea5e9', Other:'#6b7280' };

const App = (() => {
  let features = [], metadata = {};
  let selectedId = null, filterCat = 'All', filterText = '';
  let isAdding = false, tempMarker = null;
  let activeTab = 'map';
  let map, markersLayer;

  async function init() {
    features = []; metadata = { airbnb_id: AIRBNB_ID };
    try {
      const res = await fetch(GEOJSON_URL);
      if (res.ok) {
        const gj = await res.json();
        features = gj.features || [];
        if (gj._meta && gj._meta.center) {
          metadata.lat = gj._meta.center.lat;
          metadata.lon = gj._meta.center.lon;
        }
        if (gj.metadata) {
          metadata = Object.assign(metadata, gj.metadata);
        }
      } else {
        toast('Could not load map data', 'err');
      }
    } catch(e) {
      toast('Could not connect to server', 'err');
    }
    initMap();
    renderCats();
    renderList();
    refreshMarkers();
    loadMeta();
  }

  function initMap() {
    map = L.map('map', { zoomControl: true });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution:'© OpenStreetMap contributors', maxZoom:19 }).addTo(map);
    markersLayer = L.layerGroup().addTo(map);

    if (metadata.lat && metadata.lon) {
      const airbnbIcon = L.divIcon({
        html:'<div style="background:var(--airbnb);border:3px solid #fff;border-radius:50%;width:20px;height:20px;box-shadow:0 2px 8px rgba(0,0,0,.4)"></div>',
        className:'', iconSize:[20,20], iconAnchor:[10,10],
      });
      L.marker([metadata.lat, metadata.lon], {icon: airbnbIcon}).bindPopup('🏠 Your Airbnb').addTo(map);
    }

    fitBounds();
    map.on('click', onMapClick);
  }

  function fitBounds() {
    const pts = features.filter(f => f.geometry && f.geometry.type === 'Point')
      .map(f => [f.geometry.coordinates[1], f.geometry.coordinates[0]]);
    if (pts.length) map.fitBounds(L.latLngBounds(pts), {padding:[40,40]});
    else map.setView([9.47, 100.05], 14);
  }

  function makeIcon(cat, sel) {
    const c = CAT_COLORS[cat] || CAT_COLORS.Other;
    const e = CAT_ICONS[cat] || '📍';
    const outline = sel ? `outline:3px solid ${c};outline-offset:2px;` : '';
    return L.divIcon({
      className: '',
      html: `<div style="background:${c};width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;box-shadow:0 2px 6px rgba(0,0,0,0.3);border:2px solid white;${outline}">${e}</div>`,
      iconSize: [30,30], iconAnchor: [15,15], popupAnchor: [0,-18],
    });
  }

  function refreshMarkers() {
    markersLayer.clearLayers();
    features.forEach(f => {
      if (!f.geometry || f.geometry.type !== 'Point') return;
      const [lon, lat] = f.geometry.coordinates, p = f.properties;
      const m = L.marker([lat,lon], {icon: makeIcon(p.category, f.id === selectedId)});
      m.on('click', () => { selectPOI(f.id); if (isMobile()) showTab('map'); });
      m.bindTooltip(p.name, {permanent:false, direction:'top'});
      markersLayer.addLayer(m);
    });
  }

  function onMapClick(e) {
    if (!isAdding) return;
    const {lat, lng} = e.latlng;
    document.getElementById('f-lat').value = lat.toFixed(6);
    document.getElementById('f-lon').value = lng.toFixed(6);
    if (tempMarker) map.removeLayer(tempMarker);
    tempMarker = L.marker([lat,lng], {
      icon: L.divIcon({html:'<div class="temp-pulse"></div>', className:'', iconSize:[16,16], iconAnchor:[8,8]})
    }).addTo(map);
    document.getElementById('map-hint').classList.remove('show');
    isAdding = false;
    map.getContainer().style.cursor = '';
    document.getElementById('coord-hint-label').textContent = ' — placed ✓';
  }

  function renderCats() {
    const cats = ['All', ...Object.keys(CAT_ICONS)];
    const html = cats.map(c => {
      const style = c !== 'All' && c === filterCat ? `background:${CAT_COLORS[c]||'#6b7280'}` : '';
      return `<button class="cat-pill${c === filterCat ? ' active' : ''}" style="${style}" onclick="App.setCat('${c}')">${c === 'All' ? 'All' : CAT_ICONS[c]+' '+c}</button>`;
    }).join('');
    document.getElementById('d-cats').innerHTML = html;
    document.getElementById('m-cats').innerHTML = html;
  }

  function setCat(cat) { filterCat = cat; renderCats(); renderList(); }

  function renderList() {
    const q = filterText.toLowerCase();
    const vis = features.filter(f => {
      if (filterCat !== 'All' && f.properties.category !== filterCat) return false;
      if (q && !f.properties.name.toLowerCase().includes(q) && !(f.properties.notes||'').toLowerCase().includes(q)) return false;
      return true;
    });
    const html = vis.length ? vis.map(f => {
      const p = f.properties, c = CAT_COLORS[p.category] || CAT_COLORS.Other;
      return `<div class="poi-item${f.id === selectedId ? ' active' : ''}" onclick="App.selectPOI('${f.id}')">
        <span class="poi-emoji">${p.icon || CAT_ICONS[p.category] || '📍'}</span>
        <div class="poi-info">
          <div class="poi-name">${p.name}</div>
          <div class="poi-cat" style="color:${c}">${p.category}</div>
        </div>
        <div class="poi-acts">
          <button class="btn-icon btn-sm" onclick="event.stopPropagation();App.editPOI('${f.id}')" title="Edit">✏️</button>
          <button class="btn-icon btn-sm" onclick="event.stopPropagation();App.deleteById('${f.id}')" title="Delete" style="color:var(--red);border-color:#ef444433">🗑</button>
        </div>
      </div>`;
    }).join('') : `<div style="text-align:center;padding:28px 16px;color:var(--muted);font-size:13px">No spots found</div>`;

    document.getElementById('d-list').innerHTML = html;
    document.getElementById('m-list').innerHTML = html;
    const n = features.length;
    document.getElementById('d-count').textContent = n;
    document.getElementById('m-count').textContent = n;
  }

  function filter() {
    filterText = document.getElementById('d-search').value || document.getElementById('m-search').value;
    renderList();
  }

  function selectPOI(id) {
    selectedId = id;
    renderList(); refreshMarkers();
    const f = features.find(f => f.id === id);
    if (f && f.geometry && f.geometry.type === 'Point') {
      const [lon, lat] = f.geometry.coordinates;
      map.panTo([lat, lon]);
    }
    editPOI(id);
  }

  function editPOI(id) {
    const f = features.find(f => f.id === id);
    if (!f) return;
    const p = f.properties;
    document.getElementById('drawer-title').textContent = 'Edit Spot';
    document.getElementById('del-btn').style.display = '';
    document.getElementById('f-id').value = f.id;
    document.getElementById('f-name').value = p.name || '';
    document.getElementById('f-cat').value = p.category || 'Other';
    document.getElementById('f-icon').value = p.icon || '';
    document.getElementById('f-addr').value = p.address || '';
    document.getElementById('f-notes').value = p.notes || '';
    document.getElementById('f-hours').value = p.hours || '';
    document.getElementById('f-price').value = p.price || '';
    document.getElementById('f-url').value = p.url || '';
    document.getElementById('f-phone').value = p.phone || '';
    if (f.geometry && f.geometry.type === 'Point') {
      document.getElementById('f-lat').value = f.geometry.coordinates[1];
      document.getElementById('f-lon').value = f.geometry.coordinates[0];
    }
    document.getElementById('coord-hint-label').textContent = ' — tap to reposition';
    openDrawer();
  }

  function startAdd() {
    selectedId = null; isAdding = true;
    renderList(); refreshMarkers();
    document.getElementById('drawer-title').textContent = 'Add Spot';
    document.getElementById('del-btn').style.display = 'none';
    ['f-id','f-name','f-addr','f-notes','f-hours','f-price','f-url','f-phone','f-lat','f-lon'].forEach(id => document.getElementById(id).value = '');
    document.getElementById('f-cat').value = 'Other';
    document.getElementById('f-icon').value = '📍';
    document.getElementById('coord-hint-label').textContent = ' — tap the map to place';
    openDrawer();
    if (isMobile()) showTab('map');
    map.getContainer().style.cursor = 'crosshair';
    document.getElementById('map-hint').classList.add('show');
  }

  function onCatChange() {
    const c = document.getElementById('f-cat').value;
    document.getElementById('f-icon').value = CAT_ICONS[c] || '📍';
  }

  function save() {
    const id  = document.getElementById('f-id').value;
    const name = document.getElementById('f-name').value.trim();
    const cat  = document.getElementById('f-cat').value;
    const lat  = parseFloat(document.getElementById('f-lat').value);
    const lon  = parseFloat(document.getElementById('f-lon').value);

    if (!name) { toast('Name is required', 'err'); return; }
    if (isNaN(lat) || isNaN(lon)) { toast('Tap the map to place the spot', 'err'); return; }

    const props = {
      id: id || genId(),
      name, category: cat,
      icon:    document.getElementById('f-icon').value  || CAT_ICONS[cat] || '📍',
      address: document.getElementById('f-addr').value.trim(),
      notes:   document.getElementById('f-notes').value.trim(),
      hours:   document.getElementById('f-hours').value.trim(),
      price:   document.getElementById('f-price').value.trim(),
      url:     document.getElementById('f-url').value.trim(),
      phone:   document.getElementById('f-phone').value.trim(),
      coord_source: 'google_maps_pin', coord_accuracy: 'high',
    };
    Object.keys(props).forEach(k => { if (props[k] === '') delete props[k]; });

    if (id) {
      const i = features.findIndex(f => f.id === id);
      if (i >= 0) features[i] = {type:'Feature', id:props.id, geometry:{type:'Point', coordinates:[lon,lat]}, properties:props};
    } else {
      features.push({type:'Feature', id:props.id, geometry:{type:'Point', coordinates:[lon,lat]}, properties:props});
    }

    if (tempMarker) { map.removeLayer(tempMarker); tempMarker = null; }
    selectedId = props.id;
    renderList(); refreshMarkers(); closeDrawer();
    toast(id ? 'Spot updated — remember to Save all' : 'Spot added — remember to Save all');
    saveToServer();
  }

  function deletePOI() {
    const id = document.getElementById('f-id').value;
    if (!id) return;
    deleteById(id);
  }

  function deleteById(id) {
    if (!confirm('Delete this spot?')) return;
    features = features.filter(f => f.id !== id);
    selectedId = null;
    renderList(); refreshMarkers(); closeDrawer();
    saveToServer();
  }

  async function saveToServer() {
    try {
      const res = await fetch(`/api/maps/${AIRBNB_ID}/features`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({features}),
      });
      const data = await res.json();
      if (!res.ok) { toast('Save failed: ' + (data.error || res.status), 'err'); return; }
      toast(`Saved ${data.poi_count} spots to disk ✓`);
    } catch(e) {
      toast('Save error: ' + e.message, 'err');
    }
  }

  function openDrawer() {
    document.getElementById('meta-drawer').classList.remove('open');
    document.getElementById('drawer').classList.add('open');
    if (isMobile()) document.getElementById('backdrop').classList.add('show');
  }
  function closeDrawer() {
    document.getElementById('drawer').classList.remove('open');
    document.getElementById('backdrop').classList.remove('show');
    isAdding = false;
    map.getContainer().style.cursor = '';
    document.getElementById('map-hint').classList.remove('show');
    if (tempMarker) { map.removeLayer(tempMarker); tempMarker = null; }
  }

  function showTab(tab) {
    activeTab = tab;
    document.getElementById('tab-map').classList.toggle('active', tab === 'map');
    document.getElementById('tab-spots').classList.toggle('active', tab === 'spots');
    document.getElementById('tab-save').classList.toggle('active', tab === 'save');
    document.getElementById('spot-panel').classList.toggle('open', tab === 'spots');
    if (tab === 'map') map.invalidateSize();
  }

  function importJSON() { document.getElementById('file-import').click(); }
  function handleImport(e) {
    const f = e.target.files[0]; if (!f) return;
    const r = new FileReader();
    r.onload = ev => {
      try {
        const d = JSON.parse(ev.target.result);
        if (d.type !== 'FeatureCollection') throw new Error('Not a FeatureCollection');
        features = d.features || [];
        if (d.metadata) metadata = Object.assign(metadata, d.metadata);
        renderList(); refreshMarkers(); fitBounds();
        toast(`Imported ${features.length} spots — click Save all to persist`);
      } catch { toast('Invalid GeoJSON', 'err'); }
    };
    r.readAsText(f);
    e.target.value = '';
  }

  async function loadMeta() {
    try {
      const res = await fetch(`/api/maps/${AIRBNB_ID}/meta`);
      if (!res.ok) {
        document.getElementById('meta-status').textContent = 'No maps.json entry found for this listing.';
        return;
      }
      const m = await res.json();
      document.getElementById('m-title').value = m.title || '';
      document.getElementById('m-desc').value = m.description || '';
      document.getElementById('m-emoji').value = m.emoji || '🏠';
      document.getElementById('m-accent').value = m.accent_color || '#1a6b3c';
      document.getElementById('m-tags').value = (m.tags || []).join('\\n');
      document.getElementById('m-weight').value = m.weight != null ? m.weight : 55;
      document.getElementById('meta-status').textContent = '';
    } catch {}
  }

  async function saveMeta() {
    const tags = document.getElementById('m-tags').value
      .split('\\n').map(t => t.trim()).filter(Boolean);
    const payload = {
      title:       document.getElementById('m-title').value.trim(),
      description: document.getElementById('m-desc').value.trim(),
      emoji:       document.getElementById('m-emoji').value.trim(),
      accent_color: document.getElementById('m-accent').value,
      tags,
      weight: parseInt(document.getElementById('m-weight').value, 10) || 55,
    };
    try {
      const res = await fetch(`/api/maps/${AIRBNB_ID}/meta`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) { toast('Meta save failed: ' + (data.error || res.status), 'err'); return; }
      toast('Metadata saved ✓');
      closeMetaDrawer();
    } catch(e) {
      toast('Meta save error: ' + e.message, 'err');
    }
  }

  function openMetaDrawer() {
    document.getElementById('drawer').classList.remove('open');
    document.getElementById('meta-drawer').classList.add('open');
    if (isMobile()) document.getElementById('backdrop').classList.add('show');
  }
  function closeMetaDrawer() {
    document.getElementById('meta-drawer').classList.remove('open');
    document.getElementById('backdrop').classList.remove('show');
  }

  function genId() {
    const num = String(features.length + 1).padStart(3, '0');
    return `airbnb/${AIRBNB_ID}-${num}`;
  }
  function isMobile() { return window.innerWidth < 768; }
  function toast(msg, type='') {
    const t = document.getElementById('toast');
    t.textContent = msg; t.className = `toast${type ? ' '+type : ''} show`;
    setTimeout(() => t.classList.remove('show'), 2800);
  }

  return {
    init, selectPOI, editPOI, startAdd, save, deletePOI, deleteById,
    closeDrawer, saveToServer,
    importJSON, handleImport, filter, setCat, onCatChange, showTab,
    openMetaDrawer, closeMetaDrawer, saveMeta,
  };
})();

document.addEventListener('DOMContentLoaded', () => App.init());

window.addEventListener('resize', () => {
  if (typeof map !== 'undefined' && map && map.invalidateSize) map.invalidateSize();
});
</script>
</body>
</html>"""


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return Response(render_index(), mimetype="text/html")


@app.get("/edit/<listing_id>")
def editor(listing_id: str):
    path = AIRBNB_DIR / listing_id / "locations.geojson"
    if not path.exists():
        return f"<h1>404</h1><p>No GeoJSON found for listing <code>{listing_id}</code>.</p>", 404
    html = HTML_EDITOR.replace("__LISTING_ID__", listing_id)
    return Response(html, mimetype="text/html")


@app.get("/api/maps/<listing_id>")
def api_get_geojson(listing_id: str):
    path = AIRBNB_DIR / listing_id / "locations.geojson"
    if not path.exists():
        return {"error": f"no GeoJSON for listing {listing_id}"}, 404
    return Response(path.read_text("utf-8"), mimetype="application/json")


@app.put("/api/maps/<listing_id>/features")
def api_save_features(listing_id: str):
    path = AIRBNB_DIR / listing_id / "locations.geojson"
    if not path.exists():
        return {"error": f"no GeoJSON for listing {listing_id}"}, 404

    try:
        body = request.get_json(force=True)
    except Exception:
        return {"error": "invalid JSON"}, 400

    if not isinstance(body, dict) or "features" not in body:
        return {"error": "body must have 'features' array"}, 400

    new_features = body["features"]
    if not isinstance(new_features, list):
        return {"error": "'features' must be an array"}, 400

    try:
        result = save_geojson(listing_id, new_features)
        update_maps_json_counts(listing_id, result["poi_count"], result["categories"])
    except Exception as e:
        return {"error": str(e)}, 500

    return {"ok": True, **result}


@app.get("/api/maps/<listing_id>/meta")
def api_get_meta(listing_id: str):
    doc = load_maps_json()
    entry = find_map_entry(doc.get("maps", []), listing_id)
    if not entry:
        return {"error": f"no maps.json entry for listing {listing_id}"}, 404
    fields = ["title", "description", "emoji", "accent_color", "tags", "weight"]
    return {k: entry.get(k) for k in fields}


@app.put("/api/maps/<listing_id>/meta")
def api_save_meta(listing_id: str):
    try:
        body = request.get_json(force=True)
    except Exception:
        return {"error": "invalid JSON"}, 400

    doc = load_maps_json()
    entry = find_map_entry(doc.get("maps", []), listing_id)
    if not entry:
        return {"error": f"no maps.json entry for listing {listing_id} — add it to data/maps.json first"}, 404

    allowed = {"title", "description", "emoji", "accent_color", "tags", "weight"}
    for k, v in body.items():
        if k in allowed:
            entry[k] = v

    if "_meta" in doc:
        doc["_meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        save_maps_json(doc)
        update_content_frontmatter(listing_id, body)
        if "title" in body:
            update_layout_map_config(listing_id, body["title"])
    except Exception as e:
        return {"error": str(e)}, 500

    return {"ok": True}


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Airbnb map editor server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    ids = get_listing_ids()
    print(f"Editor server → http://{args.host}:{args.port}/")
    print(f"Project root  : {REPO_ROOT}")
    print(f"Listings found: {ids or '(none)'}")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
