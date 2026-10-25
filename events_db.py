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
               "lm_total_words", "management_confidence", "overall_tone",
               "qa_tone", "guidance", "analyst_tone",
               "evasiveness_count", "qa_divergence_flag"]
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
        "print_move_pct", "signed_move_pct", "status", "reason_codes",
        "price_basis", "attachment_url",
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


# ── v3 sector-aware extraction (Guidance tab) ────────────────────────────────
# Read-only, same discipline as every accessor above: return empty rather than
# raise when a table does not exist yet, so the app renders on a fresh clone
# before any extraction job has run.

@st.cache_data(ttl=3600)
def get_extraction_symbols():
    """Symbols with at least one stored v3 extraction, alphabetical."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        with _read_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM earnings_call_extraction "
                "WHERE row_status='OK' ORDER BY symbol"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r[0] for r in rows]


@st.cache_data(ttl=3600)
def get_extraction_quarters(symbol):
    """Every stored quarter for a symbol, newest first - the quarter selector
    and the history strip both read this."""
    if not os.path.exists(DB_PATH):
        return []
    columns = ["quarter_end", "fiscal_quarter", "call_date", "price_move_pct",
               "mgmt_confidence", "overall_tone", "qa_tone", "analyst_tone",
               "guidance_cell", "evasive_count", "evasive_exchanges",
               "qa_divergence", "qa_divergence_note", "transcript_url",
               "sector", "sector_card", "sector_unmapped", "row_status",
               "cite_failures", "stage2_model", "extractor_version",
               "announcement_at", "source_published_at", "feature_available_at",
               "timestamp_quality", "pushback_total", "pushback_dodged",
               "pushback_example_quote", "pushback_example_exchange_ref",
               "pushback_summary"]
    # One row per quarter, latest extractor_version only. A re-extraction
    # under a new version ADDS a row (the idempotency key includes the
    # version) rather than replacing the old one, so without this every
    # re-extracted quarter showed up twice in the quarter picker - seen
    # live after the v3.2.0 backfill left v3.0.0 rows in place beside it.
    try:
        with _read_connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM earnings_call_extraction e "
                "WHERE symbol=? AND row_status='OK' "
                "AND extractor_version = ("
                "  SELECT MAX(e2.extractor_version) FROM earnings_call_extraction e2 "
                "  WHERE e2.symbol = e.symbol AND e2.quarter_end = e.quarter_end "
                "    AND e2.row_status='OK') "
                "ORDER BY quarter_end DESC",
                (symbol,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(columns, r)) for r in rows]


@st.cache_data(ttl=3600)
def get_guidance_formal(symbol, quarter_end):
    """The four generic guidance fields for one quarter, keyed by field_key.
    NOT_GIVEN rows are included deliberately - the UI must be able to show a
    designed 'NOT GIVEN' state rather than an absent cell."""
    if not os.path.exists(DB_PATH):
        return {}
    columns = ["field_key", "status", "value_text", "value_num_low",
               "value_num_high", "unit", "applies_to_period",
               "prior_value_text", "quote", "exchange_ref"]
    try:
        with _read_connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM earnings_guidance_formal "
                "WHERE symbol=? AND quarter_end=?", (symbol, quarter_end),
            ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r[0]: dict(zip(columns, r)) for r in rows}


@st.cache_data(ttl=3600)
def get_guidance_soft(symbol, quarter_end):
    if not os.path.exists(DB_PATH):
        return []
    columns = ["item_no", "topic", "direction", "quote", "exchange_ref"]
    try:
        with _read_connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM earnings_guidance_soft "
                "WHERE symbol=? AND quarter_end=? ORDER BY item_no",
                (symbol, quarter_end),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(columns, r)) for r in rows]


@st.cache_data(ttl=3600)
def get_sector_slots(symbol, quarter_end):
    if not os.path.exists(DB_PATH):
        return []
    columns = ["slot_key", "sector", "status", "value_text", "value_num",
               "unit", "period_basis", "speaker_role", "quote", "exchange_ref"]
    try:
        with _read_connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM earnings_sector_slots "
                "WHERE symbol=? AND quarter_end=? ORDER BY slot_key",
                (symbol, quarter_end),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(columns, r)) for r in rows]


@st.cache_data(ttl=3600)
def get_estimate_surprise(symbol, period_end_date=None):
    """Stored estimate-vs-actual comparisons for a symbol (spec 5A). Every
    row carries the source's own caveats, which the UI must surface - these
    are not verified pre-print consensus figures."""
    if not os.path.exists(DB_PATH):
        return []
    columns = ["period_end_date", "source_quarter_label", "metric",
               "estimate_value", "actual_value", "surprise_pct", "unit",
               "basis", "snapshot_status", "comparability", "flags"]
    query = (f"SELECT {', '.join(columns)} FROM earnings_estimate_surprise "
             "WHERE symbol=?")
    params = [symbol]
    if period_end_date:
        query += " AND period_end_date=?"
        params.append(period_end_date)
    try:
        with _read_connection() as conn:
            rows = conn.execute(query + " ORDER BY period_end_date DESC, metric",
                                params).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(columns, r)) for r in rows]


@st.cache_data(ttl=3600)
def get_risks(symbol, quarter_end):
    """Risks disclosed on the call (Guidance tab redesign). Stored as the
    'risks_disclosed' row in earnings_frame with the full list in value_json
    - a NOT_GIVEN row means the frame was extracted but no risks were raised,
    distinct from the symbol/quarter never having been extracted at all."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        with _read_connection() as conn:
            row = conn.execute(
                "SELECT status, value_json FROM earnings_frame "
                "WHERE symbol=? AND quarter_end=? AND field_key='risks_disclosed'",
                (symbol, quarter_end),
            ).fetchone()
    except sqlite3.OperationalError:
        return []
    if not row or row[0] != "GIVEN" or not row[1]:
        return []
    try:
        return json.loads(row[1])
    except (TypeError, ValueError):
        return []


