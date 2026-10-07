"""
Correlation tab: a 15-sector overview of the F&O universe's co-movement
with click-through drill-down, plus a per-stock most/least correlated
view - for any lookback from 1 to 500 days. The full 213x213 matrix is
stored and queryable but never drawn as a grid directly - about 22,000
pairs, nobody can read that (spec section 1); the sector matrix (spec
section 5) is the readable overview, and drill-down (5A) narrows to a
stock-by-stock rectangle only once a sector is picked.

Bars live outside the repo in ~/trader_desk_data/intraday.db (or
TRADER_DESK_DATA_DIR). If that store is missing or the needed tier has
no bars yet, this tab says so and names the fetch command - it never
calls Yahoo itself.
"""

import math
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import fno
from analytics import correlation as corr
from analytics import rolling
from analytics import sector_matrix
from analytics import stock_matrix
from analytics import tiers
from data_layer import intraday_store
from theme import TABULAR, palette, sector_colors

FETCH_CMD = ".venv/bin/python -m jobs.fetch_intraday"
DEFAULT_LOOKBACK = 20
DEFAULT_TOP_N = 10
SECTIONS = ["Sector overview", "Stock matrix", "History", "Pick one stock"]
STOCK_MATRIX_CAP = 60
#: Key for the Stock matrix heatmap's st.plotly_chart - read from
#: session_state at the top of render(), below, not from the call's own
#: return value. See _apply_pending_stock_matrix_click for why.
STOCK_MATRIX_PLOT_KEY = "sm_heatmap_plot"

#: Correlation VALUES get their own richer blue-white-red diverging scale
#: (theme.CORR_NEG/CORR_MID/CORR_POS), agreed with Adarsh 7 Oct 2026 - the
#: original approach of reusing the muted UP/DOWN tokens read as mud once
#: the scale was actually data-fit (see _symmetric_zrange below). Blue for
#: positive, red for negative is a deliberate swap from this app's price-
#: direction convention: correlation isn't a gain or a loss. Sector IDENTITY
#: (chips, legend, column grouping) is unaffected - that stays the separate
#: muted palette in theme.sector_colors().
def _value_colorscale(P):
    return [[0.0, P.CORR_NEG], [0.5, P.CORR_MID], [1.0, P.CORR_POS]]


#: zmin/zmax used to be a flat [-1, 1] regardless of what the data actually
#: held - every real-world r (usually 0.05-0.5) landed in the palest slice
#: of the scale no matter how rich the palette was. Fit the range to the
#: matrix's own largest magnitude instead, rounded up to the next 0.1 and
#: floored/capped to [0.1, 1.0], so the full colour range is always in use.
#: `z` is a list of lists; None and NaN cells (self-pairs, missing data) are
#: ignored rather than treated as 0.
def _symmetric_zrange(z):
    vals = [v for row in z for v in row if v is not None and v == v]
    if not vals:
        return -1.0, 1.0
    vmax = max(0.1, min(1.0, math.ceil(max(abs(v) for v in vals) * 10) / 10))
    return -vmax, vmax


def _apply_pending_stock_matrix_click():
    """Reads any click the Stock matrix heatmap captured during the
    PREVIOUS render - Streamlit syncs the plotly widget's selection into
    session_state before this script runs, same timing the Chart tab's
    Measure tool relies on, so the click that triggered this rerun is
    already sitting in st.session_state[STOCK_MATRIX_PLOT_KEY] here.

    Must run before ANY widget this function seeds (corr_section,
    hist_symbol, hist_mode, hist_other, sm_isolate) is instantiated this
    run - seeding a widget's session_state key before its own st.* call
    is a normal pre-set default, not the StreamlitWidgetAlreadyInstantiated
    error, which only fires for a write AFTER that key's widget call.

    The five traces (see _stock_matrix_heatmap) sit on disjoint subplot
    regions or are stacked with hover/click deliberately moved off the
    heatmap underneath, so a click only ever lands on one handled curve:
    curve_number 1 is the dedicated click-to-isolate strip next to the row
    labels (a go.Scatter, not a heatmap - Plotly's point-selection model,
    which Streamlit's on_select reads, only exists for point-based traces,
    confirmed in a real browser: heatmap cells never fire a selection
    event at all) - clicking a marker isolates that row, the same effect
    as picking it in the "Isolate rows" multiselect. curve_number 3 is an
    invisible go.Scatter overlay, one marker per cell, stacked exactly on
    top of the main grid heatmap (curve_number 2, which has
    hoverinfo="skip" and is colour-only) - clicking a cell jumps to
    History pre-loaded with that row/column pair. The sector-colour strip
    (curve_number 4, back on the right edge) is colour context only and is
    not wired here. For both handled traces, Plotly's selection point
    gives x/y as the actual tick labels, not numeric indices, so the
    clicked symbols are read straight off the event with no index lookup
    needed.
    """
    payload = st.session_state.get(STOCK_MATRIX_PLOT_KEY)
    points = (payload or {}).get("selection", {}).get("points", [])
    if not points:
        return
    p = points[-1]
    sig = (p.get("curve_number"), p.get("x"), p.get("y"))
    if sig == st.session_state.get("sm_click_last_sig"):
        return
    st.session_state["sm_click_last_sig"] = sig

    curve = p.get("curve_number")
    if curve == 1:
        symbol = p.get("y")
        if symbol:
            st.session_state["sm_isolate"] = [symbol]
    elif curve == 3:
        row_symbol, col_symbol = p.get("y"), p.get("x")
        if row_symbol and col_symbol and row_symbol != col_symbol:
            st.session_state["corr_section"] = "History"
            st.session_state["hist_symbol"] = row_symbol
            st.session_state["hist_mode"] = "Another stock"
            st.session_state["hist_other"] = col_symbol


