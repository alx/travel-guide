#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""
Initialize the revente-tickets-fr SQLite database.
Run once on lamai270 before starting the pipeline.
"""
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "revente-tickets-fr" / "state.db"

DDL = """
CREATE TABLE IF NOT EXISTS listings (
    id                           TEXT PRIMARY KEY,
    reddit_url                   TEXT NOT NULL,
    title                        TEXT NOT NULL,
    selftext                     TEXT,
    created_utc                  INTEGER NOT NULL,
    status                       TEXT NOT NULL DEFAULT 'pending',
    venue                        TEXT,
    artist                       TEXT,
    event_date                   TEXT,
    tickets                      INTEGER,
    price_each                   REAL,
    seat_category                TEXT,
    lat                          REAL,
    lon                          REAL,
    geocode_source               TEXT,
    youtube_video_id             TEXT,
    youtube_search_url           TEXT,
    telegram_notification_msg_id INTEGER,
    telegram_confirm_msg_id      INTEGER,
    telegram_sold_check_msg_id   INTEGER,
    pending_structured_json      TEXT,
    processed_at                 TEXT,
    sold_at                      TEXT
);
"""


def init(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(DDL)
    con.commit()
    return con


if __name__ == "__main__":
    init()
    print(f"✅ Database initialized at {DB_PATH}")
