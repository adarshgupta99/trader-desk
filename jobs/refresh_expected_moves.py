"""Discover, backfill, and compute the expected-move feature (spec
expected_move_spec-3c050a43.txt), for a set of F&O symbols.

Run manually: python -m jobs.refresh_expected_moves --symbols TCS,RELIANCE
Backfill mode (further back, for first run on a new symbol):
  python -m jobs.refresh_expected_moves --symbols TCS --backfill
With no --symbols, runs every distinct symbol currently in events.events
with a results-related board_meeting purpose (the same orientation
events_db.py already uses - see CLAUDE.md's "Analyst estimates tracker"
section: earnings candidates are event_type='board_meeting' with
results-related purpose text, not a separate event_type).

Pipeline per symbol, in the spec's own order (section 9/12):
  1. Fetch the NSE corporate-announcements archive, identify distinct
     result filings (financial_period, filing_ts, cluster) - see
     identify_filings() and docs/expected_move_discovery.txt for the
     text-pattern/dedup rules this encodes.
  2. Fetch hourly (+ daily, for post-close/pre-open/non-session modes)
     price bars via yfinance.
  3. Compute each print's reference/windows/move via analytics.expected_move
     (pure, offline, fixture-tested separately in tests/test_expected_move.py).
  4. Write/update earnings_move_prints, then roll up the latest-8 cohort
     per upcoming event into earnings_move_summaries.

Idempotent: reruns do not duplicate rows (UNIQUE constraints) and do not
move an existing print's filing identity (trg_earnings_move_prints_identity_frozen
in db/schema_expected_move.sql blocks that - a later revision filing for
an already-recorded period is appended to results_cluster with
'source_conflict' instead, never swapped in as the new identity).

Polite fetching: one symbol at a time, a pause between NSE archive calls,
nselib's own cookie-bootstrap helper (nse_urlfetch) reused rather than a
hand-rolled session - see scripts/discover_nse_archive.py, the same
approach already verified from both a local run and a GitHub Actions
runner (docs/expected_move_discovery.txt section B/C/F/G).
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import pandas as pd
import yfinance as yf
from nselib.libutil import nse_urlfetch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from analytics import expected_move as em
import trading_calendar as tc

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "events.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "schema_expected_move.sql")
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "refresh_expected_moves.log")
IST = timezone(timedelta(hours=5, minutes=30))

ARCHIVE_BASE = "https://www.nseindia.com/api/corporate-announcements"
ARCHIVE_PAUSE_SECONDS = 2.0
HOURLY_PAUSE_SECONDS = 1.0

CALCULATION_VERSION = "v1"
PRICE_BASIS = "yfinance_auto_adjust"  # see report note: split-adjustment verified against discovery; dividend total-return NOT separately verified
LOOKBACK_TARGET = 8

#: Final text pattern from docs/expected_move_discovery.txt (TCS/HDFCBANK/
#: INFY/WIPRO base case + RELIANCE's one capped fallback round). Anchored
#: on an act verb or a text that starts directly with the results
#: statement - never a bare "financial results...ended" keyword match,
#: which is what produced the HDFCBANK earnings-call false positive.
RESULTS_PATTERN = re.compile(
    r'(submitted to the exchange,?\s*the\s*(consolidated and standalone\s+)?(unaudited\s+)?financial results.*ended'
    r'|approved the.*financial results.*(quarter and )?year ended'
    r'|please find attached.*financial results.*ended'
    r'|^\s*unaudited financial results \(consolidated and standalone\) for the quarter ended)',
    re.IGNORECASE,
)

PERIOD_END_PATTERN = re.compile(r'ended\s+([A-Za-z]+\s+\d{1,2},?\s*\d{4})', re.IGNORECASE)

# Corporate actions that change the stock's price basis (bonus/split/demerger
# etc.) without that being a market reaction to anything - unlike a plain cash
# dividend, which yfinance's auto_adjust already handles as an ordinary total-
# return adjustment. "Dividend" is deliberately excluded here.
DISCONTINUITY_KEYWORDS = (
    "bonus", "split", "subdivision", "sub-division", "scheme of arrangement",
    "demerger", "consolidation of shares", "face value",
)
RECORD_DATE_PATTERNS = (
    re.compile(r'is\s+(\d{1,2}-[A-Za-z]{3}-\d{4})'),           # "is 16-Jun-2025"
    re.compile(r'fixed as\s+([A-Za-z]+\s+\d{1,2},?\s*\d{4})'),  # "fixed as May 01, 2026"
    re.compile(r'is\s+([A-Za-z]+\s+\d{1,2},?\s*\d{4})'),        # "is May 01, 2026"
)


def _parse_record_date(text):
    for pattern in RECORD_DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        raw = m.group(1)
        for fmt in ("%d-%b-%Y", "%B %d %Y", "%B %d, %Y", "%b %d %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(raw.replace(",", ""), fmt.replace(",", "")).date()
            except ValueError:
                continue
    return None


def find_price_discontinuities(archive_rows):
    """Record dates of corporate actions (from the same already-fetched NSE
    archive used for result filings) that change the stock's price basis -
    bonus issues, splits, demergers/schemes of arrangement. yfinance's
    auto_adjust=True back-adjusts *daily* history for these but not hourly
    history (confirmed live against BAJFINANCE, SIEMENS, VEDL - see
    ARCHITECTURE.md), so any print whose filing predates one of these dates,
    computed after it, mixes two price bases and produces a nonsensical
    move%. Returns a sorted list of dates; unparseable Record Date rows are
    skipped, not guessed."""
    dates = []
    for r in archive_rows:
        if r.get("desc") != "Record Date":
            continue
        text = r.get("attchmntText") or ""
        if not any(k in text.lower() for k in DISCONTINUITY_KEYWORDS):
            continue
        d = _parse_record_date(text)
        if d is not None:
            dates.append(d)
    return sorted(set(dates))

DEFAULT_BACKFILL_DAYS = 1100  # ~ the measured hourly-bar depth ceiling (docs/expected_move_discovery.txt E2)
DEFAULT_REFRESH_DAYS = 120    # routine daily run: just the recent window


def log(msg):
    line = f"{datetime.now(IST).isoformat()} {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    # Migration for an existing db - the CREATE TABLE above only applies
    # to a brand-new one. Same try/except-per-ALTER pattern as
    # jobs/snapshot_estimates.py's get_connection().
    try:
        conn.execute("ALTER TABLE earnings_move_prints ADD COLUMN signed_move_pct REAL")
    except sqlite3.OperationalError:
        pass
    return conn


# --- step 1: filing identification -------------------------------------

def fetch_archive(symbol, from_date, to_date):
    url = (f"{ARCHIVE_BASE}?index=equities&symbol={quote(symbol, safe='')}"
           f"&from_date={from_date.strftime('%d-%m-%Y')}&to_date={to_date.strftime('%d-%m-%Y')}")
    resp = nse_urlfetch(url)
    if resp.status_code != 200:
        raise RuntimeError(f"NSE archive returned status {resp.status_code} for {symbol}")
    return resp.json()


def extract_period_end(text):
    """ISO date string from '...ended September 30, 2024' style text, or
    None if the filing text doesn't state one - caller must flag
    'ambiguous_period' rather than guess from the calendar."""
    m = PERIOD_END_PATTERN.search(text or "")
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    for fmt in ("%B %d %Y", "%b %d %Y"):  # full month name (e.g. September) or abbreviated (e.g. Jun, used for Q1 filings)
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_an_dt(raw):
    """NSE's an_dt field, e.g. '10-Oct-2024 16:06:39', as a tz-aware IST
    datetime."""
    return datetime.strptime(raw, "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)


def identify_filings(symbol, archive_rows):
    """Groups archive rows into distinct print identities by financial
    period, per docs/expected_move_discovery.txt's dedup rule: earliest
    matching row per period is the filing of record. A later match is
    only a genuine revision (source_conflict) when it falls on a
    DIFFERENT calendar day - a true re-filing of a corrected/restated
    results document, filed separately from the original results
    evening. A later match on the SAME day (e.g. a Dividend row filed a
    couple of minutes after the Outcome-of-Board-Meeting row, both
    carrying the same results text - the TCS 16:06:39/16:09:39 and
    RELIANCE Outcome/Dividend pairs) is the normal multi-row shape of one
    results announcement, not a conflict - it goes into results_cluster
    only. Same-calendar-day siblings (any desc, matching or not) are the
    audit cluster regardless.

    Returns a list of dicts: financial_period, filing_dt (IST), raw_an_dt,
    announcement_id, attachment_url, cluster (list of raw dicts),
    source_conflict (bool).
    """
    matched = []
    for r in archive_rows:
        text = r.get("attchmntText") or ""
        if not RESULTS_PATTERN.search(text):
            continue
        period = extract_period_end(text)
        if period is None:
            continue
        matched.append((period, parse_an_dt(r["an_dt"]), r))

    by_period = {}
    for period, dt, r in matched:
        by_period.setdefault(period, []).append((dt, r))

    filings = []
    for period, rows in by_period.items():
        rows.sort(key=lambda x: x[0])
        filing_dt, identity_row = rows[0]
        revisions = [rv for rv in rows[1:] if rv[0].date() != filing_dt.date()]
        identity_date = filing_dt.date()
        same_day_siblings = [
            r for r in archive_rows
            if parse_an_dt(r["an_dt"]).date() == identity_date
        ]
        cluster = {
            json.dumps(r, sort_keys=True): r
            for r in same_day_siblings + [rv[1] for rv in revisions]
        }
        filings.append({
            "financial_period": period,
            "filing_dt": filing_dt,
            "announcement_id": identity_row.get("seq_id"),
            "attachment_url": identity_row.get("attchmntFile"),
            "timestamp_source_field": "an_dt",
            "cluster": list(cluster.values()),
            "source_conflict": len(revisions) > 0,
        })
    return filings


# --- step 2: price data --------------------------------------------------

def fetch_hourly_bars(symbol):
    """{date: {start_dt(IST, tz-aware): {open,high,low,close}}}.
    auto_adjust=True requested explicitly (split-adjusted) rather than
    relying on a library default - see PRICE_BASIS note on dividend
    total-return NOT being separately verified."""
    t = yf.Ticker(f"{symbol}.NS")
    df = t.history(period="730d", interval="1h", auto_adjust=True)
    if df.empty:
        return {}
    by_date = {}
    for ts, row in df.iterrows():
        ts = ts.to_pydatetime()
        by_date.setdefault(ts.date(), {})[ts] = {
            "open": float(row["Open"]), "high": float(row["High"]), "low": float(row["Low"]),
        }
    return by_date


def fetch_daily_bars(symbol):
    """Full daily OHLC, not just Close - needed by compute_print_daily_fallback
    below, which measures a discontinuity-affected print's reaction using a
    whole day's high/low instead of an hourly window. auto_adjust=True here
    retroactively back-adjusts for any LATER split/bonus, which is exactly
    why this series (unlike the hourly one) stays on one consistent price
    scale across a corporate action - see find_price_discontinuities."""
    t = yf.Ticker(f"{symbol}.NS")
    df = t.history(period="3y", interval="1d", auto_adjust=True)
    return {
        ts.date(): {
            "open": float(row["Open"]), "high": float(row["High"]),
            "low": float(row["Low"]), "close": float(row["Close"]),
        }
        for ts, row in df.iterrows()
    }


# --- step 3: pure calculation, wired to real session/bar data -----------

def compute_print(filing_dt, hourly_by_date, daily_closes, today=None):
    """filing_dt: tz-aware IST datetime of the filing. Returns a dict of
    everything in earnings_move_prints except identity columns."""
    today = today or date.today()
    filing_date = filing_dt.date()
    trading = tc.is_trading_day(filing_date)
    mode = em.classify_release_mode(filing_dt, trading)
    reason_codes = set()
    reference = {}
    move_a = move_b = None
    high_a = low_a = high_b = low_b = None
    session_a_date = session_b_date = None
    window_b_date = None
    is_pending = False

    if mode == "intraday":
        session_open, session_close = tc.session_bounds_ist(filing_date)
        same_session_bars = hourly_by_date.get(filing_date, {})
        expected_slots = em.expected_hourly_slots(session_open, session_close)
        reference = em.select_reference_bar(filing_dt, expected_slots, same_session_bars)
        reason_codes.update(reference["reason_codes"])
        session_a_date = filing_date.isoformat()
        if reference["reference_price"] is not None:
            a_bars, a_reasons = em.select_window_a_bars(filing_dt, session_close, same_session_bars)
            reason_codes.update(a_reasons)
            high_a, low_a = em.window_extrema(a_bars)
            move_a = em.move_pct(reference["reference_price"], high_a, low_a)
        window_b_date = _safe_next_trading_day(filing_date)
    else:
        reference = em.select_daily_close_reference(filing_dt, daily_closes)
        reason_codes.update(reference["reason_codes"])
        if mode == "post_close":
            window_b_date = _safe_next_trading_day(filing_date)
        elif mode == "pre_open":
            window_b_date = filing_date  # same day, market hasn't opened yet
        else:  # non_session
            window_b_date = _safe_next_trading_day_on_or_after(filing_date)

    if window_b_date is not None:
        session_b_date = window_b_date.isoformat()
        if window_b_date >= today:
            is_pending = True
            reason_codes.add("pending_window")
        else:
            next_bars = hourly_by_date.get(window_b_date, {})
            if next_bars and reference.get("reference_price") is not None:
                high_b, low_b = em.window_extrema(list(next_bars.values()))
                move_b = em.move_pct(reference["reference_price"], high_b, low_b)
            elif not next_bars:
                reason_codes.add("missing_reaction_bars")
    else:
        is_pending = False  # no trading day resolvable at all within the lookahead window - excluded, not pending

    if is_pending:
        status = "pending"
        print_move_pct = None
    else:
        print_move_pct = em.print_move(mode, move_a, move_b)
        if reference.get("reference_price") is None:
            status = "excluded"
        elif print_move_pct is not None:
            status = "usable"
        elif mode == "intraday" and (move_a is None or move_b is None):
            status = "partial"
        else:
            status = "excluded"

    # Signed close-to-close reaction, added 15 Oct 2026 for the Sentiment
    # tab - independent of the magnitude/status logic above, since a
    # closing price is available even on a day whose intraday high/low
    # window is 'partial' or 'excluded'. For intraday filings the prior
    # trading day's close is the honest "before" price (the filing day's
    # own close is partly a reaction to the news, not a baseline); for
    # every other mode the daily-close reference already selected above
    # IS that baseline, so it's reused as-is rather than re-derived.
    before_close = (
        _prior_daily_close(filing_date, daily_closes) if mode == "intraday"
        else reference.get("reference_price")
    )
    after_close = None
    if not is_pending and window_b_date is not None:
        after_close = daily_closes.get(window_b_date)
    signed_move_pct = em.signed_move_pct(before_close, after_close)

    return {
        "release_mode": mode,
        "reference_price": reference.get("reference_price"),
        "reference_bar_ts": reference.get("reference_bar_ts").isoformat() if reference.get("reference_bar_ts") else None,
        "reference_offset_minutes": reference.get("reference_offset_minutes"),
        "reference_method": reference.get("reference_method"),
        "session_a_date": session_a_date,
        "session_b_date": session_b_date,
        "high_a": high_a, "low_a": low_a, "high_b": high_b, "low_b": low_b,
        "move_a_pct": move_a, "move_b_pct": move_b,
        "print_move_pct": print_move_pct,
        "signed_move_pct": signed_move_pct,
        "status": status,
        "reason_codes": sorted(reason_codes),
    }


def _safe_next_trading_day(d):
    try:
        return tc.next_trading_day(d)
    except ValueError:
        return None


def _safe_next_trading_day_on_or_after(d):
    try:
        return tc.next_trading_day_on_or_after(d)
    except ValueError:
        return None


def _prior_daily_close(filing_date, daily_closes):
    """The latest daily close strictly BEFORE filing_date - the honest
    pre-event baseline for an intraday filing, same reasoning
    compute_print_daily_fallback already uses for its own intraday
    reference (excluding the filing day itself, since by end of day it's
    partly a reaction, not a baseline). None if no earlier close exists."""
    eligible = [d for d in daily_closes if d < filing_date]
    return daily_closes[max(eligible)] if eligible else None


def compute_print_daily_fallback(filing_dt, daily_bars, today=None):
    """Recomputes a print entirely from daily OHLC bars, for a print whose
    normal hourly-based compute_print() straddles a real price-basis
    discontinuity (bonus/split/subdivision/demerger - see
    find_price_discontinuities). yfinance's daily interval retroactively
    back-adjusts for a LATER corporate action; its hourly interval does
    not. compute_print() mixes a daily-close reference (post_close/
    pre_open/non_session modes) or an hourly reference (intraday) with
    hourly reaction windows, so a straddled print silently mixes two price
    scales. Staying on daily bars for BOTH the reference and the reaction
    window avoids that - every number here comes from the same
    back-adjusted series, so the split cancels out exactly regardless of
    which side of it the filing or reaction day falls on.

    Coarser than the normal calculation: a whole day's high/low instead of
    an hourly window anchored to the filing minute, and (for intraday
    filings) the prior close as reference instead of a near-filing-time
    intraday price, since daily bars carry no intraday granularity. Always
    tagged reference_method='daily_close_fallback' and reason code
    'price_basis_daily_fallback' so the UI and this table never claim
    hourly-level precision for a row computed this way.
    """
    today = today or date.today()
    filing_date = filing_dt.date()
    trading = tc.is_trading_day(filing_date)
    mode = em.classify_release_mode(filing_dt, trading)
    daily_closes = {d: b["close"] for d, b in daily_bars.items()}
    if mode == "intraday":
        # select_daily_close_reference treats the filing date itself as an
        # eligible "completed" close (it only checks date <=, not time), but
        # for an intraday filing that day's own close is AFTER the filing,
        # not before it - using it would silently fold the filing-day move
        # into the reference. Exclude it so the reference is the prior
        # trading day's close, matching the docstring's stated intent.
        prior_closes = {d: c for d, c in daily_closes.items() if d < filing_date}
        reference = em.select_daily_close_reference(filing_dt, prior_closes)
    else:
        reference = em.select_daily_close_reference(filing_dt, daily_closes)
    ref_price = reference.get("reference_price")

    if mode == "pre_open":
        window_b_date = filing_date
    elif mode == "non_session":
        window_b_date = _safe_next_trading_day_on_or_after(filing_date)
    else:  # intraday, post_close
        window_b_date = _safe_next_trading_day(filing_date)

    move_a = None
    if mode == "intraday" and ref_price is not None:
        bar_a = daily_bars.get(filing_date)
        if bar_a is not None:
            move_a = em.move_pct(ref_price, bar_a["high"], bar_a["low"])

    move_b = None
    is_pending = False
    if window_b_date is None:
        pass
    elif window_b_date >= today:
        is_pending = True
    elif ref_price is not None:
        bar_b = daily_bars.get(window_b_date)
        if bar_b is not None:
            move_b = em.move_pct(ref_price, bar_b["high"], bar_b["low"])

    if is_pending:
        status, print_move_pct = "pending", None
    else:
        print_move_pct = em.print_move(mode, move_a, move_b)
        status = "usable" if print_move_pct is not None else "excluded"

    # See compute_print's matching comment - ref_price here is already a
    # daily close for every mode (unlike compute_print's intraday branch,
    # which uses an hourly open), so it doubles as the signed baseline
    # directly with no separate lookup.
    after_close = None
    if not is_pending and window_b_date is not None:
        bar_b = daily_bars.get(window_b_date)
        after_close = bar_b["close"] if bar_b is not None else None
    signed_move_pct = em.signed_move_pct(ref_price, after_close)

    return {
        "release_mode": mode,
        "reference_price": ref_price,
        "reference_bar_ts": None,
        "reference_offset_minutes": None,
        "reference_method": "daily_close_fallback",
        "session_a_date": filing_date.isoformat() if mode == "intraday" else None,
        "session_b_date": window_b_date.isoformat() if window_b_date else None,
        "signed_move_pct": signed_move_pct,
        "high_a": None, "low_a": None, "high_b": None, "low_b": None,
        "move_a_pct": move_a, "move_b_pct": move_b,
        "print_move_pct": print_move_pct,
        "status": status,
        "reason_codes": ["price_basis_daily_fallback"],
    }


# --- step 4: storage -------------------------------------------------------

def upsert_print(conn, symbol, filing, calc, inputs_cutoff):
    now = datetime.now(IST).isoformat()
    filing_dt = filing["filing_dt"]
    reason_codes = list(calc["reason_codes"])
    if filing["source_conflict"] and "source_conflict" not in reason_codes:
        reason_codes.append("source_conflict")

    existing = conn.execute(
        "SELECT id FROM earnings_move_prints WHERE symbol=? AND financial_period=? AND calculation_version=?",
        (symbol, filing["financial_period"], CALCULATION_VERSION),
    ).fetchone()

    row = {
        "symbol": symbol,
        "financial_period": filing["financial_period"],
        "event_date": None,
        "filing_ts_raw": filing_dt.strftime("%d-%b-%Y %H:%M:%S"),
        "filing_ts_utc": filing_dt.astimezone(timezone.utc).isoformat(),
        "filing_ts_ist": filing_dt.isoformat(),
        "timestamp_source_field": filing["timestamp_source_field"],
        "announcement_id": filing["announcement_id"],
        "attachment_url": filing["attachment_url"],
        "retrieved_at": now,
        "release_mode": calc["release_mode"],
        "reference_price": calc["reference_price"],
        "reference_bar_ts": calc["reference_bar_ts"],
        "reference_offset_minutes": calc["reference_offset_minutes"],
        "reference_method": calc["reference_method"],
        "session_a_date": calc["session_a_date"],
        "session_b_date": calc["session_b_date"],
        "high_a": calc["high_a"], "low_a": calc["low_a"],
        "high_b": calc["high_b"], "low_b": calc["low_b"],
        "move_a_pct": calc["move_a_pct"], "move_b_pct": calc["move_b_pct"],
        "print_move_pct": calc["print_move_pct"],
        "signed_move_pct": calc["signed_move_pct"],
        "status": calc["status"],
        "reason_codes": json.dumps(reason_codes),
        "results_cluster": json.dumps(filing["cluster"]),
        "price_basis": PRICE_BASIS,
        "calculation_version": CALCULATION_VERSION,
        "source_provenance": json.dumps({"filing": "nse_corporate_announcements", "prices": "yfinance"}),
        "inputs_cutoff": inputs_cutoff,
        "computed_at": now,
    }

    if existing:
        set_cols = [c for c in row if c not in ("symbol", "financial_period", "calculation_version",
                                                 "filing_ts_raw", "filing_ts_utc", "filing_ts_ist",
                                                 "timestamp_source_field", "announcement_id",
                                                 "attachment_url", "release_mode")]
        conn.execute(
            f"UPDATE earnings_move_prints SET {', '.join(f'{c}=?' for c in set_cols)} WHERE id=?",
            [row[c] for c in set_cols] + [existing[0]],
        )
        return existing[0]
    else:
        cols = list(row)
        conn.execute(
            f"INSERT INTO earnings_move_prints ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [row[c] for c in cols],
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def upsert_summary(conn, symbol, event_date, print_rows, inputs_cutoff):
    """print_rows: the latest LOOKBACK_TARGET print rows for this symbol
    with filing_ts_utc before event_date, newest-last. Cohort selection is
    the caller's job (refresh_symbol) - this just aggregates and stores."""
    selected = print_rows[-LOOKBACK_TARGET:]
    usable = [p for p in selected if p["status"] == "usable"]
    expected_pct, worst_pct = em.aggregate_cohort([p["print_move_pct"] for p in usable])
    now = datetime.now(IST).isoformat()

    conn.execute(
        """
        INSERT INTO earnings_move_summaries
            (symbol, event_date, lookback_target, selected_print_ids, found_count,
             selected_count, usable_count, excluded_count, expected_move_pct,
             worst_case_pct, calculation_version, inputs_cutoff, last_success_at,
             last_attempt_at, refresh_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok')
        ON CONFLICT (symbol, event_date, calculation_version) DO UPDATE SET
            selected_print_ids=excluded.selected_print_ids,
            found_count=excluded.found_count, selected_count=excluded.selected_count,
            usable_count=excluded.usable_count, excluded_count=excluded.excluded_count,
            expected_move_pct=excluded.expected_move_pct, worst_case_pct=excluded.worst_case_pct,
            inputs_cutoff=excluded.inputs_cutoff, last_success_at=excluded.last_success_at,
            last_attempt_at=excluded.last_attempt_at, refresh_status='ok'
        """,
        (symbol, event_date, LOOKBACK_TARGET, json.dumps([p["id"] for p in selected]),
         len(print_rows), len(selected), len(usable), len(selected) - len(usable),
         expected_pct, worst_pct, CALCULATION_VERSION, inputs_cutoff, now, now),
    )