def render():
    P = palette()
    _apply_pending_stock_matrix_click()

    st.markdown("<div class='chip-label'>Lookback (days)</div>",
                unsafe_allow_html=True)
    lookback = st.number_input(
        "corr_lookback", min_value=1, max_value=tiers.MAX_LOOKBACK_DAYS,
        value=DEFAULT_LOOKBACK, step=1, key="corr_lookback",
        label_visibility="collapsed",
    )

    try:
        result = corr.compute(int(lookback))
    except corr.NoDataError as exc:
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:14px;'>"
            f"No bars stored for this lookback yet ({exc}). Run:</div>"
            f"<div style='font-family:monospace;font-size:12px;color:{P.T1};"
            f"background:{P.SURFACE};border:1px solid {P.BORDER};padding:8px 10px;"
            f"margin-top:6px;display:inline-block;'>{FETCH_CMD}</div>",
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        f"<div style='color:{P.T2};font-size:12px;margin-top:4px;'>"
        f"{tiers.describe(int(lookback), result.n_sessions)}, "
        f"as of {result.asof}</div>",
        unsafe_allow_html=True,
    )

    section = st.pills("corr_section", SECTIONS, key="corr_section",
                       default="Sector overview", label_visibility="collapsed")
    section = section or "Sector overview"

    st.markdown(f"<div style='border-top:1px solid {P.BORDER};margin:14px 0 16px 0;'></div>",
                unsafe_allow_html=True)

    if section == "Sector overview":
        _sector_overview(P, result)
        _footer(P, result)
    elif section == "Stock matrix":
        _stock_matrix_section(P, result)
        _footer(P, result)
    elif section == "History":
        _history_section(P)
    else:
        _stock_section(P, result)
        _footer(P, result)


def _sector_overview(P, result):
    """Spec sections 5 / 5A: the 15-sector mean-correlation matrix, with
    click-through drill-down to a stock-by-stock rectangle.
    """
    st.markdown(
        f"<div style='color:{P.T1};font-size:14px;font-weight:600;"
        f"margin-top:14px;margin-bottom:6px;'>Sector overview — "
        f"mean stock-pair correlation</div>",
        unsafe_allow_html=True,
    )

    smr = sector_matrix.compute(result)
    colors = sector_colors()

    legend = "".join(
        f"<span style='display:inline-flex;align-items:center;gap:5px;"
        f"margin-right:14px;'>"
        f"<span style='width:10px;height:10px;background:{colors[g]};"
        f"display:inline-block;'></span>"
        f"<span style='font-size:12px;color:{P.T2};'>{g}</span></span>"
        for g in fno.DISPLAY_GROUPS
    )
    st.markdown(f"<div style='margin-bottom:8px;'>{legend}</div>",
                unsafe_allow_html=True)

    st.plotly_chart(_sector_heatmap(P, smr, colors), width="stretch",
                    config={"displayModeBar": False})

    st.markdown("<div class='chip-label' style='margin-top:10px;'>"
                "Drill into a sector</div>", unsafe_allow_html=True)
    # A fresh key per nonce, not a direct write to the pills' own session-state
    # key - Streamlit raises StreamlitWidgetAlreadyInstantiatedError if a
    # button later in the same run tries to overwrite a widget key already
    # instantiated this run. Same pattern as tabs/watchlist.py's add_nonce.
    drill_nonce = st.session_state.get("sector_drill_nonce", 0)
    sector_a = st.pills("sector_drill_a", fno.DISPLAY_GROUPS,
                        key=f"sector_drill_a_{drill_nonce}",
                        label_visibility="collapsed")
    if not sector_a:
        return

    hc1, hc2 = st.columns([5, 1])
    with hc1:
        st.markdown(
            f"<div style='color:{P.T1};font-size:13px;font-weight:600;"
            f"margin-top:10px;'>{sector_a} vs every other sector</div>",
            unsafe_allow_html=True,
        )
    with hc2:
        if st.button("Clear", key="sector_clear"):
            st.session_state.sector_drill_nonce = drill_nonce + 1
            st.rerun()

    st.plotly_chart(_drill_summary_bar(P, smr, sector_a), width="stretch",
                    config={"displayModeBar": False})

    others = [g for g in fno.DISPLAY_GROUPS if g != sector_a]
    # sector_a folded into the key so switching the top-level sector also
    # resets this pick, instead of carrying over a stale/invalid selection.
    sector_b = st.pills("sector_drill_b", others,
                        key=f"sector_drill_b_{drill_nonce}_{sector_a}",
                        label_visibility="collapsed")

    title = (f"{sector_a} vs {sector_b} — individual stocks" if sector_b else
             f"{sector_a} vs every other stock, grouped by sector")
    st.markdown(
        f"<div style='color:{P.T1};font-size:13px;font-weight:600;"
        f"margin-top:12px;'>{title}</div>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(_drill_rectangle(P, result, smr, sector_a, sector_b),
                    width="stretch", config={"displayModeBar": False})


def _sector_heatmap(P, smr, colors):
    groups = smr.groups
    z, text = [], []
    for s in groups:
        zrow, trow = [], []
        for t in groups:
            val = smr.mean_corr.at[s, t]
            zrow.append(val)
            n_valid = int(smr.valid_pairs.at[s, t])
            n_possible = int(smr.possible_pairs.at[s, t])
            if n_possible == 0:
                trow.append(f"<b>{s}</b> / <b>{t}</b><br>no data")
                continue
            cov = smr.coverage(s, t)
            cov_note = (f"<br><i>low pair coverage ({cov:.0%})</i>"
                       if smr.low_coverage(s, t) else "")
            median_n = smr.median_n.at[s, t]
            median_n_txt = f"{median_n:.0f}" if median_n == median_n else "—"
            trow.append(
                f"<b>{s}</b> / <b>{t}</b><br>"
                f"mean r = {val:+.3f}<br>"
                f"{n_valid}/{n_possible} pairs valid{cov_note}<br>"
                f"{len(smr.group_symbols[s])} / {len(smr.group_symbols[t])} stocks "
                f"&middot; median N {median_n_txt}"
            )
        z.append(zrow)
        text.append(trow)

    zmin, zmax = _symmetric_zrange(z)
    fig = go.Figure(go.Heatmap(
        z=z, x=groups, y=groups, text=text, hovertemplate="%{text}<extra></extra>",
        zmin=zmin, zmax=zmax, colorscale=_value_colorscale(P),
        colorbar=dict(title=dict(text="mean r", font=dict(size=11, color=P.T2)),
                      tickfont=dict(size=11, color=P.T2), outlinewidth=0),
        xgap=1, ygap=1,
    ))
    fig.update_layout(
        height=440, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=P.BG, plot_bgcolor=P.SURFACE,
        font=dict(family="Inter, Arial, sans-serif"),
        xaxis=dict(tickfont=dict(size=11, color=P.T2), side="top",
                  tickangle=-35, showgrid=False),
        yaxis=dict(tickfont=dict(size=11, color=P.T2), autorange="reversed",
                  showgrid=False),
        hoverlabel=dict(bgcolor=P.TOOLTIP_BG, bordercolor=P.BORDER,
                        font=dict(size=12, color=P.T1,
                                  family="Inter, Arial, sans-serif")),
    )
    return fig


