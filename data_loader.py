import os
import sqlite3
from io import StringIO
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


# ── Custom watchlists ─────────────────────────────────────────────────────────
#
# Index presets below are fixed, read-only views. These four lists are the
# only editable ones.

CUSTOM_LIST_COUNT = 4
LIST_MAX = 30


def get_custom_lists():
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT id, name FROM custom_lists ORDER BY id").fetchall()
        return [{"id": r[0], "name": r[1]} for r in rows]
    finally:
        conn.close()


def rename_custom_list(list_id, name):
    name = name.strip()
    if not name:
        return False, "Name can't be empty."
    if len(name) > 24:
        return False, "Keep the name under 25 characters."
    conn = _get_connection()
    try:
        conn.execute("UPDATE custom_lists SET name = ? WHERE id = ?", (name, list_id))
        conn.commit()
        return True, f"Renamed to {name}."
    finally:
        conn.close()


def get_list_items(list_id):
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT ticker, label FROM custom_list_items WHERE list_id = ? ORDER BY added_at",
            (list_id,),
        ).fetchall()
        return [{"ticker": r[0], "label": r[1]} for r in rows]
    finally:
        conn.close()


def count_list_with_data(list_id):
    """How many of this list's tickers actually have cached price rows."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT ticker FROM custom_list_items WHERE list_id = ?", (list_id,)
        ).fetchall()
        count = 0
        for (ticker,) in rows:
            if conn.execute(
                "SELECT 1 FROM price_cache WHERE ticker = ? LIMIT 1", (ticker,)
            ).fetchone():
                count += 1
        return count
    finally:
        conn.close()


def _friendly_name(ticker):
    """Best-effort display name: known constituents first, then whatever the
    provider reports, then the raw ticker."""
    for members in WATCHLIST_PRESETS.values():
        for known_ticker, known_label in members:
            if known_ticker == ticker:
                return known_label
    # Read the name off the chart endpoint's metadata, not .info — .info uses
    # quoteSummary, which returns 404 for tickers whose price data loads fine.
    try:
        tk = yf.Ticker(ticker)
        tk.history(period="5d")
        meta = tk.history_metadata or {}
        name = meta.get("longName") or meta.get("shortName")
        if name:
            return name.strip()
    except Exception:
        pass
    return ticker


def add_to_list(list_id, ticker, label=None):
    """Validate the ticker has data, then add it to one list. Returns (ok, message)."""
    ticker = ticker.strip()
    if not ticker:
        return False, "Enter a ticker."

    conn = _get_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM custom_list_items WHERE list_id = ?", (list_id,)
        ).fetchone()[0]
        if count >= LIST_MAX:
            return False, f"This list is full ({LIST_MAX} max). Remove one first."
        if conn.execute(
            "SELECT 1 FROM custom_list_items WHERE list_id = ? AND ticker = ?",
            (list_id, ticker),
        ).fetchone():
            return False, f"{ticker} is already in this list."
    finally:
        conn.close()

    if get_price_history(ticker, period="5d").empty:
        return False, f"No data found for '{ticker}'. Check the ticker and try again."

    display_label = label or _friendly_name(ticker)
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO custom_list_items (list_id, ticker, label, added_at) "
            "VALUES (?, ?, ?, ?)",
            (list_id, ticker, display_label, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return True, f"{display_label} added."
    finally:
        conn.close()


def remove_from_list(list_id, ticker):
    conn = _get_connection()
    try:
        conn.execute(
            "DELETE FROM custom_list_items WHERE list_id = ? AND ticker = ?",
            (list_id, ticker),
        )
        conn.commit()
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
    ("TMCV.NS",        "Tata Motors CV"),
    ("TMPV.NS",        "Tata Motors PV"),
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
    ("TMCV.NS",        "Tata Motors CV"),
    ("TMPV.NS",        "Tata Motors PV"),
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

# ── Instrument universe (search) ──────────────────────────────────────────────
#
# Single source of truth for the hand-picked instruments. app.py derives its
# per-tab grids from this, and the searchable symbol table is seeded from it.

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

SYMBOLS_MAX_AGE_DAYS = 7


def _nse_equities():
    """(ticker, name) for every mainboard NSE equity, from NSE's own daily
    bhavcopy. Returns [] if no recent trading day can be fetched."""
    from io import StringIO

    from jugaad_data.nse import bhavcopy_raw

    day = date_type.today()
    for _ in range(8):          # walk back over weekends and holidays
        try:
            raw = bhavcopy_raw(day)
        except Exception:
            day -= timedelta(days=1)
            continue
        frame = pd.read_csv(StringIO(raw))
        frame = frame[frame["SctySrs"] == "EQ"]
        pairs = {}
        for symbol, name in zip(frame["TckrSymb"], frame["FinInstrmNm"]):
            symbol = str(symbol).strip()
            if symbol:
                pairs[f"{symbol}.NS"] = str(name).strip().title()
        return sorted(pairs.items())
    return []


_WIKI_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TraderDesk/1.0)"}

# (kind, wikipedia page, ticker column, name column, suffix, dot_to_dash)
# Yahoo suffixes differ per exchange. DAX and Euro Stoxx publish an already
# suffixed symbol, so their dots must be kept; FTSE publishes a bare LSE code;
# S&P symbols are plain, and there a dot is a share class Yahoo spells with a
# dash (BRK.B -> BRK-B).
GLOBAL_INDEX_SOURCES = [
    ("S&P 500",       "List_of_S%26P_500_companies", "Symbol", "Security", "",   True),
    ("FTSE 100",      "FTSE_100_Index",              "Ticker", "Company",  ".L", True),
    ("DAX",           "DAX",                         "Ticker", "Company",  "",   False),
    ("Euro Stoxx 50", "EURO_STOXX_50",               "Ticker", "Name",     "",   False),
]


def _global_equities():
    """(ticker, name, kind) for the major global index members. Tickers are not
    validated here — refresh_symbols drops any that do not price."""
    import requests

    out = []
    for kind, page, tcol, ncol, suffix, dot_to_dash in GLOBAL_INDEX_SOURCES:
        try:
            html = requests.get(
                f"https://en.wikipedia.org/wiki/{page}",
                headers=_WIKI_HEADERS, timeout=30,
            ).text
            frame = next(
                f for f in pd.read_html(StringIO(html))
                if tcol in map(str, f.columns) and ncol in map(str, f.columns)
                and len(f) >= 30
            )
        except Exception:
            continue
        for sym, name in zip(frame[tcol], frame[ncol]):
            sym, name = str(sym).strip(), str(name).strip()
            if not sym or sym.lower() == "nan":
                continue
            if dot_to_dash:
                sym = sym.replace(".", "-")
            out.append((sym + suffix, name, kind))
    return out


def _drop_unpriced(tickers, chunk=120):
    """Return the subset of tickers that actually return price data."""
    alive = set()
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        try:
            data = yf.download(batch, period="5d", progress=False,
                               auto_adjust=True, threads=True)
        except Exception:
            continue
        if data is None or data.empty:
            continue
        close = data["Close"] if "Close" in data else data
        if isinstance(close, pd.Series):
            if close.notna().any():
                alive.add(batch[0])
            continue
        for col in close.columns:
            if close[col].notna().any():
                alive.add(col)
    return alive


SYMBOL_SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "data", "symbols.csv")


def export_symbol_seed(path=SYMBOL_SEED_PATH):
    """Write the current symbol table to the seed file committed with the repo."""
    conn = _get_connection()
    try:
        frame = pd.read_sql(
            "SELECT ticker, name, kind FROM symbols ORDER BY kind, name", conn
        )
    finally:
        conn.close()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    frame.to_csv(path, index=False)
    return len(frame)


def seed_symbols_from_file(path=SYMBOL_SEED_PATH):
    """Load the bundled instrument table. This is what the app actually starts
    from: building it from the network needs Wikipedia and NSE to both be
    reachable, which is not true from every host, and silently yields a partial
    table when it is not. Returns the number of rows loaded."""
    if not os.path.exists(path):
        return 0
    try:
        frame = pd.read_csv(path)
    except Exception:
        return 0
    rows = [(str(t), str(n), str(k)) for t, n, k
            in zip(frame["ticker"], frame["name"], frame["kind"])]
    conn = _get_connection()
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO symbols (ticker, name, kind) VALUES (?, ?, ?)", rows
        )
        # Stamp the seed's age too, otherwise the very next page load sees no
        # refresh timestamp and kicks off a full network rebuild.
        conn.execute(
            "INSERT INTO symbols_meta (key, value) VALUES ('refreshed_at', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()
        return conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    finally:
        conn.close()


def symbol_count():
    conn = _get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    finally:
        conn.close()


def refresh_symbols(force=False):
    """Make sure the instrument table is populated. Returns (changed, message).

    The bundled seed is loaded first so the app is usable immediately and does
    not depend on Wikipedia or NSE being reachable from wherever it is hosted.
    The network rebuild then only runs when the table is stale.
    """
    if symbol_count() == 0:
        loaded = seed_symbols_from_file()
        if loaded and not force:
            return True, f"{loaded} instruments (from bundled seed)."

    conn = _get_connection()
    try:
        if not force:
            row = conn.execute(
                "SELECT value FROM symbols_meta WHERE key = 'refreshed_at'"
            ).fetchone()
            count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            if row and count:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(row[0])
                if age < timedelta(days=SYMBOLS_MAX_AGE_DAYS):
                    return False, "Symbol table is current."
    finally:
        conn.close()

    equities = _nse_equities()

    # Global members come from a community-maintained source, so a symbol can
    # be wrong or stale. Keep only the ones that actually price — a dead entry
    # in the picker is worse than a missing one.
    global_rows = _global_equities()
    if global_rows:
        alive = _drop_unpriced(sorted({t for t, _, _ in global_rows}))
        global_rows = [r for r in global_rows if r[0] in alive]

    conn = _get_connection()
    try:
        rows = [(t, n, c) for c, n, t in CURATED_INSTRUMENTS]
        rows += global_rows
        rows += [(t, n, "NSE equity") for t, n in equities]
        # Curated entries are inserted first and win on conflict, so their
        # hand-written names survive the bulk NSE load.
        conn.executemany(
            "INSERT OR IGNORE INTO symbols (ticker, name, kind) VALUES (?, ?, ?)", rows
        )
        conn.execute(
            "INSERT INTO symbols_meta (key, value) VALUES ('refreshed_at', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    finally:
        conn.close()

    if not equities:
        return True, f"{total} instruments (NSE list unavailable — curated only)."
    return True, f"{total} instruments."


def get_all_symbols():
    """Every searchable instrument, ordered so a dropdown using
    `filter_mode="contains"` surfaces indices and FX first, then large-cap
    index members, then the rest of the NSE board."""
    conn = _get_connection()
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
    conn = _get_connection()
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


def clear_price_cache():
    """Delete all cached price data so everything re-fetches from yfinance."""
    conn = _get_connection()
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
