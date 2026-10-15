"""
Sector-aware earnings-call extraction (v3 pipeline). For each symbol and
quarter: find the NSE transcript, fetch/hash/archive it, segment it into
numbered exchanges, extract the sheet fields plus the universal frame and the
sector slots, verify every cite, and store the lot.

Run manually:
    python -m jobs.refresh_extraction_v3 --symbols TCS
    python -m jobs.refresh_extraction_v3 --symbols TCS --quarters 6
    python -m jobs.refresh_extraction_v3 --nifty50          (pilot, spec 7.4)

Not on a schedule. The spec's pilot gate (8.9) requires stopping after the
Nifty 50 batch for review before the full universe, so this stays a
by-hand job until Adarsh says otherwise.

WRITES ONLY ITS OWN TABLES. The eight v3 tables and nothing else - the live
earnings_call_sentiment that the current Sentiment tab reads is untouched,
so the app keeps working while this is proven. Same one-job-one-table
discipline every other job in this repo follows.

COST GUARD (spec 7.7): two LLM calls per name-quarter, segmentation then
extraction, well inside the spec's 20-call budget. Tokens in/out are logged
per call so the pilot replaces the spec's estimate with a measured number.

RATE LIMITS: NSE gets one request in flight with a pause between symbols; the
LLM gets exponential backoff on transient 503/429 and the run stops on
repeated blocks rather than hammering (spec 7.4). A deterministic content
failure - an unreadable PDF, a cite that is not in the transcript - is never
retried, because retrying cannot change what is in the document.
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv  # noqa: E402

import extraction_v3 as ex  # noqa: E402
import nse_transcripts  # noqa: E402
import sector_cards  # noqa: E402
import sentiment_store_v3 as store  # noqa: E402
import transcript_source as ts  # noqa: E402
from jobs.refresh_expected_moves import fetch_archive, identify_filings  # noqa: E402

load_dotenv()

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs",
                        "refresh_extraction_v3.log")
IST = timezone(timedelta(hours=5, minutes=30))

#: Backfill depth. Adarsh chose 6 (spec 7.2) - matches the 6-quarter pattern
#: the desk already uses and the depth the v3 extraction was validated on.
BACKFILL_QUARTERS = 6

#: How far back to look for completed results filings. `events` is NOT the
#: source here: it is a narrow rolling window built for the Calendar tab, so
#: a quarter from a few months ago has already aged out of it (found live,
#: 14 Oct 2026). NSE's own archive, via identify_filings, is the real history.
RESULTS_LOOKBACK_DAYS = 760

#: How far past a results date to look for that quarter's transcript.
TRANSCRIPT_WINDOW_DAYS = 30

#: Transient-failure backoff. Long tail because the flash tiers return 503
#: "high demand" in bursts that can last minutes - observed repeatedly on
#: 16 Oct 2026. Giving up early would leave holes in a backfill that is
#: expensive to re-run.
RETRY_DELAYS = (5, 15, 45, 90, 180)
NSE_PAUSE_SECONDS = 2
INTER_CALL_SLEEP = 2


def log(msg):
    line = f"{datetime.now(IST).isoformat()} {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def _is_rate_limit(exc):
    text = str(exc).lower()
    return any(s in text for s in
               ("429", "too many requests", "rate limit", "resource_exhausted"))


def _is_transient(exc):
    text = str(exc).lower()
    return _is_rate_limit(exc) or any(
        s in text for s in ("503", "unavailable", "high demand", "500",
                            "internal error", "timeout", "deadline"))


def call_with_retry(label, fn, *args, **kwargs):
    """Retries transient failures with backoff; never retries a deterministic
    one. A ValueError here means the document itself is the problem (an
    unreadable PDF, a cite that is not in the text) - the same fail-fast
    contract every other job in this repo uses."""
    last = None
    for attempt, delay in enumerate((0,) + RETRY_DELAYS):
        if delay:
            log(f"    retrying {label} in {delay}s (attempt {attempt + 1})")
            time.sleep(delay)
        try:
            return fn(*args, **kwargs)
        except ValueError:
            raise
        except Exception as e:
            last = e
            if not _is_transient(e):
                raise
    raise RuntimeError(f"{label} failed after {len(RETRY_DELAYS)} retries") from last


def due_quarters(symbol, limit):
    """(filing_dt, quarter_end) for the most recent `limit` completed results
    filings NSE's archive has for this symbol, newest first (spec 7.3: newest
    quarter first, so a run that stops early still leaves the useful end)."""
    from_date = date.today() - timedelta(days=RESULTS_LOOKBACK_DAYS)
    rows = fetch_archive(symbol, from_date, date.today())
    filings = identify_filings(symbol, rows)
    out = []
    for f in filings:
        try:
            qe = date.fromisoformat(f["financial_period"])
        except (TypeError, ValueError):
            continue
        out.append((f["filing_dt"], qe))
    out.sort(key=lambda p: p[1], reverse=True)
    return out[:limit]


def _fiscal_quarter(quarter_end):
    """'Q2 FY27' for a quarter ended 30 Sep 2026. Indian FY runs Apr-Mar, so
    the FY label is the calendar year of the March the quarter falls before."""
    q = (quarter_end.month - 1) // 3 + 1          # calendar quarter
    fq = {1: 4, 2: 1, 3: 2, 4: 3}[q]              # Jan-Mar is Q4 of that FY
    fy = quarter_end.year + (1 if quarter_end.month > 3 else 0)
    return f"Q{fq} FY{str(fy)[-2:]}"


def _timestamps(filing_dt, transcript_filing):
    """The section-5 timestamps for one extraction.

    source_published_at is the transcript's OWN NSE broadcast time, never the
    results announcement - a transcript appears days after the print, so for a
    transcript-derived feature feature_available_at equals that publish time.
    If NSE gives no usable publish time the row is marked UNKNOWN rather than
    defaulted, and is excluded from any time-sensitive use.
    """
    announcement_at = filing_dt.isoformat() if filing_dt else None
    published_at, quality = None, "OK"
    raw = (transcript_filing or {}).get("an_dt")
    if raw:
        try:
            published_at = (datetime.strptime(raw, "%d-%b-%Y %H:%M:%S")
                            .replace(tzinfo=IST).isoformat())
        except ValueError:
            published_at = None
    if published_at is None:
        quality = "UNKNOWN"
    return {
        "announcement_at": announcement_at,
        "announcement_at_precision": "instant" if announcement_at else None,
        "source_published_at": published_at,
        "feature_available_at": published_at,
        "timestamp_quality": quality,
        "extracted_at": datetime.now(IST).isoformat(),
    }


def _price_move(conn, symbol, quarter_end):
    """PRICE MOVE for the sheet, reusing the Expected-move tracker's own
    signed_move_pct (Adarsh's call: do not add a second price source). Its
    session handling already deals with post-close and pre-open prints, which
    is the hard part of 'next-session close vs results-day close'."""
    row = conn.execute(
        "SELECT signed_move_pct, session_a_date, session_b_date "
        "FROM earnings_move_prints WHERE symbol=? AND financial_period=? "
        "AND signed_move_pct IS NOT NULL ORDER BY calculation_version DESC LIMIT 1",
        (symbol, quarter_end.isoformat()),
    ).fetchone()
    if not row:
        return None
    return {"pct": row[0], "from_date": row[1], "to_date": row[2]}


def process_quarter(conn, api_key, symbol, filing_dt, quarter_end, force=False):
    qe = quarter_end.isoformat()
    label = f"{symbol} {qe}"

    window_end = filing_dt.date() + timedelta(days=TRANSCRIPT_WINDOW_DAYS)
    rows = call_with_retry(f"archive({symbol})", nse_transcripts.fetch_archive,
                           symbol, filing_dt.date(), window_end)
    filing = nse_transcripts.select_transcript(rows)
    if filing is None:
        log(f"  {label}: no transcript filed - recorded as unavailable")
        timestamps = _timestamps(filing_dt, None)
        with conn:
            store.write_source(conn, symbol, qe, {}, timestamps,
                               fiscal_quarter=_fiscal_quarter(quarter_end),
                               transcript_available=0)
        return "no_transcript"

    time.sleep(NSE_PAUSE_SECONDS)
    timestamps = _timestamps(filing_dt, filing)
    sector_info = {
        "sector": sector_cards.primary_group(symbol),
        "sector_card": sector_cards.card_key_for_symbol(symbol),
    }

    try:
        prepared = call_with_retry(f"fetch({symbol})", ts.prepare,
                                   symbol, qe, filing["attchmntFile"])
    except ts.TranscriptUnreadable as e:
        log(f"  {label}: unreadable PDF - {e}")
        with conn:
            store.write_source(conn, symbol, qe,
                               {"url": filing["attchmntFile"]}, timestamps,
                               fiscal_quarter=_fiscal_quarter(quarter_end),
                               pdf_unreadable=1)
            store.write_failed_row(
                conn, symbol, qe,
                {"extractor_version": ex.EXTRACTOR_VERSION}, timestamps,
                f"pdf_unreadable: {e}", sector_info, row_status="NOT_VERIFIED")
        return "unreadable"

    if not force and store.already_extracted(
            conn, symbol, qe, prepared["pdf_sha256"], ex.EXTRACTOR_VERSION):
        log(f"  {label}: already extracted at this version - skipped")
        return "skipped"

    provenance = {
        "extractor_version": ex.EXTRACTOR_VERSION,
        "source_pdf_sha256": prepared["pdf_sha256"],
        "cleaned_text_sha256": prepared["cleaned_text_sha256"],
    }
    text = prepared["cleaned_text"]

    exchanges, u1 = call_with_retry(f"segment({label})", ex.segment, api_key, text)
    time.sleep(INTER_CALL_SLEEP)
    parsed, u2 = call_with_retry(f"extract({label})", ex.extract,
                                 api_key, text, exchanges, symbol)
    parsed, failures = ex.verify_and_locate(parsed, text)

    tokens = (u1.get("total_tokens") or 0) + (u2.get("total_tokens") or 0)
    if failures:
        # verify_and_locate has already demoted each affected field to
        # NOT_VERIFIED and dropped unverifiable soft-outlook items, so what
        # remains is storable and nothing unverified is stored as GIVEN. The
        # failures are recorded on the row so QA 8.2 stays auditable.
        log(f"  {label}: {len(failures)} cite failure(s), affected field(s) "
            f"demoted to NOT_VERIFIED. first: {failures[0]}")

    store.write_all(
        conn, symbol, qe, parsed, exchanges, prepared, provenance, timestamps,
        sector_info,
        price_move=_price_move(conn, symbol, quarter_end),
        fiscal_quarter=_fiscal_quarter(quarter_end),
        call_date=filing_dt.date().isoformat(),
        cite_failures=failures,
    )
    slots = parsed.get("sector_slots") or {}
    given = sum(1 for v in slots.values() if v.get("status") == "GIVEN")
    log(f"  {label}: stored - tone {parsed['overall_tone']['score']:+d} "
        f"evasive {len(parsed['evasive_exchanges'])} "
        f"divergence {parsed['qa_divergence']['grade']} "
        f"slots {given}/{len(slots)} tokens {tokens:,}")
    return "stored"


def run(conn, api_key, symbols, quarters=BACKFILL_QUARTERS, force=False):
    counts = {"stored": 0, "skipped": 0, "no_transcript": 0, "unreadable": 0,
              "errors": 0}
    for symbol in symbols:
        log(f"{symbol}: looking for the last {quarters} results filings")
        try:
            quarters_due = call_with_retry(
                f"due_quarters({symbol})", due_quarters, symbol, quarters)
        except Exception as e:
            log(f"  {symbol}: could not list filings: {type(e).__name__}: {e}")
            counts["errors"] += 1
            continue

        for filing_dt, quarter_end in quarters_due:
            try:
                counts[process_quarter(conn, api_key, symbol, filing_dt,
                                       quarter_end, force=force)] += 1
            except Exception as e:
                if _is_rate_limit(e):
                    log(f"  {symbol}: rate limited ({e}) - stopping the run")
                    return counts
                log(f"  {symbol} {quarter_end}: failed: {type(e).__name__}: {e}")
                counts["errors"] += 1
        time.sleep(NSE_PAUSE_SECONDS)
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="comma-separated NSE symbols")
    ap.add_argument("--nifty50", action="store_true",
                    help="the pilot batch (spec 7.4): Nifty 50 constituents only")
    ap.add_argument("--quarters", type=int, default=BACKFILL_QUARTERS)
    ap.add_argument("--force", action="store_true",
                    help="re-extract even if this document and version are stored")
    args = ap.parse_args()

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.nifty50:
        from watchlists import WATCHLIST_PRESETS
        symbols = sorted({t.split(".")[0] for t, _ in WATCHLIST_PRESETS["Nifty 50"]})
    else:
        ap.error("pass --symbols or --nifty50")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set in .env - nothing to do.")
        sys.exit(1)

    conn = store.get_connection()
    try:
        log(f"v3 extraction starting: {len(symbols)} symbol(s), "
            f"{args.quarters} quarter(s) each, extractor {ex.EXTRACTOR_VERSION}, "
            f"stage1 {ex.STAGE1_MODEL}, stage2 {ex.STAGE2_MODEL}")
        counts = run(conn, api_key, symbols, args.quarters, force=args.force)
        log("Summary: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