def _drill_summary_bar(P, smr, sector_a):
    others = [g for g in smr.groups if g != sector_a]
    vals = [smr.mean_corr.at[sector_a, t] for t in others]
    text = []
    for t, v in zip(others, vals):
        if v != v:
            text.append(f"{sector_a} / {t}<br>no data")
            continue
        n_valid = int(smr.valid_pairs.at[sector_a, t])
        n_possible = int(smr.possible_pairs.at[sector_a, t])
        text.append(f"{sector_a} / {t}<br>mean r = {v:+.3f}<br>"
                   f"{n_valid}/{n_possible} pairs valid")
    colors = [P.UP if v >= 0 else P.DOWN for v in vals]

    fig = go.Figure(go.Bar(
        x=vals, y=others, orientation="h", marker_color=colors,
        text=text, hovertemplate="%{text}<extra></extra>",
    ))
    fig.update_layout(
        height=360, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=P.BG, plot_bgcolor=P.BG,
        font=dict(family="Inter, Arial, sans-serif"),
        xaxis=dict(range=[-1, 1], zeroline=True, zerolinecolor=P.RULE,
                  tickfont=dict(size=11, color=P.T2), showgrid=True, gridcolor=P.GRID),
        yaxis=dict(tickfont=dict(size=11, color=P.T2), autorange="reversed"),
        hoverlabel=dict(bgcolor=P.TOOLTIP_BG, bordercolor=P.BORDER,
                        font=dict(size=12, color=P.T1,
                                  family="Inter, Arial, sans-serif")),
    )
    return fig


def _drill_rectangle(P, result, smr, sector_a, sector_b):
    """Stock-by-stock rectangle: sector_a's stocks as rows, either every
    other eligible stock (grouped by sector) or, once a second sector is
    picked, just that sector's stocks as columns.

    Reads straight from result.corr - the same pairwise matrix the main
    sector cells were averaged from, so a drill-down cell always matches
    the value a direct lookup on that matrix would give (spec 5A
    acceptance check). Columns exclude sector_a's own stocks outright, so
    a stock in both sectors never lines up against itself (Addition 1);
    it can still appear as both a row and, under a different drill-down,
    a column, since each render only shows one sector pairing at a time.
    """
    rows = smr.group_symbols[sector_a]
    if sector_b:
        cols = smr.group_symbols[sector_b]
        col_groups = {c: sector_b for c in cols}
        boundaries = []
    else:
        row_set = set(rows)
        eligible = [s for s in result.corr.columns if s not in row_set]

        def _anchor(s):
            groups = fno.display_groups_of(s)
            idx = fno.DISPLAY_GROUPS.index(groups[0]) if groups else len(fno.DISPLAY_GROUPS)
            return (idx, s)

        cols = sorted(eligible, key=_anchor)
        col_groups = {c: (fno.display_groups_of(c)[0] if fno.display_groups_of(c) else "")
                     for c in cols}
        boundaries = [i for i in range(1, len(cols))
                     if col_groups[cols[i]] != col_groups[cols[i - 1]]]

    z, text = [], []
    for r in rows:
        zrow, trow = [], []
        for c in cols:
            if r == c:
                zrow.append(None)
                trow.append(f"{r}<br>(same stock)")
                continue
            val, n_obs = corr.pair(result, r, c)
            zrow.append(val)
            if val is None or val != val:
                trow.append(f"{r} / {c}<br>no data")
            else:
                trow.append(f"{r} / {c}<br>{col_groups.get(c, '')}<br>"
                           f"r = {val:+.3f} &middot; N = {n_obs}<br>as of {result.asof}")
        z.append(zrow)
        text.append(trow)

    zmin, zmax = _symmetric_zrange(z)
    fig = go.Figure(go.Heatmap(
        z=z, x=cols, y=rows, text=text, hovertemplate="%{text}<extra></extra>",
        zmin=zmin, zmax=zmax, colorscale=_value_colorscale(P),
        colorbar=dict(title=dict(text="r", font=dict(size=11, color=P.T2)),
                      tickfont=dict(size=11, color=P.T2), outlinewidth=0),
        xgap=0.5, ygap=0.5,
    ))
    for b in boundaries:
        fig.add_vline(x=b - 0.5, line=dict(color=P.RULE, width=1))
    fig.update_layout(
        height=max(220, 26 * len(rows) + 80),
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=P.BG, plot_bgcolor=P.SURFACE,
        font=dict(family="Inter, Arial, sans-serif"),
        xaxis=dict(tickfont=dict(size=10, color=P.T2), side="top",
                  tickangle=-60, showgrid=False),
        yaxis=dict(tickfont=dict(size=11, color=P.T2), autorange="reversed",
                  showgrid=False),
        hoverlabel=dict(bgcolor=P.TOOLTIP_BG, bordercolor=P.BORDER,
                        font=dict(size=12, color=P.T1,
                                  family="Inter, Arial, sans-serif")),
    )
    return fig


