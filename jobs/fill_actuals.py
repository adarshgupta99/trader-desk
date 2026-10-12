"""
Fills in actual EPS/revenue and surprise % for earnings_estimates snapshot
rows once results are out (see analyst_estimates_spec-c3606434.txt, section
6 - as amended 2026-10-08).

Run manually: python jobs/fill_actuals.py
Scheduled in the same daily cron as jobs/snapshot_estimates.py, after it.

SOURCE, AMENDED FROM THE ORIGINAL SPEC: nselib's financial_results_for_equity
was discovery-tested on 2026-10-08 (see docs/estimates_discovery.txt) and
found not to work for this purpose - over a full trailing year it returned
only 18 symbols, all distressed/suspended smallcaps, and zero mainstream
F&O names (RELIANCE, TCS, HDFCBANK, INFY, WIPRO all absent). It is dropped
from this path entirely, not kept as a fallback.

Primary and only automated source is yfinance:
  - EPS actual: Ticker.get_earnings_dates()['Reported EPS'], matched to the
    snapshot's event_date by calendar date (+/- 1 day tolerance - Yahoo's
    recorded earnings date can be a day off from NSE's board-meeting date).
    Verified 2026-10-08 against Ticker.quarterly_income_stmt's independently
    computed Diluted EPS for TCS: matched exactly for the Jun-2025 quarter
    (35.27 both) and closely for Mar-2026 (37.94 vs 37.92) - confirming this
    is the real reported figure, not an echo of the estimate (surprise %
    on these rows ranges from -32% to +29%, i.e. it genuinely moves
    independently of the estimate).
  - Revenue actual: Ticker.quarterly_income_stmt's 'Total Revenue' row,
    matched to the nearest quarter-end column dated on or before event_date,
    within 75 days (a quarter typically ends 2-6 weeks before results are
    announced). Plain INR, same as revenue_estimate.
Screener.in is not scraped automatically; if yfinance has a gap, that is a
manual lookup for Adarsh, same as acceptance check H.
"""

import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "events.db")
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "fill_actuals.log")
IST = timezone(timedelta(hours=5, minutes=30))
RETRY_DELAYS = (5, 15, 45)
INTER_CALL_SLEEP = 2
DATE_MATCH_TOLERANCE_DAYS = 1
REVENUE_LOOKBACK_DAYS = 75
GIVE_UP_AFTER_DAYS = 7


def log(msg):
    line = f"{datetime.now(IST).isoformat()} {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def call_with_retry(label, fn, *args, **kwargs):
    last_error = None
    for attempt, delay in enumerate((0,) + RETRY_DELAYS):
        if delay:
            log(f"  retrying {label} in {delay}s...")
            time.sleep(delay)
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_error = e
            if _is_rate_limit(e):
                raise
            log(f"  {label} attempt {attempt + 1} failed: {type(e).__name__}: {e}")
    raise RuntimeError(f"{label} failed after retries") from last_error


def _is_rate_limit(exc):
    text = str(exc).lower()
    return "429" in text or "too many requests" in text or "rate limit" in text


def _clean(val):
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(val, "item"):
        return val.item()
    return val


def due_rows(conn):
    today_str = date.today().isoformat()
    return conn.execute(
        """
        SELECT id, symbol, event_date, eps_estimate, revenue_estimate
        FROM earnings_estimates
        WHERE event_date <= ? AND eps_actual IS NULL
        ORDER BY event_date, symbol
        """,
        (today_str,),
    ).fetchall()


def find_eps_actual(ticker, event_date_str):
    event_date = date.fromisoformat(event_date_str)
    earnings_dates = call_with_retry("get_earnings_dates", ticker.get_earnings_dates, limit=12)
    if earnings_dates is None or earnings_dates.empty:
        return None
    for idx, row in earnings_dates.iterrows():
        row_date = idx.date() if hasattr(idx, "date") else None
        if row_date is None:
            continue
        if abs((row_date - event_date).days) <= DATE_MATCH_TOLERANCE_DAYS:
            eps = _clean(row.get("Reported EPS"))
            if eps is not None:
                return eps
    return None


