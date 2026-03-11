"""
database/db.py — thread-safe SQLite connection pool + all CRUD helpers.
"""
import sqlite3
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any

from database.models import SCHEMA_SQL
import config

logger = logging.getLogger(__name__)

# Ensure data directory exists
Path(config.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(
        config.DATABASE_PATH,
        check_same_thread=False,
        timeout=30,
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    """Context manager — yields a connection, commits on success, rolls back on error."""
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist."""
    with get_db() as conn:
        conn.executescript(SCHEMA_SQL)
        # Migration: add description column if not exists (for existing DBs)
        try:
            conn.execute("ALTER TABLE processed_items ADD COLUMN description TEXT")
            logger.info("Migrated: added description column to processed_items")
        except Exception:
            pass  # column already exists
    logger.info("Database initialised at %s", config.DATABASE_PATH)


# ── Sources ───────────────────────────────────────────────────────────────────

def add_source(type_: str, name: str, url: str, meta: dict = None) -> Optional[int]:
    meta_json = json.dumps(meta or {})
    try:
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO sources (type, name, url, meta) VALUES (?,?,?,?)",
                (type_, name, url, meta_json),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        logger.warning("Source already exists: %s", url)
        return None


def get_sources(type_: str = None, active_only: bool = True) -> List[Dict]:
    sql = "SELECT * FROM sources WHERE 1=1"
    params: list = []
    if active_only:
        sql += " AND active=1"
    if type_:
        sql += " AND type=?"
        params.append(type_)
    sql += " ORDER BY created_at DESC"
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def remove_source(source_id: int) -> bool:
    with get_db() as conn:
        conn.execute("UPDATE sources SET active=0 WHERE id=?", (source_id,))
    return True


def update_source_fetched(source_id: int):
    with get_db() as conn:
        conn.execute(
            "UPDATE sources SET last_fetched=datetime('now') WHERE id=?",
            (source_id,),
        )


# ── Processed Items ───────────────────────────────────────────────────────────

def item_exists(source_id: int, entry_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_items WHERE source_id=? AND entry_id=?",
            (source_id, entry_id),
        ).fetchone()
    return row is not None


def add_item(source_id: int, entry_id: str, title: str,
             url: str, published_at: str, description: str = "") -> Optional[int]:
    try:
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO processed_items
                   (source_id, entry_id, title, url, published_at, description)
                   VALUES (?,?,?,?,?,?)""",
                (source_id, entry_id, title, url, published_at, description),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def update_item_status(item_id: int, status: str, error_msg: str = None):
    with get_db() as conn:
        conn.execute(
            """UPDATE processed_items
               SET status=?, error_msg=?, processed_at=datetime('now')
               WHERE id=?""",
            (status, error_msg, item_id),
        )


def get_pending_items(limit: int = 10) -> List[Dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT pi.*, s.type as source_type, s.name as source_name
               FROM processed_items pi
               JOIN sources s ON s.id=pi.source_id
               WHERE pi.status='pending' AND s.active=1
               ORDER BY pi.published_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Summaries ─────────────────────────────────────────────────────────────────

def save_summary(item_id: int, source_id: int, title: str,
                 summary_text: str, model_used: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT OR REPLACE INTO summaries
               (item_id, source_id, title, summary_text, model_used)
               VALUES (?,?,?,?,?)""",
            (item_id, source_id, title, summary_text, model_used),
        )
        return cur.lastrowid


def get_unsent_summaries() -> List[Dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT s.*, src.name as source_name, src.type as source_type
               FROM summaries s
               JOIN sources src ON src.id=s.source_id
               WHERE s.sent_telegram=0
               ORDER BY s.created_at ASC""",
        ).fetchall()
    return [dict(r) for r in rows]


def mark_summary_sent(summary_id: int):
    with get_db() as conn:
        conn.execute(
            "UPDATE summaries SET sent_telegram=1, sent_at=datetime('now') WHERE id=?",
            (summary_id,),
        )


def get_today_summaries() -> List[Dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT s.*, src.name as source_name, src.type as source_type
               FROM summaries s
               JOIN sources src ON src.id=s.source_id
               WHERE date(s.created_at)=date('now')
               ORDER BY s.created_at DESC""",
        ).fetchall()
    return [dict(r) for r in rows]