def _stock_matrix_section(P, result):
    """Spec section 6: cluster the full eligible universe first, then
    filter/preview what is actually drawn - a 60-stock preview is always
    a subset of one stable clustered order, never a fresh re-clustering
    of whatever happens to be visible.
    """
    st.markdown(
        f"<div style='color:{P.T1};font-size:14px;font-weight:600;"
        f"margin-top:14px;margin-bottom:6px;'>Stock matrix — clustered by "
        f"co-movement</div>", unsafe_allow_html=True,
    )

    universe = set(result.symbols)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("<div class='chip-label'>Sectors</div>", unsafe_allow_html=True)
        sectors = st.multiselect(
            "sm_sectors", fno.DISPLAY_GROUPS, key="sm_sectors",
            label_visibility="collapsed", placeholder="All sectors",
        )
    with c2:
        st.markdown("<div class='chip-label'>Add specific stocks</div>",
                    unsafe_allow_html=True)
        extra = st.multiselect(
            "sm_extra", fno.symbols(), key="sm_extra",
            label_visibility="collapsed", placeholder="Search a symbol",
        )

    if sectors:
        in_sectors = {s for g in sectors for s in fno.symbols_in_display_group(g)}
    else:
        in_sectors = set(universe)
    eligible = sorted((in_sectors | set(extra)) & universe)

    if len(eligible) < 2:
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:10px;'>"
            f"Need at least two eligible stocks to cluster.</div>",
            unsafe_allow_html=True,
        )
        return

    cl = stock_matrix.compute_order(result, eligible)
    if len(cl.order) < 2:
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:10px;'>"
            f"Not enough overlapping data among this selection to "
            f"cluster.</div>", unsafe_allow_html=True,
        )
        return

    c3, c4, c5 = st.columns([1, 1, 2])
    with c3:
        st.markdown("<div class='chip-label'>Show</div>", unsafe_allow_html=True)
        preview_choice = st.pills(
            "sm_preview", ["Preview 60", "All eligible"], key="sm_preview",
            default="Preview 60", label_visibility="collapsed",
        )
    with c4:
        rank_choice = None
        if preview_choice != "All eligible":
            st.markdown("<div class='chip-label'>Rank by</div>", unsafe_allow_html=True)
            rank_choice = st.pills(
                "sm_rank", ["Sector-balanced", "Most liquid"], key="sm_rank",
                default="Sector-balanced", label_visibility="collapsed",
            )
    with c5:
        st.markdown("<div class='chip-label'>Focus (always included)</div>",
                    unsafe_allow_html=True)
        focus_choice = st.selectbox(
            "sm_focus", ["None"] + cl.order, key="sm_focus",
            label_visibility="collapsed",
        )
    focus = None if focus_choice == "None" else focus_choice

    if preview_choice == "All eligible":
        shown = cl.order
    elif rank_choice == "Most liquid":
        conn = intraday_store.get_connection()
        try:
            window = intraday_store.window_start(conn, result.tier, result.lookback_days)
            start_ts = window[0] if window else None
            bars_vol = intraday_store.read_bars_with_volume(
                conn, result.tier, start_ts, cl.order)
        finally:
            conn.close()
        shown = stock_matrix.liquidity_preview(
            bars_vol, cl.order, cap=STOCK_MATRIX_CAP, focus=focus)
    else:
        shown = stock_matrix.sector_balanced_preview(
            cl.order, cap=STOCK_MATRIX_CAP, focus=focus)

    if cl.excluded:
        names = ", ".join(s for s, _ in cl.excluded)
        st.markdown(
            f"<div style='color:{P.T3};font-size:12px;margin-top:8px;'>"
            f"Excluded from clustering (insufficient overlapping data): "
            f"{names}. Still searchable in \"Pick one stock\".</div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        f"<div style='color:{P.T2};font-size:12px;margin-top:4px;'>"
        f"{len(shown)} of {len(cl.order)} eligible stocks shown, "
        f"clustered by co-movement.</div>", unsafe_allow_html=True,
    )

    st.markdown("<div class='chip-label' style='margin-top:10px;'>"
                "Isolate rows (optional)</div>", unsafe_allow_html=True)
    isolate = st.multiselect(
        "sm_isolate", shown, key="sm_isolate", label_visibility="collapsed",
        placeholder="Pick one or more stocks to see only their rows "
                    "against everything shown",
    )
    rows = isolate if isolate else shown

    st.markdown(
        f"<div style='color:{P.T3};font-size:12px;margin-top:8px;'>"
        f"Click a row's square marker to isolate it here; click any cell to "
        f"open that pair's correlation history.</div>", unsafe_allow_html=True,
    )
    st.plotly_chart(
        _stock_matrix_heatmap(P, result, shown, rows=rows),
        width="stretch", key=STOCK_MATRIX_PLOT_KEY,
        on_select="rerun", selection_mode=("points",),
        config={"displayModeBar": False},
    )


def _discrete_colorscale(colors):
    """A flat, non-interpolated colorscale: one solid band per entry."""
    n = len(colors)
    scale = []
    for i, c in enumerate(colors):
        scale.append([i / n, c])
        scale.append([(i + 1) / n, c])
    return scale


