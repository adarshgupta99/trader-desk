"""
Read-only access to events.db for the Streamlit app. The write side lives in
scripts/fetch_events.py; the app never writes to this file.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta

import pandas as pd
import streamlit as st
import yfinance as yf

import live_data_cache
from jobs.snapshot_estimates import fetch_estimate

DB_PATH = os.path.join(os.path.dirname(__file__), "events.db")

IST = timezone(timedelta(hours=5, minutes=30))

ESTIMATE_PREVIEW_TTL = 21600  # 6h - consensus doesn't move hour to hour
EPS_HISTORY_TTL = 21600  # 6h, same rationale
EPS_DATE_MATCH_TOLERANCE_DAYS = 1  # Yahoo's earnings date can be a day off NSE's


def _read_connection():
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


@st.cache_data(ttl=3600)
def get_upcoming_events(symbols=None, lookback_days=0):
    """Events with event_date >= today (IST) minus lookback_days, ordered by
    event_date ascending.

    lookback_days defaults to 0 (today onward, the original behavior) - pass
    a positive value to also include recently-past events, e.g. so an
    earnings row's actual/surprise stays visible for a while after the
    result is out instead of disappearing the moment event_date passes.

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

    from_date = (datetime.now(IST) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    query = f"SELECT {', '.join(columns)} FROM events WHERE event_date >= ?"
    params = [from_date]

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
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        # events table doesn't exist yet (file present but never populated)
        return pd.DataFrame(columns=columns)


@st.cache_data(ttl=3600)
def get_earnings_estimates(symbols=None):
    """Frozen analyst-estimate snapshots, one row per (symbol, event_date).

    Written by jobs/snapshot_estimates.py and jobs/fill_actuals.py; this app
    only ever reads it. Returns an empty DataFrame if the table doesn't
    exist yet - never raises. If more than one snapshot row exists for the
    same (symbol, event_date) - shouldn't happen given the snapshot job's
    skip-if-exists check, but defensive - the most recently taken one wins.
    """
    columns = [
        "symbol", "event_date", "snapshot_date", "eps_estimate",
        "revenue_estimate", "n_analysts_eps", "n_analysts_rev",
        "revenue_actual_cons", "revenue_actual_std",
        "pat_actual_cons", "pat_actual_std",
        "eps_actual_cons", "eps_actual_std",
    ]
    if not os.path.exists(DB_PATH):
        return pd.DataFrame(columns=columns)

    query = f"SELECT {', '.join(columns)} FROM earnings_estimates"
    params = []
    if symbols:
        symbols = list(symbols)
        if not symbols:
            return pd.DataFrame(columns=columns)
        placeholders = ", ".join("?" * len(symbols))
        query += f" WHERE symbol IN ({placeholders})"
        params.extend(symbols)

    try:
        with _read_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params)
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        return pd.DataFrame(columns=columns)

    # read_sql_query turns a NULL numeric column into NaN, not None - the UI
    # checks "is None" to tell "no estimate" from a real zero, so normalize.
    df = df.astype(object).where(pd.notna(df), None)

    return (
        df.sort_values("snapshot_date", ascending=False)
        .drop_duplicates(subset=["symbol", "event_date"], keep="first")
    )


@st.cache_data(ttl=ESTIMATE_PREVIEW_TTL)
def get_live_estimate_preview(symbol):
    """Unfrozen, best-effort analyst consensus for an event that hasn't hit
    its T-1 snapshot yet - never stored in earnings_estimates, never
    compared against an actual, and superseded the moment
    jobs/snapshot_estimates.py freezes the real row for that event.

    Two cache layers: st.cache_data makes repeat calls within the same
    running process free; live_data_cache persists the result to disk so a
    Streamlit restart (a code change, a Cloud redeploy) doesn't throw the
    whole day's fetches away and repay the full cost on the next render -
    only process memory is lost, not the work. Returns None on any
    failure, rate limits included, or if yfinance genuinely has nothing -
    never raises either way.
    """
    cached = live_data_cache.get(symbol, "estimate_preview", ESTIMATE_PREVIEW_TTL)
    if cached is not live_data_cache.MISS:
        return cached
    try:
        value = fetch_estimate(symbol)
    except Exception:
        value = None
    live_data_cache.set(symbol, "estimate_preview", value)
    return value


