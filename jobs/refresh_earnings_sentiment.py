"""
Week 5-6 task: Earnings call sentiment tracker (see CLAUDE.md). For each
watchlist symbol's completed results quarters, finds the NSE-filed
transcript, runs Track A (Loughran-McDonald word counts, deterministic)
and Track B (Gemini structured extraction, management + Q&A scored
separately), and stores one row per (symbol, quarter).

Run manually: python -m jobs.refresh_earnings_sentiment [--symbols A,B]
Not yet on a schedule - this is new, unverified-at-scale work; run by
hand until it's been checked against a few real watchlist symbols.

SCOPE: the 4 custom watchlists only (watchlists.get_custom_lists()), not
the 5 read-only index-preset views (Nifty 50, Bank Nifty, etc - 50+
constituents each by design, a reference index view rather than "the
user's watchlist" in the sense this spec means, and processing that many
transcripts every quarter would be well outside a personal-desk feature's
intended scope). --symbols overrides this for manual testing against
specific names regardless of what's in a watchlist.

COST GUARD: 2 Gemini calls per transcript (management, then Q&A) -
comfortably inside the spec's "max ~20 calls per transcript" budget.
Both calls receive the full transcript text (no Python-side pre-split -
see nse_transcripts.py and earnings_sentiment.py for why) and token usage
from each call is logged, per the spec.

INSERT-ONLY: this job only ever writes earnings_call_sentiment, a new
additive table - it does not touch events, earnings_estimates, or
earnings_history_quarters, matching how every other job in this repo
only owns its own table.
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sqlite3  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

import earnings_sentiment  # noqa: E402
import nse_transcripts  # noqa: E402
from analytics.lm_sentiment import count_words, per_1000_words  # noqa: E402
from jobs.refresh_expected_moves import fetch_archive, identify_filings  # noqa: E402
from watchlists import get_custom_lists, get_list_items  # noqa: E402

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "events.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "schema_sentiment.sql")
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "refresh_earnings_sentiment.log")
IST = timezone(timedelta(hours=5, minutes=30))
RETRY_DELAYS = (5, 15, 45)
INTER_CALL_SLEEP = 2

#: How far past a quarter's results date to look for its transcript.
#: RELIANCE/TCS/HDFCBANK/HINDUNILVR all filed within 1-7 days of results
#: (see nse_transcripts.py's discovery notes); 30 days is a generous
#: margin for a slower filer without risking picking up the NEXT
#: quarter's call.
TRANSCRIPT_WINDOW_DAYS = 30

#: How far back to look for completed results filings. The `events`
#: table was tried first and rejected here - it's a narrow rolling
#: window (upcoming + very recent, ~1 month wide in practice), built for
#: the Calendar tab's own purpose, not a historical archive. A symbol
#: whose results happened more than a few weeks ago - which is most of
#: them, for a feature tracking sentiment "quarter over quarter" - simply
#: isn't in it (found live, 14 Oct 2026: RELIANCE's 17 Jul 2026 results
#: don't appear in `events` at all). NSE's own corporate-announcements
#: archive, queried directly via identify_filings() - already built and
#: proven for the Expected-move tracker's multi-year backfill - is the
#: real historical source.
RESULTS_LOOKBACK_DAYS = 730


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
            # A deterministic content problem (e.g. nse_transcripts'
            # "suspiciously little text" guard) - retrying can't fix
            # what's in the document. Same reasoning as the identical
            # guard in jobs/fill_actuals.py and
            # jobs/refresh_earnings_history.py.
            raise
        except Exception as e:
            last_error = e
            if _is_rate_limit(e):
                raise
            log(f"  {label} attempt {attempt + 1} failed: {type(e).__name__}: {e}")
    raise RuntimeError(f"{label} failed after retries") from last_error


def _is_rate_limit(exc):
    text = str(exc).lower()
    return "429" in text or "too many requests" in text or "rate limit" in text or "resource_exhausted" in text


def watchlist_symbols():
    """Bare NSE symbols across the 4 custom watchlists (not the 5
    read-only index-preset views - see module docstring)."""
    symbols = set()
    for lst in get_custom_lists():
        for item in get_list_items(lst["id"]):
            symbols.add(item["ticker"].split(".")[0])
    return sorted(symbols)


def due_quarters(conn, symbol):
    """(filing_dt, quarter_end) for every completed results filing NSE's
    own archive has on record for `symbol` that doesn't already have a
    row in earnings_call_sentiment. quarter_end comes straight from the
    filing's own "for the period ended ..." text (identify_filings'
    extract_period_end) - more precise than inferring it from a calendar
    date, and it's what the filing itself says the quarter is."""
    from_date = date.today() - timedelta(days=RESULTS_LOOKBACK_DAYS)
    rows = fetch_archive(symbol, from_date, date.today())
    filings = identify_filings(symbol, rows)

    out = []
    for f in filings:
        qe = date.fromisoformat(f["financial_period"])
        existing = conn.execute(
            "SELECT 1 FROM earnings_call_sentiment WHERE ticker = ? AND quarter_end = ?",
            (symbol, qe.isoformat()),
        ).fetchone()
        if not existing:
            out.append((f["filing_dt"], qe))
    return out


