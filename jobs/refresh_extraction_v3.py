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
import threading
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv  # noqa: E402

import extraction_v3 as ex  # noqa: E402
import guidance_logic  # noqa: E402
import nse_transcripts  # noqa: E402
import promise_check  # noqa: E402
import sector_cards  # noqa: E402
import sentiment_store_v3 as store  # noqa: E402
import transcript_source as ts  # noqa: E402
import verdict_synthesis  # noqa: E402
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


#: Hard ceiling on a single attempt. nse_urlfetch (nselib) opens its
#: requests.Session().get() calls with NO timeout at all - confirmed by
#: reading its source - so a connection NSE accepts but never answers hangs
#: the process forever rather than raising anything retry logic can see.
#: This is exactly what happened on the live Nifty-50 pilot run: the process
#: sat alive for 11+ hours with 31 seconds of total CPU time used, no
#: exception, nothing in the log. A plain function call cannot be timed out
#: from the outside in Python, so each attempt runs in its own thread and is
#: abandoned (not killed - the thread leaks, but the process moves on) if it
#: doesn't return in time.
CALL_TIMEOUT_SECONDS = 120


def _run_with_timeout(fn, args, kwargs, timeout):
    """Runs fn in a DAEMON thread and waits up to `timeout` seconds.

    Not ThreadPoolExecutor: its worker threads are non-daemon, so even with
    shutdown(wait=False) the leaked thread still blocks the whole PROCESS
    from exiting at the end of a run - verified live, the retry logic itself
    worked (two correctly-logged timeouts) but the script then hung on exit
    and had to be force-killed. A plain daemon thread has no such effect:
    Python exits regardless of what it's still doing.

    Returns ("ok", value), ("timeout", None) or ("error", exception).
    """
    box = {}

    def runner():
        try:
            box["value"] = fn(*args, **kwargs)
            box["state"] = "ok"
        except BaseException as e:          # noqa: BLE001 - re-raised by the caller
            box["error"] = e
            box["state"] = "error"

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        return "timeout", None
    if box.get("state") == "error":
        return "error", box["error"]
    return "ok", box.get("value")


def call_with_retry(label, fn, *args, **kwargs):
    """Retries transient failures with backoff; never retries a deterministic
    one. A ValueError here means the document itself is the problem (an
    unreadable PDF, a cite that is not in the text) - the same fail-fast
    contract every other job in this repo uses. A hang past
    CALL_TIMEOUT_SECONDS is treated as transient, same as a 503."""
    last = None
    for attempt, delay in enumerate((0,) + RETRY_DELAYS):
        if delay:
            log(f"    retrying {label} in {delay}s (attempt {attempt + 1})")
            time.sleep(delay)
        state, result = _run_with_timeout(fn, args, kwargs, CALL_TIMEOUT_SECONDS)
        if state == "ok":
            return result
        if state == "timeout":
            last = TimeoutError(f"{label} exceeded {CALL_TIMEOUT_SECONDS}s")
            log(f"    {label}: hung past {CALL_TIMEOUT_SECONDS}s - abandoning "
                f"attempt {attempt + 1}")
            continue
        if isinstance(result, ValueError):
            raise result
        last = result
        if not _is_transient(result):
            raise result
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


def _find_prior_quarter(conn, symbol, quarter_end):
    """The most recent stored quarter CHRONOLOGICALLY before this one, for
    promised-vs-delivered. Not "the previous row processed in this run" -
    due_quarters walks newest-first, so a symbol's actual prior quarter is
    very often not yet reached when the current one is being processed; this
    queries what is actually in storage, any extractor_version, regardless
    of backfill order."""
    row = conn.execute(
        "SELECT quarter_end, fiscal_quarter FROM earnings_call_extraction "
        "WHERE symbol=? AND quarter_end<? AND row_status='OK' "
        "ORDER BY quarter_end DESC LIMIT 1",
        (symbol, quarter_end),
    ).fetchone()
    return row


