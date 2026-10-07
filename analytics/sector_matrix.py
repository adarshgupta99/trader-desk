"""Sector-level correlation overview. Spec sections 5 / 5A / 5B.

The mean, over all valid stock pairs, of a sector's or sector-pair's
correlations - a descriptive overview of the 213x213 pairwise matrix, not
a correlation matrix of tradable sector indices. Built from an already
computed analytics.correlation.CorrelationResult; this module does no
fetching and no pairwise correlation math of its own.

Equal-weighted, not market-cap or Fisher-z averaged (spec decision). A
stock that belongs to two sectors contributes to both, which is intended -
sector membership is inherently multi-valued here - EXCEPT that a stock
paired with itself (because it sits in both the row and column sector)
is excluded from a cell: it is a self-correlation of 1 and would inflate
the mean, not a real cross-sector observation (spec section 5, Addition 1).
"""

import numpy as np
import pandas as pd

import fno

#: Below this share of a cell's possible pairs being valid, flag low
#: coverage rather than let a handful of survivors stand in for the cell.
LOW_COVERAGE_THRESHOLD = 0.8


class SectorMatrixResult:
    """The 15x15 mean-correlation matrix and everything the UI must disclose."""

    def __init__(self, mean_corr, valid_pairs, possible_pairs, median_n,
                 group_symbols, source):
        self.mean_corr = mean_corr
        self.valid_pairs = valid_pairs
        self.possible_pairs = possible_pairs
        self.median_n = median_n
        self.group_symbols = group_symbols
        self.source = source   # the CorrelationResult this was built from

    @property
    def groups(self):
        return list(self.mean_corr.columns)

    def coverage(self, s, t):
        """Fraction of possible pairs that were valid, or None if none exist."""
        possible = self.possible_pairs.at[s, t]
        return (self.valid_pairs.at[s, t] / possible) if possible else None

    def low_coverage(self, s, t):
        cov = self.coverage(s, t)
        return cov is not None and cov < LOW_COVERAGE_THRESHOLD


def compute(result):
    """Build the sector-level overview from a symbol-level CorrelationResult."""
    groups = fno.DISPLAY_GROUPS
    present = set(result.corr.columns)
    group_symbols = {
        g: [s for s in fno.symbols_in_display_group(g) if s in present]
        for g in groups
    }

    mean_corr = pd.DataFrame(np.nan, index=groups, columns=groups)
    valid_pairs = pd.DataFrame(0, index=groups, columns=groups, dtype=int)
    possible_pairs = pd.DataFrame(0, index=groups, columns=groups, dtype=int)
    median_n = pd.DataFrame(np.nan, index=groups, columns=groups)

    for i, s in enumerate(groups):
        stocks_s = group_symbols[s]
        for t in groups[i:]:
            stocks_t = group_symbols[t]
            if s == t:
                vals, ns, possible = _diagonal_cell(result, stocks_s)
            else:
                vals, ns, possible = _off_diagonal_cell(result, stocks_s, stocks_t)

            possible_pairs.at[s, t] = possible_pairs.at[t, s] = possible
            valid_pairs.at[s, t] = valid_pairs.at[t, s] = len(vals)
            if len(vals):
                mean_corr.at[s, t] = mean_corr.at[t, s] = float(np.mean(vals))
                median_n.at[s, t] = median_n.at[t, s] = float(np.median(ns))

    return SectorMatrixResult(
        mean_corr=mean_corr, valid_pairs=valid_pairs,
        possible_pairs=possible_pairs, median_n=median_n,
        group_symbols=group_symbols, source=result,
    )


def _diagonal_cell(result, stocks_s):
    """Within-sector: unique unordered pairs of distinct stocks only.

    A one-stock sector (e.g. Textiles) has no within-sector pair at all -
    that is "no data", not a forced 1.0.
    """
    n = len(stocks_s)
    if n < 2:
        return [], [], 0
    sub = result.corr.loc[stocks_s, stocks_s].to_numpy()
    n_obs = result.n_obs.loc[stocks_s, stocks_s].to_numpy()
    iu = np.triu_indices(n, k=1)
    cell_vals, cell_n = sub[iu], n_obs[iu]
    valid = ~np.isnan(cell_vals)
    return cell_vals[valid].tolist(), cell_n[valid].tolist(), len(cell_vals)


def _off_diagonal_cell(result, stocks_s, stocks_t):
    """Cross-sector: every (stock in S, stock in T) pair once, same-symbol
    pairs excluded (a stock in both S and T, spec Addition 1).
    """
    if not stocks_s or not stocks_t:
        return [], [], 0
    sub = result.corr.loc[stocks_s, stocks_t].to_numpy()
    n_obs = result.n_obs.loc[stocks_s, stocks_t].to_numpy()
    same = np.equal.outer(np.array(stocks_s), np.array(stocks_t))
    possible = int((~same).sum())
    valid = ~np.isnan(sub) & ~same
    return sub[valid].tolist(), n_obs[valid].tolist(), possible
