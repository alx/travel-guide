#!/usr/bin/env node
/**
 * generate-map-previews.js
 *
 * Screenshots Leaflet map pages and saves PNG previews.
 *
 * Two modes:
 *
 *   1. CI / PR preview — pass slug(s) as CLI args:
 *        node scripts/generate-map-previews.js 711-samui roadtrip-phuket-krabi-koh-yao-yai-phuket
 *      Saves to .github/previews/<slug>.png (committed to the PR branch)
 *
 *   2. Full build — no args:
 *        npx serve public -l 1414 &
 *        node scripts/generate-map-previews.js
 *      Screenshots all known maps → public/images/map-previews/<name>.png
 *
 * Env:
 *   PREVIEW_BASE_URL  default: http://localhost:1414
 *
 * Requires: playwright (devDependency in package.json)
 *   npm ci && npx playwright install chromium --with-deps
 */

const { chromium } = require('playwright');
const path = require('path');
const fs   = require('fs');

const BASE_URL = process.env.PREVIEW_BASE_URL || 'http://localhost:1414';
const ROOT     = path.join(__dirname, '..');

/** Known maps — used in full-build mode (no CLI args) */
const KNOWN_MAPS = [
  { slug: 'koh-samui',    waitFor: '.leaflet-marker-icon' },
  { slug: 'lamai',        waitFor: '.leaflet-marker-icon' },
  { slug: 'hoi-an',       waitFor: '.leaflet-marker-icon' },
  { slug: 'fishing-boat', waitFor: '#map .leaflet-overlay-pane path' },
  { slug: '711-samui',    waitFor: '.leaflet-marker-icon' },
];

/** Infer the best wait-for selector from the slug */
function waitSelector(slug) {
  if (slug === 'fishing-boat') return '#map .leaflet-overlay-pane path';
  return '.leaflet-marker-icon';
}

async function screenshotMap(page, slug, outFile) {
  const url     = `${BASE_URL}/${slug}/`;
  const waitFor = waitSelector(slug);

  console.log(`  → ${slug}: ${url}`);
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.waitForSelector('#map', { timeout: 15_000 });

  try {
    await page.waitForSelector(waitFor, { timeout: 14_000 });
  } catch {
    console.warn(`    ⚠ "${waitFor}" not found within timeout — using fixed delay`);
  }

  // Let tiles settle
  await page.waitForTimeout(2500);

  const mapEl = await page.$('#map');
  if (!mapEl) throw new Error(`#map not found on ${url}`);

  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  await mapEl.screenshot({ path: outFile, type: 'png' });
  console.log(`  ✓ saved ${path.relative(process.cwd(), outFile)}`);
  return outFile;
}

(async () => {
  const cliSlugs = process.argv.slice(2).filter(Boolean);
  const ciMode   = cliSlugs.length > 0;

  // Determine which maps to screenshot and where to save
  const jobs = ciMode
    ? cliSlugs.map(slug => ({
        slug,
        outFile: path.join(ROOT, '.github', 'previews', `${slug}.png`),
      }))
    : KNOWN_MAPS.map(({ slug }) => ({
        slug,
        outFile: path.join(ROOT, 'public', 'images', 'map-previews', `${slug}.png`),
      }));

  if (jobs.length === 0) {
    console.log('Nothing to screenshot.');
    process.exit(0);
  }

  const mode = ciMode ? `PR preview (${cliSlugs.join(', ')})` : 'full build';
  console.log(`\n🗺  Generating map previews — ${mode}\n`);

  const browser = await chromium.launch({
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });

  const results = [];
  for (const { slug, outFile } of jobs) {
    try {
      await screenshotMap(page, slug, outFile);
      results.push({ slug, outFile, ok: true });
    } catch (err) {
      console.error(`  ✗ ${slug}: ${err.message}`);
      results.push({ slug, ok: false, error: err.message });
    }
  }

  await browser.close();

  const ok  = results.filter(r => r.ok).length;
  const bad = results.filter(r => !r.ok).length;
  console.log(`\n✅ Done — ${ok} screenshot(s) saved${bad ? `, ${bad} failed` : ''}.\n`);

  if (bad > 0) process.exit(1);
})();