def _build_verdict_facts(conn, symbol, qe, parsed):
    """Compact, already-verified facts for verdict_synthesis - never the
    transcript. Pulls sector slots from `parsed` directly (already verified
    moments ago in this same call) rather than re-reading them, and reads the
    prior quarter's guidance straight off `conn` via sentiment_store_v3's
    uncached helpers - events_db's cached reads could return a stale result
    for a write this same process just made."""
    formal = parsed.get("guidance_formal") or {}
    facts = {
        "tones": {
            k: (parsed.get(k) or {}).get("score")
            for k in ("mgmt_confidence", "overall_tone", "qa_tone", "analyst_tone")
        },
        "qa_divergence": {
            "grade": (parsed.get("qa_divergence") or {}).get("grade"),
            "note": (parsed.get("qa_divergence") or {}).get("note"),
        },
        "guidance_formal": {
            key: {"status": f.get("status"), "value_text": f.get("value_text"),
                 "applies_to_period": f.get("applies_to_period")}
            for key, f in formal.items()
        },
        "risks": [{"text": r.get("text"), "sized": r.get("sized")}
                 for r in (parsed.get("risks") or [])],
        "pushback": {"total": parsed.get("pushback_total"),
                    "dodged": parsed.get("pushback_dodged")},
        "promised_vs_delivered": None,
    }

    prior = _find_prior_quarter(conn, symbol, qe)
    if prior:
        prior_qe, prior_label = prior
        prior_formal = store.read_guidance_formal(conn, symbol, prior_qe)
        current_slots = parsed.get("sector_slots") or {}
        facts["promised_vs_delivered"] = guidance_logic.promised_vs_delivered(
            prior_formal, current_slots, prior_label)
    return facts


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
            # A gap row still records WHEN we looked - that is the fact being
            # stored, and it is what stops a later run mistaking "checked,
            # nothing filed" for "never checked".
            store.write_source(conn, symbol, qe,
                               {"fetched_at": timestamps["extracted_at"]},
                               timestamps,
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
                               {"url": filing["attchmntFile"],
                                "fetched_at": timestamps["extracted_at"]},
                               timestamps,
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
    # Pushback's example quote is picked from qa_assessments, which the line
    # above just verified - deriving it any earlier risks picking an
    # unverified quote as the Guidance tab's one illustrative example.
    parsed = ex.derive_pushback(parsed)

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
        stage2_model=u2.get("model"),
        stage2_model_fallback=u2.get("model_was_fallback", False),
    )
    slots = parsed.get("sector_slots") or {}
    given = sum(1 for v in slots.values() if v.get("status") == "GIVEN")
    fallback = " (fallback model)" if u2.get("model_was_fallback") else ""
    log(f"  {label}: stored{fallback} - tone {parsed['overall_tone']['score']:+d} "
        f"evasive {len(parsed['evasive_exchanges'])} "
        f"divergence {parsed['qa_divergence']['grade']} "
        f"slots {given}/{len(slots)} tokens {tokens:,} via {u2.get('model')}")

    # Verdict synthesis: a third, small call over the facts just stored, not
    # the transcript (verdict_synthesis.py). Failure here does not undo the
    # extraction above - a missing verdict is a smaller, separately-visible
    # gap (the UI's top card falls back to a plain summary, spec 5's "hide
    # what has no data" rule), not a reason to discard a correct extraction.
    try:
        facts = _build_verdict_facts(conn, symbol, qe, parsed)
        verdict, u3 = call_with_retry(f"verdict({label})",
                                      verdict_synthesis.synthesize, api_key, facts)
        with conn:
            store.write_verdict(conn, symbol, qe, verdict, facts,
                               ex.EXTRACTOR_VERSION, timestamps["extracted_at"])
        log(f"  {label}: verdict stored - tokens {u3.get('total_tokens') or 0:,}")
    except Exception as e:
        log(f"  {label}: verdict synthesis failed (extraction itself still "
            f"stored): {type(e).__name__}: {e}")

    # Promise checking: tests the PRIOR quarter's own commitments against
    # what THIS call actually says (promise_check.py) - transcript-grounded,
    # unlike verdict synthesis, so its quotes are cite-verified against THIS
    # quarter's text the same way stage 2's are. Needs the prior quarter to
    # already have commitments stored; on the first run after v3.2.0, a
    # quarter whose prior hasn't been re-extracted yet simply has nothing to
    # test against, which is a normal, visible gap (the UI hides the section),
    # not a failure. Non-fatal for the same reason verdict synthesis is: a
    # failure here must not undo the extraction already stored.
    try:
        prior = _find_prior_quarter(conn, symbol, qe)
        if prior:
            prior_qe, _ = prior
            prior_commitments = store.read_commitments(conn, symbol, prior_qe)
            if prior_commitments:
                checks, u4 = call_with_retry(
                    f"promise_check({label})", promise_check.check,
                    api_key, prior_commitments, ex.exchange_map(exchanges), text)
                checks, pc_failures = promise_check.verify(checks, text)
                if pc_failures:
                    log(f"  {label}: promise_check {len(pc_failures)} cite "
                        f"failure(s), first: {pc_failures[0]}")
                with conn:
                    store.write_promise_checks(conn, symbol, qe, prior_qe,
                                               checks, provenance, timestamps)
                log(f"  {label}: promise_check stored - {len(checks)}/"
                    f"{len(prior_commitments)} testable, "
                    f"tokens {u4.get('total_tokens') or 0:,}")
    except Exception as e:
        log(f"  {label}: promise_check failed (extraction itself still "
            f"stored): {type(e).__name__}: {e}")

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
            f"stage1 {ex.STAGE1_MODEL}, stage2 chain {'>'.join(ex.STAGE2_MODEL_CHAIN)}")
        counts = run(conn, api_key, symbols, args.quarters, force=args.force)
        log("Summary: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
