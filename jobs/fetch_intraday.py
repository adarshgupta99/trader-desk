"""Download F&O bars into the local store. Spec section 7.

Run after market close, around 16:30 IST:

    cd ~/Documents/trader-desk && .venv/bin/python -m jobs.fetch_intraday

This job is an OPTIMISATION, not a data-preservation requirement. It
keeps the app from refetching 60 days of bars on demand. Nothing is lost
permanently if it does not run: the 15m tier only answers lookbacks up to
60 days, and Yahoo serves exactly that window, so a missed day is
recovered by the next run. That is the one real difference from the
1-minute design this feature started as, where a missed run meant history
gone for good.

The app never calls Yahoo for these bars. It only reads the store.

Batching is politeness, not speed. Step 0 downloaded all 213 tickers in
one call in 6.0 seconds; the batches and pauses are here to stay under
Yahoo's undocumented limits, and the speed measurement is a reason to be
relaxed about the pause length rather than to remove it.
"""

import argparse
import sys
import time

import pandas as pd
import yfinance as yf

import fno
from data_layer import intraday_store

BATCH_SIZE = 45
PAUSE_SECONDS = 2.0
BACKOFF_SECONDS = 20.0

#: What to ask Yahoo for. 15m beyond 60d comes back empty with an
#: explicit "within the last 60 days" error, so 60d is the cold start.
PERIODS = {
    "15m": {"incremental": "5d", "cold": "60d"},
    "1d": {"incremental": "1mo", "cold": "3y"},
}

#: Treat the store as cold if the newest bar is older than this.
STALE_AFTER_DAYS = {"15m": 3, "1d": 20}


def log(message):
    stamp = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def is_cold(conn, interval):
    """Does this tier need the full backfill rather than a top-up?"""
    rows, _, _, last = intraday_store.coverage(conn, interval)
    if not rows or not last:
        return True, "store is empty"
    age = (pd.Timestamp.now().normalize() - pd.Timestamp(last).normalize()).days
    if age > STALE_AFTER_DAYS[interval]:
        return True, f"newest bar is {age} days old"
    return False, f"newest bar {last}"


def download_batch(tickers, period, interval, attempt=1):
    """One yf.download. Returns a frame, empty if the batch failed.

    yfinance does not raise on a failed fetch — it returns an empty frame
    with the columns intact — so failure is tested by emptiness. A rate
    limit looks exactly like this, hence the backoff and single retry.
    """
    try:
        frame = yf.download(
            tickers, period=period, interval=interval,
            threads=(attempt == 1), auto_adjust=True, progress=False,
            group_by="ticker",
        )
    except Exception as exc:  # noqa: BLE001 - log and carry on
        log(f"  download raised {type(exc).__name__}: {exc}")
        frame = pd.DataFrame()

    if frame.empty and attempt == 1:
        log(f"  empty batch — backing off {BACKOFF_SECONDS:.0f}s and "
            f"retrying once, single-threaded")
        time.sleep(BACKOFF_SECONDS)
        return download_batch(tickers, period, interval, attempt=2)
    return frame


def fetch_interval(conn, interval, symbols, force_cold=False):
    """Download and store one tier. Returns a summary dict."""
    cold, why = is_cold(conn, interval)
    cold = cold or force_cold
    period = PERIODS[interval]["cold" if cold else "incremental"]
    log(f"{interval}: {'cold start' if cold else 'top-up'} ({why}) "
        f"-> period={period}, {len(symbols)} symbols")

    inserted_before = conn.execute(
        "SELECT COUNT(*) FROM bars WHERE interval = ?", (interval,)
    ).fetchone()[0]

    failed, offered = [], 0
    batches = [symbols[i:i + BATCH_SIZE]
               for i in range(0, len(symbols), BATCH_SIZE)]

    for n, batch in enumerate(batches, start=1):
        tickers = [fno.yahoo_ticker(s) for s in batch]
        t0 = time.perf_counter()
        frame = download_batch(tickers, period, interval)
        secs = time.perf_counter() - t0

        got = 0
        for symbol, ticker in zip(batch, tickers):
            try:
                sub = frame[ticker].dropna(how="all")
            except (KeyError, TypeError):
                failed.append(symbol)
                continue
            if sub.empty:
                failed.append(symbol)
                continue
            offered += intraday_store.write_bars(conn, symbol, interval, sub)
            got += 1
        conn.commit()

        log(f"  batch {n}/{len(batches)}: {got}/{len(batch)} symbols, "
            f"{secs:.1f}s")
        if n < len(batches):
            time.sleep(PAUSE_SECONDS)

    inserted_after = conn.execute(
        "SELECT COUNT(*) FROM bars WHERE interval = ?", (interval,)
    ).fetchone()[0]

    pruned = intraday_store.prune(conn, interval)
    rows, n_symbols, first, last = intraday_store.coverage(conn, interval)
    zero_vol = intraday_store.zero_volume_count(conn, interval)

    log(f"  bars offered {offered:,}, new rows "
        f"{inserted_after - inserted_before:,}, pruned {pruned:,}")
    log(f"  store now: {rows:,} rows, {n_symbols} symbols, {first} .. {last}")
    # Volume == 0 is a diagnostic only — never a staleness test. It is
    # logged so a change in Yahoo's feed does not pass unnoticed.
    log(f"  diagnostic: Volume == 0 on {zero_vol:,} bars "
        f"({100 * zero_vol / max(rows, 1):.4f}%); not treated as stale")
    if failed:
        log(f"  NO DATA for {len(failed)}: {', '.join(sorted(failed))}")

    return {"interval": interval, "symbols": len(symbols),
            "new_rows": inserted_after - inserted_before,
            "failed": sorted(failed), "rows": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", choices=["15m", "1d", "both"],
                        default="both")
    parser.add_argument("--full", action="store_true",
                        help="force the cold-start period, ignoring coverage")
    parser.add_argument("--limit", type=int, default=0,
                        help="only the first N symbols, for a smoke test")
    args = parser.parse_args(argv)

    symbols = fno.symbols()
    if args.limit:
        symbols = symbols[:args.limit]

    intervals = ["15m", "1d"] if args.interval == "both" else [args.interval]

    t0 = time.perf_counter()
    log(f"store: {intraday_store.db_path()}")
    conn = intraday_store.get_write_connection()
    try:
        summaries = [fetch_interval(conn, i, symbols, args.full)
                     for i in intervals]
    finally:
        conn.close()

    log(f"done in {time.perf_counter() - t0:.1f}s")
    return 0 if all(not s["failed"] for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