@st.cache_data(ttl=EPS_HISTORY_TTL)
def get_eps_history(symbol):
    """Yahoo's own per-quarter EPS estimate/actual/surprise history for
    `symbol`, read live via yfinance - no local storage, no T-1 freezing
    needed on our side. Once a quarter reports, Yahoo keeps that quarter's
    pre-print estimate stored permanently alongside the actual (verified
    against our own pre-print capture for TCS's 2026-10-08 result: matched
    to within rounding - 38.07 vs 38.06858), so unlike revenue, EPS doesn't
    need jobs/snapshot_estimates.py's freeze-and-store machinery.

    Returns a DataFrame with columns [event_date, eps_estimate, eps_actual,
    eps_surprise_pct], one row per quarter yfinance has (reported, or the
    next upcoming with eps_actual still None), most recent first. Empty
    DataFrame on any failure or if yfinance has nothing - never raises.
    """
    columns = ["event_date", "eps_estimate", "eps_actual", "eps_surprise_pct"]
    try:
        df = yf.Ticker(f"{symbol}.NS").get_earnings_dates(limit=12)
    except Exception:
        return pd.DataFrame(columns=columns)
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)

    df = df.reset_index()
    df = df.rename(columns={
        df.columns[0]: "_date", "EPS Estimate": "eps_estimate",
        "Reported EPS": "eps_actual", "Surprise(%)": "eps_surprise_pct",
    })
    df["event_date"] = df["_date"].apply(lambda d: d.date().isoformat() if hasattr(d, "date") else None)
    df = df.astype(object).where(pd.notna(df), None)
    return df[columns]


def match_eps_row(eps_df, event_date_str):
    """Row from get_eps_history() nearest to event_date_str within
    +/- EPS_DATE_MATCH_TOLERANCE_DAYS, or None if nothing matches that
    close. Same tolerance jobs/fill_actuals.py uses for the same reason."""
    if eps_df is None or eps_df.empty or not event_date_str:
        return None
    target = datetime.strptime(event_date_str, "%Y-%m-%d").date()
    best, best_gap = None, None
    for _, row in eps_df.iterrows():
        if row["event_date"] is None:
            continue
        gap = abs((datetime.strptime(row["event_date"], "%Y-%m-%d").date() - target).days)
        if gap <= EPS_DATE_MATCH_TOLERANCE_DAYS and (best_gap is None or gap < best_gap):
            best, best_gap = row, gap
    return best


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


