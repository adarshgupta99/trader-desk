"""
Price history, and the SQLite cache in front of it.

Every caller still gets back one daily OHLCV row per date - that external
shape hasn't changed. What feeds those rows has: for any date within
yfinance's 1h retention window (a rolling ~2 years back from today, capped
at 730 days - see _HOURLY_MAX_DAYS), the daily candle is built by resampling
hourly bars (open = first bar, high/low = session extremes, close = last
bar) instead of trusting yfinance's own daily feed. The daily feed is only
used for dates older than that window. This matters because the *last*
hourly bar of a session is reliably the real close the moment it prints,
where yfinance's daily feed for Indian tickers can come back null or
missing entirely for hours after a session ends.

On top of that, jugaad-data still fills in gaps for anything with a
jugaad-data equivalent - the three Indian indices on both providers (Nifty
50, Bank Nifty, Nifty IT), plus every NSE-listed equity - exactly as before:
unchanged, and still only touching dates the combined yfinance result left
null or omitted.
get_price_history() is the only thing other modules should need.
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


# How far back each period label reaches, in calendar days. Shared: it sizes
# the hourly request below as well as both jugaad-data fetches.
_PERIOD_TO_DAYS = {
    "5d": 7,
    "1mo": 35,
    "3mo": 95,
    "6mo": 185,
    "1y": 370,
    "3y": 1100,
}


# ── yfinance path ─────────────────────────────────────────────────────────────

def _fetch_from_yfinance(ticker, period, interval):
    df = yf.Ticker(ticker).history(period=period, interval=interval)
    return df[["Open", "High", "Low", "Close", "Volume"]]


# Yahoo hard-refuses a 1h request whose range starts more than 730 days back
# ("The requested range must be within the last 730 days"), for every ticker,
# Indian or not. Stay under it rather than on it. Note this is a *rolling*
# window anchored to today, not a fixed archive: it slides forward daily, so
# the daily feed below is permanently needed for anything older.
_HOURLY_MAX_DAYS = 720


def _fetch_hourly_from_yfinance(ticker, days):
    df = yf.Ticker(ticker).history(
        period=f"{min(days, _HOURLY_MAX_DAYS)}d", interval="1h"
    )
    if df.empty:
        return pd.DataFrame()
    return df[["Open", "High", "Low", "Close", "Volume"]]


def _resample_hourly_to_daily(df):
    """Collapse 1h bars into one candle per trading session.

    Grouping is on the tz-aware index's own local date, which is the
    exchange's calendar day - so an NSE session buckets as one Indian day and
    a US session as one New York day, without either being split or merged by
    a UTC conversion. The last bar's close is the session close (NSE's 15:15
    bucket, New York's 15:30, whatever the exchange's final hour is) - taken
    positionally rather than by hardcoded clock time so it holds across
    exchanges, and so a session still in progress yields its latest price
    instead of nothing.
    """
    if df.empty:
        return df
    daily = df.groupby(df.index.date).agg(
        Open=("Open", "first"),
        High=("High", "max"),
        Low=("Low", "min"),
        Close=("Close", "last"),
        Volume=("Volume", "sum"),
    )
    daily.index = pd.to_datetime(daily.index, utc=True)
    return daily.dropna(subset=["Close"])


def _fetch_daily_via_hourly(ticker, period, interval):
    """Daily candles for `period`, resampled from hourly bars wherever hourly
    reaches, and from yfinance's daily feed for dates older than that.

    Hourly wins on every date both cover - that is the whole point of the
    shift. A hourly fetch that fails or comes back empty (an unsupported
    ticker, a network blip) must not take the daily result down with it, so
    the daily feed is fetched whenever hourly cannot cover the full span, and
    as a standalone fallback when hourly yields nothing at all.
    """
    days = _PERIOD_TO_DAYS.get(period, 35)
    try:
        hourly = _normalize_dates(
            _resample_hourly_to_daily(_fetch_hourly_from_yfinance(ticker, days))
        )
    except Exception:
        hourly = pd.DataFrame()

    if hourly.empty:
        return _normalize_dates(_fetch_from_yfinance(ticker, period, interval))
    if days <= _HOURLY_MAX_DAYS:
        return hourly

    daily = _normalize_dates(_fetch_from_yfinance(ticker, period, interval))
    if daily.empty:
        return hourly
    return hourly.combine_first(daily)[["Open", "High", "Low", "Close", "Volume"]]


# ── jugaad-data path (NSE indices) ────────────────────────────────────────────

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


# ── jugaad-data path (NSE equities) ───────────────────────────────────────────

def _fetch_equity_from_nse(symbol, period, interval):
    from jugaad_data.nse import stock_df

    days = _PERIOD_TO_DAYS.get(period, 35)
    to_dt = date_type.today()
    from_dt = to_dt - timedelta(days=days)

    df = stock_df(symbol=symbol, from_date=from_dt, to_date=to_dt, series="EQ")
    if df.empty:
        return pd.DataFrame()

    # stock_df's DATE comes back as IST midnight minus 5:30 (e.g.
    # "2026-10-04 18:30:00" for the 5 Oct session) - the same east-of-UTC
    # rewind _normalize_dates() exists to undo for index_raw, showing up here
    # through a different column instead of a tz-aware index. Add the offset
    # back before treating it as a calendar date.
    dates = pd.to_datetime(df["DATE"]) + pd.Timedelta(hours=5, minutes=30)
    df = df.set_index(dates).sort_index()
    df = df.rename(columns={"OPEN": "Open", "HIGH": "High", "LOW": "Low",
                             "CLOSE": "Close", "VOLUME": "Volume"})
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])


def _jugaad_fallback(ticker):
    """(fetch_fn, symbol) for ticker's jugaad-data equivalent, or (None,
    None) if there isn't one. Covers the three Indian indices on both
    providers plus every NSE-listed equity (a yfinance ".NS" ticker, via
    stock_df - jugaad-data's own bhavcopy-based feed). FX, commodities, US
    and other global tickers have no jugaad equivalent."""
    index_symbol = _JUGAAD_FALLBACK_INDEX.get(ticker)
    if index_symbol is not None:
        return _fetch_from_nse, index_symbol
    if ticker.endswith(".NS"):
        return _fetch_equity_from_nse, ticker[:-len(".NS")]
    return None, None


def _fill_gaps_from_nse(ticker, df, period, interval):
    """Patch any date yfinance left null or omitted entirely using
    jugaad-data's close for that date instead, for anything with a
    jugaad-data equivalent (see _jugaad_fallback). yfinance's Indian feeds
    can either return a row with a null close for a date still landing, or
    skip the date's row altogether depending on exactly when it's queried -
    so "no NaN in df" does not mean "nothing missing", and this always
    merges rather than trying to detect a gap first. The outer
    get_price_history cache (max_age_hours) is what bounds how often this
    runs, not a check here - though with every NSE equity now in scope,
    that cache window is the only thing standing between this and hammering
    jugaad's scrape-based feed across a whole watchlist at once."""
    fetch_fn, nse_symbol = _jugaad_fallback(ticker)
    if fetch_fn is None or df.empty:
        return df
    try:
        nse_df = _normalize_dates(fetch_fn(nse_symbol, period, interval))
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

    One row per trading day, whatever the source underneath.

    Prefix ticker with 'NSE:' to fetch from NSE via jugaad-data, e.g.
    'NSE:NIFTY AUTO'. All other tickers go through yfinance, where recent
    dates are resampled from hourly bars and older ones come from the daily
    feed (see _fetch_daily_via_hourly) - and anything with a jugaad-data
    equivalent (Nifty 50, Bank Nifty, Nifty IT, and every NSE-listed ".NS"
    equity) additionally gets any date still left null or omitted filled in
    from jugaad-data (see _jugaad_fallback).

    `interval` is the cache's row identity, not a request to yfinance: the
    yfinance path always asks for hourly and aggregates. Results are cached
    in SQLite for max_age_hours.
    """
    conn = get_connection()
    try:
        if _is_fresh(conn, ticker, period, interval, max_age_hours):
            return _read_from_cache(conn, ticker, period, interval)

        if ticker.startswith(NSE_PREFIX):
            nse_symbol = ticker[len(NSE_PREFIX):]
            df = _fetch_from_nse(nse_symbol, period, interval)
        else:
            df = _fetch_daily_via_hourly(ticker, period, interval)
            df = _fill_gaps_from_nse(ticker, df, period, interval)

        if df.empty:
            return df
        _write_to_cache(conn, ticker, period, interval, df)
        return df
    finally:
        conn.close()