def find_revenue_actual(ticker, event_date_str):
    event_date = date.fromisoformat(event_date_str)
    stmt = call_with_retry("quarterly_income_stmt", lambda: ticker.quarterly_income_stmt)
    if stmt is None or stmt.empty or "Total Revenue" not in stmt.index:
        return None
    best_col, best_gap = None, None
    for col in stmt.columns:
        col_date = col.date() if hasattr(col, "date") else None
        if col_date is None or col_date > event_date:
            continue
        gap = (event_date - col_date).days
        if gap > REVENUE_LOOKBACK_DAYS:
            continue
        if best_gap is None or gap < best_gap:
            best_col, best_gap = col, gap
    if best_col is None:
        return None
    return _clean(stmt.loc["Total Revenue", best_col])


def compute_surprise(estimate, actual):
    """(actual - estimate) / |estimate| as a percentage. Same formula for
    EPS and revenue - named generically since both call sites use it."""
    if estimate is None or actual is None or estimate == 0:
        return None
    return (actual - estimate) / abs(estimate) * 100


def fill_row(conn, row_id, eps_actual, revenue_actual, surprise_pct, revenue_surprise_pct):
    now = datetime.now(IST).isoformat()
    conn.execute(
        """
        UPDATE earnings_estimates
        SET eps_actual = ?, revenue_actual = ?, surprise_pct = ?,
            revenue_surprise_pct = ?, actual_source = ?, actual_filled_at = ?
        WHERE id = ?
        """,
        (eps_actual, revenue_actual, surprise_pct, revenue_surprise_pct, "yfinance", now, row_id),
    )
    conn.commit()


def run(conn):
    today = date.today()
    counts = {"filled": 0, "no_actual_yet": 0, "gave_up": 0, "errors": 0}

    for row_id, symbol, event_date, eps_estimate, revenue_estimate in due_rows(conn):
        ticker = yf.Ticker(f"{symbol}.NS")
        try:
            eps_actual = find_eps_actual(ticker, event_date)
            time.sleep(INTER_CALL_SLEEP)
            revenue_actual = find_revenue_actual(ticker, event_date) if eps_actual is not None else None
        except Exception as e:
            if _is_rate_limit(e):
                log(f"  {symbol} {event_date}: rate limited ({e}) - stopping run, retry next day")
                break
            log(f"  {symbol} {event_date}: fetch failed: {type(e).__name__}: {e}")
            counts["errors"] += 1
            continue

        if eps_actual is None:
            days_since = (today - date.fromisoformat(event_date)).days
            if days_since > GIVE_UP_AFTER_DAYS:
                log(f"  {symbol} {event_date}: no actual found after {days_since}d - giving up")
                counts["gave_up"] += 1
            else:
                log(f"  {symbol} {event_date}: no actual yet ({days_since}d since event) - will retry")
                counts["no_actual_yet"] += 1
            time.sleep(INTER_CALL_SLEEP)
            continue

        surprise_pct = compute_surprise(eps_estimate, eps_actual)
        revenue_surprise_pct = compute_surprise(revenue_estimate, revenue_actual)
        fill_row(conn, row_id, eps_actual, revenue_actual, surprise_pct, revenue_surprise_pct)
        log(f"  {symbol} {event_date}: filled eps_actual={eps_actual} revenue_actual={revenue_actual} "
            f"surprise_pct={surprise_pct} revenue_surprise_pct={revenue_surprise_pct}")
        counts["filled"] += 1
        time.sleep(INTER_CALL_SLEEP)

    return counts


def main():
    conn = get_connection()
    try:
        log("Fill-actuals run starting")
        counts = run(conn)

        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('actuals_last_success_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('actuals_last_error', '') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        conn.commit()

        log(
            f"Summary: filled={counts['filled']} no_actual_yet={counts['no_actual_yet']} "
            f"gave_up={counts['gave_up']} errors={counts['errors']}"
        )
    except SystemExit:
        raise
    except Exception as e:
        error_text = f"{type(e).__name__}: {e}"
        log(f"FAILED: {error_text}")
        try:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('actuals_last_error', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (error_text,),
            )
            conn.commit()
        except Exception:
            pass
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
