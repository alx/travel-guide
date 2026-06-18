#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""
Initialize (or migrate) the GPS Newsletter SQLite state store.

Usage:
  uv run scripts/france_project_newsletter/init_db.py

Idempotent — safe to re-run; uses CREATE TABLE IF NOT EXISTS throughout.
"""

import pathlib
import sqlite3

DB_PATH = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter/state.db"


def init(db_path: pathlib.Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")

    con.executescript("""
        -- Cross-day deduplication: every fetched item hash is recorded here.
        CREATE TABLE IF NOT EXISTS seen_items (
            hash        TEXT PRIMARY KEY,
            first_seen  TEXT NOT NULL   -- ISO-8601 UTC
        );

        -- All news items ever fetched. AI classification fields are nullable
        -- and populated later by classify.py.
        CREATE TABLE IF NOT EXISTS news_items (
            hash                    TEXT PRIMARY KEY,
            company_id              TEXT NOT NULL,
            company                 TEXT NOT NULL,
            category                TEXT NOT NULL,
            region                  TEXT NOT NULL,
            title                   TEXT NOT NULL,
            summary                 TEXT NOT NULL DEFAULT '',
            url                     TEXT NOT NULL,
            date                    TEXT NOT NULL,   -- ISO-8601 UTC
            source                  TEXT NOT NULL,   -- company_rss | sector_rss | changedetection
            fetched_at              TEXT NOT NULL,   -- ISO-8601 UTC

            -- AI classification (NULL until classify.py runs)
            pending_classification  INTEGER NOT NULL DEFAULT 1,  -- 1=pending, 0=done
            relevant                INTEGER,         -- 1=relevant, 0=filtered out
            signal_type             TEXT,            -- funding_round | groundbreaking | production_start |
                                                     -- delay | regulatory | partnership | M&A | other
            classified_at           TEXT,            -- ISO-8601 UTC
            title_fr                TEXT,            -- phase 2: AI-translated French title
            summary_fr              TEXT,            -- phase 2: AI-written French summary
            title_en                TEXT,            -- phase 2: AI-translated English title
            summary_en              TEXT             -- phase 2: AI-written English summary
        );

        CREATE INDEX IF NOT EXISTS idx_news_pending ON news_items (pending_classification)
            WHERE pending_classification = 1;
        CREATE INDEX IF NOT EXISTS idx_news_company ON news_items (company_id);
        CREATE INDEX IF NOT EXISTS idx_news_date    ON news_items (date DESC);
        CREATE INDEX IF NOT EXISTS idx_news_signal  ON news_items (signal_type)
            WHERE signal_type IS NOT NULL;

        -- Finance model: approved extractions that have been applied to the ledger.
        CREATE TABLE IF NOT EXISTS finance_ledger (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id      TEXT    NOT NULL,
            axis            TEXT    NOT NULL,  -- capex | subsidy | employment | stage
            value_num       REAL,              -- numeric value (EUR for capex/subsidy, count for jobs)
            value_text      TEXT,              -- text value (stage name, currency note)
            source_hash     TEXT,              -- news_items.hash that produced this entry (NULL for authoritative sources)
            source_label    TEXT,              -- e.g. "France 2030", "Pappers", "AI extraction"
            event_date      TEXT,              -- ISO-8601 date of the underlying event
            recorded_at     TEXT    NOT NULL,  -- ISO-8601 UTC when inserted
            notes           TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_ledger_company ON finance_ledger (company_id);
        CREATE INDEX IF NOT EXISTS idx_ledger_axis    ON finance_ledger (company_id, axis);

        -- Finance extractions awaiting human approval via Telegram.
        CREATE TABLE IF NOT EXISTS finance_pending_review (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id      TEXT    NOT NULL,
            company         TEXT    NOT NULL,
            axis            TEXT    NOT NULL,  -- capex | subsidy | employment | stage
            value_num       REAL,
            value_text      TEXT,
            source_hash     TEXT    NOT NULL,  -- news_items.hash
            source_url      TEXT    NOT NULL,
            source_title    TEXT    NOT NULL,
            extracted_at    TEXT    NOT NULL,  -- ISO-8601 UTC
            telegram_msg_id INTEGER,           -- Telegram message ID of the review prompt
            status          TEXT    NOT NULL DEFAULT 'pending'  -- pending | approved | rejected
        );

        CREATE INDEX IF NOT EXISTS idx_review_status ON finance_pending_review (status)
            WHERE status = 'pending';

        -- Project stage state machine per company.
        -- Canonical stages: announced | permitted | under_construction | operational | delayed | cancelled
        CREATE TABLE IF NOT EXISTS project_state (
            company_id      TEXT    PRIMARY KEY,
            stage           TEXT    NOT NULL DEFAULT 'announced',
            updated_at      TEXT    NOT NULL,  -- ISO-8601 UTC
            source_hash     TEXT,              -- news_items.hash that triggered the last transition
            notes           TEXT
        );

        -- News source operational state (declaration half lives in GeoJSON / companies.json).
        -- Populated on fetch_digest.py startup by upserting from the declarative sources.
        -- changedetection.io UUIDs and runtime state live here, not in the GeoJSON.
        CREATE TABLE IF NOT EXISTS news_sources (
            company_id      TEXT    NOT NULL,
            type            TEXT    NOT NULL,   -- open enum: company_rss | sector_rss | changedetection |
                                               --   linkedin_company_rss | youtube_channel_rss |
                                               --   google_news_query_rss | bodacc_rss | …
            url             TEXT    NOT NULL,
            uuid            TEXT,              -- changedetection.io watch UUID (type=changedetection only)
            enabled         INTEGER NOT NULL DEFAULT 1,
            added_at        TEXT    NOT NULL,   -- ISO-8601 UTC, when declaration was first seen
            last_fetched_at TEXT,              -- ISO-8601 UTC
            last_error      TEXT,              -- last fetch error message, if any
            PRIMARY KEY (company_id, type, url)
        );

        CREATE INDEX IF NOT EXISTS idx_sources_company ON news_sources (company_id);
        CREATE INDEX IF NOT EXISTS idx_sources_type    ON news_sources (type);

        -- Pending source candidates awaiting human approval via Telegram.
        -- Auto-discovery scripts write here; approved rows become declarations.
        CREATE TABLE IF NOT EXISTS pending_sources (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id          TEXT    NOT NULL,
            project_id          TEXT,              -- GeoJSON feature id (project-level sources only)
            type                TEXT    NOT NULL,
            url                 TEXT    NOT NULL,
            candidate_payload   TEXT,              -- JSON: {title, snippet, favicon_url}
            discovered_by       TEXT    NOT NULL,  -- e.g. "discover_sources.py", "enrich_web.py"
            discovered_at       TEXT    NOT NULL,  -- ISO-8601 UTC
            decision            TEXT    NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
            decided_at          TEXT,
            telegram_msg_id     INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_pending_decision ON pending_sources (decision)
            WHERE decision = 'pending';
        CREATE INDEX IF NOT EXISTS idx_pending_company  ON pending_sources (company_id);

        -- Company enrichment from external registries (Pappers, France 2030, etc.)
        CREATE TABLE IF NOT EXISTS company_enrichment (
            company_id          TEXT    PRIMARY KEY,

            -- Pappers / SIRENE
            siren               TEXT,
            siret               TEXT,
            legal_form          TEXT,
            headcount_band      TEXT,   -- e.g. "50-99", "200-249"
            naf_code            TEXT,
            registered_address  TEXT,

            -- Pappers bilans (most recent filed year)
            bilan_year          INTEGER,
            revenue_eur         REAL,
            net_result_eur      REAL,
            equity_eur          REAL,
            total_assets_eur    REAL,

            -- France 2030
            france2030_project  TEXT,   -- official project label
            france2030_amount   REAL,   -- awarded grant in EUR
            france2030_call     TEXT,   -- call for projects label

            enriched_at         TEXT    NOT NULL,  -- ISO-8601 UTC of last enrichment run
            notes               TEXT
        );
    """)

    con.commit()
    con.close()
    print(f"State store ready: {db_path}")


if __name__ == "__main__":
    init()
