#!/usr/bin/env node
/**
 * generate-map-previews.js
 *
 * Run after `hugo --minify` to screenshot each map page and save the result
 * to public/images/map-previews/<name>.png so the share-card PNG export can
 * embed a real map without fighting browser CORS restrictions.
 *
 * Usage (called by CI):
 *   npx serve public -l 1414 &
 *   node scripts/generate-map-previews.js
 *
 * Requires: @playwright/test (installed as devDependency via package.json)
 */

const { chromium } = require('playwright');
const path = require('path');
const fs   = require('fs');

const BASE_URL = process.env.PREVIEW_BASE_URL || 'http://localhost:1414';

/** Each map: the route path + the filename for the saved screenshot */
const MAPS = [
  { path: '/koh-samui/', name: 'koh-samui',    waitFor: '#map .leaflet-tile-loaded' },
  { path: '/hoi-an/',    name: 'hoi-an',        waitFor: '#map .leaflet-tile-loaded' },
  { path: '/fishing-boat/', name: 'fishing-boat', waitFor: '#map .leaflet-overlay-pane path' },
];

const OUT_DIR = path.join(__dirname, '..', 'public', 'images', 'map-previews');

async function screenshotMap(page, mapPath, name, waitFor) {
  console.log(`  → ${name}: navigating to ${BASE_URL}${mapPath}`);
  await page.goto(`${BASE_URL}${mapPath}`, { waitUntil: 'domcontentloaded', timeout: 30_000 });

  // Wait for the map container
  await page.waitForSelector('#map', { timeout: 15_000 });

  // Wait for tile images / vector paths to appear
  try {
    await page.waitForSelector(waitFor, { timeout: 12_000 });
  } catch {
    console.warn(`    ⚠ Tile selector "${waitFor}" not found within timeout — using fixed delay`);
  }

  // Extra settle time so tiles are painted
  await page.waitForTimeout(2500);

  const mapEl = await page.$('#map');
  if (!mapEl) throw new Error(`#map not found on ${mapPath}`);

  const outFile = path.join(OUT_DIR, `${name}.png`);
  await mapEl.screenshot({ path: outFile, type: 'png' });
  console.log(`  ✓ saved ${path.relative(process.cwd(), outFile)}`);
}

(async () => {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  console.log(`\n🗺  Generating map previews → ${OUT_DIR}\n`);

  const browser = await chromium.launch({
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  // Viewport matches the #map CSS (full-screen for embed-style pages)
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });

  for (const { path: mapPath, name, waitFor } of MAPS) {
    try {
      await screenshotMap(page, mapPath, name, waitFor);
    } catch (err) {
      console.error(`  ✗ ${name}: ${err.message}`);
      // Non-fatal — continue with remaining maps
    }
  }

  await browser.close();
  console.log('\n✅ Done.\n');
})();
