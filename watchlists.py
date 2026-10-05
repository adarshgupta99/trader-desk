"""
Watchlist state: the four editable custom lists, and the five read-only index
views they sit alongside.

The index views are plain data below - they are not stored, cannot be edited,
and change only when the real index constituents do. Only the custom lists
touch the database.
"""

from datetime import datetime, timezone

import yfinance as yf

from prices import get_price_history
from storage import get_connection

LIST_MAX = 30


def get_custom_lists():
    conn = get_connection()
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
    conn = get_connection()
    try:
        conn.execute("UPDATE custom_lists SET name = ? WHERE id = ?", (name, list_id))
        conn.commit()
        return True, f"Renamed to {name}."
    finally:
        conn.close()


def get_list_items(list_id):
    conn = get_connection()
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
    conn = get_connection()
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

    conn = get_connection()
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
    conn = get_connection()
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
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM custom_list_items WHERE list_id = ? AND ticker = ?",
            (list_id, ticker),
        )
        conn.commit()
    finally:
        conn.close()


# ── Index views (read-only) ───────────────────────────────────────────────────
#
# Fixed (ticker, label) membership for each of the five index views. Edit
# these when an index reshuffles; nothing in the app writes to them.

PRESET_NAMES = ["Nifty 50", "Nifty Midcap", "Nifty Auto", "Nifty IT", "Bank Nifty"]

# Index-level ticker for each view's own 1D/1W return, shown beside the Change
# window toggle. Nifty Midcap has no dedicated index ticker in the app, so it
# reuses Nifty Mid Select (Adarsh's call, 5 Oct 2026).
PRESET_INDEX_TICKER = {
    "Nifty 50": "^NSEI",
    "Nifty Midcap": "NSE:NIFTY MID SELECT",
    "Nifty Auto": "NSE:NIFTY AUTO",
    "Nifty IT": "^CNXIT",
    "Bank Nifty": "^NSEBANK",
}

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
