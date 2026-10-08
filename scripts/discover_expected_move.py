"""
Discovery pass for the expected-move feature (expected_move_spec-3c050a43.txt,
section 2). Answers questions the rest of the feature is sized against before
any schema/job code is written:

  a. get_earnings_dates coverage for 5 sample names - column names, index
     tz, how many distinct past prints are actually available.
  d. interval='1h' depth - how far back does yfinance actually serve hourly
     bars per NSE equity, measured directly rather than assumed from the
     documented 730-day default.
  e. Bar timestamp labeling - does a bar's index stamp its start or its end,
     and what happens to the last bar of a session.

Section 2b/c (NSE corporate-announcements archive, exact filing timestamps,
distinguishing board-meeting notices from actual results submissions) and
2f (session calendar) are NOT covered here - see the report's own "NOT YET
ANSWERED" section for why and what's still needed before storage/jobs can
be built.

Writes docs/expected_move_discovery.txt and prints it.
"""

import time
import warnings

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

NAMES = ["TCS", "RELIANCE", "HDFCBANK", "INFY", "WIPRO"]

_lines = []


def log(s=""):
    _lines.append(str(s))
    print(s)


def section(title):
    log()
    log("=" * 70)
    log(title)
    log("=" * 70)


# --- a. get_earnings_dates coverage -----------------------------------

section("A. get_earnings_dates coverage (5 names, limit=12)")

earnings_results = {}
for sym in NAMES:
    t = yf.Ticker(f"{sym}.NS")
    try:
        df = t.get_earnings_dates(limit=12)
    except Exception as e:
        log(f"{sym}: FAILED - {e}")
        earnings_results[sym] = None
        time.sleep(1)
        continue
    earnings_results[sym] = df
    if df is None or df.empty:
        log(f"{sym}: EMPTY")
        time.sleep(1)
        continue
    idx = df.index
    now = pd.Timestamp.now(tz=idx.tz) if idx.tz is not None else pd.Timestamp.now()
    past = df[idx < now]
    future = df[idx >= now]
    log(f"{sym}: {len(df)} rows total, tz={idx.tz}, "
        f"{len(past)} past, {len(future)} future")
    log(f"  columns: {list(df.columns)}")
    log(f"  earliest: {idx.min()}  latest: {idx.max()}")
    if len(past) < 8:
        log(f"  ** fewer than 8 past prints available ({len(past)}) **")
    time.sleep(1)

# --- d. interval='1h' depth probe --------------------------------------

section("D. interval='1h' depth probe (period='max' and explicit start)")

hourly_results = {}
for sym in NAMES:
    t = yf.Ticker(f"{sym}.NS")
    try:
        df = t.history(period="730d", interval="1h")
    except Exception as e:
        log(f"{sym}: FAILED (730d) - {e}")
        hourly_results[sym] = None
        time.sleep(1)
        continue
    hourly_results[sym] = df
    if df is None or df.empty:
        log(f"{sym}: EMPTY for 1h/730d")
        time.sleep(1)
        continue
    log(f"{sym}: {len(df)} hourly bars, "
        f"earliest={df.index.min()}  latest={df.index.max()}")
    span_days = (df.index.max() - df.index.min()).days
    log(f"  span = {span_days} calendar days")
    time.sleep(1)

# try beyond 730d explicitly to see if yfinance silently clips or errors
section("D2. interval='1h' beyond 730d (period='3y') - does it clip or error?")
for sym in ["TCS"]:
    t = yf.Ticker(f"{sym}.NS")
    try:
        df = t.history(period="3y", interval="1h")
        if df is None or df.empty:
            log(f"{sym}: EMPTY for 1h/3y")
        else:
            log(f"{sym}: {len(df)} bars, earliest={df.index.min()} "
                f"latest={df.index.max()}")
    except Exception as e:
        log(f"{sym}: exception for 1h/3y - {e}")
    time.sleep(1)

# --- e. bar timestamp labeling ------------------------------------------

section("E. Bar timestamp labeling - sample session for TCS")

df = hourly_results.get("TCS")
if df is not None and not df.empty:
    # pick a recent date with a full day of bars
    last_date = df.index.max().date()
    day_df = df[df.index.date == last_date]
    log(f"Session {last_date}: {len(day_df)} bars")
    for ts, row in day_df.iterrows():
        log(f"  {ts}  O={row['Open']:.2f} H={row['High']:.2f} "
            f"L={row['Low']:.2f} C={row['Close']:.2f} V={int(row['Volume'])}")
    log()
    log("NSE regular session is 09:15-15:30 IST (6h15m = 375 min).")
    log("If bar timestamps are START-labeled, first bar ~09:15, last bar "
        "~14:xx or 15:xx with a short final bar covering the remainder.")
    log("If END-labeled, first bar would be ~10:15, last bar ~15:30.")
else:
    log("No TCS hourly data available to inspect.")

# --- summary coverage matrix ---------------------------------------------

section("SUMMARY MATRIX")
log(f"{'symbol':<10}{'past_prints':<14}{'hourly_bars':<14}{'hourly_span_days':<18}")
for sym in NAMES:
    edf = earnings_results.get(sym)
    past_n = "n/a"
    if edf is not None and not edf.empty:
        idx = edf.index
        now = pd.Timestamp.now(tz=idx.tz) if idx.tz is not None else pd.Timestamp.now()
        past_n = str(len(edf[idx < now]))
    hdf = hourly_results.get(sym)
    hbars = "n/a" if hdf is None or hdf.empty else str(len(hdf))
    hspan = "n/a" if hdf is None or hdf.empty else str((hdf.index.max() - hdf.index.min()).days)
    log(f"{sym:<10}{past_n:<14}{hbars:<14}{hspan:<18}")

section("NOT YET ANSWERED (spec 2b, 2c, 2f, 2g)")
log("- 2b/2c: NSE corporate-announcements archive (exact filing timestamps,")
log("  distinguishing board-meeting notices from actual results submissions)")
log("  needs the raw NSE API (https://www.nseindia.com/api/corporate-announcements),")
log("  not covered by nselib's event_calendar_for_equity (which only returns")
log("  the planned board-meeting date, not the post-meeting filing instant).")
log("  This is the harder half of gate zero: confirmed nselib reaches NSE from")
log("  GitHub Actions (test_nse_access.yml, 9 Oct 2026), but the raw")
log("  corporate-announcements archive endpoint is a different URL/auth surface")
log("  and has NOT been separately tested from a runner yet.")
log("- 2f: no session-calendar utility exists in this repo by design (see")
log("  prices.py, analytics/tiers.py, data_layer/intraday_store.py - all")
log("  deliberately derive session presence from actual price-data gaps")
log("  rather than a weekday+holiday-list calendar). The expected-move spec")
log("  explicitly requires a verified session calendar, which is new scope")
log("  against that existing convention and needs its own decision.")
log("- 2g: whether raw-NSE-archive access from GitHub Actions 403s or not is")
log("  unverified - distinct from the nselib check already passed.")

with open("docs/expected_move_discovery.txt", "w") as f:
    f.write("\n".join(_lines) + "\n")
print("\nSaved to docs/expected_move_discovery.txt")
