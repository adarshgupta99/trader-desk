"""
Watchlist tab: nine views over one chip row - five read-only index views and
four editable custom lists. Switching views destroys nothing, so there is no
confirmation step anywhere here.
"""

import streamlit as st

from formatting import format_price, price_decimals, signed_pct
from instruments import pick_instrument
from prices import get_price_history
from theme import TABULAR, palette
from watchlists import (LIST_MAX, PRESET_INDEX_TICKER, PRESET_NAMES,
                        WATCHLIST_PRESETS, add_to_list, count_list_with_data,
                        get_custom_lists, get_list_items, remove_from_list,
                        rename_custom_list)


def _watchlist_grid(watchlist, show_1w, list_id=None):
    """Render instrument tiles. `list_id` is the editable list these belong to;
    pass None for read-only index views so no remove control is drawn.

    Tiles are native containers rather than one HTML block so that the remove
    control can be a real button. As a link it was a page navigation, which
    reloaded the whole app — blanking the page and resetting scroll.
    """
    P = palette()
    with st.container(horizontal=True, wrap=True, gap="small", key="wl_tiles"):
        for item in watchlist:
            ticker = item["ticker"]
            label  = item["label"]

            with st.container(border=True, width="content", key=f"wl_{ticker}"):
                head = st.container(horizontal=True, gap="small",
                                    vertical_alignment="center")
                with head:
                    st.markdown(
                        f"<span style='font-size:13px;color:{P.T2};font-weight:600;"
                        f"white-space:nowrap;'>{label}</span>",
                        unsafe_allow_html=True,
                    )
                    if list_id is not None and st.button(
                        "×", key=f"rm_{list_id}_{ticker}", type="tertiary",
                        help=f"Remove {label}",
                    ):
                        remove_from_list(list_id, ticker)
                        st.rerun()

                df = get_price_history(ticker, period="5d")
                df = df.dropna(subset=["Close"])

                if df.empty:
                    # Keep failed instruments visible and removable (spec, high severity)
                    st.markdown(
                        f"<div style='font-size:13px;color:{P.DOWN};line-height:1.5;'>"
                        f"Couldn't load</div>",
                        unsafe_allow_html=True,
                    )
                    continue

                last    = df["Close"].iloc[-1]
                decimal = 4 if ticker.endswith("=X") else 2
                pct = ((last / df["Close"].iloc[0]) - 1) * 100 if show_1w else (
                      ((last / df["Close"].iloc[-2]) - 1) * 100 if len(df) >= 2 else 0.0)
                color = P.UP if pct >= 0 else P.DOWN

                st.markdown(
                    f"<div style='font-size:22px;font-weight:700;color:{color};"
                    f"line-height:1.1;margin-bottom:3px;{TABULAR}'>{signed_pct(pct)}</div>"
                    f"<div style='font-size:14px;font-weight:600;color:{P.T1};{TABULAR}'>"
                    f"{format_price(ticker, last, decimal)}</div>",
                    unsafe_allow_html=True,
                )

