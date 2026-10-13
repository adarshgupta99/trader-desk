"""
Fills in actual revenue/PAT/EPS for earnings_estimates snapshot rows once
results are out (see analyst_estimates_spec-c3606434.txt, section 6 - as
amended 2026-10-13).

Run manually: python jobs/fill_actuals.py
Scheduled in the same daily cron as jobs/snapshot_estimates.py, after it.

SOURCE, AMENDED AGAIN 2026-10-13 (Adarsh's direction): NSE's filing XBRL
is now the ONLY source for actuals - no yfinance fallback, and no surprise
% is computed at all. "Let's not compare on our own, just give the
analyst estimate and the NSE numbers."

Why no comparison: the estimate (eps_estimate/revenue_estimate) is a
single yfinance-sourced figure, but discovery
(scripts/discover_yahoo_basis.py, 13 Oct 2026) found no safe way to match
it to one NSE actual. Checked across 10 symbols and up to 4 completed
quarters each: most symbols' EPS basis IS stable over time (TCS/HCLTECH/
INFY match consolidated every quarter; RBLBANK/HDFCBANK/BAJFINANCE/SBIN/
TATAELXSI match standalone every quarter) - but RELIANCE's matching basis
flips quarter to quarter, M&M matches neither basis in any quarter
checked, and for RBLBANK and SBIN the basis that matches EPS is NOT the
one that matches revenue (one stored "basis per symbol" cannot serve
both). Comparing an estimate to a mismatched actual produced +64%
"surprises" that were artifacts of the measures being different, not the
company beating or missing anything - see the bank-revenue-derivation
fix earlier the same day for the first instance of this. Rather than ship
a detector known to be wrong for several real symbols, Adarsh's call was
to stop comparing: show the estimate, show NSE's own numbers, both bases,
let the reader judge.

What is stored per quarter, from the filing's own XBRL
(nse_results.get_quarter_actuals), BOTH standalone and consolidated since
NSE files them as two separate documents with genuinely different
numbers:
  - revenue_actual_cons / revenue_actual_std - plain INR. For a bank this
    is the DERIVED figure (InterestEarned - InterestExpended +
    OtherIncome), not the Income line - see
    nse_results._derive_bank_revenue.
  - pat_actual_cons / pat_actual_std - ProfitLossForPeriod (or the
    banking equivalent).
  - eps_actual_cons / eps_actual_std - DILUTED only, never basic. Checked
    against Yahoo's reported EPS across 6 symbols: matched the diluted
    figure in every discriminating case, never basic.
  - xbrl_url_cons / xbrl_url_std - the exact filing parsed, for audit.

A quarter not yet filed on NSE (filing lag after the board meeting) just
leaves these columns NULL for now; due_rows() picks the row back up on
the next run, same retry-for-up-to-7-days window as before.
"""

import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import nse_results  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "events.db")
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "fill_actuals.log")
IST = timezone(timedelta(hours=5, minutes=30))
RETRY_DELAYS = (5, 15, 45)
INTER_CALL_SLEEP = 2
GIVE_UP_AFTER_DAYS = 7

#: The six actual columns, in (db_column, nse_results_field) pairs for
#: each basis - one source of truth for every place that needs the list
#: (due_rows' SELECT, the merge logic, fill_row's UPDATE).
ACTUAL_FIELDS = {
    nse_results.BASIS_CONSOLIDATED: {
        "revenue_actual_cons": "revenue", "pat_actual_cons": "pat",
        "eps_actual_cons": "eps_diluted", "xbrl_url_cons": "xbrl_url",
    },
    nse_results.BASIS_STANDALONE: {
        "revenue_actual_std": "revenue", "pat_actual_std": "pat",
        "eps_actual_std": "eps_diluted", "xbrl_url_std": "xbrl_url",
    },
}
ACTUAL_COLUMNS = [col for cols in ACTUAL_FIELDS.values() for col in cols]


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
        except ValueError:
            # nse_results.parse_facts raises ValueError when the fetched
            # filing genuinely has no ~3-month context for the target
            # quarter - a property of that document, not a network blip;
            # retrying with backoff can't change what's in the file. Found
            # live 13 Oct 2026 via jobs/refresh_earnings_history.py:
            # ICICIPRULI (a life insurer) files under a different XBRL
            # taxonomy variant whose declared contexts span ~356/~362
            # days - neither is a quarter at all, by that insurer's own
            # convention, not a parsing bug here.
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
    """Rows still missing at least one of the six actual columns, within
    the retry window - bounded so long-abandoned rows (a quarter NSE
    never filed, or a symbol delisted) aren't re-fetched forever just to
    log that nothing changed."""
    today = date.today()
    missing = " OR ".join(f"{c} IS NULL" for c in ACTUAL_COLUMNS)
    return conn.execute(
        f"""
        SELECT id, symbol, event_date, {', '.join(ACTUAL_COLUMNS)}
        FROM earnings_estimates
        WHERE event_date <= ? AND event_date >= ?
          AND ({missing})
        ORDER BY event_date, symbol
        """,
        (today.isoformat(), (today - timedelta(days=GIVE_UP_AFTER_DAYS)).isoformat()),
    ).fetchall()


