"""
Shared rendering pieces: the small-multiples price card used by five tabs, and
the chart behind it.
"""

import plotly.graph_objects as go
import streamlit as st

from constituents import CONSTITUENTS
from data_loader import get_price_history
from formatting import signed_pct
from periods import PERIOD_LABELS
from theme import TABULAR, palette


def period_label(text):
    """The small uppercase label that sits above a control."""
    P = palette()
    return ("<div style='font-size:12px;color:" + P.T2 + ";text-transform:uppercase;"
            "letter-spacing:0.08em;margin:0 0 2px 0;'>" + text + "</div>")


def price_chart(label, ticker, period, height=220):
    P = palette()
    df = get_price_history(ticker, period=period)
    df = df.dropna(subset=["Close"])
    if df.empty:
        return None, None, None, None

    last  = df["Close"].iloc[-1]
    first = df["Close"].iloc[0]
    pct   = (last / first - 1) * 100
    latest_date = df.index[-1].strftime("%d %b %Y")

    spread = df["Close"].max() - df["Close"].min()
    ymin = df["Close"].min() - spread * 0.1
    ymax = df["Close"].max() + spread * 0.1

    # Multi-year ranges need the year on x-ticks; shorter ranges don't
    tickformat = "%b %Y" if period in ("1y", "3y") else "%d %b"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df.index, y=df["Close"],
        mode="lines",
        line=dict(color=(P.UP if pct >= 0 else P.DOWN), width=1.5),
        hovertemplate="<b>%{x|%d %b %Y}</b><br>%{y:,.2f}<extra></extra>",
        name=label,
    ))
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=P.SURFACE,
        plot_bgcolor=P.SURFACE,
        font=dict(family="Inter, Arial, sans-serif"),
        xaxis=dict(showgrid=False, showline=False, tickfont=dict(size=12, color=P.T2),
                   tickformat=tickformat),
        yaxis=dict(showgrid=True, gridcolor=P.GRID, gridwidth=1, showline=False, zeroline=False,
                   tickfont=dict(size=12, color=P.T2), side="right", range=[ymin, ymax]),
        showlegend=False,
        hoverlabel=dict(bgcolor=P.TOOLTIP_BG, bordercolor=P.BORDER,
                        font=dict(size=12, color=P.T1, family="Inter, Arial, sans-serif")),
    )
    return fig, last, pct, latest_date


def _constituents_popover(label, members):
    P = palette()
    with st.popover(label):
        rows = "".join(
            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
            f"<td style='padding:4px 18px 4px 0;color:{P.T1};font-size:13px;"
            f"white-space:nowrap;border:none;'>{name}</td>"
            f"<td style='padding:4px 0;color:{P.T2};font-size:13px;"
            f"text-align:right;border:none;"
            f"font-variant-numeric:tabular-nums lining-nums;'>{w:.1f}%</td>"
            f"</tr>"
            for name, w in members
        )
        st.markdown(
            f"<div style='font-size:12px;color:{P.T2};text-transform:uppercase;"
            f"letter-spacing:0.05em;margin-bottom:6px;"
            f"padding-bottom:4px;border-bottom:1px solid {P.BORDER};'>Approximate weights</div>"
            f"<table style='border-collapse:collapse;border:none;'>{rows}</table>",
            unsafe_allow_html=True,
        )


def asset_card(label, ticker, period, decimal=2, height=220, value_size=28):
    P = palette()
    fig, last, pct, latest_date = price_chart(label, ticker, period, height)
    if fig is None:
        st.warning(f"No data — {label}")
        return

    color = P.UP if pct >= 0 else P.DOWN
    members = CONSTITUENTS.get(ticker)
    period_label = PERIOD_LABELS.get(period, period)

    with st.container(border=True):
        c_label, c_pct = st.columns([5, 3])
        with c_label:
            if members:
                _constituents_popover(label, members)
            else:
                st.markdown(
                    f"<span style='font-size:14px;color:{P.T1};font-weight:500;'>{label}</span>",
                    unsafe_allow_html=True,
                )
        with c_pct:
            st.markdown(
                f"<div style='text-align:right;padding-top:1px;'>"
                f"<span style='font-size:14px;color:{color};font-weight:600;{TABULAR}'>"
                f"{signed_pct(pct)}</span>"
                f"<span style='font-size:12px;color:{P.T2};padding-left:5px;'>{period_label}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        st.markdown(
            f"<div style='display:flex;align-items:baseline;gap:10px;margin:2px 0 4px 0;'>"
            f"<span style='font-size:{value_size}px;font-weight:600;color:{P.T1};"
            f"line-height:1.1;{TABULAR}'>{last:,.{decimal}f}</span>"
            f"<span style='font-size:12px;color:{P.T2};'>{latest_date}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def render_grid(assets, period, cols=3, decimal=2, height=220, value_size=28):
    columns = st.columns(cols)
    for i, (label, ticker) in enumerate(assets):
        with columns[i % cols]:
            asset_card(label, ticker, period, decimal, height, value_size)
