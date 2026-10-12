"""Offline fixture tests for the expected-move calculation engine, per
expected_move_spec-3c050a43.txt section 11. All synthetic - no network,
no database, no stored price data. Synthetic numbers here are explicitly
NOT claimed market observations (see the spec's own synthetic examples,
reused as test cases below).
"""

from datetime import datetime, timedelta, timezone

import pytest

from analytics import expected_move as em

IST = timezone(timedelta(hours=5, minutes=30))


def ist(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=IST)


# --- classify_release_mode ---------------------------------------------------

def test_intraday_inclusive_open():
    assert em.classify_release_mode(ist(2026, 10, 8, 9, 15, 0), True) == "intraday"


def test_intraday_inclusive_1529():
    assert em.classify_release_mode(ist(2026, 10, 8, 15, 29, 59), True) == "intraday"


def test_post_close_exact_1530():
    assert em.classify_release_mode(ist(2026, 10, 8, 15, 30, 0), True) == "post_close"


def test_pre_open():
    assert em.classify_release_mode(ist(2026, 10, 8, 8, 0, 0), True) == "pre_open"


def test_non_session_overrides_time_of_day():
    # A filing timestamped during "normal session hours" on a day that
    # isn't actually a trading session (weekend/holiday) is non_session,
    # not intraday - time-of-day never overrides the calendar.
    assert em.classify_release_mode(ist(2026, 10, 2, 11, 0, 0), False) == "non_session"


# --- expected_hourly_slots ----------------------------------------------------

def test_expected_hourly_slots_seven_for_nse_session():
    open_ts = datetime(2026, 10, 8, 9, 15, tzinfo=timezone.utc)
    close_ts = datetime(2026, 10, 8, 15, 30, tzinfo=timezone.utc)
    slots = em.expected_hourly_slots(open_ts, close_ts)
    assert len(slots) == 7
    assert slots[0] == open_ts
    assert slots[-1] == open_ts + timedelta(hours=6)


# --- select_reference_bar -----------------------------------------------------

def _slots(n, start=datetime(2026, 10, 8, 9, 15, tzinfo=timezone.utc)):
    return [start + timedelta(hours=h) for h in range(n)]


def test_reference_nearest_bar_exact_match():
    slots = _slots(3)
    bars = {s: {"open": 100 + i, "high": 0, "low": 0} for i, s in enumerate(slots)}
    filing = slots[1] + timedelta(minutes=5)
    r = em.select_reference_bar(filing, slots, bars)
    assert r["reference_method"] == "nearest_bar_open"
    assert r["reference_bar_ts"] == slots[1]
    assert r["reference_price"] == 101
    assert r["reason_codes"] == []


def test_reference_tie_picks_earlier_slot():
    slots = _slots(3)
    bars = {s: {"open": 100 + i, "high": 0, "low": 0} for i, s in enumerate(slots)}
    # Exactly halfway between slots[0] and slots[1].
    filing = slots[0] + timedelta(minutes=30)
    r = em.select_reference_bar(filing, slots, bars)
    assert r["reference_bar_ts"] == slots[0]


def test_reference_missing_nearest_falls_back_to_next_bar():
    slots = _slots(3)
    bars = {slots[0]: {"open": 100, "high": 0, "low": 0},
            slots[2]: {"open": 102, "high": 0, "low": 0}}  # slots[1] missing
    filing = slots[1] + timedelta(minutes=5)  # nearest slot is slots[1], absent
    r = em.select_reference_bar(filing, slots, bars)
    assert r["reference_method"] == "reference_next_bar"
    assert r["reference_bar_ts"] == slots[2]
    assert "reference_next_bar" in r["reason_codes"]


def test_reference_no_fallback_available_is_missing_not_next_day():
    slots = _slots(2)
    bars = {slots[0]: {"open": 100, "high": 0, "low": 0}}  # nothing after filing
    filing = slots[1] + timedelta(minutes=5)
    r = em.select_reference_bar(filing, slots, bars)
    assert r["reference_method"] == "unavailable"
    assert r["reference_price"] is None
    assert r["reason_codes"] == ["missing_reference"]


# --- select_daily_close_reference --------------------------------------------

def test_daily_close_uses_filing_day_close_not_prior_day():
    closes = {
        datetime(2026, 10, 7).date(): 100.0,
        datetime(2026, 10, 8).date(): 105.0,
    }
    filing = ist(2026, 10, 8, 16, 0, 0)
    r = em.select_daily_close_reference(filing, closes)
    assert r["reference_price"] == 105.0
    assert r["reference_method"] == "daily_close"


