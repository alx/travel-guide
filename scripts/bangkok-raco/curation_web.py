#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["flask>=3.0"]
# ///
"""
SoundCloud curation UI for bangkok-raco artists.

Usage:
    uv run scripts/bangkok-raco/curation_web.py

Opens at http://localhost:5020/
"""
from __future__ import annotations

import json
import random as _random
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

PAGE_SIZE = 10

from flask import Flask, jsonify, render_template_string, request

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
MEDIACACHE_PATH = SCRIPT_DIR / ".mediacache.json"
GEOJSON_PATHS = [
    REPO_ROOT / "static/bangkok-raco/events/this-week.geojson",
    REPO_ROOT / "static/bangkok-raco/events/next-week.geojson",
]
SC_PROXY_URL = "https://proxy.searchsoundcloud.com/tracks"

app = Flask(__name__)


# ── Cache I/O ─────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    if MEDIACACHE_PATH.exists():
        return json.loads(MEDIACACHE_PATH.read_text())
    return {}


def save_cache(cache: dict) -> None:
    MEDIACACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


# ── Artist→events index ───────────────────────────────────────────────────────

def build_artist_events() -> dict[str, list[dict]]:
    """Returns {artist_name: [{venue, poster, date, title, ra_url}]}."""
    index: dict[str, list[dict]] = defaultdict(list)
    seen: set[tuple] = set()
    for path in GEOJSON_PATHS:
        if not path.exists():
            continue
        fc = json.loads(path.read_text())
        for feat in fc.get("features", []):
            p = feat["properties"]
            venue = p.get("name", "")
            for ev in p.get("events", []):
                poster = ev.get("poster", "")
                date = ev.get("date", "")
                title = ev.get("title", "")
                ra_url = ev.get("ra_url", "")
                for artist in ev.get("artists", []):
                    key = (artist, venue, date)
                    if key in seen:
                        continue
                    seen.add(key)
                    index[artist].append({
                        "venue": venue,
                        "poster": poster,
                        "date": date,
                        "title": title,
                        "ra_url": ra_url,
                    })
    return dict(index)


# ── SoundCloud proxy ──────────────────────────────────────────────────────────

def sc_search(query: str, limit: int = 8) -> list[dict]:
    params = urllib.parse.urlencode({"q": query})
    url = f"{SC_PROXY_URL}?{params}"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "maps.girard-davila.net/bangkok-raco-curation",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return data.get("collection", [])[:limit]
    except Exception:
        return []


