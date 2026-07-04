# scripts/airbnb_web

<img src="../../docs/screenshots/airbnb_web.png" width="600" alt="Airbnb neighbourhood map preview">

Flask web app that builds an Airbnb neighbourhood map through a wizard: paste
a listing URL, it scrapes the location, finds nearby family-friendly POIs
(Overpass), and generates the map page. Web-app sibling of the
[`scripts/airbnb_env`](../airbnb_env/README.md) CLI.

## Run

```bash
uv run scripts/airbnb_web/app.py                     # dev server

uv run gunicorn -k gthread --threads 4 --workers 1 \
    --bind 127.0.0.1:5010 'app:create_app()'         # prod
```

**Modules:** `app.py` (Flask app factory), `routes/wizard.py` (wizard flow),
`poi_engine.py` (Overpass POI search), `cache.py` + `cache/` (listing cache).

**Output:** `static/airbnb/<listing-id>/` map pages
**Live example:** https://maps.girard-davila.net/airbnb/