def test_daily_close_missing_is_unavailable_not_walked_back():
    closes = {datetime(2026, 10, 1).date(): 100.0}
    filing = ist(2026, 10, 8, 16, 0, 0)
    # Spec: a missing close for the required session is a missing
    # reference, not permission to use an older convenient close - this
    # helper itself only looks at dates <= filing date, so an old date IS
    # picked up (that's the "use the latest completed close AT OR BEFORE
    # filing" rule) - the unavailable case is when there's truly nothing.
    r = em.select_daily_close_reference(filing, closes)
    assert r["reference_price"] == 100.0  # latest completed close <= filing


def test_daily_close_truly_unavailable():
    r = em.select_daily_close_reference(ist(2026, 10, 8, 16, 0, 0), {})
    assert r["reference_method"] == "unavailable"


# --- select_window_a_bars (boundary contamination) ---------------------------

def test_window_a_filing_bar_retained_with_contamination_flag():
    slots = _slots(3)
    bars = {
        slots[0]: {"high": 110, "low": 90},
        slots[1]: {"high": 115, "low": 95},
        slots[2]: {"high": 120, "low": 100},
    }
    session_close = slots[2] + timedelta(hours=1)
    filing = slots[1] + timedelta(minutes=30)  # inside slots[1]'s hour
    eligible, reasons = em.select_window_a_bars(filing, session_close, bars)
    assert len(eligible) == 2  # slots[1] (contaminated) and slots[2]
    assert "boundary_hour_contamination" in reasons


def test_window_a_no_contamination_when_filing_at_bar_start():
    slots = _slots(3)
    bars = {
        slots[0]: {"high": 110, "low": 90},
        slots[1]: {"high": 115, "low": 95},
        slots[2]: {"high": 120, "low": 100},
    }
    session_close = slots[2] + timedelta(hours=1)
    filing = slots[1]
    eligible, reasons = em.select_window_a_bars(filing, session_close, bars)
    assert reasons == []
    assert len(eligible) == 2


# --- move_pct / window_extrema / print_move / aggregate_cohort --------------

def test_move_pct_farther_high_low_no_sign_split():
    # Spec's own synthetic example: R=100, A: H=104 L=99 -> move=4%.
    assert em.move_pct(100, 104, 99) == pytest.approx(4.0)


def test_move_pct_absolute_down_move():
    # A pure down move should still produce a positive magnitude.
    assert em.move_pct(100, 100, 92) == pytest.approx(8.0)


def test_move_pct_invalid_inputs_return_none():
    assert em.move_pct(None, 110, 90) is None
    assert em.move_pct(0, 110, 90) is None
    assert em.move_pct(100, 90, 110) is None  # high < low


def test_window_extrema_empty_is_none_not_zero():
    assert em.window_extrema([]) == (None, None)


def test_print_move_intraday_is_max_of_a_and_b():
    # Spec's own synthetic example: R=100; A=4%; B H=102 L=93 -> B=7%;
    # print = max(4,7) = 7.
    move_a = em.move_pct(100, 104, 99)
    move_b = em.move_pct(100, 102, 93)
    assert em.print_move("intraday", move_a, move_b) == pytest.approx(7.0)


def test_print_move_intraday_partial_when_a_missing():
    assert em.print_move("intraday", None, 7.0) is None


def test_print_move_post_close_is_b_alone():
    # Spec's own synthetic example: post-close R=200; B H=210 L=192 ->
    # print=5%; A=NULL and irrelevant to the result.
    move_b = em.move_pct(200, 210, 192)
    assert em.print_move("post_close", None, move_b) == pytest.approx(5.0)


def test_aggregate_cohort_mean_and_max():
    # Spec's own synthetic example: [7,5,4,6] -> expected=5.5, worst=7.
    expected, worst = em.aggregate_cohort([7.0, 5.0, 4.0, 6.0])
    assert expected == pytest.approx(5.5)
    assert worst == pytest.approx(7.0)


def test_aggregate_cohort_zero_usable_is_none_not_zero():
    expected, worst = em.aggregate_cohort([])
    assert expected is None
    assert worst is None


def test_aggregate_cohort_single_print_mean_equals_max():
    expected, worst = em.aggregate_cohort([6.5])
    assert expected == worst == pytest.approx(6.5)


# --- reason code vocabulary --------------------------------------------------

def test_all_emitted_reason_codes_are_in_the_vocabulary():
    slots = _slots(2)
    missing = em.select_reference_bar(slots[1] + timedelta(minutes=5), slots, {})
    for code in missing["reason_codes"]:
        assert code in em.REASON_CODES
