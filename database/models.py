"""
database/models.py — SQLite schema definitions (pure SQL, no ORM overhead).
"""

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── Sources ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL CHECK(type IN ('youtube','podcast','google_alert')),
    name        TEXT NOT NULL,
    url         TEXT NOT NULL UNIQUE,         -- RSS feed URL
    meta        TEXT,                         -- JSON blob (channel_id, podcast_id, etc.)
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    last_fetched TEXT
);

CREATE INDEX IF NOT EXISTS idx_sources_type   ON sources(type);
CREATE INDEX IF NOT EXISTS idx_sources_active ON sources(active);

-- ── Processed Items ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS processed_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    entry_id     TEXT NOT NULL,               -- GUID / URL from RSS entry
    title        TEXT,
    url          TEXT,
    published_at TEXT,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK(status IN ('pending','processing','done','failed','skipped')),
    error_msg    TEXT,
    description  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at TEXT,
    UNIQUE(source_id, entry_id)
);

CREATE INDEX IF NOT EXISTS idx_items_source   ON processed_items(source_id);
CREATE INDEX IF NOT EXISTS idx_items_status   ON processed_items(status);
CREATE INDEX IF NOT EXISTS idx_items_pub      ON processed_items(published_at);

-- ── Summaries ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS summaries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       INTEGER NOT NULL UNIQUE REFERENCES processed_items(id) ON DELETE CASCADE,
    source_id     INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    title         TEXT,
    summary_text  TEXT NOT NULL,
    model_used    TEXT,
    tokens_used   INTEGER,
    sent_telegram INTEGER NOT NULL DEFAULT 0,
    sent_at       TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_summaries_sent ON summaries(sent_telegram);
CREATE INDEX IF NOT EXISTS idx_summaries_date ON summaries(created_at);
"""