# --- orchestration ---------------------------------------------------------

def refresh_symbol(conn, symbol, backfill_days):
    today = date.today()
    from_date = today - timedelta(days=backfill_days)
    log(f"{symbol}: fetching archive {from_date} to {today}")
    archive_rows = fetch_archive(symbol, from_date, today)
    filings = identify_filings(symbol, archive_rows)
    discontinuities = find_price_discontinuities(archive_rows)
    log(f"{symbol}: {len(filings)} distinct filings identified")
    if discontinuities:
        log(f"{symbol}: price-basis discontinuity date(s) {discontinuities}")
    time.sleep(ARCHIVE_PAUSE_SECONDS)

    hourly_by_date = fetch_hourly_bars(symbol)
    daily_bars = fetch_daily_bars(symbol)
    daily_closes = {d: b["close"] for d, b in daily_bars.items()}
    time.sleep(HOURLY_PAUSE_SECONDS)

    inputs_cutoff = datetime.now(IST).isoformat()
    print_rows = []
    for filing in sorted(filings, key=lambda f: f["filing_dt"]):
        calc = compute_print(filing["filing_dt"], hourly_by_date, daily_closes, today=today)
        straddled = [d for d in discontinuities if filing["filing_dt"].date() < d <= today]
        if straddled and calc["status"] == "usable":
            # Don't just drop this print - the hourly calculation above is
            # unreliable across a real corporate action, but the daily
            # series (back-adjusted for it either way) still lets us
            # measure this quarter, just at day-level instead of hour-level
            # granularity. See compute_print_daily_fallback's docstring.
            fallback = compute_print_daily_fallback(filing["filing_dt"], daily_bars, today=today)
            fallback["reason_codes"] = sorted(set(fallback["reason_codes"]) | {
                f"price_basis_discontinuity_{d.isoformat()}" for d in straddled
            })
            calc = fallback
        print_id = upsert_print(conn, symbol, filing, calc, inputs_cutoff)
        print_rows.append({"id": print_id, "status": calc["status"],
                            "print_move_pct": calc["print_move_pct"],
                            "filing_ts_utc": filing["filing_dt"].astimezone(timezone.utc)})
    conn.commit()

    upcoming = conn.execute(
        "SELECT event_date FROM events WHERE symbol=? AND event_type='board_meeting' "
        "AND purpose LIKE '%Financial Results%' AND event_date >= ? ORDER BY event_date",
        (symbol, today.isoformat()),
    ).fetchall()
    for (event_date,) in upcoming:
        before_cutoff = [p for p in print_rows if p["filing_ts_utc"].date().isoformat() < event_date]
        before_cutoff.sort(key=lambda p: p["filing_ts_utc"])
        upsert_summary(conn, symbol, event_date, before_cutoff, inputs_cutoff)
    conn.commit()
    log(f"{symbol}: {len(upcoming)} upcoming event(s) summarized")


