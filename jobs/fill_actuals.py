"""
Fills in actual EPS/revenue and surprise % for earnings_estimates snapshot
rows once results are out (see analyst_estimates_spec-c3606434.txt, section
6 - as amended 2026-10-08).

Run manually: python jobs/fill_actuals.py
Scheduled in the same daily cron as jobs/snapshot_estimates.py, after it.

SOURCE, AMENDED AGAIN 2026-10-12 (Adarsh's direction): actuals now come
from NSE's own filing XBRL first, via nse_results.py, with yfinance kept
as the fallback rather than the primary.

What changed and why: the 8 Oct decision to use yfinance only was made
after `nselib.financial_results_for_equity` was found unusable - it reads
`/api/corporates-financial-results`, which over a full trailing year
returned 18 distressed/suspended smallcaps and zero mainstream F&O names.
That finding still holds (re-verified live 12 Oct: zero rows for a window
containing TCS's real 8 Oct results). But a DIFFERENT endpoint,
`/api/integrated-filing-results`, does work and carries the filing's own
XBRL. See nse_results.py for the live verification and the three traps
(year-to-date contexts, banking taxonomy, standalone-vs-consolidated).

Why NSE first: it is the primary source - the company's own filing, not a
third party's transcription - and it covers rows yfinance leaves empty.
ANANDRATHI's Sep-2026 quarter is the worked example: yfinance had no
actual for it days after the result, while NSE had revenue 343.99cr /
PAT 89.26cr / EPS 5.38 the whole time.

Why yfinance is KEPT rather than deleted: it is the only cover for a
symbol whose XBRL concepts aren't in nse_results.CONCEPT_CANDIDATES, and
a disagreement between the two is how a bad concept mapping gets noticed
instead of silently storing a wrong number.

Fallback source, yfinance:
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import nse_results  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "events.db")
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "fill_actuals.log")
IST = timezone(timedelta(hours=5, minutes=30))
RETRY_DELAYS = (5, 15, 45)
INTER_CALL_SLEEP = 2
DATE_MATCH_TOLERANCE_DAYS = 1
REVENUE_LOOKBACK_DAYS = 75
GIVE_UP_AFTER_DAYS = 7

#: Standalone and consolidated are both filed for every quarter with
#: genuinely different numbers (DMART Sep-2026: standalone PAT 803.60cr
#: EXCEEDS consolidated 742.98cr while its revenue is lower). Consolidated
#: is stored because that is the basis headlines and analyst consensus
#: quote, so surprise % stays comparable to the frozen estimate. Changing
#: this makes new rows incomparable to existing ones - it is not a free
#: toggle.
ACTUALS_BASIS = nse_results.BASIS_CONSOLIDATED


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


def quarter_end_for_event_date(event_date):
    """The quarter a board meeting on `event_date` is reporting on: the
    most recent quarter end STRICTLY before it. A company cannot declare
    results for a quarter on or before the day that quarter ends, so
    "strictly before" is right - TCS's 8 Oct 2026 meeting reports
    30 Sep 2026, and a 10 Jan 2026 meeting reports 31 Dec 2025."""
    for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
        candidate = date(event_date.year, month, day)
        if candidate < event_date:
            return candidate
    return date(event_date.year - 1, 12, 31)


def due_rows(conn):
    """Rows still missing EITHER actual, within the retry window.

    Was `eps_actual IS NULL` only, which meant a row that got an EPS but
    no revenue - the common yfinance outcome, e.g. TCS and DMART both
    sat at eps_actual filled / revenue_actual NULL - was never revisited
    and could never be completed. The window bound replaces re-fetching
    long-abandoned rows on every run forever just to log that they were
    given up on.
    """
    today = date.today()
    return conn.execute(
        """
        SELECT id, symbol, event_date, eps_estimate, revenue_estimate,
               eps_actual, revenue_actual
        FROM earnings_estimates
        WHERE event_date <= ? AND event_date >= ?
          AND (eps_actual IS NULL OR revenue_actual IS NULL)
        ORDER BY event_date, symbol
        """,
        (today.isoformat(), (today - timedelta(days=GIVE_UP_AFTER_DAYS)).isoformat()),
    ).fetchall()


def find_nse_actuals(symbol, event_date_str):
    """(eps, revenue, detail) from the company's own NSE filing, or
    (None, None, None) if that quarter isn't filed yet."""
    quarter_end = quarter_end_for_event_date(date.fromisoformat(event_date_str))
    facts = call_with_retry(
        f"nse_results({symbol})",
        nse_results.get_quarter_actuals, symbol, quarter_end, ACTUALS_BASIS,
    )
    if facts is None:
        return None, None, None
    detail = (f"quarter_end={quarter_end} basis={ACTUALS_BASIS} "
              f"eps_concept={facts['eps_basic_concept']} "
              f"revenue_concept={facts['revenue_concept']} "
              f"currency={facts['currency']} rounding={facts['rounding']} "
              f"xbrl={facts['xbrl_url']}")
    if facts["currency"] not in (None, "INR"):
        log(f"  {symbol}: UNEXPECTED currency {facts['currency']} - skipping NSE, {detail}")
        return None, None, None
    if facts["rounding"] is not None:
        # Absent in every document checked; if a filer ever declares a
        # scale, the raw figures may not be absolute rupees and would be
        # wrong by orders of magnitude. Refuse rather than mis-scale.
        log(f"  {symbol}: filing declares rounding {facts['rounding']} - skipping NSE, {detail}")
        return None, None, None
    return facts["eps_basic"], facts["revenue"], detail


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


