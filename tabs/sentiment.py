"""
Sentiment tab: Week 5-6 earnings-call sentiment tracker (see CLAUDE.md).
A standalone tab, not a panel under Chart or Events - the output here is
a per-company TREND across quarters, not something that belongs bolted
onto either of those.

Read-only: reads earnings_call_sentiment, written only by
jobs/refresh_earnings_sentiment.py. This tab never writes.
"""

from datetime import datetime

import plotly.graph_objects as go
import streamlit as st

import fno
from events_db import (
    get_earnings_call_sentiment,
    get_earnings_call_sentiment_symbols,
    get_expected_move_history,
)
from theme import TABULAR, palette


def _fmt_quarter(iso_date):
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    return f"QE {d.strftime('%b %Y')}"


def _tone_text(v):
    return f"{v:+.1f}" if v is not None else "-"


def _move_text(v):
    if v is None:
        return "-"
    v = round(v, 1)
    if v == 0:
        v = 0.0  # suppress negative zero after rounding, per the design language
    return f"{v:+.1f}%"


def _move_color(v, P):
    if v is None:
        return P.T2
    return P.UP if v >= 0 else P.DOWN


def _symbols_by_sector(symbols):
    """{sector display group: [symbols with sentiment data in it]}, each
    list sorted, keyed by each symbol's PRIMARY display group only (the
    first entry of fno.display_groups_of - a stock spanning two merged
    groups appears under one sector tile here, not both, since this is a
    navigation aid, not the Correlation tab's sector matrix). A symbol
    fno doesn't recognise (shouldn't happen for an F&O name, but no
    assumption worth crashing on) falls into an 'Other' bucket rather
    than being dropped."""
    by_sector = {}
    for sym in symbols:
        groups = fno.display_groups_of(sym)
        sector = groups[0] if groups else "Other"
        by_sector.setdefault(sector, []).append(sym)
    for sector in by_sector:
        by_sector[sector].sort()
    return by_sector


def _moves_by_quarter(symbol):
    """{quarter_end (ISO date): signed_move_pct} from the Expected-move
    tracker's own print history (events_db.get_expected_move_history),
    keyed by financial_period - which is the same ISO quarter-end date
    earnings_call_sentiment.quarter_end uses, so the two line up with no
    conversion. Read-only, same table jobs/refresh_expected_moves.py
    owns; a quarter with no stored print (job hasn't reached this symbol
    yet) or a signed_move_pct of None (status != 'usable') is simply
    absent here - the caller shows '-', never a fabricated 0%."""
    return {
        m["financial_period"]: m["signed_move_pct"]
        for m in get_expected_move_history(symbol)
        if m["signed_move_pct"] is not None
    }


