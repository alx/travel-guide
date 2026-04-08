# Travel Guide

A Hugo-powered travel guide with interactive maps, starting with Koh Samui.

Live site: https://alx.github.io/travel-guide/

## Adding Locations to the Map

Locations are stored in `static/koh-samui/locations.geojson` as GeoJSON features.

### Adding a location via the map UI

1. Open the Koh Samui page
2. Click **+ Add a location** in the sidebar
3. Fill in the name, category, and coordinates (paste a Google Maps URL to auto-extract coordinates)
4. Copy the generated JSON from the output box

### Adding a location to the GeoJSON file

Paste the copied JSON into `static/koh-samui/locations.geojson` inside the `"features"` array:

```json
{
  "type": "Feature",
  "properties": {
    "name": "My Place",
    "category": "Restaurant",
    "address": "123 Beach Road, Lamai, Koh Samui",
    "notes": "Great pad thai, open from 11am–10pm"
  },
  "geometry": {
    "type": "Point",
    "coordinates": [100.0529, 9.4747]
  }
}
```

**Coordinates format:** `[longitude, latitude]` (GeoJSON standard)

**Available categories:** `Beach`, `Restaurant`, `Activity`, `Hotel`, `Shopping`, `Kids`, `Other`

### Getting coordinates from Google Maps

1. Open Google Maps and find the location
2. Click the location — the URL will contain `@lat,lng,zoom`
3. Or paste the Google Maps URL into the "Add a location" modal — it extracts coordinates automatically

## Development

```bash
# Install Hugo (v0.147.0+)
# https://gohugo.io/installation/

# Run local dev server
hugo server --buildDrafts

# Build for production
hugo --minify
```

## Deploy

Pushes to `main` automatically deploy via GitHub Actions to GitHub Pages.

## Contributing

1. Fork the repo
2. Add your locations to `static/koh-samui/locations.geojson`
3. Open a pull request

All contributions welcome — especially local tips, hidden gems, and family-friendly spots.
