#!/usr/bin/env python3
"""
Convert locations.geojson → locations.min.json (columnar compact format).

Strips redundant fields (coord_source, coord_accuracy, icon, feature id),
encodes categories as integers, reduces coordinate precision to 5 dp, and
stores addresses as a sparse dict. Columnar arrays compress ~10:1 with gzip.

Run from repo root:
    python3 scripts/compact-geojson.py [dataset]

Where dataset is the directory name under static/, defaults to
videosurveillance-france.
"""
import json
import sys
import os

dataset = sys.argv[1] if len(sys.argv) > 1 else "videosurveillance-france"
src = os.path.join("static", dataset, "locations.geojson")
dst = os.path.join("static", dataset, "locations.min.json")

print(f"Reading {src} …", flush=True)
with open(src, encoding="utf-8") as f:
    data = json.load(f)

features = data.get("features", [])
print(f"  {len(features)} features", flush=True)

# Build ordered category list from actual data (preserves insertion order)
seen_cats = {}
for feat in features:
    cat = feat["properties"].get("category", "Other")
    if cat not in seen_cats:
        seen_cats[cat] = len(seen_cats)
cats = list(seen_cats.keys())
cat_idx = {c: i for i, c in enumerate(cats)}

lons, lats, cat_arr, pid_arr, name_arr = [], [], [], [], []
addr = {}

for i, feat in enumerate(features):
    props = feat["properties"]
    coords = feat["geometry"]["coordinates"]
    lons.append(round(coords[0], 5))
    lats.append(round(coords[1], 5))
    cat_arr.append(cat_idx.get(props.get("category", "Other"), 0))
    # Strip "osm:" prefix — re-added client-side
    pid = props.get("place_id", "").replace("osm:", "")
    pid_arr.append(pid)
    name_arr.append(props.get("name", ""))
    a = props.get("address")
    if a:
        addr[str(i)] = a

out = {
    "v": 1,
    "cats": cats,
    "lons": lons,
    "lats": lats,
    "cat": cat_arr,
    "pid": pid_arr,
    "name": name_arr,
    "addr": addr,
}

print(f"Writing {dst} …", flush=True)
with open(dst, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

size_mb = os.path.getsize(dst) / 1_048_576
print(f"Done. Output: {size_mb:.1f} MB")
print(f"  Categories: {cats}")
print(f"  Features with address: {len(addr)}")
