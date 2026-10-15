"""
Populates earnings_history_quarters: the last 6 reported calendar quarters
of consolidated revenue/PAT per symbol, for the Calendar subtab's HISTORY
6Q sparkline column (13 Oct 2026 spec, Adarsh's direction).

Run manually: python -m jobs.refresh_earnings_history [--symbols A,B,C]
Scheduled in the same daily cron as snapshot_estimates/fill_actuals, after
them (no cron/launchd entry exists yet - run by hand).

SCOPE, widened 15 Oct 2026 (Adarsh's direction): every F&O symbol
(fno.symbols(), 213), not just ones with an upcoming/recent events-table
row. The narrower scope (superseded - see git history for the original
reasoning, which argued this was the Expected-move tracker's call to
make, not this job's) left a real gap: the Calendar subtab's own
universe was widened the same day to show every F&O stock including ones
with no announced board meeting yet, and those rows' HISTORY 6Q column
has nothing to do with whether a next meeting is scheduled - it's just
the last 6 REPORTED quarters. A symbol with no real event_date gets
today's date as its window anchor instead (same fallback an explicit
--symbols call already used, which correctly resolves to "the 6 most
recently completed quarters" via quarter_end_for_event_date). Pass
--symbols to backfill an explicit list instead (manual testing, or to
re-run just a few names).

NSE LOAD NOTE: this does mean a routine run (once this is actually on a
cron) now walks all 213 symbols instead of ~90, every time - more total
request volume, though each symbol's upsert is already idempotent
(ON CONFLICT DO UPDATE) so a routine rerun is mostly re-checking for a
newly-filed quarter, not redoing work. Worth knowing if NSE pacing ever
becomes a real constraint; no change made to the per-symbol fetch
pattern itself (still the same polite, sequential, one-archive-fetch-
plus-up-to-6-XBRL-fetches shape).

WHY 6 FETCHES PER SYMBOL, NOT 1: each historical quarter is a SEPARATE
NSE filing with its own XBRL document - there is no "give me 6 quarters"
endpoint. One filing-index fetch, reused across the window, plus up to
6 XBRL document fetches (one per quarter) - same cost shape as the
existing actuals job, consolidated-only here so half the per-quarter cost
(no standalone fetch needed for this column).

WHAT "6 REPORTED QUARTERS" MEANS: the 6 calendar quarters immediately
before the one the upcoming board meeting will report on - e.g. for a
13 Oct 2026 meeting reporting Sep-2026, the window is Mar-2025 through
Jun-2026 inclusive. A quarter NSE genuinely has no filing for (an archive
gap, or the symbol simply didn't exist yet) is stored as a row with NULL
value columns - a real, expected gap, never dropped, never interpolated,
never silently treated as zero. The UI renders that as a blank space in
the sparkline.

REVENUE, BANKS INCLUDED: no special-case suppression here. Reuses
nse_results.parse_facts exactly as the Past earnings tab's actuals do,
bank derivation (InterestEarned - InterestExpended + OtherIncome) and
all - Adarsh's direction (13 Oct 2026) was to reuse the already-verified
derivation rather than blank the REV track for banks, since it is already
paisa-exact against yfinance's reported Total Revenue (see nse_results.py
and ARCHITECTURE.md for that verification).

PAT BASIS: total consolidated (ProfitLossForPeriod), not owner-
attributable - Adarsh's confirmed choice, same basis the Past earnings
tab stores.
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fno  # noqa: E402
import nse_results  # noqa: E402
from jobs.fill_actuals import quarter_end_for_event_date  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "events.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "schema_earnings_history.sql")
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "refresh_earnings_history.log")
IST = timezone(timedelta(hours=5, minutes=30))
RETRY_DELAYS = (5, 15, 45)
INTER_CALL_SLEEP = 2
HISTORY_QUARTERS = 6
BASIS = nse_results.BASIS_CONSOLIDATED


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
        except ValueError:
            # nse_results.parse_facts raises ValueError when the filing it
            # fetched genuinely has no ~3-month context for the target
            # quarter - a property of that document, not a network blip.
            # Retrying with backoff can't change what's in the file.
            # Found live 13 Oct 2026: ICICIPRULI (a life insurer) files
            # under a different XBRL taxonomy variant
            # (INTEGRATED_FILING_LI_...) whose declared contexts span
            # ~356 and ~362 days - neither is a quarter at all, by this
            # insurer's own filing convention, not a parsing bug here.
            raise
        except Exception as e:
            last_error = e
            if _is_rate_limit(e):
                raise
            log(f"  {label} attempt {attempt + 1} failed: {type(e).__name__}: {e}")
    raise RuntimeError(f"{label} failed after retries") from last_error


def _is_rate_limit(exc):
    text = str(exc).lower()
    return "429" in text or "too many requests" in text or "rate limit" in text


#: Must match tabs/events.py's `get_upcoming_events(..., lookback_days=14)`
#: call for the Calendar subtab - that's what keeps a just-reported
#: result's row (and its HISTORY 6Q cell) visible for a while instead of
#: vanishing the moment event_date passes. Missed this the first time
#: (13 Oct 2026): due_symbols only looked forward, so TCS/DMART/HCLTECH/
#: etc - all reported in the last few days, still shown in Calendar -
#: were never backfilled and showed "no history" despite being exactly
#: the rows a reader would most want it for. No shared constant between
#: this job and the UI module on purpose (the job intentionally has no
#: Streamlit/events_db dependency) - keep these two literals in sync by
#: hand if the Calendar's window ever changes.
CALENDAR_LOOKBACK_DAYS = 14


def due_symbols(conn):
    """Every F&O symbol, paired with the event_date its 6-quarter window
    should be anchored to: the real results board-meeting date where
    `events` has one in the Calendar's own lookback window, or today's
    date otherwise - the window calc only needs to know "which quarter
    does the next report cover", and today's date already gives the
    right answer (the 6 most recently completed quarters) for a symbol
    with nothing scheduled, same fallback an explicit --symbols call
    uses. Widened 15 Oct 2026 from "only symbols with an events row" to
    the full universe - see this module's docstring for why."""
    today = date.today()
    from_date = (today - timedelta(days=CALENDAR_LOOKBACK_DAYS)).isoformat()
    rows = conn.execute(
        """
        SELECT symbol, MIN(event_date) FROM events
        WHERE event_type = 'board_meeting'
          AND LOWER(purpose) LIKE '%result%'
          AND event_date >= ?
        GROUP BY symbol
        """,
        (from_date,),
    ).fetchall()
    scheduled = dict(rows)
    return [(s, scheduled.get(s, today.isoformat())) for s in fno.symbols()]


