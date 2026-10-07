"""Cluster order and preview selection for the stock-level matrix. Spec
section 6: cluster the FULL eligible universe first, then filter/preview
down to what is actually drawn - so a 60-stock preview is always a subset
of one stable ordering, never a fresh re-clustering of whatever happens
to be visible.

Distance is derived from an already-computed CorrelationResult, not raw
returns: d(i,j) = sqrt(max(0, 2*(1-r(i,j)))). Positive correlation pulls
two stocks together; negative correlation pushes them apart - unlike
abs(r), which would erase that distinction. Average linkage, because nothing
here guarantees the true-Euclidean geometry Ward's method assumes.
"""

import numpy as np
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform

import fno


class ClusterResult:
    """Leaf order for a set of symbols, plus whatever had to be dropped."""

    def __init__(self, order, excluded):
        self.order = order
        self.excluded = excluded  # [(symbol, reason), ...]


def compute_order(result, symbols):
    """Cluster-ordered symbols from `symbols`, restricted to what the
    correlation result actually covers.

    Missing-data repair: a pairwise distance can be undefined (no valid
    correlation between two stocks at this lookback). Rather than
    substitute a fake distance for a missing pair, repeatedly drop the
    symbol with the most undefined distances - ties broken alphabetically,
    so the result is deterministic - until what remains is fully finite.
    Dropped symbols stay searchable elsewhere in the UI; they are just not
    placed in this clustering.
    """
    active = sorted(set(symbols) & set(result.corr.columns))
    excluded = []

    while len(active) > 2:
        dist = _distance_matrix(result.corr, active)
        n_missing = np.isnan(dist).sum(axis=1)
        worst = int(n_missing.max())
        if worst == 0:
            break
        drop = sorted(s for s, m in zip(active, n_missing) if m == worst)[0]
        active.remove(drop)
        excluded.append((drop, "insufficient overlapping data for clustering"))

    if len(active) < 2:
        return ClusterResult(order=active, excluded=excluded)

    dist = _distance_matrix(result.corr, active)
    z = linkage(squareform(dist, checks=False), method="average")
    order = [active[i] for i in leaves_list(z)]
    return ClusterResult(order=order, excluded=excluded)


def _distance_matrix(corr, symbols):
    sub = corr.loc[symbols, symbols].to_numpy()
    dist = np.sqrt(np.clip(2.0 * (1.0 - sub), 0.0, None))
    np.fill_diagonal(dist, 0.0)
    return dist


def sector_balanced_preview(order, cap=60, focus=None):
    """Up to `cap` symbols from the clustered `order`, chosen round-robin
    across fno.DISPLAY_GROUPS (alphabetical within each group) so no
    sector is crowded out by a bigger one, then re-sorted back into the
    full-universe cluster order for display.

    `focus`, if present in `order`, is always kept.
    """
    pool = set(order)
    groups = {g: [s for s in fno.symbols_in_display_group(g) if s in pool]
              for g in fno.DISPLAY_GROUPS}

    selected = set()
    if focus in pool:
        selected.add(focus)

    cursors = {g: 0 for g in fno.DISPLAY_GROUPS}
    while len(selected) < min(cap, len(pool)):
        progressed = False
        for g in fno.DISPLAY_GROUPS:
            if len(selected) >= cap:
                break
            members = groups[g]
            idx = cursors[g]
            while idx < len(members) and members[idx] in selected:
                idx += 1
            cursors[g] = idx
            if idx < len(members):
                selected.add(members[idx])
                cursors[g] = idx + 1
                progressed = True
        if not progressed:
            break

    return [s for s in order if s in selected]


def liquidity_preview(bars_with_volume, order, cap=60, focus=None):
    """Up to `cap` symbols ranked by median close*volume (never used as a
    staleness signal - this is purely a liquidity ranking), then re-sorted
    back into the clustered `order` for display.
    """
    if bars_with_volume.empty:
        return []
    dollar = bars_with_volume["close"] * bars_with_volume["volume"]
    median_dollar = dollar.groupby(bars_with_volume["symbol"]).median().dropna()
    ranked = list(median_dollar.sort_values(ascending=False).index)

    selected = set()
    if focus in set(order):
        selected.add(focus)
    for s in ranked:
        if len(selected) >= cap:
            break
        if s in set(order):
            selected.add(s)

    return [s for s in order if s in selected]
