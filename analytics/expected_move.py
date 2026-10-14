"""Pure calculation engine for the expected-move feature (spec
expected_move_spec-3c050a43.txt, sections 4-7). Deterministic, offline,
no provider network calls, no database access - every function here takes
plain values/dicts and returns plain values/dicts so it can be fixture-
tested (tests/test_expected_move.py) and reused unchanged by the daily job.

Session bounds, hourly-bar proxy choice, and the move/aggregation formulas
are all spec decisions, not free design choices - see the section comment
above each group of functions for which spec rule it implements.
"""

from datetime import timedelta

# Section 4: NSE regular cash-equity session, IST. 15:30:00 exact is
# post-close; 09:15:00 exact is the first intraday instant.
SESSION_OPEN = (9, 15)
SESSION_CLOSE = (15, 30)

#: Spec section 7's reason-code vocabulary, checked against on write so a
#: typo'd code can't silently slip into storage.
REASON_CODES = frozenset({
    "missing_filing_time", "ambiguous_period", "archive_coverage_gap",
    "missing_reference", "reference_next_bar", "boundary_hour_contamination",
    "price_depth_limit", "missing_reaction_bars", "incomplete_session",
    "no_trade_or_halt", "pending_window", "corporate_action_basis_unresolved",
    "source_fetch_failed", "source_conflict",
})


# --- Section 4: release-mode classification ---------------------------------

def classify_release_mode(filing_ts_ist, is_trading_day):
    """filing_ts_ist: tz-aware datetime in IST. is_trading_day: whether
    filing_ts_ist.date() is a verified NSE trading session (from the real
    exchange calendar - see docs/expected_move_discovery.txt section F -
    never a weekday-only guess).

    Returns one of 'intraday' | 'post_close' | 'pre_open' | 'non_session'.
    A non-trading day is 'non_session' regardless of the clock time on it.
    """
    if not is_trading_day:
        return "non_session"
    t = filing_ts_ist.time()
    open_t, close_t = _time(*SESSION_OPEN), _time(*SESSION_CLOSE)
    if t < open_t:
        return "pre_open"
    if t < close_t:
        return "intraday"
    return "post_close"


def _time(hour, minute):
    from datetime import time
    return time(hour, minute)


# --- Section 5: expected hourly-bar grid for one session --------------------

def expected_hourly_slots(session_open_ts, session_close_ts, bar_minutes=60):
    """Expected bar START timestamps for one session, spaced bar_minutes
    apart from session_open_ts, stopping once a start would be >=
    session_close_ts. For NSE's 09:15-15:30 session with 60-minute bars
    this yields 7 slots (09:15..15:15); the 15:15 bar's END is 15:30
    (clipped to close), the shortened final bar the spec expects - this is
    the deterministic grid definition, independent of whatever a given
    data pull actually returned for a session (see the daily job for how
    gaps against this grid get flagged, not assumed away).
    """
    slots = []
    t = session_open_ts
    while t < session_close_ts:
        slots.append(t)
        t += timedelta(minutes=bar_minutes)
    return slots


# --- Section 5: reference bar selection --------------------------------------

def select_reference_bar(filing_ts, expected_slots, bars_by_start):
    """Chooses the hourly-bar proxy R for a filing instant, per spec
    section 5.

    expected_slots: the session's full expected bar-start grid (see
    expected_hourly_slots), used to find the nearest-start slot - including
    one yfinance may not have actually returned.
    bars_by_start: {start_ts: {"open": .., "high": .., "low": ..}} for
    whatever bars ARE actually available in this session.

    Returns a dict: reference_price, reference_bar_ts,
    reference_offset_minutes, reference_method
    ('nearest_bar_open' | 'reference_next_bar' | 'unavailable'),
    reason_codes (list).

    Tie-break on equal distance: the earlier slot wins.
    """
    if not expected_slots:
        return _unavailable_reference()

    nearest_slot = min(
        expected_slots,
        key=lambda s: (abs((s - filing_ts).total_seconds()), s),
    )
    bar = bars_by_start.get(nearest_slot)
    if bar is not None:
        offset = round((nearest_slot - filing_ts).total_seconds() / 60)
        return {
            "reference_price": bar["open"],
            "reference_bar_ts": nearest_slot,
            "reference_offset_minutes": offset,
            "reference_method": "nearest_bar_open",
            "reason_codes": [],
        }

    # Expected nearest slot has no data - fall back to the next valid bar
    # starting strictly after filing, in the SAME session only.
    candidates = sorted(s for s in bars_by_start if s > filing_ts)
    if candidates:
        nxt = candidates[0]
        bar = bars_by_start[nxt]
        offset = round((nxt - filing_ts).total_seconds() / 60)
        return {
            "reference_price": bar["open"],
            "reference_bar_ts": nxt,
            "reference_offset_minutes": offset,
            "reference_method": "reference_next_bar",
            "reason_codes": ["reference_next_bar"],
        }

    return _unavailable_reference()


def _unavailable_reference():
    return {
        "reference_price": None,
        "reference_bar_ts": None,
        "reference_offset_minutes": None,
        "reference_method": "unavailable",
        "reason_codes": ["missing_reference"],
    }


# --- Section 4/7: post-close / pre-open / non-session daily-close reference -

