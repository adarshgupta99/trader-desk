"""
Price history, and the SQLite cache in front of it.

Two providers behind one function: yfinance for everything, and jugaad-data
for the NSE indices yfinance does not carry. For the three Indian indices
that exist on both (Nifty 50, Bank Nifty, Nifty IT), yfinance stays primary
but every fetch also merges in jugaad-data, so a date yfinance left null -
or omitted entirely - still shows up if jugaad has it. Each provider's
Indian-index feed is independently flaky about when a session's close
lands, and not reliably in the same direction, so neither is "the" source
on its own. get_price_history() is the only thing other modules should need.
"""

from datetime import datetime, timedelta, timezone, date as date_type

import pandas as pd
import yfinance as yf

from storage import get_connection

# NSE index names that jugaad-data can fetch (passed as ticker in the app,
# prefixed with "NSE:" so the loader knows to route them to jugaad-data)
NSE_PREFIX = "NSE:"

# Indian indices available on both providers. Keyed by the yfinance ticker
# (the primary source for these three), valued by jugaad-data's index name.
# The other NSE-prefixed indices (Midcap, Auto, Energy, Defence) have no
# entry here because yfinance does not carry them at all - there is nothing
# to fall back from.
_JUGAAD_FALLBACK_INDEX = {
    "^NSEI": "NIFTY 50",
    "^NSEBANK": "NIFTY BANK",
    "^CNXIT": "NIFTY IT",
}


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


def _normalize_dates(df):
    """Re-tag each row's exchange-local trading date as UTC midnight (e.g. a
    KOSPI bar stays dated in Asia/Seoul, not shifted a day by a UTC
    conversion), so every provider's output lines up on the same calendar
    date and can be cached, compared or merged against another provider's."""
    if df.empty:
        return df
    df = df.copy()
    if df.index.tz is not None:
        df.index = pd.to_datetime(df.index.date, utc=True)
    else:
        df.index = df.index.tz_localize("UTC")
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


def _fill_gaps_from_nse(ticker, df, period, interval):
    """For the three indices on both providers, patch any date yfinance left
    null or omitted entirely using jugaad-data's close for that date instead.
    yfinance's Indian-index feed can either return a row with a null close
    for a date still landing, or skip the date's row altogether depending on
    exactly when it's queried - so "no NaN in df" does not mean "nothing
    missing", and this always merges rather than trying to detect a gap
    first. The outer get_price_history cache (max_age_hours) is what bounds
    how often this runs, not a check here."""
    nse_symbol = _JUGAAD_FALLBACK_INDEX.get(ticker)
    if nse_symbol is None or df.empty:
        return df
    try:
        nse_df = _normalize_dates(_fetch_from_nse(nse_symbol, period, interval))
    except Exception:
        return df  # jugaad being unreachable shouldn't break the yfinance result
    if nse_df.empty:
        return df
    # Keeps df's value wherever it isn't null; fills from nse_df only where
    # df is null (and picks up any date df is missing entirely).
    return df.combine_first(nse_df)[["Open", "High", "Low", "Close", "Volume"]]


# ── shared write ──────────────────────────────────────────────────────────────

def _none_if_nan(value):
    # sqlite3 binds float('nan') as a real (if odd) REAL value, not NULL -
    # COALESCE only falls back on an actual NULL, so a missing value has to
    # be converted here or the upsert below can't tell "missing" from "0".
    return None if pd.isna(value) else value


def _write_to_cache(conn, ticker, period, interval, df):
    # df's index is already normalized to UTC-midnight calendar dates by the
    # caller, whichever provider it came from.
    rows = [
        (ticker, period, interval, idx.isoformat(),
         _none_if_nan(r.Open), _none_if_nan(r.High), _none_if_nan(r.Low),
         _none_if_nan(r.Close), _none_if_nan(r.Volume))
        for idx, r in df.iterrows()
    ]
    # Upsert, not delete-then-insert: a refetch that comes back null for a
    # date (yfinance's Indian-index feed does this while a session's close
    # is still landing) must never erase a value a previous, better fetch
    # already cached for that same date. COALESCE keeps the old value when
    # the new one is null, and still takes the new value whenever it's real
    # - including a genuine correction.
    conn.executemany(
        """
        INSERT INTO price_cache (ticker, period, interval, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (ticker, period, interval, date) DO UPDATE SET
            open   = COALESCE(excluded.open,   price_cache.open),
            high   = COALESCE(excluded.high,   price_cache.high),
            low    = COALESCE(excluded.low,    price_cache.low),
            close  = COALESCE(excluded.close,  price_cache.close),
            volume = COALESCE(excluded.volume, price_cache.volume)
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
    'NSE:NIFTY AUTO'. All other tickers go through yfinance - except Nifty
    50, Bank Nifty and Nifty IT, which also get any date yfinance left null
    filled in from jugaad-data (see _JUGAAD_FALLBACK_INDEX).
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
            df = _normalize_dates(_fetch_from_yfinance(ticker, period, interval))
            df = _fill_gaps_from_nse(ticker, df, period, interval)

        if df.empty:
            return df
        _write_to_cache(conn, ticker, period, interval, df)
        return df
    finally:
        conn.close()
