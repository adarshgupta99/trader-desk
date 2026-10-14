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

from events_db import get_earnings_call_sentiment, get_earnings_call_sentiment_symbols
from theme import TABULAR, palette


def _fmt_quarter(iso_date):
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    return f"QE {d.strftime('%b %Y')}"


def _tone_text(v):
    return f"{v:+.1f}" if v is not None else "-"


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

    st.markdown("<div class='chip-label'>Symbol</div>", unsafe_allow_html=True)
    symbol = st.pills("sentiment_symbol", symbols, key="sentiment_symbol",
                      label_visibility="collapsed", default=symbols[0])
    symbol = symbol or symbols[0]

    rows = get_earnings_call_sentiment(symbol)
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
    mgmt_y = [r["mgmt_tone"] for r in rows]
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
        f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;"
        f"border:none;{TABULAR}'>{_tone_text(r['mgmt_tone'])}</td>"
        f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;"
        f"border:none;{TABULAR}'>{_tone_text(r['qa_tone'])}</td>"
        f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
        f"border:none;'>{r['guidance'] or '-'}</td>"
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
    header = (
        f"<tr style='border-bottom:1px solid {P.BORDER};'>"
        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
        f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Quarter</th>"
        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
        f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Mgmt tone</th>"
        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
        f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Q&amp;A tone</th>"
        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
        f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Guidance</th>"
        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
        f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>LM +</th>"
        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
        f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>LM -</th>"
        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
        f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>LM uncertain</th>"
        f"<th style='padding:4px 0 6px 0;color:{P.T3};font-size:12px;text-align:left;"
        f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Source</th>"
        f"</tr>"
    )
    st.markdown(
        f"<div style='overflow-x:auto;'>"
        f"<table style='border-collapse:collapse;border:none;width:100%;margin-top:6px;'>"
        f"{header}{table_rows}</table></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='color:{P.T3};font-size:12px;margin-top:8px;'>"
        f"Tone: -2 (very negative) to +2 (very positive), Gemini-extracted separately for "
        f"management's prepared remarks and the analyst Q&amp;A. LM columns are Loughran-"
        f"McDonald word counts per 1000 words - deterministic, no model involved.</div>",
        unsafe_allow_html=True,
    )
