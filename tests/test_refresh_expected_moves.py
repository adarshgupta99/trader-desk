"""Regression tests for jobs/refresh_expected_moves.py's pure helpers
(filing text parsing and clustering - no network, no database). Both
cases here were real bugs caught during the first live backfill run
against the 5 discovery names on 9 Oct 2026, not hypothetical edge
cases - see the handoff note for the live symptoms.
"""

from datetime import date, datetime

import pytest

from jobs.refresh_expected_moves import (
    IST,
    compute_print_daily_fallback,
    extract_period_end,
    get_connection,
    identify_filings,
    upsert_print,
)


# --- extract_period_end: abbreviated vs full month names --------------------

def test_period_end_abbreviated_month():
    # TCS's own Q1 filings read "...for the period ended Jun 30, 2026."
    # while every other quarter spells the month out - a format-string-only
    # parser silently dropped every Q1 filing for every symbol.
    assert extract_period_end("financial results for the period ended Jun 30, 2026.") == "2026-06-30"


def test_period_end_full_month_name():
    assert extract_period_end("financial results for the quarter ended September 30, 2024.") == "2024-09-30"


def test_period_end_no_match_is_none():
    assert extract_period_end("no date here at all") is None


# --- identify_filings: same-day siblings vs. a genuine revision -------------

def _row(an_dt, seq_id, desc, text):
    return {"an_dt": an_dt, "seq_id": seq_id, "desc": desc, "attchmntText": text}


RESULTS_TEXT = "Company has submitted to the Exchange, the financial results for the quarter ended March 31, 2026."


def test_same_day_siblings_are_not_a_revision():
    # RELIANCE's real shape: Outcome-of-Board-Meeting and Dividend rows,
    # ~2 minutes apart, same results text, same evening. This is the
    # TCS 16:06:39/16:09:39 pattern the results_cluster column exists
    # for - it must never be flagged as source_conflict.
    rows = [
        _row("24-Apr-2026 19:26:28", "1", "Outcome of Board Meeting", RESULTS_TEXT),
        _row("24-Apr-2026 19:28:41", "2", "Dividend", RESULTS_TEXT),
    ]
    filings = identify_filings("RELIANCE", rows)
    assert len(filings) == 1
    assert filings[0]["source_conflict"] is False
    assert len(filings[0]["cluster"]) == 2


def test_different_day_match_is_a_genuine_revision():
    rows = [
        _row("24-Apr-2026 19:26:28", "1", "Outcome of Board Meeting", RESULTS_TEXT),
        _row("02-May-2026 10:00:00", "2", "Outcome of Board Meeting", RESULTS_TEXT),
    ]
    filings = identify_filings("RELIANCE", rows)
    assert len(filings) == 1
    assert filings[0]["source_conflict"] is True
    assert filings[0]["filing_dt"].day == 24  # earliest filing stays the print of record


def test_exact_duplicate_an_dt_not_counted_as_revision():
    # The documented NSE archive-duplicate-row phenomenon: identical an_dt,
    # different seq_id, same text.
    rows = [
        _row("25-Apr-2025 20:05:15", "1", "Outcome of Board Meeting", RESULTS_TEXT),
        _row("25-Apr-2025 20:05:15", "2", "Outcome of Board Meeting", RESULTS_TEXT),
    ]
    filings = identify_filings("RELIANCE", rows)
    assert len(filings) == 1
    assert filings[0]["source_conflict"] is False


# --- compute_print_daily_fallback: price-basis-discontinuity recovery -------
# Added 9 Oct 2026 after BAJFINANCE/SIEMENS/VEDL showed impossible move % -
# yfinance back-adjusts daily bars for a later split but not hourly bars, so
# a print straddling one mixed scales. Rather than just excluding those
# prints, recompute them from daily bars alone (both reference and window),
# which stay on one consistent scale across the split either way.

