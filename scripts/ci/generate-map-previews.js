#!/usr/bin/env node
/**
 * scripts/ci/generate-map-previews.js
 *
 * Called by .github/workflows/map-preview.yml after `hugo --minify` + `serve public`.
 *
 * Usage:
 *   node scripts/ci/generate-map-previews.js <slug1> [slug2] ...
 *
 * Environment variables:
 *   PREVIEW_BASE_URL  Base URL of the served Hugo site (default: http://localhost:1414)
 *
 * Output:
 *   .github/previews/<slug>.png   — 1200×630 screenshot of the map in embed mode
 *
 * The screenshots are committed back to the PR branch by the CI workflow and
 * referenced via raw.githubusercontent.com in the PR comment.
 * The same files are also used as og:image by the Hugo partial
 * layouts/partials/og-image.html — which reads from .github/previews/ at build time.
 */

const { chromium } = require('playwright');
const fs   = require('fs');
const path = require('path');

const BASE_URL   = process.env.PREVIEW_BASE_URL || 'http://localhost:1414';
const OUT_DIR    = path.resolve('.github/previews');
const OG_WIDTH   = 1200;
const OG_HEIGHT  = 630;
// Extra ms to wait after networkidle — lets Leaflet finish rendering tiles + markers
const TILE_WAIT  = 3000;

// Slugs passed as CLI arguments
const slugs = process.argv.slice(2).filter(Boolean);

if (slugs.length === 0) {
  console.error('No slugs provided. Usage: node generate-map-previews.js <slug1> [slug2] ...');
  process.exit(1);
}

fs.mkdirSync(OUT_DIR, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  let exitCode = 0;

  for (const slug of slugs) {
    // Normalise: airbnb listings live at /airbnb/<id>/
    // but their slug in the changed-files list is "airbnb" (the section folder).
    // The workflow passes the directory name under static/ so we build the URL
    // accordingly.
    let urlPath;
    if (slug.startsWith('airbnb/')) {
      // e.g. static/airbnb/1612148974271274765/locations.geojson → slug "airbnb/1612..."
      urlPath = `/${slug}/`;
    } else {
      urlPath = `/${slug}/`;
    }

    const url    = `${BASE_URL}${urlPath}?embed=1`;
    const outFile = path.join(OUT_DIR, `${slug.replace(/\//g, '-')}.png`);

    console.log(`📸  ${slug}`);
    console.log(`    URL : ${url}`);
    console.log(`    OUT : ${outFile}`);

    const page = await browser.newPage();

    try {
      await page.setViewportSize({ width: OG_WIDTH, height: OG_HEIGHT });

      // Some map pages redirect if the GeoJSON is missing (airbnb fallback CTA).
      // We still want a screenshot of whatever renders.
      await page.goto(url, {
        waitUntil: 'networkidle',
        timeout: 45_000,
      });

      // Wait for Leaflet tiles to paint — networkidle fires before tiles finish.
      await page.waitForTimeout(TILE_WAIT);

      // Optionally wait for a Leaflet-specific element to confirm map rendered.
      // Fail gracefully if it never appears (e.g. fallback CTA page).
      try {
        await page.waitForSelector('.leaflet-tile-loaded', { timeout: 8_000 });
        // One more beat after first tile loaded
        await page.waitForTimeout(1_000);
      } catch {
        console.log(`    ⚠  No Leaflet tiles detected — screenshotting as-is`);
      }

      await page.screenshot({ path: outFile, type: 'png' });
      console.log(`    ✓ saved`);

    } catch (err) {
      console.error(`    ✗ FAILED: ${err.message}`);
      exitCode = 1;
    } finally {
      await page.close();
    }
  }

  await browser.close();
  process.exit(exitCode);
})();