def _stock_matrix_heatmap(P, result, cols, rows=None):
    """`cols` is the displayed universe (x-axis, and the strip along the
    top). `rows` defaults to the same set (a square matrix) but can be a
    narrower list - isolating a chosen stock's row(s) against every column
    still shown, rather than a fresh square subset of just the isolated
    stocks.
    """
    if rows is None:
        rows = cols

    colors = sector_colors()
    group_colors = [colors[g] for g in fno.DISPLAY_GROUPS]
    strip_scale = _discrete_colorscale(group_colors)

    def _group_idx(s):
        groups = fno.display_groups_of(s)
        return fno.DISPLAY_GROUPS.index(groups[0]) if groups else 0

    def _group_label(s):
        groups = fno.display_groups_of(s)
        return f"{s}<br>{groups[0]}" if groups else f"{s}<br>no sector"

    z, text = [], []
    for r in rows:
        zrow, trow = [], []
        for c in cols:
            if r == c:
                zrow.append(None)
                trow.append(f"{r}<br>(same stock)")
                continue
            val, n_obs = corr.pair(result, r, c)
            zrow.append(val)
            if val is None or val != val:
                trow.append(f"{r} / {c}<br>no data")
            else:
                trow.append(f"{r} / {c}<br>r = {val:+.3f} &middot; N = {n_obs}<br>"
                           f"as of {result.asof}")
        z.append(zrow)
        text.append(trow)

    n = len(rows)
    top_idx = [[_group_idx(s) for s in cols]]
    side_idx = [[_group_idx(s)] for s in rows]
    top_text = [[_group_label(s) for s in cols]]
    side_text = [[_group_label(s)] for s in rows]

    # Sector-colour strip is back on the right edge, its original spot
    # (col 3) - it is coloured context, not a click target any more.
    # The click-to-isolate strip is a SEPARATE, new trace in col 1, right
    # next to the row labels (which render on that subplot's own y-axis).
    # It is a go.Scatter, not a go.Heatmap: Plotly's point-selection model
    # (what Streamlit's on_select reads) only exists for point-based
    # traces - scatter, bar - not heatmaps, which is why clicking any of
    # the heatmap cells never produced a selection event at all. The Chart
    # tab's Measure tool is a scatter trace for the same reason.
    #
    # col 1 needs enough pixel width to hold its marker without clipping -
    # squeezed narrower than that (tried 0.015 first), the marker's right
    # edge got clipped by the subplot's own boundary, so only its left
    # sliver was visible: clicking the visible colour did nothing because
    # the real hit centre sat further right, under col 2's territory,
    # which is what made "a bit left of the colour" register instead.
    #
    # The main grid (col 2) is still a heatmap for its continuous colour
    # scale, but it no longer carries hover or click itself
    # (hoverinfo="skip") - an invisible go.Scatter marker is overlaid on
    # the exact same subplot, one per cell, to do both: heatmap cells
    # simply never fire Plotly's point-selection event, confirmed by
    # Adarsh clicking the grid in a real browser and nothing happening.
    #
    # No shared_xaxes/shared_yaxes: sharing an axis across subplots can
    # make Plotly report a click from more than one trace for the same
    # point, and Streamlit silently drops the whole click when that
    # happens. All y-axes get the same explicit categoryarray=rows
    # instead, so every strip and the main grid stay row-aligned without
    # sharing an axis.
    fig = make_subplots(
        rows=2, cols=3,
        row_heights=[0.018, 0.982], column_widths=[0.028, 0.954, 0.018],
        horizontal_spacing=0.006, vertical_spacing=0.004,
        specs=[[None, {"type": "heatmap"}, None],
               [{"type": "xy"}, {"type": "heatmap"}, {"type": "heatmap"}]],
    )

    # curve_number 0: top strip (not a click target).
    fig.add_trace(go.Heatmap(
        z=top_idx, x=cols, y=[""], zmin=0, zmax=len(fno.DISPLAY_GROUPS),
        colorscale=strip_scale, showscale=False,
        text=top_text, hovertemplate="%{text}<extra></extra>",
    ), row=1, col=2)
    # curve_number 1: click-to-isolate strip (new) - one marker per row.
    side_colors = [group_colors[_group_idx(s)] for s in rows]
    fig.add_trace(go.Scatter(
        x=[0] * n, y=rows, mode="markers",
        marker=dict(size=9, symbol="square", color=side_colors, line=dict(width=0)),
        text=[s for s in rows], hovertemplate="%{text}<extra></extra>",
        showlegend=False,
    ), row=2, col=1)
    zmin, zmax = _symmetric_zrange(z)
    # curve_number 2: main grid colour, hover and click moved to the
    # invisible overlay (curve_number 3) just below - see note above.
    fig.add_trace(go.Heatmap(
        z=z, x=cols, y=rows, zmin=zmin, zmax=zmax, colorscale=_value_colorscale(P),
        colorbar=dict(title=dict(text="r", font=dict(size=11, color=P.T2)),
                      tickfont=dict(size=11, color=P.T2), outlinewidth=0,
                      len=0.9, x=1.015, thickness=12),
        xgap=0.3, ygap=0.3, hoverinfo="skip",
    ), row=2, col=2)
    # curve_number 3: invisible one-marker-per-cell overlay, exact same
    # x/y domain as the heatmap above - this is what actually receives
    # hover and click for the main grid. marker.opacity=0 keeps it
    # invisible while staying fully hit-testable; size is generous since
    # overlap between adjacent invisible markers has no visual cost.
    cell_x = [c for _r in rows for c in cols]
    cell_y = [r for r in rows for _c in cols]
    cell_text = [t for trow in text for t in trow]
    fig.add_trace(go.Scatter(
        x=cell_x, y=cell_y, mode="markers",
        marker=dict(size=16, symbol="square", opacity=0),
        text=cell_text, hovertemplate="%{text}<extra></extra>",
        showlegend=False,
    ), row=2, col=2)
    # curve_number 4: sector-colour strip, restored to the right edge -
    # colour context only, not wired to _apply_pending_stock_matrix_click.
    fig.add_trace(go.Heatmap(
        z=side_idx, x=[""], y=rows, zmin=0, zmax=len(fno.DISPLAY_GROUPS),
        colorscale=strip_scale, showscale=False,
        text=side_text, hovertemplate="%{text}<extra></extra>",
    ), row=2, col=3)

    tick_size = 9 if max(n, len(cols)) > 45 else 10
    # Row-label text lives on col 1's OWN y-axis now, not col 2's. A
    # subplot's y-axis ticks render just outside its own domain by
    # default, so putting them on col 2 (as before) floated the stock
    # names in the sliver of a gap between col 1 and col 2 - directly on
    # top of the new click markers. Anchored to col 1 instead, the text
    # lands in the figure's own left margin, clear of the markers, with
    # the markers sitting inside col 1's domain just to the text's right.
    max_label_len = max((len(s) for s in rows), default=8)
    label_margin = min(130, max(56, int(max_label_len * tick_size * 0.62) + 16))
    fig.update_layout(
        height=max(240, 15 * n + 90),
        margin=dict(l=label_margin, r=40, t=10, b=10),
        paper_bgcolor=P.BG, plot_bgcolor=P.SURFACE,
        font=dict(family="Inter, Arial, sans-serif"),
        clickmode="event+select",
        hoverlabel=dict(bgcolor=P.TOOLTIP_BG, bordercolor=P.BORDER,
                        font=dict(size=12, color=P.T1,
                                  family="Inter, Arial, sans-serif")),
    )
    # fixedrange=True on every axis: this is a fixed-size matrix, not a
    # zoomable/pannable chart. Without it, each subplot's axis autoranges
    # with its usual padding beyond the actual categories, and that padding
    # is draggable - the heatmap visibly slides around inside its own
    # margins. Locking every axis removes that slack entirely.
    fig.update_xaxes(showticklabels=False, showgrid=False, fixedrange=True,
                     row=1, col=2)
    fig.update_yaxes(showticklabels=False, showgrid=False, fixedrange=True,
                     row=1, col=2)
    fig.update_xaxes(showticklabels=False, showgrid=False, range=[-0.3, 0.3],
                     fixedrange=True, row=2, col=1)
    # Explicit range here too, not autorange="reversed" - col 1, col 2 and
    # col 3 must land on pixel-identical row positions since they're the
    # row labels, the grid, and the sector strip for the SAME rows. Each
    # subplot has its own y-axis, and autorange resolves independently per
    # axis; a scatter trace (col 1's isolate markers) and a heatmap (col 2,
    # col 3) autorange slightly differently even over the same categories,
    # which showed up as whole rows drifting out of alignment with their
    # own labels. Pinning all three to the identical explicit range removes
    # the drift instead of relying on them to agree by coincidence.
    fig.update_yaxes(tickfont=dict(size=tick_size, color=P.T2), showgrid=False,
                     showline=False, ticks="", categoryorder="array",
                     categoryarray=rows, range=[len(rows) - 0.5, -0.5],
                     fixedrange=True, row=2, col=1)
    # Explicit range on both axes here, not just autorange - the invisible
    # click/hover overlay (curve 3) is a go.Scatter sharing this subplot,
    # and Plotly's combined auto-range for a heatmap + scatter on the same
    # axis leaves a large unexplained dead zone before the first cell (found
    # by bisecting a minimal repro; removing the overlay trace removes the
    # gap, but the overlay is required for clicking). Pinning the range to
    # exactly the category span, the same way col 1's isolate strip already
    # does, removes it.
    fig.update_xaxes(tickfont=dict(size=tick_size, color=P.T2), side="bottom",
                     tickangle=-60, showgrid=False, fixedrange=True,
                     range=[-0.5, len(cols) - 0.5], row=2, col=2)
    fig.update_yaxes(showticklabels=False, showgrid=False,
                     categoryorder="array", categoryarray=rows,
                     range=[len(rows) - 0.5, -0.5], fixedrange=True, row=2, col=2)
    fig.update_xaxes(showticklabels=False, showgrid=False, fixedrange=True,
                     row=2, col=3)
    fig.update_yaxes(showticklabels=False, showgrid=False,
                     categoryorder="array", categoryarray=rows,
                     range=[len(rows) - 0.5, -0.5], fixedrange=True, row=2, col=3)
    return fig


