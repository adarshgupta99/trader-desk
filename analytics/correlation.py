"""Pairwise correlation of F&O log returns. Spec section 6.

Correlates LOG RETURNS, never prices. Prices all trend together and
every pair then looks correlated; returns show real co-movement.

The expensive part is not the maths. Measured 6 Oct 2026 on the full
306,280 bars: reading and shaping them cost 1.45s, the 213x213
correlation 0.12s. So this module reads only the window the lookback
asks for, and memoises the finished result per process.
"""

import time

import numpy as np
import pandas as pd

from analytics import tiers
from data_layer import intraday_store

#: The stale-bar rule, in one place, quoted in the UI footer.
#: Settled 6 Oct 2026 by measurement, against this spec's own original
#: rationale. Volume is NOT a staleness test: of 306,280 bars across all
#: 213 F&O stocks, 217 had Volume == 0, all of them the 15:15 bar,
#: clustered on three dates rather than on particular symbols, and the
#: price moved inside every one. Treating them as stale would discard
#: real closing moves. The stale rate is also marginally HIGHER among the
#: 20 most liquid names (0.1075%) than the 20 thinnest (0.0941%), so
#: staleness does not track illiquidity at all.
STALE_POLICY = (
    "A bar is stale only when its open, high, low and close all equal the "
    "previous bar's close. Stale bars become NaN and are not "
    "forward-filled. Volume is never used as a staleness test."
)

#: Flag a single-bar move this large for a manual look — almost certainly
#: a corporate action that auto_adjust missed, not a real return.
EXTREME_RETURN = 0.20

_memo = {}
_memo_order = []
_MEMO_MAX = 8

#: Incremented on every real computation, so a test can prove the memo hit.
compute_calls = 0


class CorrelationResult:
    """A finished correlation matrix and everything the UI must disclose."""

    def __init__(self, corr, n_obs, tier, lookback_days, n_sessions, asof,
                 min_periods, n_bars, extremes, elapsed):
        self.corr = corr
        self.n_obs = n_obs
        self.tier = tier
        self.lookback_days = lookback_days
        self.n_sessions = n_sessions
        self.asof = asof
        self.min_periods = min_periods
        self.n_bars = n_bars
        self.extremes = extremes
        self.elapsed = elapsed

    @property
    def symbols(self):
        return list(self.corr.columns)

    @property
    def bar_label(self):
        return "15-minute" if self.tier == tiers.TIER_15M else "daily"

    def __repr__(self):
        return (f"<CorrelationResult {self.tier} {self.lookback_days}d "
                f"{len(self.symbols)} symbols asof {self.asof}>")


class NoDataError(Exception):
    """No bars stored for the tier a lookback needs."""


def compute(lookback_days, symbols=None, conn=None):
    """Correlate every pair over the last `lookback_days` trading sessions.

    Sessions, not calendar days: a lookback of 20 is the last 20 stored
    sessions, so holidays never silently shrink the window.

    Raises NoDataError when the tier has no bars — the caller shows the
    "run the fetch job" message rather than a traceback.
    """
    global compute_calls

    tier = tiers.pick_tier(lookback_days)
    lookback_days = int(lookback_days)

    own_conn = conn is None
    if own_conn:
        if not intraday_store.exists():
            raise NoDataError(
                f"No bar store at {intraday_store.db_path()}"
            )
        conn = intraday_store.get_connection()
    try:
        window = intraday_store.window_start(conn, tier, lookback_days)
        if window is None:
            raise NoDataError(f"No {tier} bars stored yet")
        start_ts, n_sessions, asof = window

        key = (tier, lookback_days, asof,
               tuple(symbols) if symbols else None)
        if key in _memo:
            return _memo[key]

        t0 = time.perf_counter()
        raw = intraday_store.read_bars(conn, tier, start_ts, symbols)
        if raw.empty:
            raise NoDataError(f"No {tier} bars in the window from {start_ts}")

        wide = close_frame(raw)
        ret = log_returns(wide, tier)
        extremes = _extreme_returns(ret)

        min_periods = tiers.min_periods(tier, n_sessions)
        corr = ret.corr(min_periods=min_periods)
        n_obs = _pair_counts(ret)

        result = CorrelationResult(
            corr=corr, n_obs=n_obs, tier=tier, lookback_days=lookback_days,
            n_sessions=n_sessions, asof=asof, min_periods=min_periods,
            n_bars=len(raw), extremes=extremes,
            elapsed=time.perf_counter() - t0,
        )
        compute_calls += 1
        _remember(key, result)
        return result
    finally:
        if own_conn:
            conn.close()


def close_frame(raw):
    """Long bars -> wide close prices, with the stale policy applied.

    `raw` is what intraday_store.read_bars returns: symbol, ts, open,
    high, low, close.
    """
    prev = raw.groupby("symbol", observed=True)["close"].shift(1)
    stale = (
        prev.notna()
        & (raw["open"] == prev)
        & (raw["high"] == prev)
        & (raw["low"] == prev)
        & (raw["close"] == prev)
    )
    close = raw["close"].where(~stale)
    frame = pd.DataFrame({"ts": raw["ts"], "symbol": raw["symbol"],
                          "close": close})
    return frame.pivot(index="ts", columns="symbol", values="close")


def log_returns(wide, tier):
    """Log returns, within a session only on the intraday tier.

    The overnight gap is not a 15-minute move. Including it would let one
    observation a day — the largest one — dominate the correlation, so
    the first bar of each session has no return.
    """
    logp = np.log(wide.where(wide > 0))
    if tier == tiers.TIER_15M:
        return logp.groupby(logp.index.normalize()).diff()
    return logp.diff()


def pair(result, a, b):
    """corr and n_obs for one pair, either direction."""
    if a not in result.corr.columns or b not in result.corr.columns:
        return None, 0
    return result.corr.at[a, b], int(result.n_obs.at[a, b])


def ranked_against(result, symbol, n=10):
    """(most_correlated, least_correlated) as frames of symbol/corr/n_obs.

    Least correlated means most negative first, not nearest zero — the
    useful end for anyone looking for a hedge.
    """
    if symbol not in result.corr.columns:
        return pd.DataFrame(), pd.DataFrame()

    series = result.corr[symbol].drop(index=symbol, errors="ignore").dropna()
    counts = result.n_obs[symbol]
    table = pd.DataFrame({
        "symbol": series.index,
        "corr": series.to_numpy(),
        "n_obs": [int(counts.at[s]) for s in series.index],
    })
    ordered = table.sort_values("corr", ascending=False)
    return (ordered.head(n).reset_index(drop=True),
            ordered.tail(n).iloc[::-1].reset_index(drop=True))


def _pair_counts(ret):
    """Observations behind every pair: how many timestamps both had."""
    present = ret.notna().to_numpy(dtype=np.int32)
    counts = present.T @ present
    return pd.DataFrame(counts, index=ret.columns, columns=ret.columns)


def _extreme_returns(ret, limit=EXTREME_RETURN):
    """Single-bar moves beyond the limit, as (symbol, ts, value) tuples.

    Corporate actions distort returns. auto_adjust=True handles most of
    them; this catches what it misses instead of trusting it silently.
    """
    hits = ret.abs() > limit
    if not hits.to_numpy().any():
        return []
    stacked = ret.where(hits).stack()
    return [(symbol, ts, float(value))
            for (ts, symbol), value in stacked.items()]


def _remember(key, result):
    _memo[key] = result
    _memo_order.append(key)
    while len(_memo_order) > _MEMO_MAX:
        _memo.pop(_memo_order.pop(0), None)


def clear_memo():
    _memo.clear()
    _memo_order.clear()
