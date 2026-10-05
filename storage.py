"""
The SQLite file behind the app, and its schema.

One database holds three unrelated things - the price cache, the searchable
symbol table, and the custom watchlists - because they are all per-install
state with the same lifetime, and one file is one thing to back up or delete.
Each table's own module owns the queries against it; this module only owns
where the file is and what shape it has.

Every caller opens its own short-lived connection and closes it. SQLite is
fine with that, and it keeps Streamlit's rerun model from holding a
connection across a script run.
"""

import sqlite3

DB_PATH = "market_cache.db"

# Four editable lists, created on first connection and never added to or
# removed. The five index views are read-only and are not rows in here.
CUSTOM_LIST_COUNT = 4


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_cache (
            ticker TEXT NOT NULL,
            period TEXT NOT NULL,
            interval TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            PRIMARY KEY (ticker, period, interval, date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cache_meta (
            ticker TEXT NOT NULL,
            period TEXT NOT NULL,
            interval TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (ticker, period, interval)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symbols (
            ticker TEXT PRIMARY KEY,
            name   TEXT NOT NULL,
            kind   TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symbols_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_lists (
            id   INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_list_items (
            list_id  INTEGER NOT NULL,
            ticker   TEXT NOT NULL,
            label    TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (list_id, ticker)
        )
        """
    )
    existing = conn.execute("SELECT COUNT(*) FROM custom_lists").fetchone()[0]
    if existing < CUSTOM_LIST_COUNT:
        conn.executemany(
            "INSERT OR IGNORE INTO custom_lists (id, name) VALUES (?, ?)",
            [(i, f"List {i}") for i in range(1, CUSTOM_LIST_COUNT + 1)],
        )
        conn.commit()
    return conn
