#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["flask>=3.0"]
# ///
"""
Media validation UI for toulouse-distorama artists.

Usage:
    uv run scripts/toulouse-distorama/review.py

Opens at http://localhost:5020

Keyboard shortcuts:
  1  approve YouTube     2  reject YouTube
  4  approve Bandcamp    5  reject Bandcamp
  ← / → / Space         navigate
"""

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template_string, request, url_for

SCRIPT_DIR = Path(__file__).parent
MEDIACACHE_PATH = SCRIPT_DIR / ".mediacache.json"

app = Flask(__name__)


# ── Cache I/O ──────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    if MEDIACACHE_PATH.exists():
        return json.loads(MEDIACACHE_PATH.read_text())
    return {}


def save_cache(cache: dict) -> None:
    MEDIACACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


# ── Filtering ─────────────────────────────────────────────────────────────────

FILTERS = [
    ("pending", "Pending"),
    ("has_media", "Has media"),
    ("all", "All"),
]


def get_filtered_artists(cache: dict, filter_name: str) -> list[str]:
    if filter_name == "all":
        return sorted(cache.keys())
    if filter_name == "has_media":
        return sorted(
            k for k, v in cache.items()
            if v.get("youtube_video_id") or v.get("bandcamp_url")
        )
    # "pending" (default): has at least one unvalidated media result
    def _is_pending(v: dict) -> bool:
        yt_pending = bool(v.get("youtube_video_id")) and not v.get("youtube_validated")
        bc_pending = bool(v.get("bandcamp_url")) and not v.get("bandcamp_validated")
        return yt_pending or bc_pending
    return sorted(k for k, v in cache.items() if _is_pending(v))


# ── Media helpers ─────────────────────────────────────────────────────────────

def _parse_youtube_id(s: str) -> str:
    """Extract video ID from a YouTube URL, or return as-is if already an ID."""
    s = s.strip()
    if re.match(r"^[A-Za-z0-9_-]{11}$", s):
        return s
    parsed = urllib.parse.urlparse(s)
    if "youtu.be" in parsed.netloc:
        return parsed.path.lstrip("/")[:11]
    if "youtube.com" in parsed.netloc:
        qs = urllib.parse.parse_qs(parsed.query)
        if "v" in qs:
            return qs["v"][0]
    return s


