#!/usr/bin/env node
// Renders the Bangkok city walk video via Remotion.
//
// Usage:
//   node scripts/bangkok-citywalk/render-walk.js [output.mp4]
//
// Options:
//   --intro-dur <s>   Intro duration in seconds (default: 3)
//   --outro-dur <s>   Outro duration in seconds (default: 5)
//   --slide-dur <s>   Per-POI duration in seconds (default: 10)

const {bundle}      = require('@remotion/bundler');
const {renderMedia, selectComposition} = require('@remotion/renderer');
const fs            = require('fs');
const http          = require('http');
const path          = require('path');
const crypto        = require('crypto');

const ROOT        = path.resolve(__dirname, '../..');
const GEOJSON     = path.join(ROOT, 'static/bangkok-citywalk/walk.geojson');
const PHOTOS_DIR  = path.join(ROOT, 'static/bangkok-citywalk/photos');
const OUTPUT_DIR  = path.join(ROOT, 'static/bangkok-citywalk');
const ENTRY_POINT = path.join(__dirname, 'remotion/src/index.tsx');

// ── CLI parsing ───────────────────────────────────────────────────────────────
const args = process.argv.slice(2);

function getFlag(flag) {
  const i = args.indexOf(flag);
  return i !== -1 ? args[i + 1] : null;
}

const CLI_INTRO_DUR = getFlag('--intro-dur');
const CLI_OUTRO_DUR = getFlag('--outro-dur');
const CLI_SLIDE_DUR = getFlag('--slide-dur');
const CLI_OUTPUT    = args.find(a => !a.startsWith('--') && a.endsWith('.mp4')) || null;

const INTRO_DUR = parseFloat(CLI_INTRO_DUR ?? 3);
const OUTRO_DUR = parseFloat(CLI_OUTRO_DUR ?? 5);
const SLIDE_DUR = parseFloat(CLI_SLIDE_DUR ?? 10);

const HASH       = crypto.randomBytes(4).toString('hex');
const OUTPUT_MP4 = CLI_OUTPUT || path.join(OUTPUT_DIR, `bangkok-citywalk-${HASH}.mp4`);

// ── Photo HTTP server ─────────────────────────────────────────────────────────
function startPhotoServer(dir) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const filePath = path.join(dir, decodeURIComponent(req.url.slice(1)));
      fs.readFile(filePath, (err, data) => {
        if (err) { res.writeHead(404); res.end(); return; }
        res.writeHead(200, {'Content-Type': 'image/jpeg'});
        res.end(data);
      });
    });
    server.listen(0, '127.0.0.1', () => resolve({server, port: server.address().port}));
    server.on('error', reject);
  });
}

// ── GeoJSON → Remotion props ──────────────────────────────────────────────────
function parseGeoJSON(photoBaseUrl) {
  const data = JSON.parse(fs.readFileSync(GEOJSON, 'utf8'));
  const poiFeatures = data.features
    .filter(f => f.geometry.type === 'Point')
    .sort((a, b) => a.properties.order - b.properties.order);
  const routeFeature = data.features.find(f => f.geometry.type === 'LineString');

  const slides = poiFeatures.map(f => {
    const p = f.properties;
    // Map GeoJSON /bangkok-citywalk/photos/<slug>-N.jpg paths to HTTP URLs
    const photos = (p.photos || [])
      .map(photoPath => {
        // Extract filename from path like /bangkok-citywalk/photos/slug-1.jpg
        const filename = photoPath.split('/').pop();
        const filePath = path.join(PHOTOS_DIR, filename);
        return fs.existsSync(filePath) ? `${photoBaseUrl}/${filename}` : null;
      })
      .filter(Boolean);
    return {
      name: p.name,
      order: p.order,
      photos,
      coordinates: f.geometry.coordinates,
    };
  });

  let routeSegments = [];
  if (routeFeature) {
    const allCoords = routeFeature.geometry.coordinates;
    const breaks = routeFeature.properties.segment_breaks;
    for (let i = 0; i < breaks.length - 1; i++) {
      const start = breaks[i];
      const end = (i + 1 < breaks.length) ? breaks[i + 1] : allCoords.length - 1;
      routeSegments.push({coords: allCoords.slice(start, end + 1)});
    }
  }

  return {slides, routeSegments};
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  fs.mkdirSync(OUTPUT_DIR, {recursive: true});

  if (!fs.existsSync(GEOJSON)) {
    console.error(`✗ ${GEOJSON} not found — run generate.py first`);
    process.exit(1);
  }


  // Serve photos over HTTP so Remotion's Chromium can load them
  const {server: photoServer, port: photoPort} = await startPhotoServer(PHOTOS_DIR);
  const photoBaseUrl = `http://127.0.0.1:${photoPort}`;

  const {slides, routeSegments} = parseGeoJSON(photoBaseUrl);
  console.log(`${slides.length} POI slides, ${routeSegments.length} route segments`);

  const inputProps = {
    slides,
    route: routeSegments,
    introDur: INTRO_DUR,
    outroDur: OUTRO_DUR,
    slideDur: SLIDE_DUR,
  };

  const totalSec = INTRO_DUR + slides.length * SLIDE_DUR + OUTRO_DUR;
  console.log(`Total duration: ${totalSec.toFixed(0)}s (${(totalSec/60).toFixed(1)} min) @ 30fps`);

  console.log('\nBundling Remotion composition…');
  const serveUrl = await bundle({entryPoint: ENTRY_POINT});

  console.log('Selecting composition…');
  const composition = await selectComposition({
    serveUrl,
    id: 'BangkokCityWalk',
    inputProps,
  });

  console.log(`\nRendering → ${OUTPUT_MP4}`);
  await renderMedia({
    composition,
    serveUrl,
    codec: 'h264',
    outputLocation: OUTPUT_MP4,
    inputProps,
    chromiumOptions: {gl: 'swangle'},
    videoBitrate: '8M',
    onProgress: ({progress}) => {
      process.stdout.write(`  ${Math.round(progress * 100)}%\r`);
    },
  });

  photoServer.close();
  console.log(`\n✓ ${OUTPUT_MP4}`);
}

main().catch(err => { console.error(err); process.exit(1); });
