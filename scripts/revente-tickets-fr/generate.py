#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""
Generate GeoJSON for the revente-tickets-fr map from SQLite state.
Called by telegram_bot.py after each listing is confirmed or marked sold.
Can also be run standalone to rebuild the GeoJSON from current state.
"""
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "revente-tickets-fr" / "state.db"
GEOJSON_PATH = REPO_ROOT / "static" / "revente-tickets-fr" / "locations.geojson"


def generate(db_path: Path = DB_PATH, geojson_path: Path = GEOJSON_PATH) -> int:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT * FROM listings
           WHERE status IN ('available', 'sold')
             AND lat IS NOT NULL AND lon IS NOT NULL
           ORDER BY
             CASE status WHEN 'available' THEN 0 ELSE 1 END,
             created_utc DESC"""
    ).fetchall()
    con.close()

    features = []
    for row in rows:
        fid = f"revente-tickets-fr-{row['id']}"
        artist = row["artist"] or ""
        venue = row["venue"] or ""
        name = f"{artist} @ {venue}" if artist and venue else venue or row["title"][:60]

        price_label = f"{int(row['price_each'])}€/place" if row["price_each"] else ""
        tickets_label = f"{row['tickets']}×" if row["tickets"] else ""

        youtube_url = ""
        if row["youtube_video_id"]:
            youtube_url = f"https://www.youtube.com/watch?v={row['youtube_video_id']}"
        elif row["youtube_search_url"]:
            youtube_url = row["youtube_search_url"]

        features.append({
            "type": "Feature",
            "id": fid,
            "geometry": {
                "type": "Point",
                "coordinates": [round(row["lon"], 6), round(row["lat"], 6)],
            },
            "properties": {
                "id": fid,
                "name": name,
                "venue": venue,
                "artist": artist,
                "event_date": row["event_date"] or "",
                "tickets": row["tickets"],
                "price_each": row["price_each"],
                "seat_category": row["seat_category"] or "",
                "status": row["status"],
                "reddit_url": row["reddit_url"],
                "youtube_video_id": row["youtube_video_id"] or "",
                "youtube_url": youtube_url,
                "icon": "🎟️",
                "category": "Disponible" if row["status"] == "available" else "Vendu",
                "price_label": price_label,
                "tickets_label": tickets_label,
                "processed_at": row["processed_at"] or "",
            },
        })

    geojson = {
        "type": "FeatureCollection",
        "_meta": {
            "crs": "EPSG:4326",
            "generated": datetime.now(timezone.utc).isoformat(),
            "source": "r/ReventeTicketsFR — VENTE flair",
        },
        "features": features,
    }

    geojson_path.parent.mkdir(parents=True, exist_ok=True)
    geojson_path.write_text(json.dumps(geojson, ensure_ascii=False, indent=2))
    return len(features)


def git_push_and_deploy(repo_root: Path = REPO_ROOT) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    steps = [
        ["git", "add", "static/revente-tickets-fr/locations.geojson"],
        ["git", "commit", "-m", f"chore(revente-tickets-fr): update listings — {ts}"],
        ["git", "push"],
    ]
    for cmd in steps:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=repo_root)
        if result.returncode != 0:
            if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                return
            raise RuntimeError(f"`{' '.join(cmd)}` failed:\n{result.stderr}")
    # Trigger Hugo deploy
    subprocess.run(
        ["gh", "workflow", "run", "deploy.yml"],
        capture_output=True, text=True, cwd=repo_root,
    )


if __name__ == "__main__":
    n = generate()
    print(f"✅ {n} features → {GEOJSON_PATH}")
    if "--push" in sys.argv:
        git_push_and_deploy()
        print("✅ Pushed and deploy dispatched.")
