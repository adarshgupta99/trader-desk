"""
Daily T-1 snapshot of analyst consensus estimates for upcoming F&O earnings
events (see analyst_estimates_spec-c3606434.txt, sections 2 and 5).

Run manually: python jobs/snapshot_estimates.py
Or for a specific event date: python jobs/snapshot_estimates.py --date 2026-10-10
Scheduled once a day (cron/launchd) around 6pm IST, after jobs/fetch_events.py
and before jobs/fill_actuals.py.

Writes to the same events.db that scripts/fetch_events.py owns. The app only
ever reads this file (see events_db.py); this job is the only writer for the
earnings_estimates table, same architecture as the rest of events.db.

KEY RULE: a snapshot row is immutable once inserted (enforced by a SQLite
trigger - see get_connection() below). Analysts revise estimates after the
print, so snapshotting late or re-snapshotting would contaminate the
actual-vs-estimate comparison. If T-1 is missed, we log it and move on -
we never backfill a stale estimate as if it were a T-1 one.
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "events.db")
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "snapshot_estimates.log")
IST = timezone(timedelta(hours=5, minutes=30))
RETRY_DELAYS = (5, 15, 45)
INTER_CALL_SLEEP = 2
PERIOD_LABEL = "0q"  # current/nearest quarter - see docs/estimates_discovery.txt


def log(msg):
    line = f"{datetime.now(IST).isoformat()} {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS earnings_estimates (
            id                INTEGER PRIMARY KEY,
            symbol            TEXT NOT NULL,
            event_date        TEXT NOT NULL,
            snapshot_date     TEXT NOT NULL,
            eps_estimate      REAL,
            revenue_estimate  REAL,
            n_analysts_eps    INTEGER,
            n_analysts_rev    INTEGER,
            eps_low           REAL,
            eps_high          REAL,
            period_label      TEXT NOT NULL,
            source            TEXT NOT NULL,
            fetched_at        TEXT NOT NULL,
            eps_actual        REAL,
            revenue_actual    REAL,
            surprise_pct      REAL,
            revenue_surprise_pct REAL,
            actual_source     TEXT,
            actual_filled_at  TEXT,
            UNIQUE (symbol, event_date, snapshot_date)
        )
        """
    )
    # revenue_surprise_pct added 2026-10-08, after surprise_pct (EPS-only
    # surprise) was all this table had - CREATE TABLE above only applies to
    # a brand-new db, so an existing one needs the column added explicitly.
    try:
        conn.execute("ALTER TABLE earnings_estimates ADD COLUMN revenue_surprise_pct REAL")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_earnings_estimates_symbol "
        "ON earnings_estimates (symbol, event_date)"
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_earnings_estimates_immutable
        BEFORE UPDATE ON earnings_estimates
        WHEN NOT (
            NEW.eps_estimate IS OLD.eps_estimate
            AND NEW.revenue_estimate IS OLD.revenue_estimate
            AND NEW.n_analysts_eps IS OLD.n_analysts_eps
            AND NEW.n_analysts_rev IS OLD.n_analysts_rev
            AND NEW.eps_low IS OLD.eps_low
            AND NEW.eps_high IS OLD.eps_high
            AND NEW.period_label IS OLD.period_label
            AND NEW.source IS OLD.source
            AND NEW.fetched_at IS OLD.fetched_at
        )
        BEGIN
            SELECT RAISE(ABORT, 'earnings_estimates: estimate columns are immutable once snapshotted');
        END
        """
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
    )
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
    """NaN/numpy scalar -> plain Python value or None."""
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


def _estimate_row(df, period=PERIOD_LABEL):
    if df is None or not hasattr(df, "index") or period not in df.index:
        return None
    return df.loc[period]


def fetch_estimate(symbol):
    """Returns a dict of estimate fields for `symbol` (bare NSE symbol).

    All values are None if yfinance has nothing - that is a valid result,
    not an error (see spec section 4).
    """
    ticker = yf.Ticker(f"{symbol}.NS")

    eps_row = None
    try:
        eps_df = call_with_retry(f"{symbol} get_earnings_estimate", ticker.get_earnings_estimate)
        eps_row = _estimate_row(eps_df)
    except Exception as e:
        if _is_rate_limit(e):
            raise
        log(f"  {symbol}: get_earnings_estimate failed: {type(e).__name__}: {e}")

    time.sleep(INTER_CALL_SLEEP)

    rev_row = None
    try:
        rev_df = call_with_retry(f"{symbol} get_revenue_estimate", ticker.get_revenue_estimate)
        rev_row = _estimate_row(rev_df)
    except Exception as e:
        if _is_rate_limit(e):
            raise
        log(f"  {symbol}: get_revenue_estimate failed: {type(e).__name__}: {e}")

    return {
        "eps_estimate": _clean(eps_row.get("avg")) if eps_row is not None else None,
        "eps_low": _clean(eps_row.get("low")) if eps_row is not None else None,
        "eps_high": _clean(eps_row.get("high")) if eps_row is not None else None,
        "n_analysts_eps": _clean(eps_row.get("numberOfAnalysts")) if eps_row is not None else None,
        "revenue_estimate": _clean(rev_row.get("avg")) if rev_row is not None else None,
        "n_analysts_rev": _clean(rev_row.get("numberOfAnalysts")) if rev_row is not None else None,
    }


def due_events(conn, target_dates):
    placeholders = ", ".join("?" * len(target_dates))
    rows = conn.execute(
        f"""
        SELECT DISTINCT symbol, event_date FROM events
        WHERE event_type = 'board_meeting'
          AND LOWER(purpose) LIKE '%result%'
          AND event_date IN ({placeholders})
        ORDER BY event_date, symbol
        """,
        target_dates,
    ).fetchall()
    return rows


def already_snapshotted(conn, symbol, event_date):
    row = conn.execute(
        "SELECT 1 FROM earnings_estimates WHERE symbol = ? AND event_date = ?",
        (symbol, event_date),
    ).fetchone()
    return row is not None


def insert_snapshot(conn, symbol, event_date, snapshot_date, est):
    now = datetime.now(IST).isoformat()
    try:
        conn.execute(
            """
            INSERT INTO earnings_estimates
                (symbol, event_date, snapshot_date, eps_estimate, revenue_estimate,
                 n_analysts_eps, n_analysts_rev, eps_low, eps_high, period_label,
                 source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol, event_date, snapshot_date,
                est["eps_estimate"], est["revenue_estimate"],
                est["n_analysts_eps"], est["n_analysts_rev"],
                est["eps_low"], est["eps_high"],
                PERIOD_LABEL, "yfinance", now,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def run(conn, target_dates, allow_past=False):
    today_str = date.today().isoformat()
    snapshot_date = datetime.now(IST).strftime("%Y-%m-%d")

    counts = {"inserted": 0, "no_estimate": 0, "skipped": 0, "missed_t1": 0, "errors": 0}

    for symbol, event_date in due_events(conn, target_dates):
        if not allow_past and event_date <= today_str:
            log(f"  {symbol} {event_date}: missed T-1 (event is today or past) - not snapshotting")
            counts["missed_t1"] += 1
            continue

        if already_snapshotted(conn, symbol, event_date):
            log(f"  {symbol} {event_date}: already snapshotted, skipping")
            counts["skipped"] += 1
            continue

        try:
            est = fetch_estimate(symbol)
        except Exception as e:
            if _is_rate_limit(e):
                log(f"  {symbol} {event_date}: rate limited ({e}) - stopping run, retry next day")
                break
            log(f"  {symbol} {event_date}: fetch failed: {type(e).__name__}: {e}")
            counts["errors"] += 1
            continue

        inserted = insert_snapshot(conn, symbol, event_date, snapshot_date, est)
        if not inserted:
            log(f"  {symbol} {event_date}: snapshot already exists (race), skipping")
            counts["skipped"] += 1
        elif est["eps_estimate"] is None and est["revenue_estimate"] is None:
            log(f"  {symbol} {event_date}: snapshotted, no estimate available")
            counts["no_estimate"] += 1
        else:
            log(f"  {symbol} {event_date}: snapshotted eps={est['eps_estimate']} "
                f"rev={est['revenue_estimate']} analysts={est['n_analysts_eps']}")
            counts["inserted"] += 1

        time.sleep(INTER_CALL_SLEEP)

    return counts


def main():
    parser = argparse.ArgumentParser(description="Snapshot analyst estimates for due earnings events.")
    parser.add_argument("--date", help="Manually snapshot a single event_date (YYYY-MM-DD), bypassing the T-1 window.")
    args = parser.parse_args()

    conn = get_connection()
    try:
        if args.date:
            log(f"Manual snapshot run for event_date={args.date}")
            target_dates = [args.date]
            allow_past = True
        else:
            tomorrow = (date.today() + timedelta(days=1)).isoformat()
            day_after = (date.today() + timedelta(days=2)).isoformat()
            log(f"Daily snapshot run, target dates: {tomorrow}, {day_after}")
            target_dates = [tomorrow, day_after]
            allow_past = False

        counts = run(conn, target_dates, allow_past=allow_past)

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('estimates_last_success_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (now,),
        )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('estimates_last_error', '') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        conn.commit()

        log(
            f"Summary: inserted={counts['inserted']} no_estimate={counts['no_estimate']} "
            f"skipped={counts['skipped']} missed_t1={counts['missed_t1']} errors={counts['errors']}"
        )

    except SystemExit:
        raise
    except Exception as e:
        error_text = f"{type(e).__name__}: {e}"
        log(f"FAILED: {error_text}")
        try:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('estimates_last_error', ?) "
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
