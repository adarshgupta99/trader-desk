"""
Trader Desk - entry point.

This file does four things and nothing else: page setup, theme, the section
navigation, and dispatch to the tab that was picked. Every section's body lives
in tabs/, and anything two sections share lives in a module next to this one:

    theme.py         design tokens for both themes, and the stylesheets
    formatting.py    number / change formatting rules
    components.py    the price card shared by five tabs

    periods.py       the history ranges, and date-range price lookups
    instruments.py   curated asset lists, the instrument picker
    constituents.py  approximate index weights

    storage.py       where market_cache.db lives, and its schema
    prices.py        price history and its cache (yfinance + jugaad-data)
    watchlists.py    custom lists, and the read-only index views
    symbols.py       the searchable instrument table
    events_db.py     read-only access to events.db
    fno.py           the 213-stock F&O universe and sector lookup

    analytics/       correlation.py, tiers.py - pure computation, no I/O
    data_layer/       intraday_store.py - the bulk-bar SQLite store
    jobs/            fetch_intraday.py - run from cron, not from the app

If you are adding a section: write tabs/<name>.py with a render(), import it
below, and add its name to NAV.
"""

import streamlit as st
import streamlit.components.v1 as components

import theme
from prices import clear_price_cache
from symbols import refresh_symbols
from tabs import (asset_grids, chart, correlation, events, guidance,
                  sentiment, watchlist)

st.set_page_config(page_title="Trader Desk", layout="wide", page_icon="📊", initial_sidebar_state="collapsed")

refresh_symbols()   # reload the instrument table from data/symbols.csv

P = theme.palette()
theme.inject_css(P)

if st.query_params.get("clear_cache") == "1":
    clear_price_cache()
    st.query_params.clear()
    st.rerun()

st.markdown(
    "<div class='clear-cache-btn'>"
    "<a href='?clear_cache=1'>CLEAR<br>CACHE</a>"
    "</div>",
    unsafe_allow_html=True,
)

st.markdown(
    f"<div style='font-size:38px;font-weight:700;color:{P.T1};letter-spacing:-0.02em;"
    f"line-height:1.1;padding-top:2px;'>Trader Desk</div>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<div style='border-top:2px solid {P.T1};margin:-8px 0 0 0;'></div>",
    unsafe_allow_html=True,
)

# ── Navigation ────────────────────────────────────────────────────────────────

# Only the selected section is rendered. st.tabs would run every tab's body on
# every interaction — 53 charts rebuilt to show at most 8 — which cost ~650ms a
# click regardless of what was clicked.
NAV = ["Watchlist", "Chart", "Indices", "India", "FX", "Commodities", "Volatility", "Events", "Correlation", "Sentiment", "Guidance"]

# `required` keeps a section selected: without it, clicking the active item
# deselects it and the app falls back to the first section.
nav = st.segmented_control(
    "nav", NAV, default=NAV[0], required=True,
    label_visibility="collapsed", key="main_nav",
)

if nav == "Watchlist":
    watchlist.render()
elif nav == "Chart":
    chart.render()
elif nav == "Events":
    events.render()
elif nav == "Correlation":
    correlation.render()
elif nav == "Sentiment":
    sentiment.render()
elif nav == "Guidance":
    guidance.render()
elif nav in asset_grids.GRIDS:
    asset_grids.render(nav)

# ── Theme watcher ─────────────────────────────────────────────────────────────
# Switching theme in the toolbar repaints Streamlit's own chrome but never
# reruns the script, so the Python-side palette (Plotly figures, inline HTML)
# would stay on the old theme until a manual refresh. This hidden button plus a
# zero-height iframe closes that gap: the script compares the browser's live
# colour-scheme against the theme this run rendered with and clicks the button
# when they disagree, which is a real widget interaction and so a real rerun.
st.button("theme sync", key="theme_sync")

components.html(
    """
<script>
(function () {
  const w = window.parent, doc = w.document;
  const app = doc.querySelector('.stApp');
  if (!app) return;
  const RENDERED = "__THEME__";
  const live = () => getComputedStyle(app).colorScheme === 'dark' ? 'dark' : 'light';
  function sync() {
    if (live() === RENDERED) { w.__tdThemeTries = 0; return; }
    if ((w.__tdThemeTries = (w.__tdThemeTries || 0) + 1) > 3) return;
    const btn = doc.querySelector('.st-key-theme_sync button');
    if (btn) btn.click();
  }
  sync();
  clearInterval(w.__tdThemeTimer);
  w.__tdThemeTimer = setInterval(sync, 500);
})();
</script>
""".replace("__THEME__", P.name),
    height=0,
)
