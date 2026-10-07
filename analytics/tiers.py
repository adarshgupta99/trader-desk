"""Which bar size answers a given lookback. Spec section 3.

One function decides, everywhere. Two tiers, measured 6 Oct 2026:

  1 to 60 days   15-minute bars   Yahoo's 15m limit is exactly 60 days
  over 60 days   daily bars

15 minutes is the floor. Yahoo serves 1-minute bars and this feature
deliberately does not use them: correlations from very fine bars are
biased toward zero by asynchronous trading (the Epps effect), worst for
the illiquid end of the F&O list, and no stale-bar filter fixes it.

Never compare returns from two bar sizes in one number. A 30-day
correlation is from 15m bars only.
"""

TIER_15M = "15m"
TIER_DAILY = "1d"

#: Yahoo returns an EMPTY frame for 15m/61d with an explicit "within the
#: last 60 days" error. Measured 6 Oct 2026 — a hard ceiling, not a soft one.
MAX_15M_LOOKBACK_DAYS = 60

#: Largest lookback the UI offers (spec section 8).
MAX_LOOKBACK_DAYS = 500

#: NSE's continuous session is 09:15-15:30, so 15m bars are stamped 09:15
#: through 15:15: 25 slots. The first bar of a session has no usable
#: return (the overnight gap is dropped), leaving 24.
BARS_PER_SESSION = {TIER_15M: 25, TIER_DAILY: 1}
RETURNS_PER_SESSION = {TIER_15M: 24, TIER_DAILY: 1}

#: Below this share of the expected observations, return NaN rather than a
#: number from a handful of points (spec section 6 item 5).
MIN_PERIODS_FRACTION = 0.5


def pick_tier(lookback_days):
    """Return "15m" or "1d" for a lookback in days.

    Boundaries: 1 -> 15m, 60 -> 15m, 61 -> 1d. Raises on 0 or negative.
    Never returns "1m"; that tier was dropped on 6 Oct 2026.
    """
    days = _as_days(lookback_days)
    return TIER_15M if days <= MAX_15M_LOOKBACK_DAYS else TIER_DAILY


def expected_returns(tier, n_sessions):
    """How many return observations a tier should yield over n_sessions.

    Taken from the sessions actually present in the window rather than
    from a calendar guess, so holidays and half days do not inflate it.
    """
    return max(int(n_sessions) * RETURNS_PER_SESSION[tier], 0)


def min_periods(tier, n_sessions):
    """min_periods for DataFrame.corr over this window. At least 2."""
    return max(int(MIN_PERIODS_FRACTION * expected_returns(tier, n_sessions)), 2)


def describe(lookback_days, n_sessions=None):
    """One line for the UI: "20 days -> 15-minute bars, ~480 obs"."""
    days = _as_days(lookback_days)
    tier = pick_tier(days)
    label = "15-minute bars" if tier == TIER_15M else "daily bars"
    if n_sessions is None:
        return f"{days} days -> {label}"
    obs = expected_returns(tier, n_sessions)
    return f"{days} days -> {label}, ~{obs:,} obs over {n_sessions} sessions"


def _as_days(lookback_days):
    try:
        days = int(lookback_days)
    except (TypeError, ValueError):
        raise ValueError(
            f"lookback must be a whole number of days, got {lookback_days!r}"
        ) from None
    if days < 1:
        raise ValueError(f"lookback must be at least 1 day, got {days}")
    return days
