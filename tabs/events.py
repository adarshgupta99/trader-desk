"""
Events tab: upcoming F&O board meetings and declared corporate actions, read
from events.db. Two different things, so two subtabs - a board meeting is a
*scheduled* agenda with no outcome yet, while a corporate action is already
declared with a fixed amount and ex-date.
"""

from datetime import datetime, timedelta, timezone

import streamlit as st

from data_loader import WATCHLIST_PRESETS, get_custom_lists, get_list_items
from events_db import IST, get_last_success, get_upcoming_events
from instruments import PRESET_NAMES
from theme import TABULAR, palette

SUBTYPE_LABELS = {
    "dividend": "Dividend", "split": "Split", "bonus": "Bonus",
    "rights": "Rights", "buyback": "Buyback", "fundraise": "Fundraise",
    "agm": "AGM", "results": "Results", "other": "Other",
}


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

    def _fmt_date(s):
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d %b %Y") if s else "—"

    def _agenda_link(symbol, text, event_type):
        slug = "corporate-filings-board-meetings" if event_type == "board_meeting" else "corporate-filings-actions"
        url = f"https://www.nseindia.com/companies-listing/{slug}?symbol={symbol}"
        return (
            f"<a href='{url}' target='_blank' rel='noopener' "
            f"style='color:inherit;text-decoration:none;border-bottom:1px dotted {P.T3};'>{text}</a>"
        )

    st.markdown("<div class='chip-label' style='margin-top:8px;'>View</div>", unsafe_allow_html=True)
    subtab = st.pills(
        "ev_subtab", ["Calendar", "Corporate actions"], key="ev_subtab",
        label_visibility="collapsed", default="Calendar",
    )
    subtab = subtab or "Calendar"
    event_type = "board_meeting" if subtab == "Calendar" else "corp_action"
    type_df = events_df[events_df["event_type"] == event_type] if len(events_df) else events_df

    st.markdown("<div class='chip-label' style='margin-top:8px;'>Search</div>", unsafe_allow_html=True)
    search_text = st.text_input(
        "ev_search", placeholder="Search by symbol", key="ev_search",
        label_visibility="collapsed",
    )
    if search_text.strip():
        type_df = type_df[type_df["symbol"].str.contains(search_text.strip(), case=False, na=False)]

    if len(type_df) == 0:
        empty_label = "No board meetings scheduled." if subtab == "Calendar" else "No declared corporate actions."
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:14px;'>{empty_label}</div>",
            unsafe_allow_html=True,
        )
    elif subtab == "Calendar":
        rows = "".join(
            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
            f"<td style='padding:6px 16px 6px 0;color:{P.T2};font-size:13px;white-space:nowrap;"
            f"border:none;{TABULAR}'>{_fmt_date(r.event_date)}</td>"
            f"<td style='padding:6px 16px 6px 0;color:{P.T1};font-size:13px;white-space:nowrap;"
            f"border:none;'>{r.symbol}</td>"
            f"<td style='padding:6px 0;color:{P.T2};font-size:13px;border:none;'>"
            f"{_agenda_link(r.symbol, r.purpose, 'board_meeting')}</td>"
            f"</tr>"
            for r in type_df.itertuples()
        )
        header = (
            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Board meeting</th>"
            f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Symbol</th>"
            f"<th style='padding:4px 0 6px 0;color:{P.T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Agenda</th>"
            f"</tr>"
        )
        st.markdown(
            f"<table style='border-collapse:collapse;border:none;width:100%;margin-top:10px;'>{header}{rows}</table>",
            unsafe_allow_html=True,
        )
    else:
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

    st.markdown(
        f"<div style='color:{P.T3};font-size:12px;margin-top:16px;'>"
        f"Results dates appear only about 1-3 weeks ahead, after the company files "
        f"with the exchange. Dates can be revised. This list updates daily and is "
        f"not a complete calendar.</div>",
        unsafe_allow_html=True,
    )
