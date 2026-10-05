"""
Step 0 discovery run for the F&O correlation matrix (see
correlation_matrix_spec-43ee700c.txt, section 4).

Answers the questions the rest of the feature is sized against, by asking
Yahoo rather than assuming:

  1. What does a 15m/60d fetch actually return per stock - bar count, the
     real session grid, gaps inside sessions, and stale bars?
  2. How long does one yf.download of all 213 F&O tickers take, and which
     tickers fail?
  3. How big is the resulting bars table on disk? Section 5 estimates
     ~40 MB total; this writes the real rows into the real schema and
     measures the file.
  4. Which names are genuinely illiquid? The spec asks for "one illiquid
     name" in the detail pass, but fno_sectors.json carries no turnover,
     so liquidity is ranked from the downloaded bars themselves instead
     of picked by guesswork.

yfinance does not raise on a failed fetch - it returns an empty frame with
the columns intact. So "did it fail" is tested by emptiness, not by
catching exceptions, though exceptions are caught too.

Writes the report to docs/intraday_discovery.txt and prints it. The temp
SQLite used for the size measurement goes to scratch/ (gitignored); no bar
data is written into the repo.
"""

import json
import os
import sqlite3
import time
import warnings
from datetime import time as time_type

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

ROOT = os.path.join(os.path.dirname(__file__), "..")
DOCS_DIR = os.path.join(ROOT, "docs")
SCRATCH_DIR = os.path.join(ROOT, "scratch")
SECTORS_PATH = os.path.join(ROOT, "fno_sectors.json")
REPORT_PATH = os.path.join(DOCS_DIR, "intraday_discovery.txt")
TMP_DB = os.path.join(SCRATCH_DIR, "intraday_size_probe.db")

INTERVAL = "15m"
PERIOD = "60d"

