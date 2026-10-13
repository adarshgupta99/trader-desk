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

import json

from events_db import (IST, get_all_expected_moves, get_earnings_estimates,
                       get_eps_history, get_expected_move_history,
                       get_expected_move_summary, get_last_success,
                       get_live_estimate_preview, get_upcoming_events,
                       match_eps_row)
from theme import TABULAR, palette
from watchlists import (PRESET_NAMES, WATCHLIST_PRESETS, get_custom_lists,
                        get_list_items)

SUBTYPE_LABELS = {
    "dividend": "Dividend", "split": "Split", "bonus": "Bonus",
    "rights": "Rights", "buyback": "Buyback", "fundraise": "Fundraise",
    "agm": "AGM", "results": "Results", "other": "Other",
}

#: expected-move row-level quality flags (spec section 10's "row warnings"),
#: plain-language so the user never has to infer them from a NULL.
REASON_CODE_LABELS = {
    "missing_filing_time": "filing time missing",
    "ambiguous_period": "financial period unclear from the filing text",
    "archive_coverage_gap": "filing not found in archive coverage",
    "missing_reference": "no reference price available",
    "reference_next_bar": "nearest reference bar missing - used next available bar",
    "boundary_hour_contamination": "filing-hour high/low may include pre-filing trades",
    "price_depth_limit": "beyond provider's historical depth",
    "missing_reaction_bars": "reaction-session bars unavailable",
    "incomplete_session": "incomplete session data",
    "no_trade_or_halt": "no trade / halted session",
    "pending_window": "next session hasn't completed yet",
    "corporate_action_basis_unresolved": "split/dividend adjustment basis unverified",
    "source_fetch_failed": "a data source fetch failed",
    "source_conflict": "a later filing exists for this period - see history",
    "price_basis_daily_fallback": "hourly prices unreliable around a bonus/split/demerger - recomputed from daily bars, less precise",
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

    def _surprise_text_color(pct):
        """(text, color) for a surprise % value - shared by the EPS surprise
        in the Calendar table and both surprises on the Past earnings tab."""
        if pct is None:
            return "-", P.T2
        sign = "+" if pct > 0 else ("" if pct < 0 else "+")
        color = P.UP if pct > 0 else (P.DOWN if pct < 0 else P.T1)
        return f"{sign}{pct:.2f}%", color

    def _has_frozen_estimate(symbol, event_date):
        """Whether a frozen T-1 revenue snapshot exists - EPS no longer goes
        through this table, so only revenue_estimate matters here now."""
        est = estimates_by_key.get((symbol, event_date))
        return est is not None and est.revenue_estimate is not None

    def _estimate_cells(symbol, event_date):
        """Returns the 5 <td> cells for Est. Rev / Est. EPS / Analysts /
        Actual EPS / Surprise %, for one Calendar row. None values from a
        missing snapshot or a NULL estimate both render as "no estimate" -
        the row itself is never dropped either way.

        Revenue still goes through the frozen T-1 snapshot (estimates_by_key,
        from earnings_estimates): if there's no frozen row yet and the event
        hasn't passed, falls back to a live, unfrozen consensus preview
        (secondary colour, "~" prefix) from preview_map - replaced by the
        real frozen row once the T-1 job runs, never stored, never used for
        surprise %. Includes event_date == today: the T-1 job should have
        frozen that row yesterday, but if it hasn't run yet there's no
        reason to show nothing when a live number is available.

        EPS (estimate, actual, surprise) comes from get_eps_history() /
        eps_map instead - Yahoo's own per-quarter table, live, no freezing
        needed on our side (see events_db.get_eps_history). Not yet reported
        renders the same "~" preview styling as an unfrozen revenue number.

        preview_map and eps_map are prefetched concurrently for every symbol
        that needs them before any row is built - see render()."""
        nonlocal saw_preview
        est = estimates_by_key.get((symbol, event_date))
        has_frozen = _has_frozen_estimate(symbol, event_date)

        rev_val = n_val = None
        is_preview = False
        if has_frozen:
            rev_val, n_val = est.revenue_estimate, est.n_analysts_eps
        elif event_date >= today_str:
            preview = preview_map.get(symbol)
            if preview and preview.get("revenue_estimate") is not None:
                rev_val = preview.get("revenue_estimate")
                n_val = preview.get("n_analysts_eps")
                is_preview = True
                saw_preview = True

        eps_row = match_eps_row(eps_map.get(symbol), event_date)
        eps_est_val = eps_row["eps_estimate"] if eps_row is not None else None
        eps_actual_val = eps_row["eps_actual"] if eps_row is not None else None
        eps_surprise_val = eps_row["eps_surprise_pct"] if eps_row is not None else None
        eps_is_preview = eps_row is not None and eps_actual_val is None

        if rev_val is None:
            no_est_td = (
                f"<td style='padding:6px 16px 6px 0;color:{P.T3};font-size:13px;"
                f"white-space:nowrap;border:none;'>no estimate</td>"
            )
        else:
            val_color = P.T2 if is_preview else P.T1
            rev_text = f"{'~' if is_preview else ''}{_fmt_rev_cr(rev_val)}"
            no_est_td = (
                f"<td style='padding:6px 16px 6px 0;color:{val_color};font-size:13px;"
                f"white-space:nowrap;border:none;{TABULAR}'>{rev_text}</td>"
            )
        rev_cell = no_est_td

        if eps_est_val is None:
            eps_cell = (
                f"<td style='padding:6px 16px 6px 0;color:{P.T3};font-size:13px;"
                f"white-space:nowrap;border:none;'>no estimate</td>"
            )
        else:
            eps_val_color = P.T2 if eps_is_preview else P.T1
            eps_text = f"{'~' if eps_is_preview else ''}{_fmt_eps(eps_est_val)}"
            eps_cell = (
                f"<td style='padding:6px 16px 6px 0;color:{eps_val_color};font-size:13px;"
                f"white-space:nowrap;border:none;{TABULAR}'>{eps_text}</td>"
            )

        analysts_cell = (
            f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;"
            f"white-space:nowrap;border:none;{TABULAR}'>"
            f"{int(n_val) if n_val is not None else 'no estimate'}</td>"
        )
        actual_text = _fmt_eps(eps_actual_val) or "-"
        actual_cell = (
            f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;"
            f"white-space:nowrap;border:none;{TABULAR}'>{actual_text}</td>"
        )
        surprise_text, surprise_color = _surprise_text_color(eps_surprise_val)
        surprise_cell = (
            f"<td style='padding:6px 0;color:{surprise_color};font-size:13px;"
            f"white-space:nowrap;border:none;{TABULAR}'>{surprise_text}</td>"
        )
        return rev_cell + eps_cell + analysts_cell + actual_cell + surprise_cell

    def _expected_move_cells(symbol, event_date):
        """2 <td> cells: Expected move %, Worst case % - absolute-magnitude
        statistics, so neutral styling only, never green/red or an arrow
        (spec section 10). 'Unavailable' (not 0.00%) when the summary
        exists but has zero usable prints; 'no data yet' when the symbol
        hasn't been backfilled/computed at all."""
        summary = get_expected_move_summary(symbol, event_date)
        if summary is None:
            dash = (
                f"<td colspan='2' style='padding:6px 0;color:{P.T3};font-size:13px;"
                f"white-space:nowrap;border:none;'>no data yet</td>"
            )
            return dash
        usable = summary["usable_count"]
        if usable == 0 or summary["expected_move_pct"] is None:
            unavailable = (
                f"<td colspan='2' style='padding:6px 0;color:{P.T3};font-size:13px;"
                f"white-space:nowrap;border:none;' title='0 usable prints of "
                f"{summary['selected_count']} selected'>Unavailable</td>"
            )
            return unavailable
        selected_count = summary["selected_count"]
        expected_cell = (
            f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;"
            f"white-space:nowrap;border:none;{TABULAR}' "
            f"title='Based on {usable} usable print(s) of {selected_count} selected'>"
            f"{summary['expected_move_pct']:.2f}%</td>"
        )
        worst_cell = (
            f"<td style='padding:6px 0;color:{P.T1};font-size:13px;"
            f"white-space:nowrap;border:none;{TABULAR}'>{summary['worst_case_pct']:.2f}%</td>"
        )
        return expected_cell + worst_cell

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
            f"{_expected_move_cells(r.symbol, r.event_date)}"
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
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Est. EPS</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Analysts</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Actual EPS</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Surprise %</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Expected move</th>"
            f"<th style='padding:4px 0 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Worst case</th>"
            f"</tr>"
        )
        st.markdown(
            f"<table style='border-collapse:collapse;border:none;width:100%;margin-top:10px;'>{header}{rows}</table>",
            unsafe_allow_html=True,
        )
        if len(estimates_by_key) or saw_preview:
            note = (
                "Revenue estimates are frozen the day before each result and "
                "never revised. EPS estimate/actual come live from Yahoo's own "
                "per-quarter history, which it keeps unrevised once a result "
                "is out."
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

        has_any_move_data = any(
            get_expected_move_summary(r.symbol, r.event_date) is not None
            for r in type_df.itertuples()
        )
        if has_any_move_data:
            st.markdown(
                f"<div style='color:{P.T3};font-size:12px;margin-top:10px;'>"
                f"Historical earnings reactions, using hourly prices. Filing-time "
                f"references are approximate; the filing-hour high/low may include "
                f"pre-filing trades. Worst case is the largest observed move, not a "
                f"limit on future moves.</div>",
                unsafe_allow_html=True,
            )
            with st.expander("How expected move is calculated"):
                st.markdown(
                    "For intraday filings, each print uses the larger of the "
                    "filing-session and next-session extreme moves, both versus "
                    "the same hourly reference proxy. For after-close filings, "
                    "the next session is measured versus the filing day's close. "
                    "Headline expected move is the average across usable prints."
                )

            for r in type_df.itertuples():
                history = get_expected_move_history(r.symbol)
                if not history:
                    continue
                with st.expander(f"{r.symbol}: expected-move history ({len(history)} filing(s))"):
                    hist_rows = []
                    for h in history:
                        reasons = json.loads(h["reason_codes"] or "[]")
                        reason_text = "; ".join(REASON_CODE_LABELS.get(c, c) for c in reasons) or "-"
                        move_a_text = f"{h['move_a_pct']:.2f}%" if h["move_a_pct"] is not None else (
                            "-" if h["session_a_date"] is None else "Missing"
                        )
                        move_b_text = f"{h['move_b_pct']:.2f}%" if h["move_b_pct"] is not None else (
                            "Pending" if h["status"] == "pending" else "Missing"
                        )
                        print_move_text = f"{h['print_move_pct']:.2f}%" if h["print_move_pct"] is not None else "-"
                        ref_text = f"{h['reference_price']:.2f}" if h["reference_price"] is not None else "unavailable"
                        link = (
                            f"<a href='{h['attachment_url']}' target='_blank' rel='noopener' "
                            f"style='color:inherit;text-decoration:none;border-bottom:1px dotted {P.T3};'>filing</a>"
                            if h["attachment_url"] else "-"
                        )
                        hist_rows.append(
                            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
                            f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;border:none;{TABULAR}'>{h['financial_period']}</td>"
                            f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;border:none;{TABULAR}'>{h['filing_ts_ist']}</td>"
                            f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;border:none;{TABULAR}'>{ref_text} (Rs, {h['reference_method']})</td>"
                            f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;border:none;{TABULAR}'>{move_a_text}</td>"
                            f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;border:none;{TABULAR}'>{move_b_text}</td>"
                            f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;border:none;{TABULAR}'>{print_move_text}</td>"
                            f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;border:none;'>{h['status']}</td>"
                            f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;border:none;'>{reason_text}</td>"
                            f"<td style='padding:6px 0;color:{P.T2};font-size:13px;white-space:nowrap;border:none;'>{link}</td>"
                            f"</tr>"
                        )
                    hist_header = (
                        f"<tr style='border-bottom:1px solid {P.BORDER};'>"
                        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;text-transform:uppercase;letter-spacing:0.05em;border:none;'>Period</th>"
                        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;text-transform:uppercase;letter-spacing:0.05em;border:none;'>Filing (IST)</th>"
                        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;text-transform:uppercase;letter-spacing:0.05em;border:none;'>Reference</th>"
                        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;text-transform:uppercase;letter-spacing:0.05em;border:none;'>Same-session move</th>"
                        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;text-transform:uppercase;letter-spacing:0.05em;border:none;'>Next-session move</th>"
                        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;text-transform:uppercase;letter-spacing:0.05em;border:none;'>Print move</th>"
                        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;text-transform:uppercase;letter-spacing:0.05em;border:none;'>Status</th>"
                        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;text-transform:uppercase;letter-spacing:0.05em;border:none;'>Flags</th>"
                        f"<th style='padding:4px 0 6px 0;color:{P.T3};font-size:12px;text-align:left;text-transform:uppercase;letter-spacing:0.05em;border:none;'>Source</th>"
                        f"</tr>"
                    )
                    st.markdown(
                        f"<table style='border-collapse:collapse;border:none;width:100%;'>{hist_header}{''.join(hist_rows)}</table>",
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
