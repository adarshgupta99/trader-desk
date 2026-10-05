"""
Correlation tab: pick an F&O stock, see which of the other 212 names
move with it and against it, for any lookback from 1 to 500 days. The
full 213x213 matrix is stored and queryable but never drawn as a grid -
about 22,000 pairs, nobody can read that (spec section 1).

Bars live outside the repo in ~/trader_desk_data/intraday.db (or
TRADER_DESK_DATA_DIR). If that store is missing or the needed tier has
no bars yet, this tab says so and names the fetch command - it never
calls Yahoo itself.
"""

import streamlit as st

import fno
from analytics import correlation as corr
from analytics import tiers
from theme import TABULAR, palette

FETCH_CMD = ".venv/bin/python -m jobs.fetch_intraday"
DEFAULT_LOOKBACK = 20
DEFAULT_TOP_N = 10


def render():
    P = palette()

    st.markdown("<div class='chip-label'>Stock</div>", unsafe_allow_html=True)
    symbol = st.selectbox(
        "corr_symbol", fno.symbols(), key="corr_symbol",
        label_visibility="collapsed",
        format_func=lambda s: f"{s} — {fno.display_name(s)}",
    )

    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown("<div class='chip-label'>Lookback (days)</div>",
                    unsafe_allow_html=True)
        lookback = st.number_input(
            "corr_lookback", min_value=1, max_value=tiers.MAX_LOOKBACK_DAYS,
            value=DEFAULT_LOOKBACK, step=1, key="corr_lookback",
            label_visibility="collapsed",
        )
    with c2:
        st.markdown("<div class='chip-label'>Top N</div>",
                    unsafe_allow_html=True)
        top_n = st.number_input(
            "corr_top_n", min_value=3, max_value=25, value=DEFAULT_TOP_N,
            step=1, key="corr_top_n", label_visibility="collapsed",
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

    most, least = corr.ranked_against(result, symbol, n=int(top_n))
    if most.empty:
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:14px;'>"
            f"No valid pairs for {symbol} at this lookback.</div>",
            unsafe_allow_html=True,
        )
        _footer(P, result)
        return

    own_sector = fno.primary_sector(symbol)

    col_most, col_least = st.columns(2)
    with col_most:
        _table(P, f"Most correlated with {symbol}", most, own_sector)
    with col_least:
        _table(P, f"Least correlated with {symbol}", least, own_sector)

    _footer(P, result)


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
