# scripts/raco

Raw RA.co (Resident Advisor) data collection. No visual output — feeds
[`scripts/bangkok-raco`](../bangkok-raco/README.md).

## Scripts

- `fetch_ra_events.js` — GraphQL area scanner: scans a range of RA area IDs,
  fetches all events for active areas, saves per-area JSON under `data/`.
  Confirmed event fields come from `.field-probe-state.json` (regenerate with
  `--probe`).
- `event_listener.js` — Playwright network sniffer (non-headless) that watches
  ra.co traffic for a target string; used to discover API fields.

## Run

```bash
node scripts/raco/fetch_ra_events.js           # scan areas, save JSON
node scripts/raco/fetch_ra_events.js --probe   # regenerate field probe state
node scripts/raco/event_listener.js            # interactive API exploration
```

**Output:** `scripts/raco/data/*.json`, `scripts/raco/schema-summary.json`
