"""
Events tab: upcoming F&O board meetings and declared corporate actions, read
from events.db. Two different things, so two subtabs - a board meeting is a
*scheduled* agenda with no outcome yet, while a corporate action is already
declared with a fixed amount and ex-date.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from analytics.sparkline import build_blocks, qoq_pct, tooltip_text
from events_db import (IST, get_all_expected_moves, get_earnings_estimates,
                       get_earnings_history_quarters, get_eps_history,
                       get_last_success, get_live_estimate_preview,
                       get_upcoming_events, match_eps_row)
from theme import TABULAR, palette
from watchlists import (PRESET_NAMES, WATCHLIST_PRESETS, get_custom_lists,
                        get_list_items)

SUBTYPE_LABELS = {
    "dividend": "Dividend", "split": "Split", "bonus": "Bonus",
    "rights": "Rights", "buyback": "Buyback", "fundraise": "Fundraise",
    "agm": "AGM", "results": "Results", "other": "Other",
}

#: get_live_estimate_preview is a live, synchronous yfinance call - cheap
#: once cached (6h TTL, plus a disk cache that survives a process restart -
#: see live_data_cache.py) but the first render after that window expires
#: pays the fetch cost for every uncached symbol. Fetching them concurrently
#: (instead of the flat per-render cap this used to be - 8 Oct 2026, dropped
#: after it silently hid rows that didn't fit the cap) bounds the wall time
#: to roughly one call's latency instead of the sum of all of them, with
#: every row still getting a genuine attempt.
PREVIEW_MAX_WORKERS = 5


def _prefetch_concurrent(fn, items, max_workers=PREVIEW_MAX_WORKERS):
    """Runs fn(item) for each item in items concurrently, returning
    {item: result}. items is deduplicated first since get_live_estimate_preview
    is per-symbol, not per-row, and several rows can share a symbol."""
    items = sorted(set(items))
    if not items:
        return {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        return dict(zip(items, pool.map(fn, items)))


def render():
    P = palette()

    last_success_at, last_error = get_last_success()
    if last_success_at:
        updated_label = (
            datetime.fromisoformat(last_success_at).astimezone(IST).strftime("%d %b %Y, %H:%M IST")
        )
        stale = datetime.now(timezone.utc) - datetime.fromisoformat(last_success_at) > timedelta(days=3)
    else:
        updated_label = "never"
        stale = True

    st.markdown(
        f"<div style='color:{P.T2};font-size:12px;margin-bottom:10px;'>Last updated: {updated_label}</div>",
        unsafe_allow_html=True,
    )
    if stale:
        msg = ("Event data has never been fetched." if not last_success_at
               else "Event data hasn't updated in over 3 days.")
        if last_error:
            msg += f" Last error: {last_error}"
        st.warning(msg)

    st.markdown("<div class='chip-label'>Scope</div>", unsafe_allow_html=True)
    scope = st.pills("ev_scope", ["All F&O", "My watchlist"], key="ev_scope",
                      label_visibility="collapsed", default="All F&O")

    symbols = None
    if scope == "My watchlist":
        all_tickers = set()
        for name in PRESET_NAMES:
            all_tickers.update(t for t, _ in WATCHLIST_PRESETS[name])
        for l in get_custom_lists():
            all_tickers.update(it["ticker"] for it in get_list_items(l["id"]))
        symbols = {t.split(".")[0] for t in all_tickers}

    events_df = get_upcoming_events(symbols=symbols)
    estimates_df = get_earnings_estimates(symbols=symbols)
    estimates_by_key = {
        (r.symbol, r.event_date): r for r in estimates_df.itertuples()
    }
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    saw_preview = False
    preview_map = {}
    eps_map = {}

    def _fmt_date(s):
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d %b %Y") if s else "—"

    def _num(v):
        """A real number, or None. Guards the documented pandas trap: a
        NULL read out of a mixed-type column arrives as float('nan'),
        which is NOT None and IS truthy, so `if v is not None` lets it
        through and it reaches a format string as 'nan'."""
        return v if isinstance(v, (int, float)) and pd.notna(v) else None

    def _fmt_eps(v):
        v = _num(v)
        return f"{v:.2f}" if v is not None else None

    def _fmt_rev_cr(v):
        return f"{v / 1e7:,.2f}" if v is not None else None

    def _fmt_cr0(v):
        """Rs crore, no decimals - the HISTORY 6Q sparkline's latest-value
        print, a coarser format than the 2-decimal Est./Actual columns
        since this one is about shape, not precision."""
        return f"{v / 1e7:,.0f}" if v is not None else None

    def _has_frozen_estimate(symbol, event_date):
        """Whether a frozen T-1 revenue snapshot exists - EPS no longer goes
        through this table, so only revenue_estimate matters here now."""
        est = estimates_by_key.get((symbol, event_date))
        return est is not None and est.revenue_estimate is not None

    def _estimate_cells(symbol, event_date):
        """Returns the 3 <td> cells for Est. Rev / Est. EPS / Analysts, for
        one Calendar row. None values from a missing snapshot or a NULL
        estimate both render as "no estimate" - the row itself is never
        dropped either way.

        Revenue still goes through the frozen T-1 snapshot (estimates_by_key,
        from earnings_estimates): if there's no frozen row yet and the event
        hasn't passed, falls back to a live, unfrozen consensus preview
        (secondary colour, "~" prefix) from preview_map - replaced by the
        real frozen row once the T-1 job runs, never stored. Includes
        event_date == today: the T-1 job should have frozen that row
        yesterday, but if it hasn't run yet there's no reason to show
        nothing when a live number is available.

        EPS estimate comes from get_eps_history()/eps_map instead - Yahoo's
        own per-quarter table, live, no freezing needed on our side (see
        events_db.get_eps_history). Not yet reported renders the same "~"
        preview styling as an unfrozen revenue number - eps_is_preview below
        is still computed from whether Yahoo has an actual for the quarter
        yet, even though the actual itself is no longer shown here (see
        the Past earnings tab for NSE's own actuals, both bases, no
        comparison to the estimate - 13 Oct 2026, Adarsh's direction, which
        this Calendar column dropped for consistency).

        No standalone Analysts column any more (13 Oct 2026) - it was
        showing n_analysts_eps next to the REVENUE figure, a real bug:
        TCS read "20 analysts" beside a revenue consensus actually built
        from 30. Each estimate's own analyst count is a hover tooltip on
        that cell instead - n_analysts_rev on Est. Rev, n_analysts_eps on
        Est. EPS - so the count can never again be attached to the wrong
        figure.

        preview_map and eps_map are prefetched concurrently for every symbol
        that needs them before any row is built - see render()."""
        nonlocal saw_preview
        est = estimates_by_key.get((symbol, event_date))
        has_frozen = _has_frozen_estimate(symbol, event_date)

        rev_val = n_rev_val = None
        is_preview = False
        if has_frozen:
            rev_val, n_rev_val = est.revenue_estimate, est.n_analysts_rev
        elif event_date >= today_str:
            preview = preview_map.get(symbol)
            if preview and preview.get("revenue_estimate") is not None:
                rev_val = preview.get("revenue_estimate")
                n_rev_val = preview.get("n_analysts_rev")
                is_preview = True
                saw_preview = True

        eps_row = match_eps_row(eps_map.get(symbol), event_date)
        eps_est_val = eps_row["eps_estimate"] if eps_row is not None else None
        # Not rendered any more (Actual EPS/Surprise % dropped from Calendar),
        # but still needed to tell "Yahoo has an estimate, quarter hasn't
        # reported yet" apart from "Yahoo has a real reported quarter" for
        # the Est. EPS cell's own preview styling below.
        eps_is_preview = eps_row is not None and eps_row["eps_actual"] is None
        # EPS analyst count: the frozen snapshot's n_analysts_eps once T-1
        # has run, same live preview otherwise - same has_frozen/preview
        # split as revenue above, just the EPS-specific field from each.
        if has_frozen:
            n_eps_val = est.n_analysts_eps
        elif event_date >= today_str:
            preview = preview_map.get(symbol)
            n_eps_val = preview.get("n_analysts_eps") if preview else None
        else:
            n_eps_val = None

        def _analysts_title(n):
            n = _num(n)
            if n is None:
                return ""
            n = int(n)
            plural = "s" if n != 1 else ""
            return f" title='{n} analyst{plural}'"

        if rev_val is None:
            rev_cell = (
                f"<td style='padding:6px 16px 6px 0;color:{P.T3};font-size:13px;"
                f"white-space:nowrap;border:none;'>no estimate</td>"
            )
        else:
            val_color = P.T2 if is_preview else P.T1
            rev_text = f"{'~' if is_preview else ''}{_fmt_rev_cr(rev_val)}"
            rev_cell = (
                f"<td style='padding:6px 16px 6px 0;color:{val_color};font-size:13px;"
                f"white-space:nowrap;border:none;cursor:help;{TABULAR}'"
                f"{_analysts_title(n_rev_val)}>{rev_text}</td>"
            )

        if eps_est_val is None:
            eps_cell = (
                f"<td style='padding:6px 0;color:{P.T3};font-size:13px;"
                f"white-space:nowrap;border:none;'>no estimate</td>"
            )
        else:
            eps_val_color = P.T2 if eps_is_preview else P.T1
            eps_text = f"{'~' if eps_is_preview else ''}{_fmt_eps(eps_est_val)}"
            eps_cell = (
                f"<td style='padding:6px 0;color:{eps_val_color};font-size:13px;"
                f"white-space:nowrap;border:none;cursor:help;{TABULAR}'"
                f"{_analysts_title(n_eps_val)}>{eps_text}</td>"
            )

        return rev_cell + eps_cell

    def _quarter_label(iso_date):
        d = datetime.strptime(iso_date, "%Y-%m-%d")
        return f"QE {d.strftime('%b %Y')}"

    def _history_sparkline_cell(symbol, subtype):
        """One <td> for the HISTORY 6Q column: two stacked sparkline
        lines (REV, PAT), 6 unicode blocks oldest-to-newest plus the
        latest reported value, ~1.6x line spacing via line-height (no
        blank line). Empty for a non-results board meeting (dividend/
        other/fundraise) - there is no earnings history to show there.

        Revenue/PAT come straight from get_earnings_history_quarters,
        which already carries the bank-revenue derivation (InterestEarned
        - InterestExpended + OtherIncome) the same pipeline uses for the
        Past earnings tab's actuals - no separate bank handling needed
        here (13 Oct 2026, Adarsh's direction: reuse it rather than
        suppress the REV track for banks).

        Scaling, colour and the gap/negative rules all live in
        analytics.sparkline.build_blocks - this function only turns that
        into HTML."""
        if subtype != "results":
            return "<td style='border:none;'></td>"

        history = get_earnings_history_quarters(symbol)
        has_any = any(h["revenue_cons"] is not None or h["pat_cons"] is not None
                      for h in history)
        if not history or not has_any:
            return (
                f"<td style='padding:6px 0;color:{P.T3};font-size:12px;"
                f"white-space:nowrap;border:none;'>no history</td>"
            )

        def _line(label, field):
            window = [(h["quarter_end"], h[field]) for h in history]
            blocks = build_blocks(window)
            spans = []
            for i, b in enumerate(blocks):
                if b.char is None:
                    spans.append(
                        f"<span title='{_quarter_label(b.quarter_end)}: no filing found' "
                        f"style='color:{P.T3};'> </span>"
                    )
                    continue
                prev_val = blocks[i - 1].value if i > 0 else None
                qoq = qoq_pct(b.value, prev_val)  # scale-invariant - fine on raw INR
                color = {"up": P.UP, "down": P.DOWN}.get(b.direction, P.T1)
                # tooltip_text prints its value argument as-is - must be
                # crore here (same unit as the latest-value print below),
                # not the raw INR build_blocks works in internally.
                title = tooltip_text(_quarter_label(b.quarter_end), b.value / 1e7, qoq)
                # The negative marker is its own span, not baked into the
                # block colour - a loss quarter is still coloured by its
                # OWN QoQ direction (a smaller loss is still "up"), and
                # the marker exists only to flag "this one went negative",
                # separately from whether it improved or worsened.
                neg = f"<span style='color:{P.T3};'>-</span>" if b.is_negative else ""
                spans.append(
                    f"{neg}<span title='{title}' style='color:{color};"
                    f"cursor:help;'>{b.char}</span>"
                )
            latest = next((b.value for b in reversed(blocks) if b.value is not None), None)
            latest_text = _fmt_cr0(latest) if latest is not None else "-"
            return (
                f"<span style='color:{P.T3};font-size:10px;letter-spacing:0.03em;'>{label}</span> "
                f"{''.join(spans)}&nbsp;&nbsp;"
                f"<span style='color:{P.T1};{TABULAR}'>{latest_text}</span>"
            )

        rev_line = _line("REV", "revenue_cons")
        pat_line = _line("PAT", "pat_cons")
        return (
            f"<td style='padding:6px 0;font-size:13px;white-space:nowrap;"
            f"border:none;line-height:1.6;'>{rev_line}<br/>{pat_line}</td>"
        )

    def _agenda_link(symbol, text, event_type):
        slug = "corporate-filings-board-meetings" if event_type == "board_meeting" else "corporate-filings-actions"
        url = f"https://www.nseindia.com/companies-listing/{slug}?symbol={symbol}"
        return (
            f"<a href='{url}' target='_blank' rel='noopener' "
            f"style='color:inherit;text-decoration:none;border-bottom:1px dotted {P.T3};'>{text}</a>"
        )

    st.markdown("<div class='chip-label' style='margin-top:8px;'>View</div>", unsafe_allow_html=True)
    subtab = st.pills(
        "ev_subtab", ["Calendar", "Expected moves", "Past earnings", "Corporate actions"],
        key="ev_subtab", label_visibility="collapsed", default="Calendar",
    )
    subtab = subtab or "Calendar"
    event_type = "corp_action" if subtab == "Corporate actions" else "board_meeting"
    if subtab == "Expected moves":
        type_df = pd.DataFrame(get_all_expected_moves())
        if "symbol" not in type_df.columns:
            type_df = pd.DataFrame(columns=[
                "symbol", "found_count", "selected_count", "usable_count",
                "expected_move_pct", "worst_case_pct", "next_event_date",
            ])
        if symbols:
            type_df = type_df[type_df["symbol"].isin(symbols)] if len(type_df) else type_df
        if len(type_df):
            type_df = type_df.sort_values(
                "expected_move_pct", ascending=False, na_position="last",
            )
    elif subtab == "Calendar":
        # Widen the window backward so a result's actual/surprise stays
        # visible for a while after event_date passes, instead of the whole
        # row vanishing the moment "upcoming" no longer applies to it.
        calendar_events_df = get_upcoming_events(symbols=symbols, lookback_days=14)
        type_df = calendar_events_df[calendar_events_df["event_type"] == event_type]
    elif subtab == "Past earnings":
        # Results already out - the frozen estimate next to NSE's own
        # reported numbers, for every quarter where at least one actual
        # column has come in. No events-table join needed, earnings_estimates
        # alone has both sides.
        actual_cols = ["revenue_actual_cons", "revenue_actual_std",
                       "pat_actual_cons", "pat_actual_std",
                       "eps_actual_cons", "eps_actual_std"]
        has_actual = estimates_df[actual_cols].notna().any(axis=1)
        type_df = estimates_df[has_actual].sort_values("event_date", ascending=False)
    else:
        type_df = events_df[events_df["event_type"] == event_type] if len(events_df) else events_df

    st.markdown("<div class='chip-label' style='margin-top:8px;'>Search</div>", unsafe_allow_html=True)
    search_text = st.text_input(
        "ev_search", placeholder="Search by symbol", key="ev_search",
        label_visibility="collapsed",
    )
    if search_text.strip():
        type_df = type_df[type_df["symbol"].str.contains(search_text.strip(), case=False, na=False)]

    if len(type_df) == 0:
        empty_label = {
            "Calendar": "No board meetings scheduled.",
            "Expected moves": "No expected-move data yet.",
            "Past earnings": "No earnings results recorded yet.",
        }.get(subtab, "No declared corporate actions.")
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:14px;'>{empty_label}</div>",
            unsafe_allow_html=True,
        )
    elif subtab == "Calendar":
        # Prefetch every live preview this page's rows will need, concurrently,
        # before building any row - see PREVIEW_MAX_WORKERS above.
        preview_symbols = [
            r.symbol for r in type_df.itertuples()
            if r.event_date >= today_str and not _has_frozen_estimate(r.symbol, r.event_date)
        ]
        preview_map = _prefetch_concurrent(get_live_estimate_preview, preview_symbols)
        eps_map = _prefetch_concurrent(get_eps_history, [r.symbol for r in type_df.itertuples()])

        rows = "".join(
            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
            f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
            f"border:none;{TABULAR}'>{_fmt_date(r.event_date)}</td>"
            f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;"
            f"border:none;'>{r.symbol}</td>"
            f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;border:none;'>"
            f"{_agenda_link(r.symbol, r.purpose, 'board_meeting')}</td>"
            f"{_estimate_cells(r.symbol, r.event_date)}"
            f"{_history_sparkline_cell(r.symbol, r.subtype)}"
            f"</tr>"
            for r in type_df.itertuples()
        )
        header = (
            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Board meeting</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Symbol</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Agenda</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Est. Rev (Rs cr)</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;' "
            f"title='Hover a value for its analyst count'>Est. EPS</th>"
            f"<th style='padding:4px 0 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;' "
            f"title='Last 6 reported quarters, consolidated. Hover a block for its value "
            f"and QoQ change.'>History 6Q (Rs cr)</th>"
            f"</tr>"
        )
        st.markdown(
            f"<div style='overflow-x:auto;'>"
            f"<table style='border-collapse:collapse;border:none;width:100%;margin-top:10px;'>"
            f"{header}{rows}</table></div>",
            unsafe_allow_html=True,
        )
        if len(estimates_by_key) or saw_preview:
            note = (
                "Revenue estimate is frozen the day before each result and "
                "never revised. EPS estimate comes live from Yahoo's own "
                "per-quarter history, which CAN revise after a result is out "
                "- see the Past earnings tab for NSE's own reported numbers, "
                "which never revise once filed."
            )
            if saw_preview:
                note += (
                    " Figures marked ~ are a live, unfrozen consensus for a "
                    "result that hasn't happened yet."
                )
            note += " Source: Yahoo Finance consensus."
            st.markdown(
                f"<div style='color:{P.T3};font-size:12px;margin-top:8px;'>{note}</div>",
                unsafe_allow_html=True,
            )

    elif subtab == "Expected moves":
        def _move_cell(pct, padding_right=16):
            has_value = isinstance(pct, (int, float)) and pd.notna(pct)
            text = f"{pct:.2f}%" if has_value else "Unavailable"
            color = P.T1 if has_value else P.T3
            return (
                f"<td style='padding:6px {padding_right}px 6px 0;color:{color};font-size:13px;"
                f"white-space:nowrap;border:none;{TABULAR}'>{text}</td>"
            )

        rows = "".join(
            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
            f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;"
            f"border:none;'>{r.symbol}</td>"
            f"{_move_cell(r.expected_move_pct)}"
            f"{_move_cell(r.worst_case_pct)}"
            f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
            f"border:none;{TABULAR}'>{r.usable_count}/{r.selected_count}</td>"
            f"<td style='padding:6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
            f"border:none;{TABULAR}'>{_fmt_date(r.next_event_date) if isinstance(r.next_event_date, str) else 'not scheduled'}</td>"
            f"</tr>"
            for r in type_df.itertuples()
        )
        header = (
            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Symbol</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Expected move</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Worst case</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Prints used</th>"
            f"<th style='padding:4px 0 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Next results</th>"
            f"</tr>"
        )
        st.markdown(
            f"<table style='border-collapse:collapse;border:none;width:100%;margin-top:10px;'>{header}{rows}</table>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div style='color:{P.T3};font-size:12px;margin-top:10px;'>"
            f"Every F&amp;O stock, ranked by its largest historical earnings-day reaction. "
            f"Expected move and worst case come from each stock's latest up to 8 completed "
            f"results, using hourly prices - not tied to whether its next result date is "
            f"scheduled yet. Worst case is the largest observed move, not a limit on future "
            f"moves. Prints used is usable filings out of filings found.</div>",
            unsafe_allow_html=True,
        )
    elif subtab == "Past earnings":
        # No comparison is made between the estimate and any actual here
        # (Adarsh's direction, 13 Oct 2026, after discovery found no safe
        # way to match a single yfinance-sourced estimate to one NSE
        # basis - see scripts/discover_yahoo_basis.py and
        # jobs/fill_actuals.py's module docstring). The estimate is shown
        # as context, NSE's own reported numbers are shown as fact, both
        # bases, and the reader judges - no surprise % column.

        def _cell(val, fmt, color=P.T1, empty="-"):
            text = fmt(_num(val)) if _num(val) is not None else None
            return (
                f"<td style='padding:6px 16px 6px 0;color:{color if text else P.T3};"
                f"font-size:13px;white-space:nowrap;border:none;{TABULAR}'>{text or empty}</td>"
            )

        def _past_row_cells(r):
            return (
                _cell(r.revenue_estimate, _fmt_rev_cr, color=P.T2, empty="no estimate")
                + _cell(r.eps_estimate, _fmt_eps, color=P.T2, empty="no estimate")
                + _cell(r.revenue_actual_cons, _fmt_rev_cr)
                + _cell(r.revenue_actual_std, _fmt_rev_cr)
                + _cell(r.pat_actual_cons, _fmt_rev_cr)
                + _cell(r.pat_actual_std, _fmt_rev_cr)
                + _cell(r.eps_actual_cons, _fmt_eps)
                + _cell(r.eps_actual_std, _fmt_eps)
            )

        rows = "".join(
            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
            f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
            f"border:none;{TABULAR}'>{_fmt_date(r.event_date)}</td>"
            f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;"
            f"border:none;'>{r.symbol}</td>"
            f"{_past_row_cells(r)}"
            f"</tr>"
            for r in type_df.itertuples()
        )

        def _th(label, last=False):
            pad = "4px 0 6px 0" if last else "4px 16px 6px 0"
            return (f"<th style='padding:{pad};color:{P.T3};font-size:12px;text-align:left;"
                    f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>{label}</th>")

        header = (
            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
            + _th("Results date") + _th("Symbol")
            + _th("Est. Rev (Rs cr)") + _th("Est. EPS")
            + _th("Revenue cons. (Rs cr)") + _th("Revenue stand. (Rs cr)")
            + _th("PAT cons. (Rs cr)") + _th("PAT stand. (Rs cr)")
            + _th("EPS dil. cons.") + _th("EPS dil. stand.", last=True)
            + "</tr>"
        )
        st.markdown(
            f"<div style='overflow-x:auto;'>"
            f"<table style='border-collapse:collapse;border:none;width:100%;margin-top:10px;'>"
            f"{header}{rows}</table></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div style='color:{P.T3};font-size:12px;margin-top:8px;'>"
            f"Estimate is the frozen T-1 consensus (Yahoo Finance), captured the day before "
            f"the result and never re-fetched. Revenue, PAT and diluted EPS are the company's "
            f"own filed result (NSE filing XBRL), standalone and consolidated as separately "
            f"filed - no surprise % is computed, since the estimate's basis cannot be reliably "
            f"matched to one of these.</div>",
            unsafe_allow_html=True,
        )
    elif subtab == "Corporate actions":
        rows = "".join(
            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
            f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
            f"border:none;{TABULAR}'>{_fmt_date(r.event_date)}</td>"
            f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;"
            f"border:none;'>{r.symbol}</td>"
            f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
            f"border:none;{TABULAR}'>{_fmt_date(r.record_date)}</td>"
            f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
            f"border:none;'>{SUBTYPE_LABELS.get(r.subtype, r.subtype.title())}</td>"
            f"<td style='padding:6px 0;color:{P.T2};font-size:13px;border:none;'>"
            f"{_agenda_link(r.symbol, r.purpose, 'corp_action')}</td>"
            f"</tr>"
            for r in type_df.itertuples()
        )
        header = (
            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Ex-date</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Symbol</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Record date</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Type</th>"
            f"<th style='padding:4px 0 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Details</th>"
            f"</tr>"
        )
        st.markdown(
            f"<table style='border-collapse:collapse;border:none;width:100%;margin-top:10px;'>{header}{rows}</table>",
            unsafe_allow_html=True,
        )

    if subtab != "Expected moves":
        st.markdown(
            f"<div style='color:{P.T3};font-size:12px;margin-top:16px;'>"
            f"Results dates appear only about 1-3 weeks ahead, after the company files "
            f"with the exchange. Dates can be revised. This list updates daily and is "
            f"not a complete calendar.</div>",
            unsafe_allow_html=True,
        )
