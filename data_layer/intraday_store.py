"""SQLite store for F&O bars. Spec section 5.

Lives OUTSIDE the repo: ~/trader_desk_data/intraday.db by default, or
TRADER_DESK_DATA_DIR from .env. 306,280 rows of 15m bars measured at
50.6 MB (165 bytes a row) on 6 Oct 2026; bar data does not belong in git.

Everything here is disposable. The 15m tier only answers lookbacks up to
60 days and Yahoo serves that whole window on demand, so the file can
always be rebuilt, a missed fetch day costs nothing, and no history is
ever stranded. Prune freely.

Timestamps are stored as IST-naive ISO text, "YYYY-MM-DDTHH:MM:SS", with
no offset. Three reasons, and they are the point of this module rather
than an implementation detail: the format sorts lexicographically, so a
window is a plain `WHERE ts >= ?` and SQLite can use the index for it; it
parses with one fixed format instead of ISO8601 sniffing; and NSE bars
are only ever IST, so the offset would be a constant repeated 306,280
times. Measured 6 Oct 2026, loading the full table and parsing it cost
1.45s against 0.12s for the correlation itself — twelve times the maths.
Hence: read only the window asked for, never the whole table.

Connection convention follows storage.py — every caller opens its own
short-lived connection and closes it, so Streamlit's rerun model never
holds one across a script run.
"""

import os
import sqlite3

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

TS_FORMAT = "%Y-%m-%dT%H:%M:%S"

#: Retention, in SESSIONS rather than calendar days. The spec says "about
#: 65 days" of 15m bars, but a 60-session lookback spans 83 calendar days
#: (measured: 58 sessions from 14 Jul to 5 Oct 2026), so pruning on a
#: 65-calendar-day window would delete bars the longest lookback needs.
#: Counting sessions makes retention mean the same thing as the lookback
#: it has to serve, and holidays stop mattering.
RETENTION_SESSIONS = {"15m": 65, "1d": 800}


def data_dir():
    """Where bulk bar data lives. Created on demand."""
    path = os.getenv("TRADER_DESK_DATA_DIR") or os.path.join(
        os.path.expanduser("~"), "trader_desk_data"
    )
    return os.path.expanduser(path)


def db_path():
    return os.path.join(data_dir(), "intraday.db")


def exists():
    """Is there a store to read? The UI asks before it tries."""
    return os.path.exists(db_path())