def _history_section(P):
    """Spec section 7: rolling correlation of a focus stock against
    another stock, NIFTY 50, or a sector basket (15 merged display
    groups), plus frozen top-5-partner mini panels. Always the daily
    tier - this view's own span/window controls are independent of the
    page's shared lookback above.
    """
    st.markdown(
        f"<div style='color:{P.T1};font-size:14px;font-weight:600;"
        f"margin-top:14px;margin-bottom:6px;'>Historical rolling "
        f"correlation</div>", unsafe_allow_html=True,
    )

    c0, c1 = st.columns([2, 2])
    with c0:
        st.markdown("<div class='chip-label'>Focus stock</div>", unsafe_allow_html=True)
        symbol = st.selectbox(
            "hist_symbol", fno.symbols(), key="hist_symbol",
            label_visibility="collapsed",
            format_func=lambda s: f"{s} — {fno.display_name(s)}",
        )
    with c1:
        st.markdown("<div class='chip-label'>Compare against</div>", unsafe_allow_html=True)
        mode = st.pills(
            "hist_mode", ["NIFTY 50", "Another stock", "Sector basket"],
            key="hist_mode", default="NIFTY 50", label_visibility="collapsed",
        )
    mode = mode or "NIFTY 50"

    other_symbol, sector_group = None, None
    if mode == "Another stock":
        others = [s for s in fno.symbols() if s != symbol]
        st.markdown("<div class='chip-label'>Other stock</div>", unsafe_allow_html=True)
        other_symbol = st.selectbox(
            "hist_other", others, key="hist_other", label_visibility="collapsed",
            format_func=lambda s: f"{s} — {fno.display_name(s)}",
        )
    elif mode == "Sector basket":
        own = fno.display_groups_of(symbol)
        default_idx = fno.DISPLAY_GROUPS.index(own[0]) if own else 0
        st.markdown("<div class='chip-label'>Sector</div>", unsafe_allow_html=True)
        sector_group = st.selectbox(
            "hist_sector", fno.DISPLAY_GROUPS, index=default_idx,
            key="hist_sector", label_visibility="collapsed",
        )

    c2, c3 = st.columns(2)
    with c2:
        st.markdown("<div class='chip-label'>Window (sessions)</div>", unsafe_allow_html=True)
        window = st.number_input(
            "hist_window", min_value=rolling.MIN_WINDOW, max_value=500,
            value=rolling.DEFAULT_WINDOW, step=1, key="hist_window",
            label_visibility="collapsed",
        )
    with c3:
        st.markdown("<div class='chip-label'>History span</div>", unsafe_allow_html=True)
        span = st.pills(
            "hist_span", list(rolling.HISTORY_SPANS), key="hist_span",
            default=rolling.DEFAULT_SPAN, label_visibility="collapsed",
        )
    span = span or rolling.DEFAULT_SPAN
    window = int(window)

    end = date.today()
    start = end - timedelta(days=rolling.HISTORY_SPANS[span])
    fetch_start = start - timedelta(days=int(window * 1.6) + 10)
    start_ts = f"{fetch_start.isoformat()}T00:00:00"

    a_full = rolling.stock_log_returns([symbol], start_ts)
    if a_full.empty or symbol not in a_full.columns:
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:10px;'>"
            f"No daily bars stored for {symbol} yet. Run:</div>"
            f"<div style='font-family:monospace;font-size:12px;color:{P.T1};"
            f"background:{P.SURFACE};border:1px solid {P.BORDER};padding:8px 10px;"
            f"margin-top:6px;display:inline-block;'>{FETCH_CMD}</div>",
            unsafe_allow_html=True,
        )
        return
    a_ret = a_full[symbol]

    constituents_note = None
    if mode == "NIFTY 50":
        b_ret = rolling.nifty_log_returns(fetch_start, end)
        label_b = rolling.NIFTY_LABEL
    elif mode == "Another stock":
        b_full = rolling.stock_log_returns([other_symbol], start_ts)
        if b_full.empty or other_symbol not in b_full.columns:
            st.markdown(
                f"<div style='color:{P.T2};font-size:13px;margin-top:10px;'>"
                f"No daily bars stored for {other_symbol} yet.</div>",
                unsafe_allow_html=True,
            )
            return
        b_ret = b_full[other_symbol]
        label_b = other_symbol
    else:
        b_ret, constituents = rolling.sector_basket_returns(sector_group, symbol, start_ts)
        label_b = f"Sector basket (equal-weight log returns, excluding {symbol})"
        constituents_note = (
            f"{sector_group} basket &middot; {len(constituents)} constituents "
            f"&middot; membership as of today, held fixed across this chart "
            f"(not point-in-time reconstructed): {', '.join(constituents) or '—'}"
        )

    if b_ret.empty:
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:10px;'>"
            f"No comparison data available for this selection.</div>",
            unsafe_allow_html=True,
        )
        return

    rho, n_valid = rolling.rolling_correlation(a_ret, b_ret, window)
    if rho.empty:
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:10px;'>"
            f"Not enough overlapping data to compute a rolling "
            f"correlation.</div>", unsafe_allow_html=True,
        )
        return

    mask = ((rho.index.normalize() >= pd.Timestamp(start))
            & (rho.index.normalize() <= pd.Timestamp(end)))
    visible, visible_n = rho[mask], n_valid[mask]

    if visible.dropna().empty:
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:10px;'>"
            f"Not enough overlapping data in this window to compute a "
            f"rolling correlation.</div>", unsafe_allow_html=True,
        )
        return

    st.plotly_chart(
        _rolling_chart(P, visible, symbol, label_b, window),
        width="stretch", config={"displayModeBar": False},
    )

    last = visible.dropna()
    latest_date = last.index[-1].date()
    stale = (end - latest_date).days > 5
    stale_note = " — stale, no more recent computable value" if stale else ""
    st.markdown(
        f"<div style='color:{P.T2};font-size:12px;margin-top:4px;'>"
        f"Latest computable: {latest_date} = {last.iloc[-1]:+.3f}{stale_note}"
        f"</div>", unsafe_allow_html=True,
    )

    low_n_days = int((visible_n < rolling.MIN_VALID_WARN).sum())
    if low_n_days:
        st.markdown(
            f"<div style='color:{P.T3};font-size:12px;margin-top:2px;'>"
            f"{low_n_days} day(s) in this window have fewer than "
            f"{rolling.MIN_VALID_WARN} valid paired observations under "
            f"them — treat the correlation there cautiously.</div>",
            unsafe_allow_html=True,
        )

    if constituents_note:
        st.markdown(
            f"<div style='color:{P.T3};font-size:12px;margin-top:2px;'>"
            f"{constituents_note}</div>", unsafe_allow_html=True,
        )

    st.markdown(
        f"<div style='color:{P.T1};font-size:13px;font-weight:600;"
        f"margin-top:18px;margin-bottom:4px;'>Today's top 5, shown "
        f"through time</div>", unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='color:{P.T3};font-size:12px;margin-bottom:6px;'>"
        f"{symbol}'s five highest current partners, picked once from "
        f"today's daily-tier snapshot and plotted across this same "
        f"history — not re-picked day by day.</div>", unsafe_allow_html=True,
    )

    top5 = rolling.top5_partners(symbol, n=5)
    if top5.empty:
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;'>"
            f"No valid partners for {symbol} at the daily tier.</div>",
            unsafe_allow_html=True,
        )
        return

    cols = st.columns(len(top5))
    for col, row in zip(cols, top5.itertuples()):
        partner = row.symbol
        with col:
            st.markdown(
                f"<div style='color:{P.T1};font-size:12px;font-weight:600;'>"
                f"{partner} <span style='color:{P.T2};font-weight:400;'>"
                f"r={row.corr:+.3f}</span></div>", unsafe_allow_html=True,
            )
            p_full = rolling.stock_log_returns([partner], start_ts)
            if p_full.empty or partner not in p_full.columns:
                st.markdown(f"<div style='color:{P.T3};font-size:12px;'>"
                           f"no data</div>", unsafe_allow_html=True)
                continue
            p_rho, _ = rolling.rolling_correlation(a_ret, p_full[partner], window)
            p_mask = ((p_rho.index.normalize() >= pd.Timestamp(start))
                     & (p_rho.index.normalize() <= pd.Timestamp(end)))
            p_visible = p_rho[p_mask]
            st.plotly_chart(
                _rolling_chart(P, p_visible, symbol, partner, window, compact=True),
                width="stretch", config={"displayModeBar": False},
            )


