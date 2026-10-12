"""Verified NSE trading-session calendar, for the expected-move feature
(expected_move_spec-3c050a43.txt section 4f). pandas_market_calendars is
already a transitive dependency of nselib and already used internally by
nselib/libutil.py (mcal.get_calendar("NSE")) - this reuses the same
calendar rather than adding a new dependency or deriving sessions from
price-data gaps (the approach prices.py/analytics/tiers.py/
data_layer/intraday_store.py deliberately use instead, for the rest of
the app - that convention is unchanged; this feature alone needs an
actual verified calendar because it must tell a holiday from "the market
just has no data for that day yet").

Verified 9 Oct 2026: cal.schedule('2026-10-01', '2026-10-15') correctly
excludes 2 Oct 2026 (Gandhi Jayanti), a real NSE holiday, not just a
weekend gap - see docs/expected_move_discovery.txt section F.
"""

from datetime import date, datetime, time, timedelta, timezone

import pandas_market_calendars as mcal

IST = timezone(timedelta(hours=5, minutes=30))

SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)

_nse = mcal.get_calendar("NSE")


def is_trading_day(d):
    """d: a date. True if NSE's regular cash-equity market was open that
    day, verified against the real exchange calendar (holidays included),
    not a weekday-only guess."""
    sched = _nse.schedule(start_date=d, end_date=d)
    return not sched.empty


def next_trading_day(d):
    """The first trading day strictly after d."""
    sched = _nse.schedule(start_date=d + timedelta(days=1), end_date=d + timedelta(days=14))
    if sched.empty:
        raise ValueError(f"no trading day found within 14 days after {d}")
    return sched.index[0].date()


def session_bounds_ist(d):
    """(open_dt, close_dt) as tz-aware IST datetimes for the regular
    09:15-15:30 session on date d. Does not check d is actually a trading
    day - callers should check is_trading_day first; a verified special-
    hours session is a future extension, not handled here (spec section 4
    says flag it rather than apply normal hours blindly - v1 has not
    observed one in the 5 discovery names and does not guess at its
    bounds)."""
    open_dt = datetime.combine(d, SESSION_OPEN, tzinfo=IST)
    close_dt = datetime.combine(d, SESSION_CLOSE, tzinfo=IST)
    return open_dt, close_dt


def next_trading_day_on_or_after(d):
    """The first trading day >= d (d itself if d is already a trading
    day). Used for the non_session case: 'the first exchange session
    opening after filing', resolved forward from a holiday/weekend."""
    sched = _nse.schedule(start_date=d, end_date=d + timedelta(days=14))
    if sched.empty:
        raise ValueError(f"no trading day found within 14 days on/after {d}")
    return sched.index[0].date()


def latest_trading_day_at_or_before(d):
    """The latest trading day <= d (could be d itself)."""
    sched = _nse.schedule(start_date=d - timedelta(days=14), end_date=d)
    if sched.empty:
        raise ValueError(f"no trading day found within 14 days before {d}")
    return sched.index[-1].date()