def get_connection(create=True):
    """Open the store, creating the file and schema if needed.

    Pass create=False to open read-only-ish without making directories;
    raises FileNotFoundError if there is nothing there yet.
    """
    path = db_path()
    if not create and not os.path.exists(path):
        raise FileNotFoundError(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bars (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            ts TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (symbol, interval, ts)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bars_interval_ts ON bars(interval, ts)"
    )
    # interval stays in the key and the schema even while one tier is
    # stored, so adding a bar size later needs no migration.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS corr_cache (
            tier TEXT NOT NULL,
            lookback_days INTEGER NOT NULL,
            asof_date TEXT NOT NULL,
            symbol_a TEXT NOT NULL,
            symbol_b TEXT NOT NULL,
            corr REAL, n_obs INTEGER,
            PRIMARY KEY (tier, lookback_days, asof_date, symbol_a, symbol_b)
        )
    """)
    # corr_cache is optional and currently unwritten: the worst-case
    # correlation measured 0.12s, so a per-process memo in
    # analytics/correlation.py is enough. The table is here so a later
    # feature that wants correlations without loading bars has somewhere
    # to put them. See spec section 5.
    conn.commit()
    return conn


def write_bars(conn, symbol, interval, df):
    """INSERT OR IGNORE one symbol's bars. Returns rows offered.

    `df` is a yfinance frame: DatetimeIndex, Open/High/Low/Close/Volume.
    Refetching an overlapping window is safe by design — that is what
    lets the fetch job ask for 5 days every day without bookkeeping.
    """
    if df is None or len(df) == 0:
        return 0

    index = pd.DatetimeIndex(df.index)
    if index.tz is not None:
        index = index.tz_convert("Asia/Kolkata").tz_localize(None)

    # Column-wise, not iterrows: 213 symbols x ~1,450 bars is 306,000
    # rows a cold start, and iterrows builds a Series per row.
    stamps = index.strftime(TS_FORMAT).tolist()
    price = [_floats(df, c) for c in ("Open", "High", "Low", "Close")]
    volume = _ints(df, "Volume")
    rows = list(zip(
        [symbol] * len(stamps), [interval] * len(stamps), stamps,
        price[0], price[1], price[2], price[3], volume,
    ))
    conn.executemany(
        "INSERT OR IGNORE INTO bars VALUES (?,?,?,?,?,?,?,?)", rows
    )
    return len(rows)


def session_dates(conn, interval):
    """Every session present for this interval, oldest first.

    Sessions, not calendar days: a lookback of 20 means the last 20
    trading sessions, so holidays never silently shrink the window.
    """
    rows = conn.execute(
        "SELECT DISTINCT substr(ts, 1, 10) FROM bars WHERE interval = ?"
        " ORDER BY 1",
        (interval,),
    ).fetchall()
    return [r[0] for r in rows]


def window_start(conn, interval, lookback_days):
    """The ts floor covering the last `lookback_days` stored sessions.

    Returns (start_ts_text, n_sessions, asof_date), or None if the
    interval has no bars at all.
    """
    dates = session_dates(conn, interval)
    if not dates:
        return None
    wanted = dates[-int(lookback_days):] if lookback_days > 0 else dates
    return f"{wanted[0]}T00:00:00", len(wanted), wanted[-1]


def read_bars(conn, interval, start_ts=None, symbols=None):
    """Long frame of bars for the window: symbol, ts, open/high/low/close.

    Volume is left out on purpose. The stale-bar policy does not use it
    (settled 6 Oct 2026 by measurement) and it is a third of the row
    width, so loading it here would be paying for nothing. The fetch job
    reads it separately for its diagnostic.
    """
    sql = ["SELECT symbol, ts, open, high, low, close FROM bars"
           " WHERE interval = ?"]
    params = [interval]
    if start_ts:
        sql.append(" AND ts >= ?")
        params.append(start_ts)
    if symbols:
        sql.append(f" AND symbol IN ({','.join('?' * len(symbols))})")
        params.extend(symbols)
    sql.append(" ORDER BY symbol, ts")

    raw = pd.read_sql("".join(sql), conn, params=params)
    if not raw.empty:
        raw["ts"] = pd.to_datetime(raw["ts"], format=TS_FORMAT)
    return raw


def zero_volume_count(conn, interval, start_ts=None):
    """Diagnostic for the fetch job: how many bars report no trades.

    Not a staleness test — measured 6 Oct 2026, these are feed artifacts
    whose price moved (217 of 306,280, all the 15:15 bar, clustered by
    date). Logged so a change in Yahoo's feed does not pass unnoticed.
    """
    sql = "SELECT COUNT(*) FROM bars WHERE interval = ? AND volume = 0"
    params = [interval]
    if start_ts:
        sql += " AND ts >= ?"
        params.append(start_ts)
    return conn.execute(sql, params).fetchone()[0]


def coverage(conn, interval):
    """(n_rows, n_symbols, first_ts, last_ts) for a tier. For the footer."""
    row = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(ts), MAX(ts)"
        " FROM bars WHERE interval = ?",
        (interval,),
    ).fetchone()
    return row


def prune(conn, interval, keep_sessions=None):
    """Drop everything before the newest N sessions. Returns rows removed."""
    keep = int(keep_sessions or RETENTION_SESSIONS.get(interval, 65))
    dates = session_dates(conn, interval)
    if len(dates) <= keep:
        return 0
    cutoff = f"{dates[-keep]}T00:00:00"
    cur = conn.execute(
        "DELETE FROM bars WHERE interval = ? AND ts < ?", (interval, cutoff)
    )
    conn.commit()
    return cur.rowcount


def _floats(df, column):
    """One column as plain Python floats, NaN as None. sqlite3 will not
    bind numpy scalars, and NULL is the honest encoding of a missing bar.
    """
    if column not in df:
        return [None] * len(df)
    return [None if v != v else float(v)
            for v in df[column].to_numpy(dtype="float64", na_value=float("nan"))]


def _ints(df, column):
    if column not in df:
        return [None] * len(df)
    return [None if v != v else int(v)
            for v in df[column].to_numpy(dtype="float64", na_value=float("nan"))]
