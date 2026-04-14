/**
 * Cloudflare Worker — GitHub Device Flow CORS proxy
 * deploy: npx wrangler deploy workers/github-proxy.js --name travels-github-proxy
 *
 * Endpoints:
 *   POST /github-device-code   → proxy to github.com/login/device/code
 *   POST /github-device-token  → proxy to github.com/login/oauth/access_token
 *   OPTIONS *                  → CORS preflight
 *
 * Required wrangler.toml vars:
 *   GITHUB_CLIENT_ID = "Ov23li..."   # from GitHub OAuth App settings
 *
 * The GitHub Device Flow does NOT require a client_secret for the token exchange,
 * so this worker only needs the public client_id.
 *
 * GitHub OAuth App setup:
 *   1. Go to https://github.com/settings/developers → OAuth Apps → New OAuth App
 *   2. Application name: "Girard-Davila Travels"
 *   3. Homepage URL: https://maps.girard-davila.net
 *   4. Authorization callback URL: (not used for device flow — put homepage URL)
 *   5. Copy the Client ID into GITHUB_CLIENT_ID in wrangler.toml
 */

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': 'https://maps.girard-davila.net',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Access-Control-Max-Age': '86400',
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // ── POST /github-device-code ──────────────────────────────────────────
    if (request.method === 'POST' && url.pathname === '/github-device-code') {
      const body = await request.json().catch(() => ({}));
      const clientId = env.GITHUB_CLIENT_ID || body.client_id;
      if (!clientId) {
        return jsonResponse({ error: 'GITHUB_CLIENT_ID not configured' }, 500);
      }

      const ghRes = await fetch('https://github.com/login/device/code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ client_id: clientId, scope: body.scope || 'repo read:user' }),
      });

      const data = await ghRes.json();
      return jsonResponse(data);
    }

    // ── POST /github-device-token ─────────────────────────────────────────
    if (request.method === 'POST' && url.pathname === '/github-device-token') {
      const body = await request.json().catch(() => ({}));
      const clientId = env.GITHUB_CLIENT_ID || body.client_id;
      if (!clientId) {
        return jsonResponse({ error: 'GITHUB_CLIENT_ID not configured' }, 500);
      }

      const ghRes = await fetch('https://github.com/login/oauth/access_token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          client_id: clientId,
          device_code: body.device_code,
          grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
        }),
      });

      const data = await ghRes.json();
      return jsonResponse(data);
    }

    // ── GET /osm-tile/{z}/{x}/{y} ─────────────────────────────────────────
    // CORS proxy for OSM tiles: the browser blocks canvas.toDataURL() when
    // tiles are drawn from tile.openstreetmap.org (no CORS headers). This
    // endpoint fetches the tile server-side and re-serves with CORS headers,
    // enabling the PNG share-card export to use the same OSM style as the site.
    if (request.method === 'GET' && url.pathname.startsWith('/osm-tile/')) {
      const match = url.pathname.match(/^\/osm-tile\/(\d+)\/(\d+)\/(\d+)$/);
      if (!match) return new Response('Bad request', { status: 400, headers: CORS_HEADERS });
      const [, z, x, y] = match;

      // Round-robin between OSM subdomains (a/b/c) to spread load
      const subs = ['a', 'b', 'c'];
      const sub  = subs[(parseInt(x) + parseInt(y)) % 3];
      const osmUrl = `https://${sub}.tile.openstreetmap.org/${z}/${x}/${y}.png`;

      const osmRes = await fetch(osmUrl, {
        headers: {
          'User-Agent': 'maps.girard-davila.net map-export/1.0 (share card PNG)',
          'Referer':    'https://maps.girard-davila.net',
        },
      });
      if (!osmRes.ok) return new Response('Tile error', { status: osmRes.status, headers: CORS_HEADERS });

      const body = await osmRes.arrayBuffer();
      return new Response(body, {
        status: 200,
        headers: {
          'Content-Type':                'image/png',
          'Cache-Control':               'public, max-age=86400',
          'Access-Control-Allow-Origin': '*',
        },
      });
    }

    return new Response('Not found', { status: 404, headers: CORS_HEADERS });
  },
};

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
  });
}
