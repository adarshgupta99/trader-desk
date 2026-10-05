"""
The instrument universe: what the app can plot, and what the search box can
find.

CURATED_INSTRUMENTS is the hand-picked set the per-tab grids are built from.
The searchable table is wider - every mainboard NSE equity as well - and is
loaded from the committed seed file data/symbols.csv, so search is instant and
can never be rate-limited mid-keystroke.
"""

import os

import pandas as pd

from storage import get_connection

CURATED_INSTRUMENTS = [
    ("Indices",     "S&P 500",          "^GSPC"),
    ("Indices",     "Nasdaq",           "^IXIC"),
    ("Indices",     "Nifty 50",         "^NSEI"),
    ("Indices",     "KOSPI (Korea)",    "^KS11"),
    ("Indices",     "FTSE 100 (UK)",    "^FTSE"),
    ("Indices",     "DAX (Germany)",    "^GDAXI"),
    ("Indices",     "Euro Stoxx 50",    "^STOXX50E"),
    ("Indices",     "Nikkei (Japan)",   "^N225"),
    ("India",       "Bank Nifty",       "^NSEBANK"),
    ("India",       "Nifty IT",         "^CNXIT"),
    ("India",       "Nifty Auto",       "NSE:NIFTY AUTO"),
    ("India",       "Nifty Energy",     "NSE:NIFTY ENERGY"),
    ("India",       "Nifty Mid Select", "NSE:NIFTY MID SELECT"),
    ("India",       "Nifty Defence",    "NSE:NIFTY INDIA DEFENCE"),
    ("FX",          "USD / INR",        "USDINR=X"),
    ("FX",          "EUR / USD",        "EURUSD=X"),
    ("FX",          "GBP / USD",        "GBPUSD=X"),
    ("FX",          "EUR / INR",        "EURINR=X"),
    ("Commodities", "WTI Crude Oil",    "CL=F"),
    ("Commodities", "Gold",             "GC=F"),
    ("Commodities", "Silver",           "SI=F"),
    ("Volatility",  "VIX (US)",         "^VIX"),
]

SYMBOL_SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "data", "symbols.csv")


def refresh_symbols(force=False):
    """Populate the instrument table from the committed seed file.

    data/symbols.csv is the single source of truth. To update the table
    (after an index reshuffle or to add new instruments), edit the CSV and
    commit it — no network calls, no weekly rebuilds, no partial failures.
    Returns (changed, message).
    """
    if not os.path.exists(SYMBOL_SEED_PATH):
        return False, "Seed file missing — instrument search unavailable."
    try:
        frame = pd.read_csv(SYMBOL_SEED_PATH)
    except Exception as e:
        return False, f"Could not read seed file: {e}"

    rows = [(str(t), str(n), str(k)) for t, n, k
            in zip(frame["ticker"], frame["name"], frame["kind"])]
    conn = get_connection()
    try:
        conn.execute("DELETE FROM symbols")
        conn.executemany(
            "INSERT INTO symbols (ticker, name, kind) VALUES (?, ?, ?)", rows
        )
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    finally:
        conn.close()
    return True, f"{total} instruments."


# symbol_count, export_symbol_seed and search_instruments below are not called
# anywhere in the app - they are hand-run helpers for a Python shell. Kept
# because that is what they are for, not because something depends on them.

def symbol_count():
    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    finally:
        conn.close()


def export_symbol_seed(path=SYMBOL_SEED_PATH):
    """Write the current symbol table back to the seed file.

    Run this locally after manually adding or removing instruments, then
    commit data/symbols.csv so the next deploy picks up the changes.
    """
    conn = get_connection()
    try:
        frame = pd.read_sql(
            "SELECT ticker, name, kind FROM symbols ORDER BY kind, name", conn
        )
    finally:
        conn.close()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    frame.to_csv(path, index=False)
    return len(frame)


def get_all_symbols():
    """Every searchable instrument, ordered so a dropdown using
    `filter_mode="contains"` surfaces indices and FX first, then large-cap
    index members, then the rest of the NSE board."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT ticker, name, kind
            FROM symbols
            ORDER BY
                CASE
                    WHEN kind IN ('Indices','India','FX','Commodities','Volatility') THEN 0
                    WHEN kind <> 'NSE equity' THEN 1
                    ELSE 2
                END,
                name
            """
        ).fetchall()
        return [{"ticker": r[0], "name": r[1], "kind": r[2]} for r in rows]
    finally:
        conn.close()


def search_instruments(query, limit=10):
    """Match a query against instrument names and tickers, best match first."""
    query = (query or "").strip()
    if len(query) < 2:
        return []
    like = f"%{query}%"
    starts = f"{query}%"
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT ticker, name, kind
            FROM symbols
            WHERE name LIKE ? OR ticker LIKE ?
            ORDER BY
                CASE WHEN kind <> 'NSE equity' THEN 0 ELSE 1 END,
                CASE
                    WHEN ticker LIKE ? THEN 0
                    WHEN name   LIKE ? THEN 1
                    ELSE 2
                END,
                LENGTH(name),
                name
            LIMIT ?
            """,
            (like, like, starts, starts, limit),
        ).fetchall()
        return [{"ticker": r[0], "name": r[1], "kind": r[2]} for r in rows]
    finally:
        conn.close()
