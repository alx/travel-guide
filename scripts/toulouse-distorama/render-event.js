#!/usr/bin/env node
// Renders one MP4 per event (with youtube_video_id) from this-week and next-week GeoJSONs.
// Output: static/toulouse-distorama/event-clips/{date}_{videoId}.mp4
// Skips events whose output file already exists.
//
// Usage:
//   node scripts/toulouse-distorama/render-event.js
//
// Options:
//   --clip-offset <s>   Seconds into each clip to start (default: 30)
//   --slide-dur <s>     Clip duration in seconds (default: 6)
//   --fade-out <s>      Audio fade-out duration (default: 1)

const {bundle}        = require('@remotion/bundler');
const {renderMedia, selectComposition} = require('@remotion/renderer');
const {spawnSync}     = require('child_process');
const fs              = require('fs');
const http            = require('http');
const path            = require('path');

const ROOT          = path.resolve(__dirname, '../..');
const TMP_MEDIA_DIR = path.join(ROOT, 'static/toulouse-distorama/tmp-media');
const OUTPUT_DIR    = path.join(ROOT, 'static/toulouse-distorama/event-clips');
const ENTRY_POINT   = path.join(__dirname, 'remotion/src/index.tsx');

const GEOJSONS = [
  path.join(ROOT, 'static/toulouse-distorama/events/this-week.geojson'),
  path.join(ROOT, 'static/toulouse-distorama/events/next-week.geojson'),
];

// ── CLI parsing ───────────────────────────────────────────────────────────────
const args = process.argv.slice(2);

function getFlag(flag) {
  const i = args.indexOf(flag);
  return i !== -1 ? args[i + 1] : null;
}

const CLIP_OFFSET  = parseFloat(getFlag('--clip-offset') ?? 30);
const SLIDE_DUR    = parseFloat(getFlag('--slide-dur')   ?? 6);
const FADE_OUT_DUR = parseFloat(getFlag('--fade-out')    ?? 1);

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

// ── GeoJSON → event list ──────────────────────────────────────────────────────
function loadEvents() {
  const seen = new Set();
  const events = [];

  for (const geojson of GEOJSONS) {
    if (!fs.existsSync(geojson)) {
      console.warn(`⚠ GeoJSON not found, skipping: ${geojson}`);
      continue;
    }
    const data = JSON.parse(fs.readFileSync(geojson, 'utf8'));
    data.features.forEach(feat => {
      (feat.properties.events || []).forEach(ev => {
        if (!ev.youtube_video_id) return;
        const key = `${ev.date || ''}_${ev.youtube_video_id}`;
        if (seen.has(key)) return;
        seen.add(key);
        events.push({
          videoId:     ev.youtube_video_id,
          artist:      ev.artist || ev.desc || '',
          venue:       feat.properties.name,
          date:        ev.date  || '',
          time:        ev.time  || '',
          price:       ev.price || '',
          coordinates: feat.geometry.coordinates,
        });
      });
    });
  }

  events.sort((a, b) => (a.date || '').localeCompare(b.date || '') || a.venue.localeCompare(b.venue));
  return events;
}

// ── yt-dlp helper ─────────────────────────────────────────────────────────────
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
    server.listen(0, '127.0.0.1', () => resolve({server, port: server.address().port}));
    server.on('error', reject);
  });
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  [TMP_MEDIA_DIR, OUTPUT_DIR].forEach(d => fs.mkdirSync(d, {recursive: true}));

  const env = loadEnv();
  const maptilerKey = env.MAPTILER_API_KEY || '';
  if (!maptilerKey) console.warn('⚠ MAPTILER_API_KEY not set — map tiles may fail');

  // 1. Load all events.
  const allEvents = loadEvents();
  console.log(`${allEvents.length} events with youtube_video_id found.`);

  // 2. Determine which need rendering.
  const pending = allEvents.filter(ev => {
    const out = path.join(OUTPUT_DIR, `${ev.date}_${ev.videoId}.mp4`);
    if (fs.existsSync(out)) {
      console.log(`  ✓ skip ${ev.date}_${ev.videoId}.mp4 (exists)`);
      return false;
    }
    return true;
  });

  if (!pending.length) {
    console.log('All events already rendered.');
    return;
  }
  console.log(`${pending.length} event(s) to render.`);

  // 3. Download media for pending events.
  console.log('\nDownloading media…');
  const mediaMap = {};
  const uniqueIds = [...new Set(pending.map(ev => ev.videoId))];
  uniqueIds.forEach(id => { mediaMap[id] = downloadMedia(id); });

  // 4. Start local HTTP server.
  const {server: mediaServer, port: mediaPort} = await startMediaServer(TMP_MEDIA_DIR);
  const toMediaUrl = absPath => `http://127.0.0.1:${mediaPort}/${path.basename(absPath)}`;

  // 5. Bundle once.
  console.log('\nBundling Remotion composition…');
  const serveUrl = await bundle({entryPoint: ENTRY_POINT});

  // 6. Render one MP4 per event.
  let rendered = 0;
  let failed = 0;

  for (const ev of pending) {
    const localPath = mediaMap[ev.videoId];
    if (!localPath) {
      console.warn(`  ⚠ skipping ${ev.videoId} — download failed`);
      failed++;
      continue;
    }

    const outputMp4 = path.join(OUTPUT_DIR, `${ev.date}_${ev.videoId}.mp4`);
    const slide = {...ev, mediaPath: toMediaUrl(localPath)};

    const inputProps = {
      slides:       [slide],
      clipOffset:   CLIP_OFFSET,
      fadeOutDur:   FADE_OUT_DUR,
      introDur:     0,
      outroDur:     0,
      slideDur:     SLIDE_DUR,
      youtubeFillerPath: null,
      maptilerKey,
    };

    console.log(`\n[${rendered + failed + 1}/${pending.length}] ${ev.date} — ${ev.artist} @ ${ev.venue}`);

    const composition = await selectComposition({
      serveUrl,
      id: 'DistoramaSlideShow',
      inputProps,
    });

    await renderMedia({
      composition,
      serveUrl,
      codec:          'h264',
      outputLocation: outputMp4,
      inputProps,
      chromiumOptions: {gl: 'swangle'},
      videoBitrate:   '8M',
      onProgress: ({progress}) => {
        process.stdout.write(`  Rendering ${Math.round(progress * 100)}%\r`);
      },
    });

    const kb = Math.round(fs.statSync(outputMp4).size / 1024);
    console.log(`  ✓ ${path.basename(outputMp4)} (${kb} KB)`);
    rendered++;
  }

  mediaServer.close();
  console.log(`\nDone. ${rendered} rendered, ${failed} failed.`);
}

main().catch(err => { console.error(err); process.exit(1); });