def _rolling_chart(P, rho, label_a, label_b, window, compact=False):
    """One neutral line (P.T1, not a new blue accent - the design language
    forbids colors outside the granted sector-identity exception), zero
    baseline, fixed [-1, 1] range, and real gaps left as gaps.
    """
    fig = go.Figure(go.Scatter(
        x=rho.index, y=rho.to_numpy(), mode="lines",
        line=dict(color=P.T1, width=1.6), connectgaps=False,
        hovertemplate="%{x|%d %b %Y}<br>r = %{y:+.3f}<extra></extra>",
    ))
    fig.add_hline(y=0, line=dict(color=P.RULE, width=1))
    fig.update_layout(
        height=150 if compact else 320,
        margin=dict(l=10, r=10, t=(6 if compact else 28), b=10),
        paper_bgcolor=P.BG, plot_bgcolor=P.BG,
        font=dict(family="Inter, Arial, sans-serif"),
        xaxis=dict(tickfont=dict(size=9 if compact else 11, color=P.T2),
                  showgrid=False),
        yaxis=dict(range=[-1, 1], tickfont=dict(size=9 if compact else 11, color=P.T2),
                  showgrid=True, gridcolor=P.GRID, zeroline=False),
        showlegend=False,
        hoverlabel=dict(bgcolor=P.TOOLTIP_BG, bordercolor=P.BORDER,
                        font=dict(size=12, color=P.T1,
                                  family="Inter, Arial, sans-serif")),
    )
    if not compact:
        fig.update_layout(title=dict(
            text=f"{label_a} vs {label_b} — {window}-session rolling r",
            font=dict(size=12, color=P.T2), x=0, pad=dict(b=4),
        ))
    return fig


