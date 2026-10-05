"""
Price history, and the SQLite cache in front of it.

Two providers behind one function: yfinance for everything, and jugaad-data
for the NSE indices yfinance does not carry. get_price_history() is the only
thing other modules should need.
"""

from datetime import datetime, timedelta, timezone, date as date_type

import pandas as pd
import yfinance as yf

from storage import get_connection

# NSE index names that jugaad-data can fetch (passed as ticker in the app,
# prefixed with "NSE:" so the loader knows to route them to jugaad-data)
NSE_PREFIX = "NSE:"


def clear_price_cache():
    """Delete all cached price data so everything re-fetches from yfinance."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM price_cache")
        conn.execute("DELETE FROM cache_meta")
        conn.commit()
    finally:
        conn.close()


def _is_fresh(conn, ticker, period, interval, max_age_hours):
    row = conn.execute(
        "SELECT fetched_at FROM cache_meta WHERE ticker = ? AND period = ? AND interval = ?",
        (ticker, period, interval),
    ).fetchone()
    if row is None:
        return False
    fetched_at = datetime.fromisoformat(row[0])
    return datetime.now(timezone.utc) - fetched_at < timedelta(hours=max_age_hours)


def _read_from_cache(conn, ticker, period, interval):
    df = pd.read_sql(
        """
        SELECT date, open, high, low, close, volume
        FROM price_cache
        WHERE ticker = ? AND period = ? AND interval = ?
        ORDER BY date
        """,
        conn,
        params=(ticker, period, interval),
        parse_dates={"date": {"utc": True}},
        index_col="date",
    )
    df.columns = [c.capitalize() for c in df.columns]
    return df


# ── yfinance path ─────────────────────────────────────────────────────────────

def _fetch_from_yfinance(ticker, period, interval):
    df = yf.Ticker(ticker).history(period=period, interval=interval)
    return df[["Open", "High", "Low", "Close", "Volume"]]


# ── jugaad-data path (NSE indices) ────────────────────────────────────────────

_PERIOD_TO_DAYS = {
    "5d": 7,
    "1mo": 35,
    "3mo": 95,
    "6mo": 185,
    "1y": 370,
    "3y": 1100,
}

def _fetch_from_nse(nse_symbol, period, interval):
    from jugaad_data.nse import index_raw

    days = _PERIOD_TO_DAYS.get(period, 35)
    to_dt = date_type.today()
    from_dt = to_dt - timedelta(days=days)

    raw = index_raw(symbol=nse_symbol, from_date=from_dt, to_date=to_dt)
    if not raw:
        return pd.DataFrame()

    df = pd.DataFrame(raw)
    df["date"] = pd.to_datetime(df["HistoricalDate"], format="%d %b %Y", utc=True)
    df = df.set_index("date").sort_index()
    df = df.rename(columns={"OPEN": "Open", "HIGH": "High", "LOW": "Low", "CLOSE": "Close"})
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Volume"] = 0.0
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


# ── shared write ──────────────────────────────────────────────────────────────

def _write_to_cache(conn, ticker, period, interval, df):
    # Keep each row's exchange-local trading date (e.g. a KOSPI bar stays
    # dated in Asia/Seoul, not shifted to the previous day by a UTC
    # conversion) while still storing a single consistent format: re-tag the
    # local calendar date as UTC midnight, same convention the NSE path
    # already uses when it parses "HistoricalDate" with utc=True.
    if df.index.tz is not None:
        df.index = pd.to_datetime(df.index.date, utc=True)
    else:
        df.index = df.index.tz_localize("UTC")

    conn.execute(
        "DELETE FROM price_cache WHERE ticker = ? AND period = ? AND interval = ?",
        (ticker, period, interval),
    )
    rows = [
        (ticker, period, interval, idx.isoformat(), r.Open, r.High, r.Low, r.Close, r.Volume)
        for idx, r in df.iterrows()
    ]
    conn.executemany(
        """
        INSERT INTO price_cache (ticker, period, interval, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.execute(
        """
        INSERT INTO cache_meta (ticker, period, interval, fetched_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (ticker, period, interval) DO UPDATE SET fetched_at = excluded.fetched_at
        """,
        (ticker, period, interval, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


# ── public API ────────────────────────────────────────────────────────────────

def get_price_history(ticker, period="1mo", interval="1d", max_age_hours=6):
    """Return OHLCV data for ticker.

    Prefix ticker with 'NSE:' to fetch from NSE via jugaad-data, e.g.
    'NSE:NIFTY AUTO'. All other tickers go through yfinance.
    Results are cached in SQLite for max_age_hours.
    """
    conn = get_connection()
    try:
        if _is_fresh(conn, ticker, period, interval, max_age_hours):
            return _read_from_cache(conn, ticker, period, interval)

        if ticker.startswith(NSE_PREFIX):
            nse_symbol = ticker[len(NSE_PREFIX):]
            df = _fetch_from_nse(nse_symbol, period, interval)
        else:
            df = _fetch_from_yfinance(ticker, period, interval)

        if df.empty:
            return df
        _write_to_cache(conn, ticker, period, interval, df)
        return df
    finally:
        conn.close()