def select_daily_close_reference(filing_ts, closes_by_date):
    """R for post_close/pre_open/non_session modes: the latest COMPLETED
    regular-session close at or before filing. closes_by_date is
    {date: close_price} for verified completed sessions only (never a
    half-formed or still-open session's running price).

    Returns the same shape as select_reference_bar's result, with
    reference_method='daily_close' (or 'unavailable').
    """
    eligible_dates = [d for d in closes_by_date if d <= filing_ts.date()]
    if not eligible_dates:
        return {
            "reference_price": None, "reference_bar_ts": None,
            "reference_offset_minutes": None,
            "reference_method": "unavailable",
            "reason_codes": ["missing_reference"],
        }
    latest = max(eligible_dates)
    return {
        "reference_price": closes_by_date[latest],
        "reference_bar_ts": None,
        "reference_offset_minutes": None,
        "reference_method": "daily_close",
        "reason_codes": [],
    }


# --- Section 5: window bar selection -----------------------------------------

def select_window_a_bars(filing_ts, session_close_ts, bars_by_start):
    """Bars overlapping [filing_ts, session_close_ts) in the filing's own
    session - intraday only. A bar of length bar_minutes starting at s is
    treated as covering [s, s + its own span); since callers only pass
    same-session bars this reduces to "bar start < session_close AND bar
    is the filing-containing bar OR starts at/after filing".

    Returns (bars_list, reason_codes) - reason_codes gets
    'boundary_hour_contamination' if any retained bar starts before
    filing_ts (its pre-filing portion can't be honestly excluded from
    hourly OHLC, per spec section 5).
    """
    eligible = []
    contaminated = False
    starts = sorted(bars_by_start)
    for i, s in enumerate(starts):
        if s >= session_close_ts:
            continue
        next_start = starts[i + 1] if i + 1 < len(starts) else session_close_ts
        bar_covers_post_filing = next_start > filing_ts
        if s >= filing_ts or bar_covers_post_filing:
            if s < filing_ts:
                contaminated = True
            eligible.append(bars_by_start[s])
    reason_codes = ["boundary_hour_contamination"] if contaminated else []
    return eligible, reason_codes


def select_window_b_bars(bars_by_start):
    """All valid hourly bars of the complete next session - no filtering
    by time of day, the whole next session contributes."""
    return [bars_by_start[s] for s in sorted(bars_by_start)]


# --- Section 6: the move/print/aggregation formulas --------------------------

def move_pct(reference, high, low):
    """move(W) = 100 * max(abs(H/R - 1), abs(L/R - 1)).

    Returns None (not 0.0) if reference/high/low aren't all finite,
    positive, and high >= low - callers must treat None as 'missing', and
    flag the specific reason (missing_reaction_bars, invalid OHLC, etc.)
    rather than silently defaulting to a numeric 0.
    """
    if reference is None or high is None or low is None:
        return None
    if reference <= 0 or low <= 0 or high < low:
        return None
    return 100 * max(abs(high / reference - 1), abs(low / reference - 1))


def signed_move_pct(before_close, after_close):
    """Directional counterpart to move_pct, added 15 Oct 2026 for the
    Sentiment tab (Adarsh's request: pair each quarter's sentiment score
    with the SIGNED market reaction, not the magnitude already used for
    the expected-move/worst-case cohort stats above).

    100 * (after_close/before_close - 1) - plain close-to-close, not
    high/low based. move_pct's high/low extrema are deliberately
    sign-less (a session can spike up then close down, so "which extreme
    moved further" has no honest sign); a signed figure needs an actual
    settled price on each side instead.

    Returns None (not 0.0) unless both prices are finite and positive -
    same missing-data discipline as move_pct.
    """
    if before_close is None or after_close is None:
        return None
    if before_close <= 0 or after_close <= 0:
        return None
    return 100 * (after_close / before_close - 1)


def window_extrema(bars):
    """bars: list of {"high": .., "low": ..} dicts already filtered to the
    eligible set for one window. Returns (high, low), or (None, None) for
    an empty list - never 0, which would look like a real flat window."""
    if not bars:
        return None, None
    return max(b["high"] for b in bars), min(b["low"] for b in bars)


def print_move(release_mode, move_a, move_b):
    """print_move = max(move_a, move_b) for intraday; move_b alone for
    post_close/pre_open/non_session (move_a is N/A, not a 0 input to max).

    Returns None if the required window(s) for this mode aren't both
    present - an intraday print needs BOTH move_a and move_b to produce a
    headline-eligible value; missing either one is 'partial', not a
    silent max() over whatever exists.
    """
    if release_mode == "intraday":
        if move_a is None or move_b is None:
            return None
        return max(move_a, move_b)
    return move_b


def aggregate_cohort(usable_print_moves):
    """Spec section 6: for m usable prints, 1<=m<=8:
      expected_move_pct = mean(print_move_i)
      worst_case_pct = max(print_move_i)
    Returns (None, None) for m=0 - the UI must say 'Unavailable', never
    0.00%. Full precision in; callers round only for display.
    """
    if not usable_print_moves:
        return None, None
    return (
        sum(usable_print_moves) / len(usable_print_moves),
        max(usable_print_moves),
    )