def fill_row(conn, row_id, eps_actual, revenue_actual, surprise_pct,
             revenue_surprise_pct, source):
    now = datetime.now(IST).isoformat()
    conn.execute(
        """
        UPDATE earnings_estimates
        SET eps_actual = ?, revenue_actual = ?, surprise_pct = ?,
            revenue_surprise_pct = ?, actual_source = ?, actual_filled_at = ?
        WHERE id = ?
        """,
        (eps_actual, revenue_actual, surprise_pct, revenue_surprise_pct, source, now, row_id),
    )
    conn.commit()


def run(conn):
    today = date.today()
    counts = {"filled": 0, "no_actual_yet": 0, "gave_up": 0, "errors": 0}

    for (row_id, symbol, event_date, eps_estimate, revenue_estimate,
         existing_eps, existing_revenue) in due_rows(conn):
        eps_actual, revenue_actual = existing_eps, existing_revenue
        sources = []

        # NSE first - the company's own filing. Each field falls back to
        # yfinance independently, so a bank whose EPS concept isn't mapped
        # still gets its revenue from NSE rather than losing both.
        try:
            nse_eps, nse_revenue, detail = find_nse_actuals(symbol, event_date)
            if nse_eps is not None or nse_revenue is not None:
                if eps_actual is None and nse_eps is not None:
                    eps_actual = nse_eps
                if revenue_actual is None and nse_revenue is not None:
                    revenue_actual = nse_revenue
                sources.append(f"nse_xbrl:{ACTUALS_BASIS}")
                log(f"  {symbol} {event_date}: NSE {detail}")
        except Exception as e:
            log(f"  {symbol} {event_date}: NSE lookup failed: {type(e).__name__}: {e}")
        time.sleep(INTER_CALL_SLEEP)

        if eps_actual is not None and revenue_actual is not None:
            surprise_pct = compute_surprise(eps_estimate, eps_actual)
            revenue_surprise_pct = compute_surprise(revenue_estimate, revenue_actual)
            fill_row(conn, row_id, eps_actual, revenue_actual, surprise_pct,
                     revenue_surprise_pct, "+".join(sources))
            log(f"  {symbol} {event_date}: filled eps_actual={eps_actual} "
                f"revenue_actual={revenue_actual} surprise_pct={surprise_pct} "
                f"revenue_surprise_pct={revenue_surprise_pct} source={'+'.join(sources)}")
            counts["filled"] += 1
            continue

        ticker = yf.Ticker(f"{symbol}.NS")
        try:
            if eps_actual is None:
                eps_actual = find_eps_actual(ticker, event_date)
                if eps_actual is not None:
                    sources.append("yfinance")
                time.sleep(INTER_CALL_SLEEP)
            if revenue_actual is None and eps_actual is not None:
                revenue_actual = find_revenue_actual(ticker, event_date)
                if revenue_actual is not None and "yfinance" not in sources:
                    sources.append("yfinance")
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
        fill_row(conn, row_id, eps_actual, revenue_actual, surprise_pct,
                 revenue_surprise_pct, "+".join(sources))
        log(f"  {symbol} {event_date}: filled eps_actual={eps_actual} revenue_actual={revenue_actual} "
            f"surprise_pct={surprise_pct} revenue_surprise_pct={revenue_surprise_pct} "
            f"source={'+'.join(sources)}")
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