def fmt_duration(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


# ── Template ──────────────────────────────────────────────────────────────────

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bangkok RA.co — SoundCloud Curation</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0a0a;color:#ddd;font-family:'Space Mono',monospace;font-size:13px;padding:1.2rem;max-width:1100px}
h1{color:#fff;font-size:.9rem;letter-spacing:.12em;margin-bottom:.8rem}
.stats{font-size:.7rem;color:#555;margin-bottom:1rem}
.tabs{display:flex;gap:.4rem;margin-bottom:1.4rem;flex-wrap:wrap}
.tab{padding:.3rem .7rem;border:1px solid #333;color:#666;cursor:pointer;text-decoration:none;font-size:.72rem;letter-spacing:.05em}
.tab.active{border-color:#fff;color:#fff}

/* ── Artist card ── */
.card{border:1px solid #222;margin-bottom:1rem;background:#0f0f0f}
.card-header{display:flex;align-items:center;gap:.6rem;padding:.55rem .7rem;border-bottom:1px solid #1e1e1e;flex-wrap:wrap}
.artist-name{font-size:.85rem;font-weight:700;color:#fff;letter-spacing:.04em;flex:0 0 auto;min-width:160px}
.badge{font-size:.62rem;padding:.1rem .32rem;border-radius:2px;margin-left:.2rem;vertical-align:middle}
.badge.ok{background:#1b5e20;color:#81c784}
.badge.pending{background:#1a237e;color:#90caf9}
.badge.none{background:#1a1a1a;color:#444}
.spacer{flex:1}
.btn{padding:.25rem .55rem;border:1px solid #333;background:transparent;color:#888;cursor:pointer;font-size:.7rem;font-family:inherit;letter-spacing:.03em;white-space:nowrap}
.btn:hover{border-color:#aaa;color:#fff}
.btn.approve{border-color:#2e7d32;color:#4caf50}
.btn.approve:hover{background:#2e7d32;color:#fff}
.btn.reject{border-color:#7f0000;color:#e57373}
.btn.reject:hover{background:#7f0000;color:#fff}
.btn.alts{border-color:#1565c0;color:#64b5f6}
.btn.alts:hover{background:#1565c0;color:#fff}
.btn:disabled{opacity:.35;cursor:default}

/* ── Card body: poster + embed ── */
.card-body{display:flex;gap:0}
.poster-col{flex:0 0 160px;min-height:100px;background:#0a0a0a;position:relative;overflow:hidden}
.poster-col img{width:160px;height:160px;object-fit:cover;display:block}
.poster-col .no-poster{width:160px;height:160px;display:flex;align-items:center;justify-content:center;color:#2a2a2a;font-size:2rem}
.venue-pill{background:rgba(0,0,0,.75);color:#bbb;font-size:.62rem;padding:.2rem .4rem;position:absolute;bottom:0;left:0;right:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.embed-col{flex:1;display:flex;flex-direction:column;padding:.6rem .7rem;gap:.4rem;min-width:0}
.no-track-msg{color:#333;font-size:.78rem;padding:.5rem 0}
.embed-col iframe{border:0;width:100%}
.event-meta{font-size:.68rem;color:#555;line-height:1.5}
.event-meta a{color:#555;text-decoration:none}
.event-meta a:hover{color:#999}
.event-date{color:#777;font-size:.68rem}
.event-title{color:#999;font-size:.7rem;font-style:italic;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ── Alternatives ── */
.alts-section{border-top:1px solid #1e1e1e;padding:.6rem .7rem}
.alts-section.hidden{display:none}
.alts-label{font-size:.65rem;color:#444;letter-spacing:.1em;text-transform:uppercase;margin-bottom:.5rem}
.alts-grid{display:flex;flex-direction:column;gap:.6rem}
.alt-item{border:1px solid #1e1e1e;padding:.4rem .5rem;background:#0a0a0a}
.alt-header{display:flex;align-items:flex-start;gap:.5rem;margin-bottom:.35rem;flex-wrap:wrap}
.alt-info{flex:1;min-width:0}
.alt-title{font-size:.75rem;color:#ccc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.alt-sub{font-size:.65rem;color:#555;margin-top:.15rem;line-height:1.5}
.alt-sub span{margin-right:.6rem}
.loading{color:#555;font-size:.72rem;padding:.3rem 0}
#poster-zoom{position:fixed;z-index:999;pointer-events:none;display:none;box-shadow:0 8px 40px rgba(0,0,0,.9);border:1px solid #333}
#poster-zoom img{display:block;width:380px;height:auto}
</style>
</head>
<body>
<div id="poster-zoom"><img id="poster-zoom-img" src="" alt=""></div>
<h1>BANGKOK RA.CO — SOUNDCLOUD CURATION</h1>
<div class="stats">{{ validated }} validated · {{ pending }} pending · {{ no_track }} no track
  · page {{ page }}/{{ total_pages }}</div>
<div class="tabs">
  <a href="/?tab=pending" class="tab{% if tab=='pending' %} active{% endif %}">Pending ({{ pending }})</a>
  <a href="/?tab=validated" class="tab{% if tab=='validated' %} active{% endif %}">Validated ({{ validated }})</a>
  <a href="/?tab=no_track" class="tab{% if tab=='no_track' %} active{% endif %}">No track ({{ no_track }})</a>
  <a href="/?tab=all" class="tab{% if tab=='all' %} active{% endif %}">All ({{ total }})</a>
  <button class="tab" onclick="randomize()" title="Shuffle order">⇄ Random</button>
</div>

{% for artist in artists %}
{% set entry = cache[artist] %}
{% set track_id = entry.soundcloud_track_id %}
{% set sc_url = entry.soundcloud_url %}
{% set is_validated = entry.soundcloud_validated %}
{% set events = artist_events.get(artist, []) %}
{% set ev0 = events[0] if events else {} %}

<div class="card" id="card-{{ loop.index }}" data-artist="{{ artist | e }}">

  {# ── Header ── #}
  <div class="card-header" data-idx="{{ loop.index }}" data-artist="{{ artist }}">
    <span class="artist-name">
      {{ artist }}
      {% if is_validated %}<span class="badge ok" id="badge-{{ loop.index }}">✓ validated</span>
      {% elif track_id %}<span class="badge pending" id="badge-{{ loop.index }}">pending</span>
      {% else %}<span class="badge none" id="badge-{{ loop.index }}">no track</span>{% endif %}
    </span>
    <span class="spacer"></span>
    {% if track_id and not is_validated %}
    <button class="btn approve" id="approve-btn-{{ loop.index }}" onclick="doApprove({{ loop.index }})">✓ Approve</button>
    {% endif %}
    {% if track_id %}
    <button class="btn reject" id="reject-btn-{{ loop.index }}" onclick="doReject({{ loop.index }})">✕ Reject</button>
    {% endif %}
    <button class="btn alts" onclick="toggleAlts({{ loop.index }})">⇅ Alternatives</button>
  </div>

  {# ── Body: poster + embed ── #}
  <div class="card-body">
    <div class="poster-col">
      {% if ev0.poster %}
        <img src="{{ ev0.poster }}" alt="event poster" loading="lazy"
          onmouseenter="showZoom(event, this.src)" onmouseleave="hideZoom()">
      {% else %}
        <div class="no-poster">◈</div>
      {% endif %}
      {% if ev0.venue %}
        <div class="venue-pill">{{ ev0.venue }}</div>
      {% endif %}
    </div>
    <div class="embed-col">
      {% if events %}
      <div class="event-meta">
        {% for ev in events %}
        <div>
          <span class="event-date">{{ ev.date }}</span>
          {% if ev.title %} — <span class="event-title" title="{{ ev.title }}">{{ ev.title }}</span>{% endif %}
          {% if ev.venue and ev.venue != ev0.venue %} @ {{ ev.venue }}{% endif %}
          {% if ev.ra_url %} <a href="{{ ev.ra_url }}" target="_blank" rel="noopener">RA↗</a>{% endif %}
        </div>
        {% endfor %}
      </div>
      {% endif %}
      {% if track_id %}
        <iframe
          src="https://w.soundcloud.com/player/?url=https%3A//api.soundcloud.com/tracks/{{ track_id }}&color=%23ff5500&auto_play=false&show_comments=false&show_user=true&show_reposts=false&visual=false"
          height="80" scrolling="no" allow="autoplay" loading="lazy"></iframe>
        <div class="event-meta">
          <a href="{{ sc_url }}" target="_blank" rel="noopener">{{ sc_url }}</a>
        </div>
      {% else %}
        <div class="no-track-msg">No SoundCloud track found — use alternatives to assign one.</div>
      {% endif %}
    </div>
  </div>

  {# ── Alternatives ── #}
  <div class="alts-section hidden" id="alts-section-{{ loop.index }}">
    <div class="alts-label">Alternatives</div>
    <div class="loading" id="loading-{{ loop.index }}">Loading…</div>
    <div class="alts-grid" id="alts-{{ loop.index }}"></div>
  </div>

</div>
{% endfor %}

{% if not artists %}
<p style="color:#444;padding:1rem 0">Nothing here.</p>
{% endif %}

{% if total_pages > 1 %}
{% set base = '/?tab=' ~ tab ~ ('&seed=' ~ seed if seed else '') %}
<div class="tabs" style="margin-top:1rem">
  {% if page > 1 %}
  <a href="{{ base }}&page={{ page - 1 }}" class="tab">← Prev</a>
  {% else %}
  <span class="tab" style="opacity:.3">← Prev</span>
  {% endif %}
  {% for p in range(1, total_pages + 1) %}
    {% if p == page %}
    <span class="tab active">{{ p }}</span>
    {% elif p == 1 or p == total_pages or (p >= page - 2 and p <= page + 2) %}
    <a href="{{ base }}&page={{ p }}" class="tab">{{ p }}</a>
    {% elif p == page - 3 or p == page + 3 %}
    <span class="tab" style="opacity:.3;pointer-events:none">…</span>
    {% endif %}
  {% endfor %}
  {% if page < total_pages %}
  <a href="{{ base }}&page={{ page + 1 }}" class="tab">Next →</a>
  {% else %}
  <span class="tab" style="opacity:.3">Next →</span>
  {% endif %}
</div>
{% endif %}

<script>
const REJECTED = {{ rejected_map | tojson }};

// ── Randomize ──────────────────────────────────────────────────────────────

function randomize() {
  const seed = Math.floor(Math.random() * 1e9);
  const tab = new URLSearchParams(window.location.search).get('tab') || 'pending';
  window.location.href = `/?tab=${tab}&seed=${seed}&page=1`;
}

// ── Poster zoom ────────────────────────────────────────────────────────────

const _zoom = document.getElementById('poster-zoom');
const _zoomImg = document.getElementById('poster-zoom-img');

function showZoom(e, src) {
  _zoomImg.src = src;
  _zoom.style.display = 'block';
  _positionZoom(e);
  document.addEventListener('mousemove', _positionZoom);
}

function hideZoom() {
  _zoom.style.display = 'none';
  document.removeEventListener('mousemove', _positionZoom);
}

function _positionZoom(e) {
  const pad = 16, w = 380;
  let x = e.clientX + pad;
  let y = e.clientY + pad;
  if (x + w > window.innerWidth) x = e.clientX - w - pad;
  const h = _zoom.offsetHeight;
  if (y + h > window.innerHeight) y = e.clientY - h - pad;
  _zoom.style.left = x + 'px';
  _zoom.style.top  = y + 'px';
}

// ── In-place card mutations ────────────────────────────────────────────────

function _artist(idx) {
  return document.getElementById('card-' + idx)?.dataset.artist || '';
}

function setBadge(idx, cls, text) {
  const el = document.getElementById('badge-' + idx);
  if (!el) return;
  el.className = 'badge ' + cls;
  el.textContent = text;
}

async function doApprove(idx) {
  const artist = _artist(idx);
  const btn = document.getElementById('approve-btn-' + idx);
  if (btn) btn.disabled = true;
  const r = await fetch('/approve/' + encodeURIComponent(artist), {method: 'POST'});
  if (!r.ok) { if (btn) btn.disabled = false; return; }
  setBadge(idx, 'ok', '✓ validated');
  document.getElementById('approve-btn-' + idx)?.remove();
}

async function doReject(idx) {
  const artist = _artist(idx);
  const btn = document.getElementById('reject-btn-' + idx);
  if (btn) btn.disabled = true;
  const r = await fetch('/reject/' + encodeURIComponent(artist), {method: 'POST'});
  if (!r.ok) { if (btn) btn.disabled = false; return; }
  setBadge(idx, 'none', 'no track');
  document.getElementById('approve-btn-' + idx)?.remove();
  document.getElementById('reject-btn-' + idx)?.remove();
  const col = document.querySelector('#card-' + idx + ' .embed-col');
  if (col) {
    col.querySelector('iframe')?.remove();
    const linkWrap = col.querySelector('.event-meta:last-of-type');
    if (linkWrap && linkWrap.querySelector('a')) linkWrap.remove();
    if (!col.querySelector('.no-track-msg'))
      col.insertAdjacentHTML('beforeend', '<div class="no-track-msg">No SoundCloud track found — use alternatives to assign one.</div>');
  }
  _resetAlts(idx);
}

async function doSelect(idx, trackId, scUrl) {
  const artist = _artist(idx);
  const body = new URLSearchParams({track_id: trackId, sc_url: scUrl});
  const r = await fetch('/select/' + encodeURIComponent(artist), {method: 'POST', body});
  if (!r.ok) return;
  setBadge(idx, 'ok', '✓ validated');
  document.getElementById('approve-btn-' + idx)?.remove();
  const col = document.querySelector('#card-' + idx + ' .embed-col');
  if (col) {
    const scSrc = `https://w.soundcloud.com/player/?url=https%3A//api.soundcloud.com/tracks/${trackId}&color=%23ff5500&auto_play=false&show_comments=false&show_user=true&show_reposts=false&visual=false`;
    let iframe = col.querySelector('iframe');
    if (iframe) {
      iframe.src = scSrc;
    } else {
      col.querySelector('.no-track-msg')?.remove();
      col.insertAdjacentHTML('beforeend', `<iframe src="${scSrc}" height="80" width="100%" style="border:0" scrolling="no" allow="autoplay"></iframe>`);
    }
    let linkWrap = col.querySelector('.event-meta:last-of-type');
    if (linkWrap && linkWrap.querySelector('a')) {
      const a = linkWrap.querySelector('a');
      a.href = scUrl; a.textContent = scUrl;
    } else {
      col.insertAdjacentHTML('beforeend', `<div class="event-meta"><a href="${esc(scUrl)}" target="_blank" rel="noopener">${esc(scUrl)}</a></div>`);
    }
    if (!document.getElementById('reject-btn-' + idx)) {
      const hdr = document.querySelector('#card-' + idx + ' .card-header');
      const altsBtn = hdr?.querySelector('.btn.alts');
      if (altsBtn) {
        const rb = document.createElement('button');
        rb.className = 'btn reject'; rb.id = 'reject-btn-' + idx;
        rb.textContent = '✕ Reject';
        rb.onclick = () => doReject(idx);
        hdr.insertBefore(rb, altsBtn);
      }
    }
  }
  document.getElementById('alts-section-' + idx)?.classList.add('hidden');
  _resetAlts(idx);
}

function doSelectBtn(btn) {
  doSelect(btn.dataset.cardIdx || btn.dataset.card, btn.dataset.trackId, btn.dataset.scUrl);
}

function _resetAlts(idx) {
  const el = document.getElementById('alts-' + idx);
  if (el) { el.innerHTML = ''; delete el.dataset.loaded; }
  const load = document.getElementById('loading-' + idx);
  if (load) { load.textContent = 'Loading…'; load.style.display = ''; }
}

// ── Alternatives panel ─────────────────────────────────────────────────────

function toggleAlts(idx) {
  const sec = document.getElementById('alts-section-' + idx);
  const isHidden = sec.classList.contains('hidden');
  if (!isHidden) { sec.classList.add('hidden'); return; }
  sec.classList.remove('hidden');
  const altsEl = document.getElementById('alts-' + idx);
  if (altsEl.dataset.loaded) return;
  fetchAlts(idx);
}

function fmtDur(ms) {
  if (!ms) return '';
  const s = Math.floor(ms / 1000);
  return Math.floor(s/60) + ':' + String(s%60).padStart(2,'0');
}

async function fetchAlts(idx) {
  const artist = _artist(idx);
  const loadEl = document.getElementById('loading-' + idx);
  const altsEl = document.getElementById('alts-' + idx);
  const rejected = (REJECTED[artist] || []).map(String);
  try {
    const r = await fetch('/alternatives/' + encodeURIComponent(artist));
    const tracks = await r.json();
    loadEl.style.display = 'none';
    if (!tracks.length) {
      loadEl.textContent = 'No alternatives found.';
      loadEl.style.display = '';
      return;
    }
    altsEl.innerHTML = tracks.map(t => {
      const isRejected = rejected.includes(String(t.id));
      const dur = t.full_duration || t.duration || 0;
      const user = (t.user || {}).username || '';
      const genre = t.genre || '';
      const likes = t.likes_count != null ? t.likes_count + ' ♥' : '';
      const artwork = t.artwork_url ? t.artwork_url.replace('-large', '-t200x200') : '';
      return `<div class="alt-item" style="${isRejected ? 'opacity:.4' : ''}">
        <div class="alt-header">
          ${artwork ? `<img src="${esc(artwork)}" width="48" height="48" style="object-fit:cover;border-radius:2px;flex-shrink:0" loading="lazy">` : ''}
          <div class="alt-info">
            <div class="alt-title">${esc(t.title || '')}${isRejected ? ' <span style="color:#e57373;font-size:.62rem">[rejected]</span>' : ''}</div>
            <div class="alt-sub">
              <span>${esc(user)}</span>
              ${genre ? `<span>${esc(genre)}</span>` : ''}
              ${dur ? `<span>${fmtDur(dur)}</span>` : ''}
              ${likes ? `<span>${likes}</span>` : ''}
            </div>
          </div>
          <button class="btn approve" style="flex-shrink:0"
            data-card="${idx}" data-track-id="${t.id}" data-sc-url="${esc(t.permalink_url || '')}"
            onclick="doSelectBtn(this)" ${isRejected ? 'disabled' : ''}>✓ Use</button>
        </div>
        <iframe src="https://w.soundcloud.com/player/?url=https%3A//api.soundcloud.com/tracks/${t.id}&color=%23ff5500&auto_play=false&show_comments=false&show_user=true&show_reposts=false&visual=false"
          height="80" width="100%" style="border:0" scrolling="no" allow="autoplay" loading="lazy"></iframe>
      </div>`;
    }).join('');
    altsEl.dataset.loaded = '1';
  } catch(e) {
    loadEl.textContent = 'Error loading alternatives.';
    loadEl.style.display = '';
  }
}

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
</script>
</body>
</html>
"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    cache = load_cache()
    artist_events = build_artist_events()
    tab  = request.args.get("tab", "pending")
    seed = request.args.get("seed", "")
    try:
        page = max(1, int(request.args.get("page", 1) or 1))
    except ValueError:
        page = 1

    all_artists = sorted(cache.keys(), key=str.lower)
    pending   = [a for a in all_artists if cache[a].get("soundcloud_track_id") and not cache[a].get("soundcloud_validated")]
    validated = [a for a in all_artists if cache[a].get("soundcloud_validated")]
    no_track  = [a for a in all_artists if not cache[a].get("soundcloud_track_id")]

    tab_map = {"pending": pending, "validated": validated, "no_track": no_track, "all": all_artists}
    artists_full = list(tab_map.get(tab, pending))

    if seed:
        try:
            _random.Random(int(seed)).shuffle(artists_full)
        except ValueError:
            pass

    total_pages = max(1, (len(artists_full) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    artists = artists_full[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

    rejected_map = {a: [str(x) for x in cache[a].get("soundcloud_rejected_ids", [])] for a in all_artists}

    return render_template_string(
        PAGE,
        cache=cache,
        artists=artists,
        artist_events=artist_events,
        tab=tab,
        page=page,
        total_pages=total_pages,
        seed=seed,
        pending=len(pending),
        validated=len(validated),
        no_track=len(no_track),
        total=len(all_artists),
        rejected_map=rejected_map,
    )


@app.post("/approve/<path:artist>")
def approve(artist: str):
    cache = load_cache()
    if artist in cache:
        cache[artist]["soundcloud_validated"] = True
        save_cache(cache)
    return jsonify({"ok": True})


@app.post("/reject/<path:artist>")
def reject(artist: str):
    cache = load_cache()
    if artist in cache:
        entry = cache[artist]
        tid = entry.get("soundcloud_track_id", "")
        if tid:
            rejected = entry.get("soundcloud_rejected_ids", [])
            if tid not in rejected:
                rejected.append(tid)
            entry["soundcloud_rejected_ids"] = rejected
        entry["soundcloud_track_id"] = ""
        entry["soundcloud_url"] = ""
        entry["soundcloud_validated"] = False
        save_cache(cache)
    return jsonify({"ok": True})


@app.post("/select/<path:artist>")
def select(artist: str):
    cache = load_cache()
    if artist in cache:
        entry = cache[artist]
        old_tid = entry.get("soundcloud_track_id", "")
        new_tid = request.form.get("track_id", "")
        sc_url  = request.form.get("sc_url", "")
        if old_tid and old_tid != new_tid:
            rejected = entry.get("soundcloud_rejected_ids", [])
            if old_tid not in rejected:
                rejected.append(old_tid)
            entry["soundcloud_rejected_ids"] = rejected
        entry["soundcloud_track_id"] = new_tid
        entry["soundcloud_url"] = sc_url
        entry["soundcloud_validated"] = True
        save_cache(cache)
    return jsonify({"ok": True})


@app.get("/alternatives/<path:artist>")
def alternatives(artist: str):
    cache = load_cache()
    entry = cache.get(artist, {})
    rejected = {str(x) for x in entry.get("soundcloud_rejected_ids", [])}
    tracks = sc_search(artist)
    non_rejected = [t for t in tracks if str(t.get("id", "")) not in rejected]
    rejected_tracks = [t for t in tracks if str(t.get("id", "")) in rejected]
    return jsonify(non_rejected + rejected_tracks)


if __name__ == "__main__":
    print("Bangkok RA.co SoundCloud curation → http://localhost:5020/")
    app.run(debug=True, host="127.0.0.1", port=5020, threaded=True)
