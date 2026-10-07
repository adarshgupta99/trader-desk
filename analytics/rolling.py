"""Historical rolling correlation: focus stock vs. another stock, NIFTY
50, or a sector basket, plus frozen top-5 partner panels. Spec section 7.

Always the daily tier. Section 7's history runs out to 2 years plus
warm-up - far past the 15m tier's 60-day Yahoo ceiling (analytics.tiers),
so there is no tier choice to make here the way analytics.correlation has
to make one.
"""

import numpy as np
import pandas as pd

import fno
import periods
from analytics import correlation as corr
from analytics import tiers
from data_layer import intraday_store

NIFTY_TICKER = "^NSEI"
NIFTY_LABEL = "NIFTY 50"

MIN_WINDOW = 3
DEFAULT_WINDOW = 100

#: History span choices (spec section 7), distinct from the rolling
#: window - this is how far back the line is drawn, not how it is
#: averaged.
HISTORY_SPANS = {"6mo": 182, "1yr": 365, "2yr": 730}
DEFAULT_SPAN = "2yr"

#: Warn (do not mask) when fewer than this many valid paired observations
#: sit under a window point, even if the window itself is >= 50.
MIN_VALID_WARN = 50

#: A sector-basket day needs at least this share of its mapped, eligible
#: constituents present (minimum two) or it is NaN for that day.
BASKET_MIN_COVERAGE = 0.8
BASKET_MIN_CONSTITUENTS = 2


def daily_tier_result(min_days=tiers.MAX_15M_LOOKBACK_DAYS + 1):
    """A CorrelationResult pinned to the daily tier, for the top-5 partner
    snapshot - independent of whatever tier the page's own shared
    lookback happens to be using right now.
    """
    return corr.compute(max(int(min_days), tiers.MAX_15M_LOOKBACK_DAYS + 1))


def stock_log_returns(symbols, start_ts):
    """Daily log returns for a set of F&O symbols from the stored daily
    bars, reusing the same close_frame/log_returns the sector matrix and
    pairwise correlation use - no second staleness policy to keep in sync.
    """
    conn = intraday_store.get_connection()
    try:
        raw = intraday_store.read_bars(conn, tiers.TIER_DAILY, start_ts, symbols)
    finally:
        conn.close()
    if raw.empty:
        return pd.DataFrame()
    wide = corr.close_frame(raw)
    return corr.log_returns(wide, tiers.TIER_DAILY)


def nifty_log_returns(start, end):
    """NIFTY 50 (^NSEI) daily log returns.

    Verified 6 Oct 2026 against live data: Close == Adj Close for this
    ticker - a plain price index with no dividend adjustment to
    reconcile, so a straight log-diff of Close is correct.
    """
    df = periods.range_history(NIFTY_TICKER, start, end)
    if df.empty or "Close" not in df:
        return pd.Series(dtype=float)
    close = df["Close"].dropna()
    # yfinance's daily index is a session DATE stamped at UTC midnight,
    # not a real UTC instant - converting tz would shift it to 05:30 IST
    # and break alignment against the stored daily bars' midnight-naive
    # session dates. Just drop the (nominal) tz, do not convert it.
    index = pd.DatetimeIndex(close.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    close = close.set_axis(index)
    return np.log(close).diff()


def sector_basket_returns(group, exclude, start_ts):
    """Equal-weight mean log return of a display group's constituents,
    excluding `exclude` (stock A itself, so it never correlates with a
    basket that partly contains it).

    NaN for any session where fewer than BASKET_MIN_COVERAGE of the
    mapped constituents (minimum BASKET_MIN_CONSTITUENTS) have a return.
    Returns (series, constituents) - constituents is the frozen,
    current-membership list the UI must disclose alongside the line.
    """
    constituents = sorted(
        s for s in fno.symbols_in_display_group(group) if s != exclude
    )
    if not constituents:
        return pd.Series(dtype=float), constituents

    ret = stock_log_returns(constituents, start_ts)
    if ret.empty:
        return pd.Series(dtype=float), constituents

    cols = [c for c in constituents if c in ret.columns]
    if not cols:
        return pd.Series(dtype=float), constituents

    present = ret[cols].notna()
    n_present = present.sum(axis=1)
    min_needed = max(BASKET_MIN_CONSTITUENTS,
                      int(np.ceil(BASKET_MIN_COVERAGE * len(cols))))
    enough = n_present >= min_needed

    basket = ret[cols].mean(axis=1, skipna=True).where(enough)
    return basket, constituents


def rolling_correlation(a, b, window):
    """Session-indexed rolling correlation of two return series, masked by
    an 80%-coverage threshold rather than pre-dropping NaNs - so a gap in
    either series stays a gap in the index, not a silently shrunk window.

    Returns (rho, n_valid): rho is the rolling correlation, n_valid is the
    count of paired (non-NaN, non-NaN) observations actually sitting
    under each window - for the "warn when N < 50" caption, not for
    masking (that is what min_pairs already does).
    """
    aligned = pd.concat([a.rename("a"), b.rename("b")], axis=1)
    valid = aligned["a"].notna() & aligned["b"].notna()
    n_valid = valid.astype(int).rolling(window, min_periods=1).sum()

    min_pairs = max(2, int(np.ceil(0.8 * window)))
    rho = aligned["a"].rolling(window, min_periods=min_pairs).corr(aligned["b"])
    return rho, n_valid


def top5_partners(symbol, n=5, min_days=tiers.MAX_15M_LOOKBACK_DAYS + 1):
    """The five highest SIGNED current valid partners of `symbol`, from a
    dedicated daily-tier snapshot - frozen identities meant to be plotted
    through history, not re-picked at every date (spec section 7).
    """
    result = daily_tier_result(min_days)
    most, _ = corr.ranked_against(result, symbol, n=n)
    return most
