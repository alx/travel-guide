#!/usr/bin/env node
// Renders the toulouse-distorama weekly slideshow via Remotion.
// Alternative to capture-slideshow.js — no Hugo server, no Playwright, no ffmpeg graph.
//
// Usage:
//   node scripts/toulouse-distorama/render-slideshow.js [output.mp4]
//
// Options:
//   --youtube-url <url>   YouTube URL whose audio fills intro and outro
//   --clip-offset <s>     Seconds into each clip to start audio (default: 30)
//   --intro-dur <s>       Intro duration (default: 3)
//   --outro-dur <s>       Outro duration (default: 5)
//   --slide-dur <s>       Per-slide duration (default: 7)
//   --fade-out <s>        Audio fade-out duration per slide (default: 2)

const {bundle}        = require('@remotion/bundler');
const {renderMedia, selectComposition} = require('@remotion/renderer');
const {spawnSync}     = require('child_process');
const fs              = require('fs');
const http            = require('http');
const path            = require('path');
const crypto          = require('crypto');

const ROOT          = path.resolve(__dirname, '../..');
const GEOJSON       = path.join(ROOT, 'static/toulouse-distorama/events/this-week.geojson');
const TMP_MEDIA_DIR = path.join(ROOT, 'static/toulouse-distorama/tmp-media');
const OUTPUT_DIR    = path.join(ROOT, 'static/toulouse-distorama/slideshows');
const ENTRY_POINT   = path.join(__dirname, 'remotion/src/index.tsx');

// ── CLI parsing ───────────────────────────────────────────────────────────────
const args = process.argv.slice(2);

function getFlag(flag) {
  const i = args.indexOf(flag);
  return i !== -1 ? args[i + 1] : null;
}

const CLI_YOUTUBE_URL  = getFlag('--youtube-url');
const CLI_CLIP_OFFSET  = getFlag('--clip-offset');
const CLI_INTRO_DUR    = getFlag('--intro-dur');
const CLI_OUTRO_DUR    = getFlag('--outro-dur');
const CLI_SLIDE_DUR    = getFlag('--slide-dur');
const CLI_FADE_OUT     = getFlag('--fade-out');
const CLI_OUTPUT       = args.find(a => !a.startsWith('--') && a.endsWith('.mp4')) || null;

const CLIP_OFFSET  = parseFloat(CLI_CLIP_OFFSET ?? 30);
const INTRO_DUR    = parseFloat(CLI_INTRO_DUR   ?? 3);
const OUTRO_DUR    = parseFloat(CLI_OUTRO_DUR   ?? 5);
const SLIDE_DUR    = parseFloat(CLI_SLIDE_DUR   ?? 7);
const FADE_OUT_DUR = parseFloat(CLI_FADE_OUT    ?? 2);

// ── Naming ────────────────────────────────────────────────────────────────────
function getWeekNumber(d) {
  d = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
}

const HASH     = crypto.randomBytes(4).toString('hex');
const WEEK     = getWeekNumber(new Date());
const BASENAME = `distorama-week-${WEEK}_${HASH}`;
const OUTPUT_MP4 = CLI_OUTPUT || path.join(OUTPUT_DIR, `${BASENAME}.mp4`);