def _stock_section(P, result):
    st.markdown(
        f"<div style='color:{P.T1};font-size:14px;font-weight:600;"
        f"margin-bottom:6px;'>Pick one stock</div>",
        unsafe_allow_html=True,
    )

    c0, c2 = st.columns([2, 1])
    with c0:
        st.markdown("<div class='chip-label'>Stock</div>", unsafe_allow_html=True)
        symbol = st.selectbox(
            "corr_symbol", fno.symbols(), key="corr_symbol",
            label_visibility="collapsed",
            format_func=lambda s: f"{s} — {fno.display_name(s)}",
        )
    with c2:
        st.markdown("<div class='chip-label'>Top N</div>",
                    unsafe_allow_html=True)
        top_n = st.number_input(
            "corr_top_n", min_value=3, max_value=25, value=DEFAULT_TOP_N,
            step=1, key="corr_top_n", label_visibility="collapsed",
        )

    most, least = corr.ranked_against(result, symbol, n=int(top_n))
    if most.empty:
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:14px;'>"
            f"No valid pairs for {symbol} at this lookback.</div>",
            unsafe_allow_html=True,
        )
        return

    own_sector = fno.primary_sector(symbol)

    col_most, col_least = st.columns(2)
    with col_most:
        _table(P, f"Most correlated with {symbol}", most, own_sector)
    with col_least:
        _table(P, f"Least correlated with {symbol}", least, own_sector)


def _table(P, title, rows, own_sector):
    st.markdown(
        f"<div style='color:{P.T1};font-size:14px;font-weight:600;"
        f"margin-top:14px;margin-bottom:4px;'>{title}</div>",
        unsafe_allow_html=True,
    )

    header = (
        f"<tr style='border-bottom:1px solid {P.BORDER};'>"
        f"<th style='padding:4px 12px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
        f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Symbol</th>"
        f"<th style='padding:4px 12px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
        f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Sector</th>"
        f"<th style='padding:4px 12px 6px 0;color:{P.T3};font-size:12px;text-align:right;"
        f"text-transform:uppercase;letter-spacing:0.05em;border:none;{TABULAR}'>Corr</th>"
        f"<th style='padding:4px 0 6px 0;color:{P.T3};font-size:12px;text-align:right;"
        f"text-transform:uppercase;letter-spacing:0.05em;border:none;{TABULAR}'>N</th>"
        f"</tr>"
    )

    body = []
    for r in rows.itertuples():
        sectors = fno.sectors_of(r.symbol)
        primary = sectors[0] if sectors else "—"
        title_attr = ", ".join(sectors) if len(sectors) > 1 else ""
        sector_color = P.T1 if primary == own_sector and own_sector else P.T2
        body.append(
            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
            f"<td style='padding:6px 12px 6px 0;color:{P.T1};font-size:13px;"
            f"white-space:nowrap;border:none;'>{r.symbol}</td>"
            f"<td style='padding:6px 12px 6px 0;color:{sector_color};font-size:13px;"
            f"white-space:nowrap;border:none;' title='{title_attr}'>{primary}</td>"
            f"<td style='padding:6px 12px 6px 0;color:{P.T1};font-size:13px;"
            f"text-align:right;border:none;{TABULAR}'>{r.corr:+.4f}</td>"
            f"<td style='padding:6px 0;color:{P.T2};font-size:13px;"
            f"text-align:right;border:none;{TABULAR}'>{r.n_obs}</td>"
            f"</tr>"
        )

    st.markdown(
        f"<table style='border-collapse:collapse;border:none;width:100%;'>"
        f"{header}{''.join(body)}</table>",
        unsafe_allow_html=True,
    )


def _footer(P, result):
    st.markdown(
        f"<div style='color:{P.T3};font-size:12px;margin-top:16px;'>"
        f"{result.bar_label} bars &middot; {corr.STALE_POLICY} &middot; "
        f"source: Yahoo Finance via yfinance, personal use only.</div>",
        unsafe_allow_html=True,
    )
    if result.extremes:
        shown = ", ".join(
            f"{s} {v:+.1%} at {ts}" for s, ts, v in result.extremes[:5]
        )
        st.markdown(
            f"<div style='color:{P.T3};font-size:12px;margin-top:4px;'>"
            f"Flagged for a manual look (single-bar move over "
            f"{corr.EXTREME_RETURN:.0%}): {shown}</div>",
            unsafe_allow_html=True,
        )