@st.cache_data(ttl=3600)
def get_verdict(symbol, quarter_end):
    """The stage-3 synthesized verdict for one quarter, or None if the
    synthesis call hasn't run or failed - the UI must not invent a verdict
    when this is absent."""
    if not os.path.exists(DB_PATH):
        return None
    columns = ["headline", "supporting_line", "chips", "generated_at"]
    try:
        with _read_connection() as conn:
            row = conn.execute(
                f"SELECT {', '.join(columns)} FROM earnings_verdict "
                "WHERE symbol=? AND quarter_end=? "
                "ORDER BY extractor_version DESC LIMIT 1",
                (symbol, quarter_end),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    result = dict(zip(columns, row))
    try:
        result["chips"] = json.loads(result["chips"]) if result["chips"] else []
    except (TypeError, ValueError):
        result["chips"] = []
    return result


@st.cache_data(ttl=3600)
def get_commitments(symbol, quarter_end):
    """Commitments This Call (v3.2.0 Guidance tab redesign): company-specific
    named line items management put a number, count or timeline on - broader
    than the four generic earnings_guidance_formal fields, which are
    NOT_GIVEN for most companies. Latest extractor_version only."""
    if not os.path.exists(DB_PATH):
        return []
    columns = ["item_no", "metric", "value", "support", "status", "quote",
               "exchange_ref"]
    try:
        with _read_connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM earnings_commitments "
                "WHERE symbol=? AND quarter_end=? "
                "AND extractor_version = (SELECT MAX(extractor_version) "
                "FROM earnings_commitments c2 WHERE c2.symbol=earnings_commitments.symbol "
                "AND c2.quarter_end=earnings_commitments.quarter_end) "
                "ORDER BY item_no",
                (symbol, quarter_end),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(columns, r)) for r in rows]


@st.cache_data(ttl=3600)
def get_promise_checks(symbol, quarter_end):
    """Promises Tested (v3.2.0 Guidance tab redesign): each row tests one of
    the PRIOR quarter's own commitments against what THIS quarter's call
    actually says. Empty when the prior quarter has no stored commitments to
    test (e.g. its own extraction predates v3.2.0) - a normal, visible gap,
    not an error; the UI hides the section rather than showing it empty."""
    if not os.path.exists(DB_PATH):
        return []
    columns = ["item_no", "metric", "promised_value", "promised_support",
               "delivered_value", "quote", "exchange_ref", "verdict",
               "prior_quarter_end"]
    try:
        with _read_connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM earnings_promise_checks "
                "WHERE symbol=? AND quarter_end=? "
                "AND extractor_version = (SELECT MAX(extractor_version) "
                "FROM earnings_promise_checks p2 WHERE p2.symbol=earnings_promise_checks.symbol "
                "AND p2.quarter_end=earnings_promise_checks.quarter_end) "
                "ORDER BY item_no",
                (symbol, quarter_end),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(columns, r)) for r in rows]