_BC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _extract_bandcamp_embed(url: str) -> tuple[str, str]:
    """Given a Bandcamp artist or album URL, return (url, embed_url)."""
    def _get(u: str) -> str:
        try:
            req = urllib.request.Request(u, headers=_BC_HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception:
            return ""

    html = _get(url)
    if not html:
        return url, ""

    if "/album/" not in url:
        m = re.search(r'href="((?:https://[^"]+)?/album/[^"]+)"', html)
        if not m:
            return url, ""
        album_path = m.group(1)
        if album_path.startswith("/"):
            base = re.match(r"(https://[^/]+)", url)
            album_url = (base.group(1) + album_path) if base else ""
        else:
            album_url = album_path
        html = _get(album_url) if album_url else ""
        if not html:
            return url, ""

    m_id = re.search(r"bandcamp\.com/EmbeddedPlayer/(?:v=2/)?album=(\d+)/", html)
    if not m_id:
        m_id = re.search(r'data-tralbumid="(\d+)"', html)
    if not m_id:
        return url, ""

    embed_url = (
        f"https://bandcamp.com/EmbeddedPlayer/album={m_id.group(1)}"
        f"/size=small/bgcol=111111/linkcol=ffffff/transparent=true/"
    )
    return url, embed_url


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    f = request.args.get("filter", "pending")
    return redirect(url_for("review", filter=f, idx=0))


@app.route("/review")
def review():
    f = request.args.get("filter", "pending")
    idx = int(request.args.get("idx", 0))
    cache = load_cache()
    artists = get_filtered_artists(cache, f)

    if not artists:
        return render_template_string(
            TEMPLATE,
            artist=None, entry={}, idx=0, total=0,
            prev_idx=None, next_idx=None,
            filter=f, filters=FILTERS,
        )

    idx = max(0, min(idx, len(artists) - 1))
    artist = artists[idx]
    entry = cache.get(artist, {})

    return render_template_string(
        TEMPLATE,
        artist=artist,
        entry=entry,
        idx=idx,
        total=len(artists),
        prev_idx=idx - 1 if idx > 0 else None,
        next_idx=idx + 1 if idx < len(artists) - 1 else None,
        filter=f,
        filters=FILTERS,
    )


@app.route("/api/action", methods=["POST"])
def action():
    data = request.get_json()
    artist = data.get("artist", "")
    act = data.get("action", "")

    cache = load_cache()
    entry = cache.setdefault(artist, {})

    if act == "approve_yt":
        entry["youtube_validated"] = True

    elif act == "reject_yt":
        vid = entry.get("youtube_video_id", "")
        if vid:
            rejected = entry.setdefault("youtube_rejected_ids", [])
            if vid not in rejected:
                rejected.append(vid)
        entry["youtube_video_id"] = ""
        entry["youtube_validated"] = True

    elif act == "approve_bc":
        entry["bandcamp_validated"] = True

    elif act == "reject_bc":
        bc_url = entry.get("bandcamp_url", "")
        if bc_url:
            rejected = entry.setdefault("bandcamp_rejected_urls", [])
            if bc_url not in rejected:
                rejected.append(bc_url)
        entry["bandcamp_url"] = ""
        entry["bandcamp_embed_url"] = ""
        entry["bandcamp_validated"] = True

    elif act == "modify_yt":
        raw = data.get("value", "").strip()
        new_id = _parse_youtube_id(raw)
        old_id = entry.get("youtube_video_id", "")
        if old_id and old_id != new_id:
            rejected = entry.setdefault("youtube_rejected_ids", [])
            if old_id not in rejected:
                rejected.append(old_id)
        entry["youtube_video_id"] = new_id
        entry["youtube_validated"] = True

    elif act == "modify_bc":
        new_url = data.get("value", "").strip()
        old_url = entry.get("bandcamp_url", "")
        if old_url and old_url != new_url:
            rejected = entry.setdefault("bandcamp_rejected_urls", [])
            if old_url not in rejected:
                rejected.append(old_url)
        _, embed_url = _extract_bandcamp_embed(new_url) if new_url else ("", "")
        entry["bandcamp_url"] = new_url
        entry["bandcamp_embed_url"] = embed_url
        entry["bandcamp_validated"] = True

    else:
        return jsonify({"ok": False, "error": "unknown action"}), 400

    cache[artist] = entry
    save_cache(cache)
    return jsonify({"ok": True, "entry": entry})


# ── Template ──────────────────────────────────────────────────────────────────

TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Distorama — Review</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #0d0d0d; color: #e0e0e0;
  font-family: 'Courier New', monospace; font-size: 13px;
  height: 100vh; display: flex; flex-direction: column; overflow: hidden;
}