def test_daily_fallback_post_close_move_from_consistent_daily_scale():
    filing_dt = datetime(2024, 1, 5, 16, 0, 0, tzinfo=IST)  # Friday, post-close
    daily_bars = {
        date(2024, 1, 5): {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        date(2024, 1, 8): {"open": 100.0, "high": 112.0, "low": 98.0, "close": 110.0},
    }
    result = compute_print_daily_fallback(filing_dt, daily_bars, today=date(2024, 1, 10))
    assert result["release_mode"] == "post_close"
    assert result["reference_price"] == pytest.approx(100.0)
    assert result["reference_method"] == "daily_close_fallback"
    assert result["status"] == "usable"
    assert result["print_move_pct"] == pytest.approx(12.0)
    assert result["reason_codes"] == ["price_basis_daily_fallback"]


def test_daily_fallback_intraday_takes_larger_of_both_days():
    filing_dt = datetime(2024, 1, 8, 11, 0, 0, tzinfo=IST)  # Monday, intraday
    daily_bars = {
        date(2024, 1, 5): {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        date(2024, 1, 8): {"open": 100.0, "high": 103.0, "low": 97.0, "close": 99.0},
        date(2024, 1, 9): {"open": 99.0, "high": 118.0, "low": 95.0, "close": 100.0},
    }
    result = compute_print_daily_fallback(filing_dt, daily_bars, today=date(2024, 1, 10))
    assert result["release_mode"] == "intraday"
    assert result["reference_price"] == pytest.approx(100.0)  # prior (Friday) close
    assert result["move_a_pct"] == pytest.approx(3.0)   # filing-day range vs reference
    assert result["move_b_pct"] == pytest.approx(18.0)  # next-day range vs same reference
    assert result["print_move_pct"] == pytest.approx(18.0)  # max(move_a, move_b)
    assert result["status"] == "usable"


def test_daily_fallback_pending_when_reaction_day_not_yet_trading():
    filing_dt = datetime(2024, 1, 5, 16, 0, 0, tzinfo=IST)
    daily_bars = {date(2024, 1, 5): {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}}
    result = compute_print_daily_fallback(filing_dt, daily_bars, today=date(2024, 1, 8))
    assert result["status"] == "pending"
    assert result["print_move_pct"] is None


def test_daily_fallback_excluded_when_reaction_bar_missing():
    filing_dt = datetime(2024, 1, 5, 16, 0, 0, tzinfo=IST)
    daily_bars = {date(2024, 1, 5): {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}}
    # 2024-01-08 (the next trading day) never got a bar - e.g. a gap in the
    # 3-year window - so the reaction can't be measured even though it's
    # not pending.
    result = compute_print_daily_fallback(filing_dt, daily_bars, today=date(2024, 6, 1))
    assert result["status"] == "excluded"
    assert result["print_move_pct"] is None


# --- upsert_print: every calc field must actually reach storage -------------

def test_upsert_print_persists_signed_move_pct(tmp_path, monkeypatch):
    # A real bug (15 Oct 2026): signed_move_pct was added to compute_print's
    # and compute_print_daily_fallback's return dicts, but upsert_print's
    # own `row` dict (built by naming each calc[...] key explicitly, not by
    # copying the whole dict) was never updated to include it - so every
    # print silently stored NULL despite the calculation itself being
    # correct. No existing test caught this because the other tests here
    # only check compute_print_daily_fallback's return value, never that a
    # field actually makes it through upsert_print into the database -
    # this test closes that gap generally, not just for this one field.
    db_path = str(tmp_path / "events_test.db")
    monkeypatch.setattr("jobs.refresh_expected_moves.DB_PATH", db_path)
    conn = get_connection()

    filing = {
        "financial_period": "2026-06-30",
        "filing_dt": datetime(2026, 7, 17, 19, 7, 44, tzinfo=IST),
        "timestamp_source_field": "an_dt",
        "announcement_id": "123",
        "attachment_url": "http://x/result.pdf",
        "cluster": [],
        "source_conflict": False,
    }
    calc = {
        "release_mode": "post_close", "reference_price": 1327.2,
        "reference_bar_ts": None, "reference_offset_minutes": None,
        "reference_method": "daily_close",
        "session_a_date": None, "session_b_date": "2026-07-20",
        "high_a": None, "low_a": None, "high_b": 1345.8, "low_b": 1316.8,
        "move_a_pct": None, "move_b_pct": 1.4, "print_move_pct": 1.4,
        "signed_move_pct": -0.309,
        "status": "usable", "reason_codes": [],
    }
    upsert_print(conn, "RELIANCE", filing, calc, "2026-07-20T00:00:00")
    conn.commit()

    row = conn.execute(
        "SELECT signed_move_pct FROM earnings_move_prints "
        "WHERE symbol='RELIANCE' AND financial_period='2026-06-30'"
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(-0.309)
