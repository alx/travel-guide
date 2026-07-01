#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import json
import urllib.request
from pathlib import Path

STOPS = [
    {
        "name": "Marwin Car & Motorbike Rental",
        "coords": [100.0498894, 9.4712162],
        "category": "Rental",
        "icon": "🛵",
        "leg": 1,
        "notes": "Taxi pickup — contact Arm, +66 86 952 3076, 600 THB fixed price to Nathon Pier.",
        "maps_url": "https://maps.app.goo.gl/AszEQ2SbBwrTs2JZ7",
    },
    {
        "name": "Nathon Pier",
        "coords": [99.9267, 9.5352],
        "category": "Pier",
        "icon": "⛴️",
        "leg": 2,
        "notes": "Seatran Ferry — every hour. Preferred: 6pm boat (sunset ~6:30–7pm on the water).",
        "maps_url": None,
    },
    {
        "name": "Donsak Pier",
        "coords": [99.73559450995812, 9.32026505379197],
        "category": "Pier",
        "icon": "⛴️",
        "leg": 3,
        "notes": "Seatran arrival on mainland. Bus time depends on ferry — 8:15pm for the 6pm ferry.",
        "maps_url": "https://maps.app.goo.gl/LEmgcFwwkdgZgF3u8",
    },
    {
        "name": "Surat Thani Bus Station",
        "coords": [99.3238638, 9.1397503],
        "category": "Bus",
        "icon": "🚌",
        "leg": 4,
        "notes": "Combined Seatran boat+bus ticket 380 THB. Journey ~70 min from Donsak.",
        "maps_url": "https://maps.app.goo.gl/REK9PTFKWhYPmaw66",
    },
    {
        "name": "Phunphin Train Station",
        "coords": [99.23048, 9.104137],
        "category": "Train",
        "icon": "🚂",
        "leg": 4,
        "notes": "Grab from bus station ~160 THB, ~20 min. Hotel: พุนพินสเตชั่น Phunphin Station.",
        "maps_url": "https://maps.app.goo.gl/7SthmuDgo2jXxaVN9",
    },
    {
        "name": "Uplift Station Dispensary",
        "coords": [99.2307289, 9.1050365],
        "category": "Dispensary",
        "icon": "🌿",
        "leg": 4,
        "notes": "Next to the train station.",
        "maps_url": "https://maps.app.goo.gl/8q7h7jggwGkBnvAq9",
    },
]

LEGS = [
    {
        "leg": 1,
        "mode": "Taxi",
        "icon": "🚕",
        "color": "#f59e0b",
        "cost_thb": 600,
        "duration": "~30 min",
        "notes": "Arm Taxi — fixed price 600 THB. Call: +66 62 978 3966. Confirm in advance.",
        "routing": "osrm",
        "from_idx": 0,
        "to_idx": 1,
    },
    {
        "leg": 2,
        "mode": "Ferry",
        "icon": "⛴️",
        "color": "#0ea5e9",
        "cost_thb": 380,
        "duration": "~90 min",
        "notes": "Seatran Ferry — combined boat+bus ticket 380 THB. Every hour. Preferred: 6pm boat.",
        "website": "https://www.seatranferry.com/",
        "routing": "straight",
        "from_idx": 1,
        "to_idx": 2,
    },
    {
        "leg": 3,
        "mode": "Bus",
        "icon": "🚌",
        "color": "#22c55e",
        "cost_thb": 0,
        "duration": "~70 min",
        "notes": "Included in Seatran combined ticket. Departs Donsak 8:15pm.",
        "routing": "osrm",
        "from_idx": 2,
        "to_idx": 3,
    },
    {
        "leg": 4,
        "mode": "Grab",
        "icon": "🚗",
        "color": "#8b5cf6",
        "cost_thb": 160,
        "duration": "~20 min",
        "notes": "Grab taxi from bus station to Phunphin train station.",
        "routing": "osrm",
        "from_idx": 3,
        "to_idx": 4,
    },
]


def osrm_route(start_coords, end_coords):
    """Fetch driving route from OSRM. Coords are [lng, lat]."""
    lng1, lat1 = start_coords
    lng2, lat2 = end_coords
    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{lng1},{lat1};{lng2},{lat2}"
        f"?overview=full&geometries=geojson"
    )
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read())
    return data["routes"][0]["geometry"]["coordinates"]


features = []

for i, stop in enumerate(STOPS):
    features.append({
        "type": "Feature",
        "id": f"surat-thani-koh-samui-transit-stop-{i + 1:02d}",
        "geometry": {"type": "Point", "coordinates": stop["coords"]},
        "properties": {
            "feature_type": "stop",
            "name": stop["name"],
            "category": stop["category"],
            "icon": stop["icon"],
            "leg": stop["leg"],
            "notes": stop["notes"],
            "maps_url": stop["maps_url"],
        },
    })

for leg in LEGS:
    start = STOPS[leg["from_idx"]]["coords"]
    end = STOPS[leg["to_idx"]]["coords"]
    if leg["routing"] == "osrm":
        print(f"Leg {leg['leg']}: fetching OSRM route {STOPS[leg['from_idx']]['name']} → {STOPS[leg['to_idx']]['name']}…")
        coords = osrm_route(start, end)
    else:
        coords = [start, end]
    features.append({
        "type": "Feature",
        "id": f"surat-thani-koh-samui-transit-leg-{leg['leg']:02d}",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "feature_type": "leg",
            "leg": leg["leg"],
            "mode": leg["mode"],
            "icon": leg["icon"],
            "color": leg["color"],
            "cost_thb": leg["cost_thb"],
            "duration": leg["duration"],
            "notes": leg["notes"],
            "website": leg.get("website"),
        },
    })

geojson = {"type": "FeatureCollection", "features": features}
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
out_path = REPO_ROOT / "static" / "surat-thani-koh-samui-transit" / "locations.geojson"
out_path.parent.mkdir(parents=True, exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(geojson, f, ensure_ascii=False, indent=2)

print(f"Wrote {len(features)} features ({len(STOPS)} stops + {len(LEGS)} legs) to {out_path}")
