# scripts/raco

Raw RA.co (Resident Advisor) data collection. No visual output — feeds
[`scripts/bangkok-raco`](../bangkok-raco/README.md).

## Scripts

- `fetch_ra_events.js` — hardcoded GraphQL fetch of RA.co's `GET_EVENT_LISTINGS`
  query for a single area (area ID `453`, Koh Samui), filtered to listings
  from `2026-06-14` onward. Fetches page 1 and page 2 (20 results per page)
  and prints each event's date, title, and venue to the console.
- `event_listener.js` — Playwright network sniffer (non-headless) that watches
  ra.co traffic for a target string; used to discover API fields.

## Run

```bash
node scripts/raco/fetch_ra_events.js           # fetch Koh Samui events, print to console
node scripts/raco/event_listener.js            # interactive API exploration
```

**Output:** none — `fetch_ra_events.js` prints results to stdout.