// ── Environment ───────────────────────────────────────────────────────────────
function loadEnv() {
  const env = {...process.env};
  const envFile = path.join(ROOT, '.env');
  if (fs.existsSync(envFile)) {
    fs.readFileSync(envFile, 'utf8').split('\n').forEach(line => {
      const m = line.match(/^([^#=\s][^=]*)=(.*)$/);
      if (m) env[m[1].trim()] = m[2].trim().replace(/^["']|["']$/g, '');
    });
  }
  return env;
}

// ── GeoJSON → slide list ──────────────────────────────────────────────────────
function loadSlides() {
  const data = JSON.parse(fs.readFileSync(GEOJSON, 'utf8'));
  const rows = [];
  data.features.forEach(feat => {
    (feat.properties.events || []).forEach(ev => {
      if (ev.youtube_video_id) {
        rows.push({
          videoId: ev.youtube_video_id,
          artist:  ev.artist || ev.desc || '',
          venue:   feat.properties.name,
          date:    ev.date  || '',
          time:    ev.time  || '',
          price:   ev.price || '',
          coordinates: feat.geometry.coordinates, // [lng, lat]
        });
      }
    });
  });
  rows.sort((a, b) => (a.date || '').localeCompare(b.date || '') || a.venue.localeCompare(b.venue));
  return rows;
}

// ── yt-dlp helpers ────────────────────────────────────────────────────────────
function downloadMedia(videoId) {
  const dest = path.join(TMP_MEDIA_DIR, `${videoId}.mp4`);
  if (fs.existsSync(dest) && fs.statSync(dest).size > 10000) return dest;
  console.log(`  ↓ downloading ${videoId}…`);
  const r = spawnSync('yt-dlp', [
    '--js-runtimes', 'node',
    '-f', '18',
    '--no-playlist', '--quiet', '--no-warnings',
    '-o', dest,
    `https://www.youtube.com/watch?v=${videoId}`,
  ], {stdio: ['ignore', 'pipe', 'pipe']});
  if (r.status !== 0 || !fs.existsSync(dest) || fs.statSync(dest).size < 10000) {
    console.warn(`  ⚠ yt-dlp failed for ${videoId}`);
    return null;
  }
  return dest;
}

function downloadAudioOnly(url, dest) {
  if (fs.existsSync(dest) && fs.statSync(dest).size > 10000) return dest;
  console.log(`  ↓ downloading audio ${path.basename(dest)}…`);
  const r = spawnSync('yt-dlp', [
    '--js-runtimes', 'node',
    '-x', '--audio-format', 'mp3',
    '--no-playlist', '--quiet', '--no-warnings',
    '-o', dest, url,
  ], {stdio: ['ignore', 'pipe', 'pipe']});
  if (r.status !== 0 || !fs.existsSync(dest) || fs.statSync(dest).size < 10000) {
    console.warn(`  ⚠ audio download failed: ${url}`);
    return null;
  }
  return dest;
}

function updateSymlinks(mp4Path) {
  const LATEST = path.join(OUTPUT_DIR, 'distorama-week-latest.mp4');
  try { fs.unlinkSync(LATEST); } catch (e) {}
  try {
    fs.symlinkSync(path.basename(mp4Path), LATEST);
    console.log('✓ Symlink updated: distorama-week-latest.mp4');
  } catch (e) {
    console.warn(`⚠ Failed to create symlink: ${e.message}`);
  }
}

// ── Local HTTP file server for tmp-media ──────────────────────────────────────
function startMediaServer(dir) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const filePath = path.join(dir, decodeURIComponent(req.url.slice(1)));
      fs.readFile(filePath, (err, data) => {
        if (err) { res.writeHead(404); res.end(); return; }
        res.writeHead(200, {'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes'});
        res.end(data);
      });
    });
    server.listen(0, '127.0.0.1', () => {
      resolve({server, port: server.address().port});
    });
    server.on('error', reject);
  });
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  [TMP_MEDIA_DIR, OUTPUT_DIR].forEach(d => fs.mkdirSync(d, {recursive: true}));

  const env = loadEnv();
  const maptilerKey = env.MAPTILER_API_KEY || '';
  if (!maptilerKey) console.warn('⚠ MAPTILER_API_KEY not set — map tiles may fail');

  // 1. Load slides from GeoJSON.
  const rawSlides = loadSlides();
  console.log(`${rawSlides.length} slides with youtube_video_id found.`);

  // 2. Download video clips.
  console.log('\nDownloading media…');
  const uniqueIds = [...new Set(rawSlides.map(s => s.videoId))];
  const mediaMap = {};
  uniqueIds.forEach(id => { mediaMap[id] = downloadMedia(id); });

  // 3. Optionally download filler audio.
  let fillerLocalPath = null;
  if (CLI_YOUTUBE_URL) {
    const videoId = CLI_YOUTUBE_URL.match(/[?&]v=([^&]+)/)?.[1] || 'filler';
    const dest = path.join(TMP_MEDIA_DIR, `filler_${videoId}.mp3`);
    fillerLocalPath = downloadAudioOnly(CLI_YOUTUBE_URL, dest);
  }

  // 4. Serve tmp-media over HTTP so Remotion (Chromium) can load local files.
  const {server: mediaServer, port: mediaPort} = await startMediaServer(TMP_MEDIA_DIR);
  const toMediaUrl = (absPath) => `http://127.0.0.1:${mediaPort}/${path.basename(absPath)}`;

  // 5. Filter out failed downloads.
  const slides = rawSlides
    .filter(s => mediaMap[s.videoId])
    .map(s => ({
      ...s,
      mediaPath: toMediaUrl(mediaMap[s.videoId]),
    }));

  const dropped = rawSlides.length - slides.length;
  if (dropped > 0) {
    const failed = rawSlides.filter(s => !mediaMap[s.videoId]).map(s => s.videoId);
    console.log(`  ⚠ skipping ${dropped} slide(s) with failed download: ${failed.join(' ')}`);
  }
  if (!slides.length) {
    mediaServer.close();
    console.error('No slides with media — aborting.');
    process.exit(1);
  }

  const youtubeFillerPath = fillerLocalPath ? toMediaUrl(fillerLocalPath) : null;

  // 5. Build Remotion input props.
  const inputProps = {
    slides,
    clipOffset:   CLIP_OFFSET,
    fadeOutDur:   FADE_OUT_DUR,
    introDur:     INTRO_DUR,
    outroDur:     OUTRO_DUR,
    slideDur:     SLIDE_DUR,
    youtubeFillerPath,
    maptilerKey,
  };

  console.log(`\nBundling Remotion composition…`);
  const serveUrl = await bundle({entryPoint: ENTRY_POINT});

  console.log('Selecting composition…');
  const composition = await selectComposition({
    serveUrl,
    id: 'DistoramaSlideShow',
    inputProps,
  });

  const totalSec = INTRO_DUR + slides.length * SLIDE_DUR + OUTRO_DUR;
  console.log(`\nRendering ${slides.length} slides (${totalSec.toFixed(0)}s @ 30fps) → ${OUTPUT_MP4}`);

  await renderMedia({
    composition,
    serveUrl,
    codec: 'h264',
    outputLocation: OUTPUT_MP4,
    inputProps,
    chromiumOptions: {gl: 'swangle'}, // software WebGL for headless/CI
    videoBitrate: '8M',
    onProgress: ({progress}) => {
      process.stdout.write(`  Rendering ${Math.round(progress * 100)}%\r`);
    },
  });

  mediaServer.close();
  console.log(`\n✓ ${OUTPUT_MP4} (${Math.round(fs.statSync(OUTPUT_MP4).size / 1024)} KB)`);
  updateSymlinks(OUTPUT_MP4);
}

main().catch(err => { console.error(err); process.exit(1); });