@st.cache_data(ttl=3600)
def get_expected_move_summary(symbol, event_date):
    """The latest-calculation-version cohort summary for one upcoming
    event, or None if jobs/refresh_expected_moves.py hasn't produced one
    yet (e.g. fewer than 1 usable print exists, or the symbol/event hasn't
    been backfilled). Written by jobs/refresh_expected_moves.py; this app
    only ever reads it."""
    if not os.path.exists(DB_PATH):
        return None
    try:
        with _read_connection() as conn:
            row = conn.execute(
                "SELECT symbol, event_date, lookback_target, found_count, "
                "selected_count, usable_count, excluded_count, expected_move_pct, "
                "worst_case_pct, calculation_version, inputs_cutoff, last_success_at, "
                "refresh_status FROM earnings_move_summaries "
                "WHERE symbol = ? AND event_date = ? "
                "ORDER BY calculation_version DESC LIMIT 1",
                (symbol, event_date),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    cols = ["symbol", "event_date", "lookback_target", "found_count", "selected_count",
            "usable_count", "excluded_count", "expected_move_pct", "worst_case_pct",
            "calculation_version", "inputs_cutoff", "last_success_at", "refresh_status"]
    return dict(zip(cols, row))


@st.cache_data(ttl=3600)
def get_all_expected_moves():
    """One row per F&O-universe symbol (from fno_sectors.json), rolling up
    each symbol's latest 8 prints (by filing date, any status) into the
    same expected/worst-case aggregate jobs/refresh_expected_moves.py
    computes per upcoming event - except not tied to a specific upcoming
    event_date, so a symbol with no scheduled next result (e.g. one that
    already reported this cycle) still shows its most recent completed-
    quarters profile. next_event_date is attached separately, for display
    only, and is None if nothing is currently scheduled.

    A symbol with zero stored prints (e.g. MCX, which NSE's corporate-
    announcements archive returns no filings for at all) is still
    returned, with expected_move_pct/worst_case_pct as None - same "kept,
    never dropped" rule the analyst-estimates tracker uses for a row with
    no estimate.

    For a (symbol, financial_period) recomputed under more than one
    calculation_version, only the highest version's row is used - same
    "latest version wins" rule get_expected_move_summary applies per
    event. Returns [] if fno_sectors.json is missing or the table doesn't
    exist yet - never raises."""
    try:
        with open(os.path.join(os.path.dirname(__file__), "fno_sectors.json")) as f:
            universe = sorted(json.load(f)["stocks"].keys())
    except (OSError, KeyError, ValueError):
        return []
    if not os.path.exists(DB_PATH):
        return []
    try:
        with _read_connection() as conn:
            rows = conn.execute(
                "SELECT symbol, financial_period, calculation_version, status, "
                "print_move_pct, filing_ts_utc FROM earnings_move_prints"
            ).fetchall()
            next_event_rows = conn.execute(
                "SELECT symbol, MIN(event_date) FROM events WHERE event_type='board_meeting' "
                "AND purpose LIKE '%Financial Results%' AND event_date >= ? GROUP BY symbol",
                (datetime.now(IST).strftime("%Y-%m-%d"),),
            ).fetchall()
    except sqlite3.OperationalError:
        rows, next_event_rows = [], []

    next_event = dict(next_event_rows)

    # Keep only the highest calculation_version row per (symbol, period).
    latest_version = {}
    for symbol, period, version, status, move_pct, filing_ts in rows:
        key = (symbol, period)
        if key not in latest_version or version > latest_version[key][0]:
            latest_version[key] = (version, status, move_pct, filing_ts)

    by_symbol = {}
    for (symbol, _period), (_version, status, move_pct, filing_ts) in latest_version.items():
        by_symbol.setdefault(symbol, []).append((filing_ts, status, move_pct))

    results = []
    for symbol in universe:
        prints = by_symbol.get(symbol, [])
        prints.sort(key=lambda p: p[0])
        selected = prints[-8:]
        usable_moves = [p[2] for p in selected if p[1] == "usable" and p[2] is not None]
        if usable_moves:
            expected_pct = sum(usable_moves) / len(usable_moves)
            worst_pct = max(usable_moves)
        else:
            expected_pct = worst_pct = None
        results.append({
            "symbol": symbol,
            "found_count": len(prints),
            "selected_count": len(selected),
            "usable_count": len(usable_moves),
            "expected_move_pct": expected_pct,
            "worst_case_pct": worst_pct,
            "next_event_date": next_event.get(symbol),
        })
    return results


@st.cache_data(ttl=3600)
def get_earnings_call_sentiment_symbols():
    """Distinct tickers with at least one stored row in
    earnings_call_sentiment, alphabetical - the Sentiment tab's symbol
    picker only ever offers symbols that actually have something to show.
    Empty list if the table doesn't exist yet or has no rows - never
    raises."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        with _read_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ticker FROM earnings_call_sentiment ORDER BY ticker"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r[0] for r in rows]


@st.cache_data(ttl=3600)
def get_earnings_call_sentiment(ticker):
    """Every stored quarter for `ticker`, oldest first - written by
    jobs/refresh_earnings_sentiment.py, read-only here. Returns an empty
    list if the table doesn't exist yet or the ticker has no rows -
    never raises."""
    if not os.path.exists(DB_PATH):
        return []
    columns = ["quarter_end", "call_date", "transcript_source_url",
               "lm_positive", "lm_negative", "lm_uncertainty", "lm_litigious",
               "lm_total_words", "mgmt_tone", "qa_tone", "guidance"]
    try:
        with _read_connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM earnings_call_sentiment "
                "WHERE ticker = ? ORDER BY quarter_end ASC",
                (ticker,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(columns, r)) for r in rows]


@st.cache_data(ttl=3600)
def get_earnings_history_quarters(symbol):
    """The last 6 reported quarters stored for `symbol` (one row per
    quarter_end, oldest first) for the Calendar subtab's HISTORY 6Q
    sparkline - see jobs/refresh_earnings_history.py and
    db/schema_earnings_history.sql.

    A row with revenue_cons/pat_cons both None is a real, stored gap (NSE
    has no filing for that quarter) - kept, not filtered out, so the
    sparkline can render a blank space at the right position rather than
    silently compressing the window. Returns an empty list if the table
    doesn't exist yet or the symbol has never been refreshed - never
    raises; the caller treats that as "no history" the same way."""
    if not os.path.exists(DB_PATH):
        return []
    columns = ["quarter_end", "revenue_cons", "pat_cons"]
    try:
        with _read_connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM earnings_history_quarters "
                "WHERE symbol = ? ORDER BY quarter_end ASC",
                (symbol,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(columns, r)) for r in rows]


@st.cache_data(ttl=3600)
def get_expected_move_history(symbol):
    """Every stored print for a symbol, newest filing first - the
    expandable history behind the Calendar row's summary. Returns an
    empty list if the table doesn't exist yet or has no rows for this
    symbol - never raises."""
    if not os.path.exists(DB_PATH):
        return []
    columns = [
        "financial_period", "filing_ts_raw", "filing_ts_ist", "release_mode",
        "reference_price", "reference_method", "reference_offset_minutes",
        "session_a_date", "session_b_date", "move_a_pct", "move_b_pct",
        "print_move_pct", "status", "reason_codes", "price_basis", "attachment_url",
    ]
    try:
        with _read_connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM earnings_move_prints "
                "WHERE symbol = ? ORDER BY filing_ts_utc DESC",
                (symbol,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(columns, r)) for r in rows]