def upsert_quarter(conn, symbol, quarter_end, facts):
    now = datetime.now(IST).isoformat()
    conn.execute(
        """
        INSERT INTO earnings_history_quarters
            (symbol, quarter_end, revenue_cons, pat_cons,
             revenue_concept, pat_concept, xbrl_url, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, quarter_end) DO UPDATE SET
            revenue_cons = excluded.revenue_cons,
            pat_cons = excluded.pat_cons,
            revenue_concept = excluded.revenue_concept,
            pat_concept = excluded.pat_concept,
            xbrl_url = excluded.xbrl_url,
            fetched_at = excluded.fetched_at
        """,
        (
            symbol, quarter_end.isoformat(),
            facts["revenue"] if facts else None,
            facts["pat"] if facts else None,
            facts["revenue_concept"] if facts else None,
            facts["pat_concept"] if facts else None,
            facts["xbrl_url"] if facts else None,
            now,
        ),
    )
    conn.commit()


def refresh_symbol(conn, symbol, event_date_str):
    end_quarter = nse_results.previous_quarter_end(
        quarter_end_for_event_date(date.fromisoformat(event_date_str))
    )
    window = nse_results.quarter_window(end_quarter, HISTORY_QUARTERS)
    rows = call_with_retry(f"filing_index({symbol})", nse_results.fetch_filing_index, symbol)

    filled, gaps = 0, 0
    for qe in window:
        try:
            facts = call_with_retry(
                f"{symbol} {qe}", nse_results.get_quarter_actuals, symbol, qe, BASIS, rows=rows,
            )
        except Exception as e:
            if _is_rate_limit(e):
                raise
            log(f"  {symbol} {qe}: fetch failed, storing as gap: {type(e).__name__}: {e}")
            facts = None

        if facts is None:
            log(f"  {symbol} {qe}: no filing found - storing as a gap")
            gaps += 1
        elif facts["currency"] not in (None, "INR"):
            log(f"  {symbol} {qe}: unexpected currency {facts['currency']} - storing as a gap")
            facts, gaps = None, gaps + 1
        elif facts["rounding"] is not None:
            log(f"  {symbol} {qe}: filing declares rounding {facts['rounding']} - storing as a gap")
            facts, gaps = None, gaps + 1
        else:
            filled += 1

        upsert_quarter(conn, symbol, qe, facts)
        time.sleep(INTER_CALL_SLEEP)

    return filled, gaps


def run(conn, symbols=None):
    if symbols:
        targets = [(s, date.today().isoformat()) for s in symbols]
    else:
        targets = due_symbols(conn)

    counts = {"symbols": 0, "quarters_filled": 0, "quarters_gap": 0, "errors": 0}
    for symbol, event_date_str in targets:
        try:
            filled, gaps = refresh_symbol(conn, symbol, event_date_str)
        except Exception as e:
            if _is_rate_limit(e):
                log(f"  {symbol}: rate limited ({e}) - stopping run, retry next day")
                break
            log(f"  {symbol}: refresh failed: {type(e).__name__}: {e}")
            counts["errors"] += 1
            continue
        log(f"  {symbol}: {filled} quarter(s) filled, {gaps} gap(s)")
        counts["symbols"] += 1
        counts["quarters_filled"] += filled
        counts["quarters_gap"] += gaps

    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="comma-separated; default is every symbol with an "
                                       "upcoming results board meeting")
    args = ap.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    conn = get_connection()
    try:
        log("Earnings-history refresh starting")
        counts = run(conn, symbols)

        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('earnings_history_last_success_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('earnings_history_last_error', '') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        conn.commit()

        log(f"Summary: symbols={counts['symbols']} quarters_filled={counts['quarters_filled']} "
            f"quarters_gap={counts['quarters_gap']} errors={counts['errors']}")
    except SystemExit:
        raise
    except Exception as e:
        error_text = f"{type(e).__name__}: {e}"
        log(f"FAILED: {error_text}")
        try:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('earnings_history_last_error', ?) "
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