def render():
    P = palette()

    lists = get_custom_lists()

    # The active view is session state. It used to live in the URL because the
    # chips and the remove control were links; now they are native widgets,
    # nothing navigates, and there is no page load to survive.
    if "wl_view" not in st.session_state:
        st.session_state.wl_view = f"p:{PRESET_NAMES[0]}"

    # Two chip rows, one selection between them: picking in one clears the other.
    def _pick_index():
        if st.session_state.wl_idx:
            st.session_state.wl_view = f"p:{st.session_state.wl_idx}"
            st.session_state.wl_cst = None

    def _pick_custom():
        if st.session_state.wl_cst:
            st.session_state.wl_view = f"c:{st.session_state.wl_cst}"
            st.session_state.wl_idx = None

    view = st.session_state.wl_view
    is_custom = view.startswith("c:")
    list_id = int(view[2:]) if is_custom and view[2:].isdigit() else None
    if is_custom and list_id not in {l["id"] for l in lists}:
        view = st.session_state.wl_view = f"p:{PRESET_NAMES[0]}"
        is_custom, list_id = False, None

    err = st.session_state.pop("wl_err", None)

    # ── View selector ─────────────────────────────────────────────────────────
    names = {l["id"]: l["name"] for l in lists}
    st.markdown("<div class='chip-label'>Indices</div>", unsafe_allow_html=True)
    st.pills("idx", PRESET_NAMES, key="wl_idx", on_change=_pick_index,
             label_visibility="collapsed",
             default=view[2:] if not is_custom else None)
    st.markdown("<div class='chip-label' style='margin-top:8px;'>My lists</div>",
                unsafe_allow_html=True)
    st.pills("cst", [l["id"] for l in lists], key="wl_cst",
             format_func=lambda i: names[i], on_change=_pick_custom,
             label_visibility="collapsed",
             default=list_id if is_custom else None)

    # ── Resolve the items for this view ───────────────────────────────────────
    if is_custom:
        items = get_list_items(list_id)
        active_name = next(l["name"] for l in lists if l["id"] == list_id)
    else:
        preset_name = view[2:]
        if preset_name not in PRESET_NAMES:
            preset_name = PRESET_NAMES[0]
        items = [{"ticker": t, "label": lb} for t, lb in WATCHLIST_PRESETS[preset_name]]
        active_name = preset_name

    # ── Editing controls: custom lists only ───────────────────────────────────
    if is_custom:
        count = len(items)
        failed = count - count_list_with_data(list_id)
        status = f"{count} / {LIST_MAX} instruments"
        if failed:
            status += f"  ·  {failed} couldn't load"
        st.markdown(
            f"<div style='color:{P.T2};font-size:12px;margin:10px 0 6px 0;'>{status}</div>",
            unsafe_allow_html=True,
        )

        is_full = count >= LIST_MAX

        # A new key after each add gives a fresh, empty dropdown
        pick_nonce = st.session_state.get(f"add_nonce_{list_id}", 0)

        c_search, c_name, c_save = st.columns([6, 3, 1])
        with c_search:
            picked = pick_instrument(
                f"add_{list_id}_{pick_nonce}",
                placeholder="Search instruments — name or ticker",
                disabled=is_full,
            )
        with c_name:
            new_name = st.text_input(
                "name", value=active_name, placeholder="List name",
                label_visibility="collapsed", key=f"name_{list_id}",
            )
        with c_save:
            rename_clicked = st.button("Rename", width="stretch")

        if err:
            st.markdown(
                f"<div style='color:{P.DOWN};font-size:12px;margin:2px 0 4px 0;'>{err}</div>",
                unsafe_allow_html=True,
            )

        if is_full:
            note = f"This list is full ({LIST_MAX} of {LIST_MAX}). Remove one to add another."
            if failed:
                note += f" Entries that couldn't load still count ({failed})."
            st.markdown(
                f"<div style='color:{P.DOWN};font-size:12px;margin:-2px 0 4px 0;'>{note}</div>",
                unsafe_allow_html=True,
            )
        elif picked:
            if picked in {i["ticker"] for i in items}:
                st.markdown(
                    f"<div style='color:{P.T2};font-size:12px;margin:2px 0 4px 0;'>"
                    f"{picked} is already in this list.</div>",
                    unsafe_allow_html=True,
                )
            else:
                ok, msg = add_to_list(list_id, picked)
                st.session_state[f"add_nonce_{list_id}"] = pick_nonce + 1
                if ok:
                    st.rerun()
                else:
                    st.session_state["wl_err"] = msg
                    st.rerun()

        if rename_clicked:
            ok, msg = rename_custom_list(list_id, new_name)
            if ok:
                st.rerun()
            else:
                st.error(msg)

    # ── Change window + grid ──────────────────────────────────────────────────
    st.markdown(
        f"<div style='font-size:12px;color:{P.T2};text-transform:uppercase;"
        f"letter-spacing:0.08em;margin:10px 0 4px 0;'>Change window</div>",
        unsafe_allow_html=True,
    )
    c_toggle, c_idx = st.columns([2, 5], vertical_alignment="center")
    with c_toggle:
        wl_period = st.segmented_control(
            "wl_period", ["1D", "1W"], default="1D", required=True,
            label_visibility="collapsed", key="wl_period"
        )
    show_1w = (wl_period == "1W")

    if not is_custom:
        idx_ticker = PRESET_INDEX_TICKER.get(active_name)
        idf = get_price_history(idx_ticker, period="5d").dropna(subset=["Close"]) if idx_ticker else None
        if idf is not None and not idf.empty:
            idx_last = idf["Close"].iloc[-1]
            idx_pct = ((idx_last / idf["Close"].iloc[0]) - 1) * 100 if show_1w else (
                       ((idx_last / idf["Close"].iloc[-2]) - 1) * 100 if len(idf) >= 2 else 0.0)
            idx_color = P.UP if idx_pct >= 0 else P.DOWN
            idx_date = idf.index[-1].strftime("%d %b %Y")
            idx_price = format_price(idx_ticker, idx_last, price_decimals(idx_ticker))
            with c_idx:
                st.markdown(
                    f"<div style='display:flex;align-items:center;gap:8px;'>"
                    f"<span style='font-size:13px;color:{P.T2};font-weight:600;"
                    f"white-space:nowrap;'>{active_name}</span>"
                    f"<span style='font-size:19px;font-weight:700;color:{P.T1};"
                    f"{TABULAR}'>{idx_price}</span>"
                    f"<span style='font-size:19px;font-weight:700;color:{idx_color};"
                    f"{TABULAR}'>{signed_pct(idx_pct)}</span>"
                    f"<span style='font-size:12px;color:{P.T2};'>{wl_period}</span>"
                    f"<span style='font-size:12px;color:{P.T2};'>as of {idx_date}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    if not items:
        st.markdown(
            f"<div style='color:{P.T2};padding:40px 0;'>"
            f"{active_name} is empty — add an instrument above.</div>",
            unsafe_allow_html=True,
        )
    else:
        _watchlist_grid(items, show_1w, list_id if is_custom else None)
