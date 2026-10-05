"""
Chart tab: one or two instruments over an explicit date range, with a Measure
tool for the change between two clicked points.
"""

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from formatting import (elapsed_label, format_price, price_decimals,
                        signed_pct, signed_price_change)
from instruments import display_name, pick_instrument
from periods import CHART_PRESETS, MAX_HISTORY_DAYS, range_history
from theme import TABULAR, palette


def render():
    P = palette()

    # Remember the plotted instruments across tab switches
    for k in ("_ct1", "_ct2"):
        if k not in st.session_state:
            st.session_state[k] = ""

    lbl = (f"<div style='font-size:12px;color:{P.T2};text-transform:uppercase;"
           f"letter-spacing:0.08em;margin:0 0 4px 0;'>{{}}</div>")

    # ── History range ─────────────────────────────────────────────────────────
    # One range with two ways to set it: a preset writes the dates, and editing a
    # date drops the preset. Only one of them ever governs the chart (spec).
    today    = date.today()
    earliest = today - timedelta(days=MAX_HISTORY_DAYS)

    if "chart_start" not in st.session_state:
        st.session_state.chart_start = today - timedelta(days=CHART_PRESETS["3M"])
        st.session_state.chart_end   = today

    def _apply_preset():
        choice = st.session_state.chart_period
        if choice:
            st.session_state.chart_start = today - timedelta(days=CHART_PRESETS[choice])
            st.session_state.chart_end   = today

    def _apply_dates():
        st.session_state.chart_period = None   # the dates govern now

    c1, c2, r2, r3 = st.columns(4)
    with c1:
        st.markdown(lbl.format("Instrument 1"), unsafe_allow_html=True)
        t1 = pick_instrument("chart_t1", placeholder="Search instruments",
                             default=st.session_state["_ct1"]) or ""
        st.session_state["_ct1"] = t1
    with c2:
        st.markdown(lbl.format("Instrument 2 (optional)"), unsafe_allow_html=True)
        t2 = pick_instrument("chart_t2", placeholder="Search instruments",
                             default=st.session_state["_ct2"]) or ""
        st.session_state["_ct2"] = t2
    with r2:
        st.markdown(lbl.format("Start"), unsafe_allow_html=True)
        start = st.date_input("s", key="chart_start", on_change=_apply_dates,
                              min_value=earliest, max_value=today,
                              format="DD/MM/YYYY", label_visibility="collapsed")
    with r3:
        st.markdown(lbl.format("End"), unsafe_allow_html=True)
        end = st.date_input("e", key="chart_end", on_change=_apply_dates,
                            min_value=earliest, max_value=today,
                            format="DD/MM/YYYY", label_visibility="collapsed")

    r1, r4 = st.columns([4, 2])
    with r1:
        # This control, not the global one, governs the chart below (spec)
        st.markdown(lbl.format("History — this chart"), unsafe_allow_html=True)
        chart_period_label = st.segmented_control(
            "cp", list(CHART_PRESETS), default="3M", label_visibility="collapsed",
            key="chart_period", on_change=_apply_preset,
        )
    with r4:
        # ── Measure ──────────────────────────────────────────────────────────
        # Click two points on the primary series to see the change between
        # them. A click replaces Plotly's selection outright (it does not
        # accumulate), so the two picked points live in session_state, not in
        # the widget's own return value — each new click is compared against
        # the last one processed (via its x/point_index "signature") so an
        # unrelated rerun doesn't get mistaken for a fresh click.
        st.markdown(lbl.format("&nbsp;"), unsafe_allow_html=True)
        measure_on = st.toggle("Measure", key="measure_on")

    if start > end:
        st.warning("Start date is after the end date — showing nothing. "
                   "Pick a start on or before the end.")
        t1 = ""   # nothing sensible to plot

    span_label = (chart_period_label if chart_period_label else
                  f"{start:%d %b %Y} – {end:%d %b %Y}")

    scope = (t1.strip(), t2.strip(), start, end)
    if st.session_state.get("measure_scope") != scope:
        # Instrument or range changed — stale points wouldn't exist in the new
        # frame, and switching the active series mid-measurement is undefined.
        st.session_state.measure_scope = scope
        st.session_state.measure_a = None
        st.session_state.measure_b = None
        st.session_state.measure_last_sig = None
    if not measure_on:
        st.session_state.measure_a = None
        st.session_state.measure_b = None
        st.session_state.measure_last_sig = None

    if t1:
        df1 = range_history(t1.strip(), start, end)

        df2 = None
        if t2:
            df2 = range_history(t2.strip(), start, end)
            if df2.empty:
                st.warning(f"No data for '{t2.strip()}' between {start:%d %b %Y} "
                           f"and {end:%d %b %Y}")
                df2 = None

        if df1.empty:
            st.warning(f"No data for '{t1.strip()}' between {start:%d %b %Y} "
                       f"and {end:%d %b %Y}")
        else:
            has_t2 = df2 is not None and not df2.empty
            # Series 2's close aligned to series 1's dates (nearest match) —
            # needed both to tag a click with instrument B's value at that
            # date, and later to build the hover tooltip.
            series2_at_t1 = df2["Close"].reindex(df1.index, method="nearest") if has_t2 else None

            # ── Measure: process the click from the previous render ──────────
            # Streamlit syncs the plotly widget's selection into session_state
            # before this script runs, so the click that triggered this rerun
            # is already sitting in session_state[MEASURE_KEY] — read it here,
            # before the chart (and its vrect) is built. The click-target trace
            # is always added last (see the figure-building section below) so
            # it renders on top and actually receives the click; its curve
            # number shifts by one when a second series is plotted.
            MEASURE_KEY = "chart_measure_plot"
            measure_curve = 2 if has_t2 else 1
            if measure_on:
                pending = st.session_state.get(MEASURE_KEY)
                pts = [p for p in (pending or {}).get("selection", {}).get("points", [])
                       if p.get("curve_number") == measure_curve]
                if pts:
                    p = pts[-1]
                    sig = (p.get("point_index"), p.get("x"))
                    if sig != st.session_state.measure_last_sig:
                        st.session_state.measure_last_sig = sig
                        idx = p.get("point_index")
                        if idx is None or not (0 <= idx < len(df1)):
                            # Snap-to-nearest fallback if point_index is missing
                            idx = df1.index.get_indexer(
                                [pd.Timestamp(p["x"])], method="nearest")[0]
                        picked = {"date": df1.index[idx],
                                  "close": float(df1["Close"].iloc[idx])}
                        if has_t2:
                            picked["close2"] = float(series2_at_t1.iloc[idx])

                        a, b = st.session_state.measure_a, st.session_state.measure_b
                        if a is None:
                            st.session_state.measure_a = picked
                        elif b is None and picked["date"] != a["date"]:
                            st.session_state.measure_b = picked
                        elif b is not None:
                            # A third click starts a fresh pair
                            st.session_state.measure_a = picked
                            st.session_state.measure_b = None

            ma, mb = st.session_state.measure_a, st.session_state.measure_b
            measure_ready = ma is not None and mb is not None
            measure_armed = ma is not None and mb is None
            if measure_ready and mb["date"] < ma["date"]:
                ma, mb = mb, ma   # always chronological, regardless of click order

            # Comparison identity: series 1 black solid, series 2 gray dashed
            C1, C2 = P.T1, P.T2
            name1, name2 = t1.strip(), (t2.strip() if t2 else "")
            disp1, disp2 = display_name(name1), (display_name(name2) if name2 else "")
            m_dec = price_decimals(name1)
            m2_dec = price_decimals(name2) if has_t2 else None
            pct1 = (df1["Close"].iloc[-1] / df1["Close"].iloc[0] - 1) * 100
            pct1_color = P.UP if pct1 >= 0 else P.DOWN

            cp_label = span_label

            # ── Summary bar ───────────────────────────────────────────────────
            summary = (
                f"<div style='display:flex;gap:24px;margin-bottom:12px;align-items:center;"
                f"flex-wrap:wrap;'>"
                f"<span style='display:inline-flex;align-items:center;gap:8px;'>"
                f"<span style='width:16px;height:0;border-top:2px solid {C1};"
                f"display:inline-block;'></span>"
                f"<span style='font-size:14px;color:{P.T1};font-weight:500;'>{disp1}</span>"
                f"<span style='font-size:14px;color:{pct1_color};font-weight:600;{TABULAR}'>"
                f"{signed_pct(pct1)}</span>"
                f"<span style='font-size:12px;color:{P.T2};'>{cp_label}</span>"
                f"</span>"
            )
            if has_t2:
                pct2 = (df2["Close"].iloc[-1] / df2["Close"].iloc[0] - 1) * 100
                pct2_color = P.UP if pct2 >= 0 else P.DOWN
                summary += (
                    f"<span style='display:inline-flex;align-items:center;gap:8px;'>"
                    f"<span style='width:16px;height:0;border-top:2px dashed {C2};"
                    f"display:inline-block;'></span>"
                    f"<span style='font-size:14px;color:{P.T1};font-weight:500;'>{disp2}</span>"
                    f"<span style='font-size:14px;color:{pct2_color};font-weight:600;{TABULAR}'>"
                    f"{signed_pct(pct2)}</span>"
                    f"<span style='font-size:12px;color:{P.T2};'>{cp_label}</span>"
                    f"</span>"
                )
            summary += "</div>"
            st.markdown(summary, unsafe_allow_html=True)

            # ── Measure readout ───────────────────────────────────────────────
            if measure_ready:
                m_chg = mb["close"] - ma["close"]
                m_pct = ((mb["close"] / ma["close"]) - 1) * 100 if ma["close"] else 0.0
                m_color = P.UP if m_pct >= 0 else P.DOWN
                m_dir = "up" if m_pct >= 0 else "down"
                pos_a, pos_b = df1.index.get_loc(ma["date"]), df1.index.get_loc(mb["date"])
                m_bars = pos_b - pos_a + 1
                m_days = (mb["date"] - ma["date"]).days

                row2 = ""
                if has_t2 and "close2" in ma and "close2" in mb:
                    m_chg2 = mb["close2"] - ma["close2"]
                    m_pct2 = (((mb["close2"] / ma["close2"]) - 1) * 100
                              if ma["close2"] else 0.0)
                    m_color2 = P.UP if m_pct2 >= 0 else P.DOWN
                    row2 = (
                        f"<span style='font-size:12px;color:{P.T2};'>{disp2}</span>"
                        f"<span style='font-size:19px;font-weight:700;color:{m_color2};{TABULAR}'>"
                        f"{signed_pct(m_pct2)}</span>"
                        f"<span style='font-size:14px;font-weight:600;color:{m_color2};{TABULAR}'>"
                        f"{signed_price_change(name2, m_chg2, m2_dec)}</span>"
                    )

                st.markdown(
                    f"<div style='display:flex;gap:26px;align-items:center;flex-wrap:wrap;"
                    f"border:1px solid {P.BORDER};padding:10px 14px;margin-bottom:12px;'>"
                    f"<span style='font-size:11px;color:{P.T2};text-transform:uppercase;"
                    f"letter-spacing:0.08em;'>Measured, {m_dir}</span>"
                    f"<span style='font-size:12px;color:{P.T2};'>{disp1}</span>"
                    f"<span style='font-size:19px;font-weight:700;color:{m_color};{TABULAR}'>"
                    f"{signed_pct(m_pct)}</span>"
                    f"<span style='font-size:14px;font-weight:600;color:{m_color};{TABULAR}'>"
                    f"{signed_price_change(t1.strip(), m_chg, m_dec)}</span>"
                    f"{row2}"
                    f"<span style='font-size:13px;color:{P.T2};{TABULAR}'>{m_bars} bars</span>"
                    f"<span style='font-size:13px;color:{P.T2};{TABULAR}'>{elapsed_label(m_days)}</span>"
                    f"<span style='font-size:12px;color:{P.T2};{TABULAR}'>"
                    f"{ma['date']:%d %b %Y} → {mb['date']:%d %b %Y}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            elif measure_armed:
                st.markdown(
                    f"<div style='font-size:12px;color:{P.T2};margin-bottom:12px;'>"
                    f"Measure — hover {disp1} to preview the change, click to set "
                    f"the end point.</div>",
                    unsafe_allow_html=True,
                )
            elif measure_on:
                st.markdown(
                    f"<div style='font-size:12px;color:{P.T2};margin-bottom:12px;'>"
                    f"Measure — click a start point, then an end point, on "
                    f"{disp1}.</div>",
                    unsafe_allow_html=True,
                )

            # ── Chart ─────────────────────────────────────────────────────────
            # Years appear once the range spans more than one (spec)
            tickformat_x = "%b %Y" if (end - start).days > 180 else "%d %b"

            fig = go.Figure()

            # When Measure is on, the line itself skips hover/click hit-testing.
            # Plotly's "closest" hovermode resolves ties between coincident
            # points by trace order regardless of z-order, so with both the
            # line and the click-target markers sitting on the same data, the
            # line would always win the hit test and clicks could never reach
            # the markers. Hover info moves to the markers trace instead.
            fig.add_trace(go.Scatter(
                x=df1.index, y=df1["Close"],
                name=disp1, mode="lines",
                line=dict(color=C1, width=1.8),
                yaxis="y1",
                hoverinfo="skip" if measure_on else None,
                hovertemplate=(None if measure_on else
                               "<b>%{x|%d %b %Y}</b><br>%{y:,.4f}<extra>" + disp1 + "</extra>"),
            ))

            if measure_ready:
                fig.add_vrect(
                    x0=ma["date"], x1=mb["date"],
                    fillcolor=(P.T1 if m_pct >= 0 else P.DOWN),
                    opacity=0.10, line_width=0,
                )

            yaxis2_cfg = None
            if has_t2:
                # Streamlit's selection capture drops the whole click when more
                # than one trace reports a point for it, which happens whenever
                # a second trace is also hoverable under hovermode "x" — so
                # during Measure, series 2 skips hit-testing just like series 1,
                # and its price rides along in the click-target's own
                # hovertemplate instead (aligned to series 1's dates, nearest
                # match) rather than getting its own tooltip.
                fig.add_trace(go.Scatter(
                    x=df2.index, y=df2["Close"],
                    name=disp2, mode="lines",
                    line=dict(color=C2, width=1.8, dash="dash"),
                    yaxis="y2",
                    hoverinfo="skip" if measure_on else None,
                    hovertemplate=(None if measure_on else
                                   "<b>%{x|%d %b %Y}</b><br>%{y:,.4f}<extra>" + disp2 + "</extra>"),
                ))
                yaxis2_cfg = dict(
                    title=dict(text=disp2, font=dict(size=12, color=P.T2)),
                    tickfont=dict(size=12, color=P.T2), showgrid=False,
                    zeroline=False, overlaying="y", side="right",
                    tickformat=",.0f",   # match left axis; never mix raw with k
                )

            if measure_on:
                # Click target. The line (above) skips hit-testing, so this is
                # the only trace Plotly can match a click or hover against.
                # Its position must match measure_curve, computed above from
                # the same has_t2 before any click on it could be processed.
                assert len(fig.data) == measure_curve
                if measure_armed:
                    # Point A is picked, point B isn't yet — mirror TradingView's
                    # live readout by showing the change from A to whatever the
                    # cursor is over, continuously, with no server round-trip
                    # (the hover text is computed client-side from customdata
                    # baked in at render time; moving the mouse never reruns
                    # the app). With a second instrument, point A also pinned
                    # its value, so both series show their own % change from
                    # the same start date as the cursor moves.
                    base = ma["close"]
                    if has_t2 and "close2" in ma:
                        base2 = ma["close2"]
                        customdata = [
                            (signed_pct((c / base - 1) * 100),
                             signed_pct((c2 / base2 - 1) * 100) if base2 else "+0.00%")
                            for c, c2 in zip(df1["Close"], series2_at_t1)
                        ]
                        hovertemplate = ("<b>%{x|%d %b %Y}</b><br>" + disp1 +
                                          ": %{customdata[0]}<br>" +
                                          disp2 + ": %{customdata[1]}<extra></extra>")
                    else:
                        customdata = [
                            (signed_pct((c / base - 1) * 100),
                             signed_price_change(name1, c - base, m_dec))
                            for c in df1["Close"]
                        ]
                        hovertemplate = ("<b>%{x|%d %b %Y}</b><br>%{customdata[0]}<br>"
                                          "%{customdata[1]} from start<extra></extra>")
                elif has_t2:
                    customdata = [format_price(name2, c2, m2_dec) for c2 in series2_at_t1]
                    hovertemplate = ("<b>%{x|%d %b %Y}</b><br>%{y:,.4f}<br>"
                                      + disp2 + ": %{customdata}<extra>" + disp1 + "</extra>")
                else:
                    customdata = None
                    hovertemplate = ("<b>%{x|%d %b %Y}</b><br>%{y:,.4f}<extra>"
                                      + disp1 + "</extra>")
                fig.add_trace(go.Scatter(
                    x=df1.index, y=df1["Close"],
                    mode="markers", marker=dict(size=7, color=C1, opacity=0.02),
                    customdata=customdata,
                    hovertemplate=hovertemplate,
                    showlegend=False, name="",
                ))

            if ma is not None:
                # Mark the picked start point so it stays visible once the
                # cursor (and the live preview) move away from it. Added after
                # the click target so it doesn't shift measure_curve's index.
                fig.add_vline(x=ma["date"], line=dict(color=P.SELECTED, width=1, dash="dot"))
                fig.add_trace(go.Scatter(
                    x=[ma["date"]], y=[ma["close"]],
                    mode="markers", marker=dict(size=9, color=P.SELECTED,
                                                 line=dict(color=P.BG, width=1.5)),
                    hoverinfo="skip", showlegend=False, name="",
                ))

            fig.update_layout(
                height=460,
                margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor=P.BG, plot_bgcolor=P.BG,
                font=dict(family="Inter, Arial, sans-serif"),
                xaxis=dict(showgrid=False, showline=False,
                           tickfont=dict(size=12, color=P.T2), tickformat=tickformat_x),
                yaxis=dict(title=dict(text=disp1, font=dict(size=12, color=P.T2)),
                           tickfont=dict(size=12, color=P.T2), showgrid=True,
                           gridcolor=P.GRID, zeroline=False, side="left",
                           tickformat=",.0f"),
                yaxis2=yaxis2_cfg,
                hoverlabel=dict(bgcolor=P.TOOLTIP_BG, bordercolor=P.BORDER,
                                font=dict(size=12, color=P.T1,
                                          family="Inter, Arial, sans-serif")),
                showlegend=False,
                clickmode="event+select" if measure_on else "event",
                # "x": match the nearest point by date alone, so hover/click
                # don't also require landing within pixels of the line's y —
                # needed for the live preview, and for picking a point without
                # a second series' line stealing the hit test.
                hovermode="x" if measure_on else "closest",
            )
            if measure_on:
                st.plotly_chart(fig, width="stretch", key=MEASURE_KEY,
                                on_select="rerun", selection_mode=("points",),
                                config={"displayModeBar": False})
            else:
                st.plotly_chart(fig, width="stretch",
                                config={"displayModeBar": False})
    else:
        st.markdown(
            f"<div style='color:{P.T2};text-align:center;padding:80px 0;'>"
            f"Enter a ticker above to plot it.</div>",
            unsafe_allow_html=True,
        )