def render():
    P = palette()
    st.markdown(
        f"<div style='font-size:28px;font-weight:700;color:{P.T1};"
        f"letter-spacing:-0.01em;margin-bottom:2px;'>Sentiment</div>"
        f"<div style='font-size:13px;color:{P.T2};margin-bottom:14px;'>"
        f"Earnings-call tone, management vs analyst Q&amp;A, quarter over quarter. "
        f"Loughran-McDonald word counts (deterministic) and Gemini-extracted tone/"
        f"guidance, both from the company's own NSE-filed transcript.</div>",
        unsafe_allow_html=True,
    )

    symbols = get_earnings_call_sentiment_symbols()
    if not symbols:
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:10px;'>"
            f"No sentiment data yet. Run "
            f"<code>jobs.refresh_earnings_sentiment</code> for a watchlist "
            f"symbol to populate this tab.</div>",
            unsafe_allow_html=True,
        )
        return

    st.markdown("<div class='chip-label'>Search</div>", unsafe_allow_html=True)
    search_text = st.text_input(
        "sentiment_search", placeholder="Search by symbol", key="sentiment_search",
        label_visibility="collapsed",
    )

    by_sector = _symbols_by_sector(symbols)
    sector_options = [g for g in fno.DISPLAY_GROUPS if g in by_sector]
    if "Other" in by_sector:
        sector_options.append("Other")

    searching = bool(search_text.strip())
    if not searching:
        # Sector tiles, then stock tiles within the picked sector - never
        # all stocks flattened into one row (Adarsh's explicit direction,
        # 15 Oct 2026). The sector row stays visible even once a sector is
        # picked, so switching sectors is one click, not a back button.
        st.markdown("<div class='chip-label' style='margin-top:10px;'>Sector</div>",
                    unsafe_allow_html=True)
        sector = st.pills(
            "sentiment_sector", sector_options, key="sentiment_sector",
            label_visibility="collapsed", default=sector_options[0],
        )
        sector = sector or sector_options[0]
        candidates = by_sector.get(sector, [])
        picker_label = f"{sector} stocks"
        # Per-sector widget key: a stock picked under one sector stays
        # remembered if you come back to it, and switching sectors can
        # never hand st.pills a prior selection that isn't in its new
        # option list.
        picker_key = f"sentiment_symbol_{sector}"
    else:
        candidates = sorted(
            s for s in symbols if search_text.strip().upper() in s.upper()
        )
        picker_label = "Search results"
        picker_key = "sentiment_symbol_search"

    st.markdown(f"<div class='chip-label' style='margin-top:10px;'>{picker_label}</div>",
                unsafe_allow_html=True)
    if not candidates:
        empty_text = (
            f"No match for \"{search_text.strip()}\" among stocks with sentiment data."
            if searching else
            "No sentiment data stored for this sector yet."
        )
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:6px;'>{empty_text}</div>",
            unsafe_allow_html=True,
        )
        return

    symbol = st.pills(
        picker_key, candidates, key=picker_key,
        label_visibility="collapsed", default=candidates[0],
    )
    symbol = symbol or candidates[0]

    rows = get_earnings_call_sentiment(symbol)
    moves = _moves_by_quarter(symbol)
    if not rows:
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:10px;'>"
            f"No stored quarters for {symbol}.</div>",
            unsafe_allow_html=True,
        )
        return

    # Comparison identity, same convention as the Chart tab's two-series
    # mode: series 1 (management) solid P.T1, series 2 (Q&A) dashed P.T2 -
    # never direction-coloured (P.UP/P.DOWN), since tone isn't a change
    # value the way a price is.
    C1, C2 = P.T1, P.T2
    x = [_fmt_quarter(r["quarter_end"]) for r in rows]
    mgmt_y = [r["management_confidence"] for r in rows]
    qa_y = [r["qa_tone"] for r in rows]

    summary = (
        f"<div style='display:flex;gap:24px;margin:4px 0 10px 0;align-items:center;"
        f"flex-wrap:wrap;'>"
        f"<span style='display:inline-flex;align-items:center;gap:8px;'>"
        f"<span style='width:16px;height:0;border-top:2px solid {C1};"
        f"display:inline-block;'></span>"
        f"<span style='font-size:13px;color:{P.T1};font-weight:500;'>Management</span>"
        f"</span>"
        f"<span style='display:inline-flex;align-items:center;gap:8px;'>"
        f"<span style='width:16px;height:0;border-top:2px dashed {C2};"
        f"display:inline-block;'></span>"
        f"<span style='font-size:13px;color:{P.T1};font-weight:500;'>Analyst Q&amp;A</span>"
        f"</span>"
        f"</div>"
    )
    st.markdown(summary, unsafe_allow_html=True)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=mgmt_y, mode="lines+markers",
        line=dict(color=C1, width=1.8), marker=dict(size=5),
        hovertemplate="<b>%{x}</b><br>Management: %{y:+.1f}<extra></extra>",
        name="Management",
    ))
    fig.add_trace(go.Scatter(
        x=x, y=qa_y, mode="lines+markers",
        line=dict(color=C2, width=1.8, dash="dash"), marker=dict(size=5),
        hovertemplate="<b>%{x}</b><br>Analyst Q&amp;A: %{y:+.1f}<extra></extra>",
        name="Analyst Q&A",
    ))
    fig.update_layout(
        height=300,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=P.SURFACE,
        plot_bgcolor=P.SURFACE,
        font=dict(family="Inter, Arial, sans-serif"),
        showlegend=False,
        xaxis=dict(showgrid=False, showline=False, tickfont=dict(size=12, color=P.T2)),
        yaxis=dict(
            title=dict(text="Tone (-2 to +2)", font=dict(size=12, color=P.T2)),
            showgrid=True, gridcolor=P.GRID, gridwidth=1, zeroline=True,
            zerolinecolor=P.GRID, range=[-2.2, 2.2],
            tickfont=dict(size=12, color=P.T2),
        ),
        hoverlabel=dict(bgcolor=P.TOOLTIP_BG, bordercolor=P.BORDER,
                        font=dict(size=12, color=P.T1, family="Inter, Arial, sans-serif")),
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    st.markdown("<div class='chip-label' style='margin-top:10px;'>By quarter</div>",
                unsafe_allow_html=True)
    table_rows = "".join(
        f"<tr style='border-bottom:1px solid {P.BORDER};'>"
        f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
        f"border:none;{TABULAR}'>{_fmt_quarter(r['quarter_end'])}</td>"
        f"<td style='padding:6px 16px 6px 0;color:{_move_color(moves.get(r['quarter_end']), P)};"
        f"font-size:13px;white-space:nowrap;border:none;{TABULAR}'>"
        f"{_move_text(moves.get(r['quarter_end']))}</td>"
        f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;"
        f"border:none;{TABULAR}'>{_tone_text(r['management_confidence'])}</td>"
        f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;"
        f"border:none;{TABULAR}'>{_tone_text(r['overall_tone'])}</td>"
        f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;"
        f"border:none;{TABULAR}'>{_tone_text(r['qa_tone'])}</td>"
        f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
        f"border:none;'>{r['guidance'] or '-'}</td>"
        f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
        f"border:none;'>{r['analyst_tone'] or '-'}</td>"
        f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
        f"border:none;{TABULAR}'>{r['evasiveness_count'] if r['evasiveness_count'] is not None else '-'}</td>"
        f"<td style='padding:6px 16px 6px 0;color:{P.T1 if r['qa_divergence_flag'] else P.T2};"
        f"font-weight:{600 if r['qa_divergence_flag'] else 400};"
        f"font-size:13px;white-space:nowrap;border:none;'>{'Yes' if r['qa_divergence_flag'] else 'No'}</td>"
        f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
        f"border:none;{TABULAR}'>{r['lm_positive']:.1f}</td>"
        f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
        f"border:none;{TABULAR}'>{r['lm_negative']:.1f}</td>"
        f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
        f"border:none;{TABULAR}'>{r['lm_uncertainty']:.1f}</td>"
        f"<td style='padding:6px 0;color:{P.T2};font-size:13px;border:none;'>"
        f"<a href='{r['transcript_source_url']}' target='_blank' rel='noopener' "
        f"style='color:inherit;text-decoration:none;border-bottom:1px dotted {P.T3};'>"
        f"transcript</a></td>"
        f"</tr>"
        for r in reversed(rows)  # most recent quarter first in the table
    )
    _cols = ["Quarter", "Price move", "Mgmt confidence", "Overall tone", "Q&A tone",
             "Guidance", "Analyst tone", "Evasive Qs", "QA divergence",
             "LM +", "LM -", "LM uncertain", "Source"]
    header = "<tr style='border-bottom:1px solid {b};'>{cells}</tr>".format(
        b=P.BORDER,
        cells="".join(
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>{c}</th>"
            for c in _cols
        ),
    )
    st.markdown(
        f"<div style='overflow-x:auto;'>"
        f"<table style='border-collapse:collapse;border:none;width:100%;margin-top:6px;'>"
        f"{header}{table_rows}</table></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='color:{P.T3};font-size:12px;margin-top:8px;'>"
        f"Price move is the signed close-to-close market reaction around that quarter's "
        f"result, from the Expected-move tracker's own stored prints (Events tab) - a "
        f"settled, directional change, not the magnitude figure that tracker's cohort stats "
        f"use. Shown as – for a quarter the Expected-move job hasn't reached yet, or one "
        f"whose reaction window isn't usable (never a fabricated 0%). "
        f"Mgmt confidence, overall tone, and Q&amp;A tone: -2 (very negative) to +2 (very "
        f"positive), each Gemini-extracted separately with a quoted line backing the "
        f"judgment. Analyst tone is a separate category (constructive/neutral/skeptical) "
        f"covering the same Q&amp;A session; evasive questions and a prepared-vs-Q&amp;A "
        f"divergence flag are the other Q&amp;A signals, shown as their own columns. LM "
        f"columns are Loughran-McDonald word counts per 1000 words - deterministic, no "
        f"model involved.</div>",
        unsafe_allow_html=True,
    )