def find_nse_actuals(symbol, event_date_str):
    """{db_column: value} for every actual column NSE has filed for this
    quarter, across both bases. Only includes columns for a basis that
    was actually found and passed its sanity checks - a basis NSE hasn't
    filed yet (common: consolidated and standalone post within seconds of
    each other, but not always) is simply absent from the result, not a
    row of Nones, so a partial fill never looks like "checked and empty".
    """
    quarter_end = quarter_end_for_event_date(date.fromisoformat(event_date_str))
    rows = call_with_retry(f"nse_filing_index({symbol})", nse_results.fetch_filing_index, symbol)

    out = {}
    for basis, field_map in ACTUAL_FIELDS.items():
        try:
            facts = call_with_retry(
                f"nse_results({symbol},{basis})",
                nse_results.get_quarter_actuals, symbol, quarter_end, basis, rows=rows,
            )
        except Exception as e:
            log(f"  {symbol} {event_date_str}: NSE {basis} lookup failed: {type(e).__name__}: {e}")
            continue
        if facts is None:
            continue  # that basis isn't filed yet
        if facts["currency"] not in (None, "INR"):
            log(f"  {symbol} {event_date_str}: NSE {basis} unexpected currency "
                f"{facts['currency']} - skipping")
            continue
        if facts["rounding"] is not None:
            # Absent in every document checked so far; if a filer ever
            # declares a scale, the raw XBRL figures may not be absolute
            # rupees. Refuse rather than silently mis-scale by 10^5.
            log(f"  {symbol} {event_date_str}: NSE {basis} declares rounding "
                f"{facts['rounding']} - skipping")
            continue
        for db_col, facts_key in field_map.items():
            out[db_col] = facts[facts_key]
    return out


def fill_row(conn, row_id, values):
    """`values` is {db_column: value} for whichever of ACTUAL_COLUMNS are
    newly known - a partial dict is fine and expected (one basis filed,
    the other not yet)."""
    if not values:
        return
    now = datetime.now(IST).isoformat()
    set_clause = ", ".join(f"{col} = ?" for col in values)
    conn.execute(
        f"""
        UPDATE earnings_estimates
        SET {set_clause}, actual_source = ?, actual_filled_at = ?
        WHERE id = ?
        """,
        (*values.values(), "nse_xbrl", now, row_id),
    )
    conn.commit()


def run(conn):
    today = date.today()
    counts = {"filled": 0, "complete": 0, "no_actual_yet": 0, "gave_up": 0, "errors": 0}

    for row in due_rows(conn):
        row_id, symbol, event_date = row[0], row[1], row[2]
        existing = dict(zip(ACTUAL_COLUMNS, row[3:]))

        try:
            found = find_nse_actuals(symbol, event_date)
        except Exception as e:
            if _is_rate_limit(e):
                log(f"  {symbol} {event_date}: rate limited ({e}) - stopping run, retry next day")
                break
            log(f"  {symbol} {event_date}: fetch failed: {type(e).__name__}: {e}")
            counts["errors"] += 1
            continue
        time.sleep(INTER_CALL_SLEEP)

        # Only write columns that were NULL and are now known - a column
        # already filled is never touched again (matches the one-shot
        # philosophy the old surprise-% columns followed, even though
        # these aren't trigger-enforced).
        new_values = {
            col: val for col, val in found.items()
            if existing.get(col) is None and val is not None
        }
        if new_values:
            fill_row(conn, row_id, new_values)
            log(f"  {symbol} {event_date}: filled {', '.join(sorted(new_values))}")
            counts["filled"] += 1

        still_missing = [c for c in ACTUAL_COLUMNS
                          if existing.get(c) is None and c not in new_values]
        if not still_missing:
            counts["complete"] += 1
            continue

        days_since = (today - date.fromisoformat(event_date)).days
        if days_since > GIVE_UP_AFTER_DAYS:
            log(f"  {symbol} {event_date}: still missing {still_missing} after "
                f"{days_since}d - giving up")
            counts["gave_up"] += 1
        else:
            log(f"  {symbol} {event_date}: still missing {still_missing} "
                f"({days_since}d since event) - will retry")
            counts["no_actual_yet"] += 1

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
            f"Summary: filled={counts['filled']} complete={counts['complete']} "
            f"no_actual_yet={counts['no_actual_yet']} gave_up={counts['gave_up']} "
            f"errors={counts['errors']}"
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