def process_quarter(conn, api_key, symbol, filing_dt, quarter_end):
    window_end = filing_dt.date() + timedelta(days=TRANSCRIPT_WINDOW_DAYS)

    rows = call_with_retry(
        f"archive({symbol})", nse_transcripts.fetch_archive, symbol, filing_dt.date(), window_end,
    )
    filing = nse_transcripts.select_transcript(rows)
    if filing is None:
        log(f"  {symbol} {quarter_end}: no transcript filed yet - will retry next run")
        return "pending"

    text = call_with_retry(
        f"transcript_text({symbol})", nse_transcripts.fetch_transcript_text, filing["attchmntFile"],
    )
    time.sleep(INTER_CALL_SLEEP)

    lm_counts = count_words(text)
    lm_norm = per_1000_words(lm_counts)

    mgmt, mgmt_usage = call_with_retry(
        f"gemini_mgmt({symbol})", earnings_sentiment.extract_track_b, api_key, text, "management",
    )
    log(f"  {symbol} {quarter_end}: management extraction, tokens={mgmt_usage['total_tokens']}")
    time.sleep(INTER_CALL_SLEEP)

    qa, qa_usage = call_with_retry(
        f"gemini_qa({symbol})", earnings_sentiment.extract_track_b, api_key, text, "qa",
    )
    log(f"  {symbol} {quarter_end}: qa extraction, tokens={qa_usage['total_tokens']}")

    # filing["an_dt"] is NSE's own "19-Jul-2026 18:06:37" format - parsed
    # to ISO so call_date matches the schema's stated format and every
    # other date column in this app.
    call_date = datetime.strptime(filing["an_dt"], "%d-%b-%Y %H:%M:%S").date().isoformat()
    now = datetime.now(IST).isoformat()
    conn.execute(
        """
        INSERT INTO earnings_call_sentiment
            (ticker, quarter_end, call_date, transcript_source_url,
             lm_positive, lm_negative, lm_uncertainty, lm_litigious, lm_total_words,
             mgmt_tone, qa_tone, guidance, extraction_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, quarter_end) DO UPDATE SET
            call_date = excluded.call_date,
            transcript_source_url = excluded.transcript_source_url,
            lm_positive = excluded.lm_positive,
            lm_negative = excluded.lm_negative,
            lm_uncertainty = excluded.lm_uncertainty,
            lm_litigious = excluded.lm_litigious,
            lm_total_words = excluded.lm_total_words,
            mgmt_tone = excluded.mgmt_tone,
            qa_tone = excluded.qa_tone,
            guidance = excluded.guidance,
            extraction_json = excluded.extraction_json,
            created_at = excluded.created_at
        """,
        (
            symbol, quarter_end.isoformat(), call_date, filing["attchmntFile"],
            lm_norm["positive"], lm_norm["negative"], lm_norm["uncertainty"], lm_norm["litigious"],
            lm_counts["total_words"],
            mgmt["tone_score"], qa["tone_score"], mgmt["guidance"],
            json.dumps({"management": mgmt, "qa": qa}),
            now,
        ),
    )
    conn.commit()
    log(f"  {symbol} {quarter_end}: stored - mgmt_tone={mgmt['tone_score']} "
        f"qa_tone={qa['tone_score']} guidance={mgmt['guidance']}")
    return "stored"


def run(conn, api_key, symbols=None):
    targets = symbols or watchlist_symbols()
    if not targets:
        log("No watchlist symbols to process (custom watchlists are empty, "
            "and --symbols wasn't given).")
        return {"symbols": 0, "stored": 0, "pending": 0, "errors": 0}

    counts = {"symbols": 0, "stored": 0, "pending": 0, "errors": 0}
    for symbol in targets:
        quarters = due_quarters(conn, symbol)
        if not quarters:
            continue
        counts["symbols"] += 1
        for filing_dt, quarter_end in quarters:
            try:
                status = process_quarter(conn, api_key, symbol, filing_dt, quarter_end)
                counts[status] += 1
            except Exception as e:
                if _is_rate_limit(e):
                    log(f"  {symbol}: rate limited ({e}) - stopping run, retry next time")
                    return counts
                log(f"  {symbol} {quarter_end}: failed: {type(e).__name__}: {e}")
                counts["errors"] += 1
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="comma-separated; default is the 4 custom watchlists")
    args = ap.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set in .env - nothing to do.")
        sys.exit(1)

    conn = get_connection()
    try:
        log("Earnings-sentiment refresh starting")
        counts = run(conn, api_key, symbols)
        log(f"Summary: symbols={counts['symbols']} stored={counts['stored']} "
            f"pending={counts['pending']} errors={counts['errors']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
