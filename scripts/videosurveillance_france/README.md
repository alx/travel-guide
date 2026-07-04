# scripts/videosurveillance_france

<img src="../../docs/screenshots/videosurveillance_france.png" width="600" alt="Videosurveillance France map preview">

Fetches surveillance cameras and speed radars across France from OpenStreetMap
(Overpass API) and outputs GeoJSON.

## Run

```bash
uv run scripts/videosurveillance_france/fetch_videosurveillance_france.py > static/videosurveillance-france/locations.geojson
```

No external dependencies (stdlib only). Also run automatically by
`scripts/fetch_all.sh`.

**Output:** `static/videosurveillance-france/locations.geojson`
**Live map:** https://maps.girard-davila.net/videosurveillance-france/
