"""
Read-only access to events.db for the Streamlit app. The write side lives in
scripts/fetch_events.py; the app never writes to this file.
"""

import os
import sqlite3
from datetime import datetime, timezone, timedelta

import pandas as pd
import streamlit as st

DB_PATH = os.path.join(os.path.dirname(__file__), "events.db")

IST = timezone(timedelta(hours=5, minutes=30))


def _read_connection():
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


@st.cache_data(ttl=3600)
def get_upcoming_events(symbols=None):
    """Events with event_date >= today (IST), ordered by event_date ascending.

    symbols, if given, is an iterable of bare NSE symbols (e.g. "TCS", not
    "TCS.NS") to filter to. Returns an empty DataFrame if events.db doesn't
    exist yet or has no rows - never raises.
    """
    columns = [
        "symbol", "event_type", "subtype", "event_date",
        "record_date", "purpose", "source",
    ]
    if not os.path.exists(DB_PATH):
        return pd.DataFrame(columns=columns)

    today_ist = datetime.now(IST).strftime("%Y-%m-%d")
    query = f"SELECT {', '.join(columns)} FROM events WHERE event_date >= ?"
    params = [today_ist]

    if symbols:
        symbols = list(symbols)
        if not symbols:
            return pd.DataFrame(columns=columns)
        placeholders = ", ".join("?" * len(symbols))
        query += f" AND symbol IN ({placeholders})"
        params.extend(symbols)

    query += " ORDER BY event_date ASC"

    try:
        with _read_connection() as conn:
            return pd.read_sql_query(query, conn, params=params)
    except sqlite3.OperationalError:
        # events table doesn't exist yet (file present but never populated)
        return pd.DataFrame(columns=columns)


@st.cache_data(ttl=3600)
def get_last_success():
    """Returns (last_success_at_iso_or_None, last_error_text_or_empty)."""
    if not os.path.exists(DB_PATH):
        return None, ""
    try:
        with _read_connection() as conn:
            rows = dict(
                conn.execute(
                    "SELECT key, value FROM meta WHERE key IN "
                    "('last_success_at', 'last_error')"
                ).fetchall()
            )
    except sqlite3.OperationalError:
        return None, ""
    return rows.get("last_success_at"), rows.get("last_error", "") or ""
