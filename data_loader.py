import sqlite3
from datetime import datetime, timedelta, timezone, date as date_type

import pandas as pd
import yfinance as yf

DB_PATH = "market_cache.db"

# NSE index names that jugaad-data can fetch (passed as ticker in the app,
# prefixed with "NSE:" so the loader knows to route them to jugaad-data)
NSE_PREFIX = "NSE:"


def _get_connection():
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
        CREATE TABLE IF NOT EXISTS watchlist (
            ticker TEXT PRIMARY KEY,
            label  TEXT NOT NULL,
            added_at TEXT NOT NULL
        )
        """
    )
    return conn


# ── Watchlist ─────────────────────────────────────────────────────────────────

WATCHLIST_MAX = 30

def get_watchlist():
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT ticker, label FROM watchlist ORDER BY added_at"
        ).fetchall()
        return [{"ticker": r[0], "label": r[1]} for r in rows]
    finally:
        conn.close()


def add_to_watchlist(ticker, label=None):
    """Validate ticker has data, then save to watchlist. Returns (ok, message)."""
    ticker = ticker.strip()
    if not ticker:
        return False, "Enter a ticker."

    conn = _get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        if count >= WATCHLIST_MAX:
            return False, f"Watchlist is full ({WATCHLIST_MAX} max). Remove one first."

        exists = conn.execute(
            "SELECT 1 FROM watchlist WHERE ticker = ?", (ticker,)
        ).fetchone()
        if exists:
            return False, f"{ticker} is already in your watchlist."
    finally:
        conn.close()

    df = get_price_history(ticker, period="5d")
    if df.empty:
        return False, f"No data found for '{ticker}'. Check the ticker and try again."

    display_label = label or ticker
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO watchlist (ticker, label, added_at) VALUES (?, ?, ?)",
            (ticker, display_label, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return True, f"{display_label} added."
    finally:
        conn.close()


WATCHLIST_PRESETS = {}

WATCHLIST_PRESETS["Nifty 50"] = [
    ("HDFCBANK.NS",    "HDFC Bank"),
    ("RELIANCE.NS",    "Reliance"),
    ("ICICIBANK.NS",   "ICICI Bank"),
    ("INFY.NS",        "Infosys"),
    ("TCS.NS",         "TCS"),
    ("BHARTIARTL.NS",  "Bharti Airtel"),
    ("ITC.NS",         "ITC"),
    ("LT.NS",          "L&T"),
    ("AXISBANK.NS",    "Axis Bank"),
    ("HINDUNILVR.NS",  "HUL"),
    ("KOTAKBANK.NS",   "Kotak Bank"),
    ("SBIN.NS",        "SBI"),
    ("MARUTI.NS",      "Maruti"),
    ("SUNPHARMA.NS",   "Sun Pharma"),
    ("M&M.NS",         "M&M"),
    ("BAJFINANCE.NS",  "Bajaj Finance"),
    ("WIPRO.NS",       "Wipro"),
    ("HCLTECH.NS",     "HCL Tech"),
    ("TITAN.NS",       "Titan"),
    ("NTPC.NS",        "NTPC"),
    ("POWERGRID.NS",   "Power Grid"),
    ("TATAMOTORS.NS",  "Tata Motors"),
    ("ONGC.NS",        "ONGC"),
    ("ADANIPORTS.NS",  "Adani Ports"),
    ("JSWSTEEL.NS",    "JSW Steel"),
    ("TATASTEEL.NS",   "Tata Steel"),
    ("CIPLA.NS",       "Cipla"),
    ("DRREDDY.NS",     "Dr Reddy's"),
    ("BAJAJ-AUTO.NS",  "Bajaj Auto"),
    ("NESTLEIND.NS",   "Nestle India"),
]

WATCHLIST_PRESETS["Nifty Midcap"] = [
    ("TRENT.NS",       "Trent"),
    ("PERSISTENT.NS",  "Persistent"),
    ("TIINDIA.NS",     "Tube Investments"),
    ("KALYANKJIL.NS",  "Kalyan Jewellers"),
    ("OBEROIRLTY.NS",  "Oberoi Realty"),
    ("BHARATFORG.NS",  "Bharat Forge"),
    ("CUMMINSIND.NS",  "Cummins India"),
    ("JSWENERGY.NS",   "JSW Energy"),
    ("KPITTECH.NS",    "KPIT Tech"),
    ("VOLTAS.NS",      "Voltas"),
    ("MAXHEALTH.NS",   "Max Healthcare"),
    ("LUPIN.NS",       "Lupin"),
    ("TORNTPHARM.NS",  "Torrent Pharma"),
    ("HAL.NS",         "HAL"),
    ("PAGEIND.NS",     "Page Industries"),
    ("ASTRAL.NS",      "Astral"),
    ("BALKRISIND.NS",  "Balkrishna Ind"),
    ("INDHOTEL.NS",    "Indian Hotels"),
    ("MUTHOOTFIN.NS",  "Muthoot Finance"),
    ("CHOLAFIN.NS",    "Cholamandalam"),
]

WATCHLIST_PRESETS["Nifty Auto"] = [
    ("MARUTI.NS",      "Maruti"),
    ("M&M.NS",         "M&M"),
    ("TATAMOTORS.NS",  "Tata Motors"),
    ("BAJAJ-AUTO.NS",  "Bajaj Auto"),
    ("HEROMOTOCO.NS",  "Hero MotoCorp"),
    ("EICHERMOT.NS",   "Eicher Motors"),
    ("TVSMOTOR.NS",    "TVS Motor"),
    ("BOSCHLTD.NS",    "Bosch"),
    ("MRF.NS",         "MRF"),
    ("ASHOKLEY.NS",    "Ashok Leyland"),
    ("MOTHERSON.NS",   "Motherson"),
    ("APOLLOTYRE.NS",  "Apollo Tyres"),
    ("TIINDIA.NS",     "Tube Investments"),
    ("EXIDEIND.NS",    "Exide"),
    ("BALKRISIND.NS",  "Balkrishna Ind"),
]

WATCHLIST_PRESETS["Nifty IT"] = [
    ("TCS.NS",         "TCS"),
    ("INFY.NS",        "Infosys"),
    ("HCLTECH.NS",     "HCL Tech"),
    ("WIPRO.NS",       "Wipro"),
    ("TECHM.NS",       "Tech Mahindra"),
    ("LTIM.NS",        "LTIMindtree"),
    ("PERSISTENT.NS",  "Persistent"),
    ("MPHASIS.NS",     "Mphasis"),
    ("COFORGE.NS",     "Coforge"),
    ("KPITTECH.NS",    "KPIT Tech"),
]

WATCHLIST_PRESETS["Bank Nifty"] = [
    ("HDFCBANK.NS",    "HDFC Bank"),
    ("ICICIBANK.NS",   "ICICI Bank"),
    ("KOTAKBANK.NS",   "Kotak Bank"),
    ("AXISBANK.NS",    "Axis Bank"),
    ("SBIN.NS",        "SBI"),
    ("INDUSINDBK.NS",  "IndusInd Bank"),
    ("BANKBARODA.NS",  "Bank of Baroda"),
    ("PNB.NS",         "PNB"),
    ("IDFCFIRSTB.NS",  "IDFC First"),
    ("FEDERALBNK.NS",  "Federal Bank"),
    ("AUBANK.NS",      "AU Small Finance"),
    ("BANDHANBNK.NS",  "Bandhan Bank"),
]

NIFTY50_TOP30 = WATCHLIST_PRESETS["Nifty 50"]  # kept for seed_watchlist_if_empty


def seed_watchlist_if_empty():
    """Populate watchlist with top 30 Nifty stocks on first ever launch only."""
    conn = _get_connection()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS app_flags (key TEXT PRIMARY KEY, value TEXT)")
        already_seeded = conn.execute(
            "SELECT 1 FROM app_flags WHERE key = 'watchlist_seeded'"
        ).fetchone()
        if already_seeded:
            return
        conn.execute("INSERT INTO app_flags (key, value) VALUES ('watchlist_seeded', '1')")
        conn.commit()
        now = datetime.now(timezone.utc)
        rows = [
            (ticker, label, (now.replace(microsecond=i)).isoformat())
            for i, (ticker, label) in enumerate(NIFTY50_TOP30)
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO watchlist (ticker, label, added_at) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def reset_watchlist_to_preset(preset_name):
    """Clear watchlist and repopulate with a named preset."""
    stocks = WATCHLIST_PRESETS.get(preset_name, [])
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM watchlist")
        now = datetime.now(timezone.utc)
        rows = [
            (ticker, label, (now.replace(microsecond=i)).isoformat())
            for i, (ticker, label) in enumerate(stocks)
        ]
        conn.executemany(
            "INSERT INTO watchlist (ticker, label, added_at) VALUES (?, ?, ?)", rows
        )
        conn.commit()
    finally:
        conn.close()


def reset_watchlist_to_nifty50():
    reset_watchlist_to_preset("Nifty 50")


def remove_from_watchlist(ticker):
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
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
    # Normalize index to UTC so all stored timestamps are in a single format
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

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
    conn = _get_connection()
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