# NSE continuous session: 09:15 to 15:30, so 15m bars are stamped 09:15
# through 15:15 - 25 slots. Confirmed against a full RELIANCE session.
SESSION_SLOTS = [
    time_type(9 + (15 + 15 * i) // 60, (15 + 15 * i) % 60) for i in range(25)
]

DETAIL_NAMES = ["RELIANCE", "HDFCBANK", "TCS", "INDUSINDBK"]

_lines = []


def say(line=""):
    print(line)
    _lines.append(line)


def load_fno_symbols():
    with open(SECTORS_PATH) as f:
        return sorted(json.load(f)["stocks"].keys())


def stale_mask(df):
    """A bar is stale when O/H/L/C all equal the previous close (spec 6.3).
    Volume is deliberately not a staleness test - measured 6 Oct 2026, the
    zero-volume bars in this universe are feed artifacts whose price moved.
    """
    prev = df["Close"].shift(1)
    return (
        prev.notna()
        & (df["Open"] == prev)
        & (df["High"] == prev)
        & (df["Low"] == prev)
        & (df["Close"] == prev)
    )


def describe(symbol, df, fetch_secs, error=None):
    say(f"\n{'-' * 68}")
    say(f"{symbol}  ({INTERVAL} / {PERIOD})")
    say(f"{'-' * 68}")

    if error:
        say(f"  EXCEPTION: {type(error).__name__}: {error}")
        return
    if df.empty:
        say("  EMPTY frame returned - yfinance does not raise, so this is")
        say("  how a rate limit or a bad symbol shows up.")
        return

    sessions = df.index.normalize()
    n_sessions = sessions.nunique()
    zero_vol = df["Volume"] == 0
    stale = stale_mask(df)

    # Gaps inside sessions: compare each session against the 25-slot grid,
    # ignoring sessions that are short at the end (a live or half day).
    per_session = df.groupby(sessions).size()
    deficits = (25 - per_session)
    short = deficits[deficits > 0]

    say(f"  bars                 {len(df)}")
    say(f"  sessions             {n_sessions}")
    say(f"  bars per session     {len(df) / n_sessions:.2f}   (grid is 25)")
    say(f"  first bar            {df.index[0]}")
    say(f"  last bar             {df.index[-1]}")
    say(f"  fetch time           {fetch_secs:.2f}s")
    say(f"  Volume == 0          {int(zero_vol.sum())}"
        f"   ({100 * zero_vol.mean():.3f}%)")
    if zero_vol.any():
        moved = zero_vol & ~stale
        say(f"    of which price moved  {int(moved.sum())}"
            f"   (feed artifact, KEPT by the policy)")
        say(f"    timestamps            "
            f"{', '.join(str(t) for t in df.index[zero_vol][:5])}")
    say(f"  stale (flat OHLC)    {int(stale.sum())}"
        f"   ({100 * stale.mean():.3f}%)   <- dropped by the policy")
    say(f"  sessions under 25    {len(short)}")
    if len(short):
        worst = short.sort_values(ascending=False).head(5)
        for day, missing in worst.items():
            say(f"    {day.date()}  missing {int(missing)}")


def main():
    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(SCRATCH_DIR, exist_ok=True)

    symbols = load_fno_symbols()
    say("=" * 68)
    say("F&O CORRELATION - STEP 0 DISCOVERY")
    say("=" * 68)
    say(f"run at              {pd.Timestamp.now(tz='Asia/Kolkata')}")
    say(f"yfinance            {yf.__version__}")
    say(f"pandas              {pd.__version__}")
    say(f"F&O symbols         {len(symbols)} from fno_sectors.json")
    say(f"request             interval={INTERVAL} period={PERIOD}")

    # ---- Pass 1: per-stock detail -------------------------------------
    say("\n" + "=" * 68)
    say("PASS 1 - PER-STOCK DETAIL")
    say("=" * 68)

    for symbol in DETAIL_NAMES:
        t0 = time.perf_counter()
        err, df = None, pd.DataFrame()
        try:
            df = yf.Ticker(f"{symbol}.NS").history(
                period=PERIOD, interval=INTERVAL
            )
        except Exception as e:  # noqa: BLE001 - reporting, not handling
            err = e
        describe(symbol, df, time.perf_counter() - t0, err)

    # ---- Pass 2: all 213 in one download ------------------------------
    say("\n" + "=" * 68)
    say("PASS 2 - ALL 213 TICKERS IN ONE yf.download")
    say("=" * 68)

    tickers = [f"{s}.NS" for s in symbols]
    t0 = time.perf_counter()
    bulk = yf.download(
        tickers, period=PERIOD, interval=INTERVAL,
        threads=True, auto_adjust=True, progress=False, group_by="ticker",
    )
    bulk_secs = time.perf_counter() - t0

    say(f"  wall clock           {bulk_secs:.1f}s")
    say(f"  frame shape          {bulk.shape}")

    got, failed = {}, []
    for symbol, ticker in zip(symbols, tickers):
        try:
            sub = bulk[ticker].dropna(how="all")
        except KeyError:
            failed.append(symbol)
            continue
        if sub.empty:
            failed.append(symbol)
        else:
            got[symbol] = sub

    say(f"  tickers with data    {len(got)} / {len(tickers)}")
    say(f"  tickers failed       {len(failed)}")
    if failed:
        say(f"    {', '.join(failed)}")

    if not got:
        say("\n  Nothing came back - stopping before the size probe.")
        return

    counts = pd.Series({s: len(d) for s, d in got.items()})
    say(f"  bars per ticker      min {counts.min()}  median "
        f"{int(counts.median())}  max {counts.max()}")
    say(f"  total bars           {int(counts.sum()):,}")

    # ---- Pass 3: real on-disk size in the real schema -----------------
    say("\n" + "=" * 68)
    say("PASS 3 - ON-DISK SIZE IN THE SECTION 5 SCHEMA")
    say("=" * 68)

    if os.path.exists(TMP_DB):
        os.remove(TMP_DB)
    conn = sqlite3.connect(TMP_DB)
    conn.execute("""
        CREATE TABLE bars (
            symbol TEXT, interval TEXT, ts TEXT,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (symbol, interval, ts)
        )
    """)
    conn.execute("CREATE INDEX idx_bars_interval_ts ON bars(interval, ts)")

    rows = 0
    for symbol, d in got.items():
        payload = [
            (symbol, INTERVAL, ts.isoformat(),
             _f(r.get("Open")), _f(r.get("High")), _f(r.get("Low")),
             _f(r.get("Close")), _i(r.get("Volume")))
            for ts, r in d.iterrows()
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO bars VALUES (?,?,?,?,?,?,?,?)", payload
        )
        rows += len(payload)
    conn.commit()
    conn.execute("VACUUM")
    conn.close()

    size_mb = os.path.getsize(TMP_DB) / 1e6
    say(f"  rows inserted        {rows:,}")
    say(f"  db file              {size_mb:.1f} MB")
    say(f"  bytes per row        {os.path.getsize(TMP_DB) / max(rows, 1):.0f}")
    say("  spec 5 estimate      ~40 MB for ~320,000 rows (measured above)")
    say(f"  probe path           {os.path.relpath(TMP_DB, ROOT)}"
        f"  (scratch/, gitignored)")

    # ---- Pass 4: liquidity ranking from the bars themselves -----------
    say("\n" + "=" * 68)
    say("PASS 4 - LIQUIDITY RANKING (measured, not guessed)")
    say("=" * 68)
    say("  Median traded value per 15m bar = median(close x volume).")
    say("  fno_sectors.json has no turnover field, so the spec's")
    say("  'one illiquid name' is chosen from this instead.")

    liq, diag = {}, {}
    for symbol, d in got.items():
        value = (d["Close"] * d["Volume"]).replace(0, pd.NA).dropna()
        if len(value):
            liq[symbol] = value.median()
        diag[symbol] = (
            int((d["Volume"] == 0).sum()), int(stale_mask(d).sum()), len(d)
        )

    ranked = pd.Series(liq).sort_values()
    say("\n  thinnest 10 by median traded value per bar:")
    for symbol, v in ranked.head(10).items():
        zv, st, n = diag[symbol]
        say(f"    {symbol:14} Rs {v / 1e5:9.1f} lakh/bar   "
            f"bars={n:5}  zerovol={zv:3}  stale={st:3}")
    say("\n  most liquid 5 for contrast:")
    for symbol, v in ranked.tail(5)[::-1].items():
        zv, st, n = diag[symbol]
        say(f"    {symbol:14} Rs {v / 1e5:9.1f} lakh/bar   "
            f"bars={n:5}  zerovol={zv:3}  stale={st:3}")

    tot_bars = sum(n for _, _, n in diag.values())
    tot_zv = sum(zv for zv, _, _ in diag.values())
    tot_st = sum(st for _, st, _ in diag.values())
    say(f"\n  ACROSS ALL {len(diag)} TICKERS:")
    say(f"    bars               {tot_bars:,}")
    say(f"    Volume == 0        {tot_zv:,}  ({100 * tot_zv / tot_bars:.4f}%)")
    say(f"    stale (flat OHLC)  {tot_st:,}  ({100 * tot_st / tot_bars:.4f}%)")

    # Does staleness track illiquidity? That is the claim the spec's
    # original volume-based policy rested on.
    thin = ranked.head(20).index
    thick = ranked.tail(20).index
    say(f"\n  stale rate, thinnest 20:  "
        f"{100 * sum(diag[s][1] for s in thin) / sum(diag[s][2] for s in thin):.4f}%")
    say(f"  stale rate, thickest 20:  "
        f"{100 * sum(diag[s][1] for s in thick) / sum(diag[s][2] for s in thick):.4f}%")

    say("\n" + "=" * 68)
    say("STOP HERE - confirm before building the store, job or tab.")
    say("=" * 68)


def _f(v):
    return None if v is None or pd.isna(v) else float(v)


def _i(v):
    return None if v is None or pd.isna(v) else int(v)


if __name__ == "__main__":
    try:
        main()
    finally:
        os.makedirs(DOCS_DIR, exist_ok=True)
        with open(REPORT_PATH, "w") as f:
            f.write("\n".join(_lines) + "\n")
        print(f"\nReport written to {os.path.relpath(REPORT_PATH, ROOT)}")
