# /airbnb/{id}/ — Airbnb Host Maps

This document explains the three-page flow added for Airbnb hosts.

## Pages

| URL | Layout | What it does |
|-----|--------|-------------|
| `/airbnb/{id}/` | `layouts/airbnb/single.html` | Shows the live map if `static/airbnb/{id}/locations.geojson` exists; otherwise shows the "buy" CTA |
| `/airbnb/{id}/edit/` | `layouts/airbnb/edit.html` | Full in-browser POI editor (static, no backend) |

---

## How Hugo routing works here

Hugo doesn't support dynamic URL segments (`/airbnb/:id/`) natively.
The workaround: **one content file per Airbnb listing**, where the filename becomes the ID.

```
content/airbnb/
  1612148974271274765.md    → /airbnb/1612148974271274765/
  1612148974271274765/
    edit.md                 → /airbnb/1612148974271274765/edit/
```

Both files share the same front matter pattern:

**`content/airbnb/{id}.md`**
```yaml
---
title: "Airbnb Map — {description}"
layout: "single"
---
```

**`content/airbnb/{id}/edit.md`**
```yaml
---
title: "Edit Map — {description}"
layout: "edit"
---
```

The layouts use `{{ .File.BaseFileName }}` to read the Airbnb ID from the filename.

---

## Publishing a map for an Airbnb listing

### Step 1 — Generate the GeoJSON

Use the existing script:

```bash
uv run --script scripts/airbnb_env/airbnb_nearby.py \
  https://www.airbnb.fr/rooms/1612148974271274765 \
  --gmaps "https://www.google.com/maps?ll=4.588657,101.095776&z=16&..." \
  --radius 2000
```

Or export from the `/edit/` page editor directly.

### Step 2 — Place the GeoJSON file

```
static/airbnb/1612148974271274765/locations.geojson
```

The `single.html` layout fetches `/airbnb/{id}/locations.geojson` at load time.
If the fetch returns 200 → shows map.
If 404 or empty → shows the host CTA.

The GeoJSON should include a `metadata` block for the map title and Airbnb pin:

```json
{
  "type": "FeatureCollection",
  "metadata": {
    "airbnb_id": "1612148974271274765",
    "title": "Ipoh — Around the Airbnb",
    "lat": 4.5887,
    "lon": 101.0958
  },
  "features": [...]
}
```

### Step 3 — Create the Hugo content files

```bash
# Map page
echo '---
title: "Ipoh — Around the Airbnb"
layout: "single"
---' > content/airbnb/1612148974271274765.md

# Editor page
mkdir -p content/airbnb/1612148974271274765
echo '---
title: "Edit — Ipoh Airbnb Map"
layout: "edit"
---' > content/airbnb/1612148974271274765/edit.md
```

### Step 4 — Commit & push

```bash
git add static/airbnb/1612148974271274765/ content/airbnb/1612148974271274765*
git commit -m "airbnb: add map for listing 1612148974271274765"
git push origin main
```

GitHub Actions deploys automatically. The map is live at:
`https://maps.girard-davila.net/airbnb/1612148974271274765/`

---

## Editor features (`/edit/`)

- **Add spots** — click "+ Add a spot", then click the map to place it
- **Edit spots** — click any pin or list item, edit in the drawer, save
- **Delete spots** — open a spot, click 🗑 Delete
- **Filter** — by category pill or free-text search
- **Import** — load an existing GeoJSON file from disk
- **Export** — copy to clipboard or download as `.geojson` file
- **Auto-save** — edits are saved to `localStorage` so nothing is lost on refresh

The editor is fully static. No data leaves the browser until the host exports the GeoJSON and shares it (or you build a backend save endpoint later).

---

## CTA page (fallback)

When `/airbnb/{id}/locations.geojson` returns 404, the visitor sees:
- Headline: "Give your guests a neighbourhood map they'll actually use"
- Features grid (practical spots, local favourites, GPS-precise, editable)
- 4-step "how it works" flow
- CTA button → `/#contact-form-float` (your existing contact form)
- The detected listing ID displayed as a pill (so you know which listing they came from)

To share this with a host before their map is built:
`https://maps.girard-davila.net/airbnb/THEIR_LISTING_ID/`
(no files needed — the fallback shows automatically)
