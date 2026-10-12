"""
On-disk cache for live (non-frozen) yfinance lookups used by the Events tab -
analyst estimate previews and shares-outstanding for the profit estimate.

Owned and read-written by the app itself, unlike events.db (which the app
only ever reads - see events_db.py's own docstring). st.cache_data alone
only lives for the Streamlit process's lifetime, so every restart (a code
change, a Streamlit Cloud redeploy) throws it away and repays the full
fetch cost; this file survives restarts, so that cost is paid once per TTL
window rather than once per restart.
"""

import json
import os
import sqlite3
import time

CACHE_PATH = os.path.join(os.path.dirname(__file__), "live_data_cache.db")

#: Returned by get() when there is no cached entry at all, distinct from a
#: cached entry whose value is None (yfinance had nothing for that symbol -
#: itself worth remembering, so a fresh "nothing found" isn't re-fetched
#: every TTL window either).
MISS = object()


def _connection():
    conn = sqlite3.connect(CACHE_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cache (
            symbol      TEXT NOT NULL,
            kind        TEXT NOT NULL,
            fetched_at  REAL NOT NULL,
            value_json  TEXT,
            PRIMARY KEY (symbol, kind)
        )
        """
    )
    return conn


def get(symbol, kind, ttl_seconds):
    try:
        conn = _connection()
        row = conn.execute(
            "SELECT fetched_at, value_json FROM cache WHERE symbol = ? AND kind = ?",
            (symbol, kind),
        ).fetchone()
        conn.close()
    except sqlite3.OperationalError:
        return MISS
    if row is None:
        return MISS
    fetched_at, value_json = row
    if time.time() - fetched_at > ttl_seconds:
        return MISS
    return json.loads(value_json) if value_json is not None else None


def set(symbol, kind, value):
    conn = _connection()
    conn.execute(
        """
        INSERT INTO cache (symbol, kind, fetched_at, value_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(symbol, kind) DO UPDATE SET
            fetched_at = excluded.fetched_at, value_json = excluded.value_json
        """,
        (symbol, kind, time.time(), json.dumps(value)),
    )
    conn.commit()
    conn.close()
