import sqlite3
import os
import threading

DB_PATH = os.environ.get("DB_PATH", "/data/events.db")
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            event_id     TEXT PRIMARY KEY,
            store_id     TEXT NOT NULL,
            camera_id    TEXT NOT NULL,
            visitor_id   TEXT NOT NULL,
            event_type   TEXT NOT NULL,
            timestamp    TEXT NOT NULL,
            zone_id      TEXT,
            dwell_ms     INTEGER DEFAULT 0,
            is_staff     INTEGER DEFAULT 0,
            confidence   REAL NOT NULL,
            queue_depth  INTEGER,
            sku_zone     TEXT,
            session_seq  INTEGER DEFAULT 0,
            ingested_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_store_ts   ON events(store_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_visitor     ON events(visitor_id);
        CREATE INDEX IF NOT EXISTS idx_event_type  ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_store_type  ON events(store_id, event_type);
    """)
    conn.commit()
    conn.close()