/* ── Header ── */
#header {
  padding: 8px 14px; border-bottom: 1px solid #222;
  display: flex; align-items: center; gap: 10px; flex-shrink: 0;
}
#progress { color: #555; white-space: nowrap; }
#artist-name {
  flex: 1; font-weight: bold; font-size: 14px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0;
}
.filter-btn {
  padding: 3px 9px; border: 1px solid #2a2a2a; color: #555;
  text-decoration: none; font-size: 11px; white-space: nowrap;
}
.filter-btn.active  { border-color: #bbb; color: #bbb; }
.filter-btn:hover   { border-color: #888; color: #888; }
.nav-btn {
  padding: 3px 10px; border: 1px solid #2a2a2a; color: #777;
  text-decoration: none; font-size: 13px;
}
.nav-btn:hover     { border-color: #ccc; color: #ccc; }
.nav-btn.disabled  { opacity: 0.25; pointer-events: none; }

/* ── Panels ── */
#panels { display: flex; flex: 1; overflow: hidden; }
.panel { flex: 1; display: flex; flex-direction: column; border-right: 1px solid #1a1a1a; }
.panel:last-child { border-right: none; }

.panel-head {
  padding: 7px 12px; background: #141414;
  display: flex; align-items: center; gap: 8px;
  border-bottom: 1px solid #1a1a1a; flex-shrink: 0;
}
.panel-label { font-size: 11px; font-weight: bold; color: #888; letter-spacing: 1px; text-transform: uppercase; }
.badge { padding: 2px 7px; font-size: 10px; }
.badge-ok { background: #0b240b; color: #4cd64c; border: 1px solid #174a17; }
.badge-no { background: #240b0b; color: #d64c4c; border: 1px solid #4a1717; }

.embed-wrap { flex: 1; padding: 8px; overflow: hidden; }
.embed-wrap iframe { width: 100%; height: 100%; border: none; display: block; }
.no-media {
  height: 100%; display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  color: #2e2e2e; gap: 6px; font-size: 13px; text-align: center; padding: 12px;
}
.no-media a { color: #555; word-break: break-all; }

.panel-actions {
  padding: 7px 12px; border-top: 1px solid #1a1a1a;
  display: flex; align-items: center; gap: 7px; flex-shrink: 0;
}
.btn {
  padding: 5px 13px; border: 1px solid; background: transparent;
  cursor: pointer; font-family: inherit; font-size: 12px;
}
.btn-ok       { border-color: #1e4a1e; color: #4cd64c; }
.btn-ok:hover { background: #0b240b; }
.btn-ok.lit   { background: #0b240b; border-color: #4cd64c; }
.btn-no       { border-color: #4a1e1e; color: #d64c4c; }
.btn-no:hover { background: #240b0b; }
.btn-no.lit   { background: #240b0b; border-color: #d64c4c; }
.btn-edit { border-color: #2a2a2a; color: #555; font-size: 11px; margin-left: auto; }
.btn-edit:hover { border-color: #888; color: #aaa; }
.key { font-size: 10px; color: #444; }

.edit-row {
  padding: 6px 12px; border-top: 1px solid #1a1a1a;
  display: none; gap: 6px; flex-shrink: 0;
}
.edit-row.open { display: flex; }
.edit-row input {
  flex: 1; background: #191919; border: 1px solid #333; color: #ccc;
  padding: 4px 8px; font-family: inherit; font-size: 12px; outline: none;
}
.edit-row input:focus { border-color: #666; }
.edit-row button {
  padding: 4px 10px; border: 1px solid #555; background: transparent;
  color: #ccc; cursor: pointer; font-family: inherit; font-size: 12px;
}
.edit-row button:hover { background: #222; }

/* ── Empty state ── */
#empty {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  color: #444; gap: 12px; font-size: 14px;
}
#empty a { color: #777; text-decoration: none; }
#empty a:hover { color: #ccc; }

/* ── Shortcut bar ── */
#shortcuts {
  padding: 5px 14px; border-top: 1px solid #1a1a1a;
  color: #2e2e2e; font-size: 11px; flex-shrink: 0;
  display: flex; gap: 18px;
}
</style>
</head>
<body>

{% if artist is none %}
<div id="empty">
  <div>No artists to review.</div>
  <div>
    {% for fid, flabel in filters %}
      <a href="/review?filter={{ fid }}">{{ flabel }}</a>{% if not loop.last %}&nbsp;·&nbsp;{% endif %}
    {% endfor %}
  </div>
</div>

{% else %}

<div id="header">
  <span id="progress">{{ idx + 1 }}&nbsp;/&nbsp;{{ total }}</span>
  <span id="artist-name" title="{{ artist }}">{{ artist }}</span>
  {% for fid, flabel in filters %}
    <a class="filter-btn{% if filter == fid %} active{% endif %}"
       href="/review?filter={{ fid }}&idx={{ idx }}">{{ flabel }}</a>
  {% endfor %}
  <a id="nav-prev"
     class="nav-btn{% if prev_idx is none %} disabled{% endif %}"
     href="/review?filter={{ filter }}&idx={{ prev_idx if prev_idx is not none else 0 }}">←</a>
  <a id="nav-next"
     class="nav-btn{% if next_idx is none %} disabled{% endif %}"
     href="/review?filter={{ filter }}&idx={{ next_idx if next_idx is not none else idx }}">→</a>
</div>

<div id="panels">

  {# ── YouTube panel ── #}
  <div class="panel" id="panel-yt">
    <div class="panel-head">
      <span class="panel-label">YouTube</span>
      {% if entry.get('youtube_validated') %}
        {% if entry.get('youtube_video_id') %}
          <span class="badge badge-ok" id="badge-yt">approved</span>
        {% else %}
          <span class="badge badge-no" id="badge-yt">rejected</span>
        {% endif %}
      {% endif %}
    </div>
    <div class="embed-wrap">
      {% if entry.get('youtube_video_id') %}
        <iframe id="yt-iframe"
          src="https://www.youtube-nocookie.com/embed/{{ entry['youtube_video_id'] }}"
          allow="autoplay; encrypted-media" allowfullscreen></iframe>
      {% else %}
        <div class="no-media">No YouTube result</div>
      {% endif %}
    </div>
    <div class="panel-actions">
      {% if entry.get('youtube_video_id') %}
        <button id="btn-yt-ok"
                class="btn btn-ok{% if entry.get('youtube_validated') and entry.get('youtube_video_id') %} lit{% endif %}"
                onclick="doAction('approve_yt')">
          Approve <span class="key">[1]</span>
        </button>
        <button id="btn-yt-no"
                class="btn btn-no{% if entry.get('youtube_validated') and not entry.get('youtube_video_id') %} lit{% endif %}"
                onclick="doAction('reject_yt')">
          Reject <span class="key">[2]</span>
        </button>
      {% endif %}
      <button class="btn btn-edit" onclick="toggleEdit('yt')">Edit</button>
    </div>
    <div class="edit-row" id="edit-yt">
      <input id="edit-yt-input" type="text"
             placeholder="YouTube video ID or URL"
             value="{{ entry.get('youtube_video_id', '') }}">
      <button onclick="doModify('yt')">Save</button>
    </div>
  </div>

  {# ── Bandcamp panel ── #}
  <div class="panel" id="panel-bc">
    <div class="panel-head">
      <span class="panel-label">Bandcamp</span>
      {% if entry.get('bandcamp_validated') %}
        {% if entry.get('bandcamp_url') %}
          <span class="badge badge-ok" id="badge-bc">approved</span>
        {% else %}
          <span class="badge badge-no" id="badge-bc">rejected</span>
        {% endif %}
      {% endif %}
    </div>
    <div class="embed-wrap">
      {% if entry.get('bandcamp_embed_url') %}
        <iframe id="bc-iframe"
          src="{{ entry['bandcamp_embed_url'] }}"
          seamless></iframe>
      {% elif entry.get('bandcamp_url') %}
        <div class="no-media">
          URL found — no embed available<br>
          <a href="{{ entry['bandcamp_url'] }}" target="_blank">{{ entry['bandcamp_url'] }}</a>
        </div>
      {% else %}
        <div class="no-media">No Bandcamp result</div>
      {% endif %}
    </div>
    <div class="panel-actions">
      {% if entry.get('bandcamp_url') %}
        <button id="btn-bc-ok"
                class="btn btn-ok{% if entry.get('bandcamp_validated') and entry.get('bandcamp_url') %} lit{% endif %}"
                onclick="doAction('approve_bc')">
          Approve <span class="key">[4]</span>
        </button>
        <button id="btn-bc-no"
                class="btn btn-no{% if entry.get('bandcamp_validated') and not entry.get('bandcamp_url') %} lit{% endif %}"
                onclick="doAction('reject_bc')">
          Reject <span class="key">[5]</span>
        </button>
      {% endif %}
      <button class="btn btn-edit" onclick="toggleEdit('bc')">Edit</button>
    </div>
    <div class="edit-row" id="edit-bc">
      <input id="edit-bc-input" type="text"
             placeholder="Bandcamp artist or album URL"
             value="{{ entry.get('bandcamp_url', '') }}">
      <button onclick="doModify('bc')">Save</button>
    </div>
  </div>

</div>

<div id="shortcuts">
  <span>1&nbsp;approve YT</span>
  <span>2&nbsp;reject YT</span>
  <span>4&nbsp;approve BC</span>
  <span>5&nbsp;reject BC</span>
  <span>← → Space&nbsp;navigate</span>
</div>

<script>
const ARTIST   = {{ artist | tojson }};
const FILTER   = {{ filter | tojson }};
const IDX      = {{ idx }};
const NEXT_IDX = {{ next_idx if next_idx is not none else 'null' }};
const PREV_IDX = {{ prev_idx if prev_idx is not none else 'null' }};

let ytValidated = {{ 'true' if entry.get('youtube_validated') else 'false' }};
let bcValidated = {{ 'true' if entry.get('bandcamp_validated') else 'false' }};
const ytHasMedia = {{ 'true' if entry.get('youtube_video_id') else 'false' }};
const bcHasMedia = {{ 'true' if entry.get('bandcamp_url') else 'false' }};

function navigate(dir) {
  const target = dir === 'next' ? NEXT_IDX : PREV_IDX;
  if (target !== null) location.href = `/review?filter=${FILTER}&idx=${target}`;
}

function advanceIfDone() {
  const ytDone = !ytHasMedia || ytValidated;
  const bcDone = !bcHasMedia || bcValidated;
  if (ytDone && bcDone && NEXT_IDX !== null) {
    setTimeout(() => navigate('next'), 350);
  }
}

function setBadge(type, status) {
  const head = document.querySelector(`#panel-${type} .panel-head`);
  let badge = document.getElementById(`badge-${type}`);
  if (!badge) {
    badge = document.createElement('span');
    badge.id = `badge-${type}`;
    head.appendChild(badge);
  }
  badge.className = 'badge ' + (status === 'approved' ? 'badge-ok' : 'badge-no');
  badge.textContent = status;
}

async function doAction(action) {
  const res = await fetch('/api/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ artist: ARTIST, action }),
  });
  const data = await res.json();
  if (!data.ok) return;

  if (action === 'approve_yt' || action === 'reject_yt') {
    ytValidated = true;
    const approved = action === 'approve_yt';
    document.getElementById('btn-yt-ok')?.classList.toggle('lit', approved);
    document.getElementById('btn-yt-no')?.classList.toggle('lit', !approved);
    setBadge('yt', approved ? 'approved' : 'rejected');
  } else {
    bcValidated = true;
    const approved = action === 'approve_bc';
    document.getElementById('btn-bc-ok')?.classList.toggle('lit', approved);
    document.getElementById('btn-bc-no')?.classList.toggle('lit', !approved);
    setBadge('bc', approved ? 'approved' : 'rejected');
  }
  advanceIfDone();
}

async function doModify(type) {
  const input = document.getElementById(`edit-${type}-input`);
  const value = input.value.trim();
  if (!value) return;
  const res = await fetch('/api/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ artist: ARTIST, action: `modify_${type}`, value }),
  });
  const data = await res.json();
  if (!data.ok) return;

  if (type === 'yt') {
    ytValidated = true;
    const newId = data.entry?.youtube_video_id || '';
    if (newId) {
      let iframe = document.getElementById('yt-iframe');
      if (!iframe) {
        const wrap = document.querySelector('#panel-yt .embed-wrap');
        wrap.innerHTML = '';
        iframe = document.createElement('iframe');
        iframe.id = 'yt-iframe';
        iframe.allow = 'autoplay; encrypted-media';
        iframe.allowFullscreen = true;
        wrap.appendChild(iframe);
      }
      iframe.src = `https://www.youtube-nocookie.com/embed/${newId}`;
    }
    setBadge('yt', 'approved');
  } else {
    bcValidated = true;
    const embedUrl = data.entry?.bandcamp_embed_url || '';
    if (embedUrl) {
      let iframe = document.getElementById('bc-iframe');
      if (!iframe) {
        const wrap = document.querySelector('#panel-bc .embed-wrap');
        wrap.innerHTML = '';
        iframe = document.createElement('iframe');
        iframe.id = 'bc-iframe';
        iframe.setAttribute('seamless', '');
        wrap.appendChild(iframe);
      }
      iframe.src = embedUrl;
    }
    setBadge('bc', 'approved');
  }
  toggleEdit(type);
  advanceIfDone();
}

function toggleEdit(type) {
  const row = document.getElementById(`edit-${type}`);
  row.classList.toggle('open');
  if (row.classList.contains('open')) {
    document.getElementById(`edit-${type}-input`).focus();
  }
}

document.addEventListener('keydown', e => {
  if (['INPUT', 'TEXTAREA'].includes(e.target.tagName)) return;
  switch (e.key) {
    case '1': doAction('approve_yt'); break;
    case '2': doAction('reject_yt'); break;
    case '4': doAction('approve_bc'); break;
    case '5': doAction('reject_bc'); break;
    case 'ArrowRight':
    case ' ':
      e.preventDefault();
      navigate('next');
      break;
    case 'ArrowLeft':
      navigate('prev');
      break;
  }
});
</script>
{% endif %}
</body>
</html>
"""


if __name__ == "__main__":
    app.run(port=5020, use_reloader=False)