def default_symbols(conn):
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM events WHERE event_type='board_meeting' "
        "AND purpose LIKE '%Financial Results%'"
    ).fetchall()
    return sorted(r[0] for r in rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", help="Comma-separated NSE symbols; default: all with a results-related upcoming board meeting")
    parser.add_argument("--backfill", action="store_true", help=f"Use the full ~{DEFAULT_BACKFILL_DAYS}-day lookback instead of the routine {DEFAULT_REFRESH_DAYS}-day window")
    args = parser.parse_args()

    conn = get_connection()
    symbols = args.symbols.split(",") if args.symbols else default_symbols(conn)
    backfill_days = DEFAULT_BACKFILL_DAYS if args.backfill else DEFAULT_REFRESH_DAYS
    log(f"Starting refresh for {len(symbols)} symbol(s), backfill_days={backfill_days}")

    failures = []
    for symbol in symbols:
        symbol = symbol.strip()
        try:
            refresh_symbol(conn, symbol, backfill_days)
        except Exception as e:
            log(f"{symbol}: FAILED - {e}")
            failures.append(symbol)
        time.sleep(ARCHIVE_PAUSE_SECONDS)

    conn.close()
    log(f"Done. {len(symbols) - len(failures)} succeeded, {len(failures)} failed: {failures}")


if __name__ == "__main__":
    main()
