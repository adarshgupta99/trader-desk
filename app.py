from datetime import date, timedelta

import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import streamlit.components.v1 as components

from data_loader import (get_price_history, clear_price_cache, WATCHLIST_PRESETS,
                         LIST_MAX, get_custom_lists, rename_custom_list,
                         get_list_items, add_to_list, remove_from_list,
                         count_list_with_data, get_all_symbols,
                         refresh_symbols, CURATED_INSTRUMENTS)

st.set_page_config(page_title="Trader Desk", layout="wide", page_icon="📊", initial_sidebar_state="collapsed")

refresh_symbols()   # reload the instrument table from data/symbols.csv

# ── Design tokens ─────────────────────────────────────────────────────────────
# Two palettes, identical roles: Swiss Editorial (light) and Charcoal Warm
# (dark). Colours only — geometry, type and spacing are shared.
# `accent` and `selected` are split from `down` on purpose: in dark they are
# accent_selected, never loss_color, so a blind hex swap would be wrong.
_LIGHT = dict(
    canvas="#DFDACB", card_bg="#D7D2C1", card_border="#BDB7A5",
    t1="#1F1D19", t2="#6B675F", t3="#9C988D",
    up="#35875B", down="#C25C36",
    accent="#C25C36", selected="#1F1D19",
    rule="#BDB7A5", tooltip_bg="#FFFFFF",
    down_rgb="194,92,54",
)
_DARK = dict(
    canvas="#24211E", card_bg="#302B27", card_border="#554A40",
    t1="#ECE5DA", t2="#AAA197", t3="#AAA197",
    up="#9AC69C", down="#E8A078",
    accent="#D8B58A", selected="#D8B58A",
    rule="#96816A", tooltip_bg="#302B27",
    down_rgb="232,160,120",
)
THEME = "dark" if getattr(st.context.theme, "type", "light") == "dark" else "light"
_P = _DARK if THEME == "dark" else _LIGHT

UP      = _P["up"]          # positive change
DOWN    = _P["down"]        # negative change
BG      = _P["canvas"]      # page canvas
SURFACE = _P["card_bg"]     # card surface
BORDER  = _P["card_border"] # rule / border
GRID    = _P["card_border"] # chart grid
T1      = _P["t1"]          # text primary
T2      = _P["t2"]          # text secondary
T3      = _P["t3"]          # text tertiary
ACCENT  = _P["accent"]      # active navigation
SELECTED = _P["selected"]   # filled selected control, pinned chart mark
RULE    = _P["rule"]        # editorial rules, chart axis lines
TOOLTIP_BG = _P["tooltip_bg"]

st.markdown(f"""
<style>
    :root {{
        --canvas: {_P['canvas']};
        --card-bg: {_P['card_bg']};
        --card-border: {_P['card_border']};
        --primary-text: {_P['t1']};
        --secondary-text: {_P['t2']};
        --tertiary-text: {_P['t3']};
        --loss: {_P['down']};
        --accent: {_P['accent']};
        --selected: {_P['selected']};
        --rule: {_P['rule']};
        --loss-soft: rgba({_P['down_rgb']}, 0.07);
        --loss-softer: rgba({_P['down_rgb']}, 0.04);
        --loss-hover: rgba({_P['down_rgb']}, 0.16);
        --loss-hover-soft: rgba({_P['down_rgb']}, 0.08);
    }}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    /* ── Layout ── */
    html, body, [data-testid="stAppViewContainer"], .main {
        background-color: var(--canvas) !important;
        font-family: 'Inter', Arial, sans-serif !important;
    }
    .block-container { padding-top: 2rem; padding-bottom: 1rem; }
    footer { visibility: hidden; }
    [data-testid="stSidebarCollapsedControl"] { display: none !important; }
    section[data-testid="stSidebar"] { display: none !important; }

    /* ── Clear cache button ── */
    .clear-cache-btn {
        position: fixed; bottom: 24px; right: 24px; z-index: 999;
    }
    .clear-cache-btn a {
        display: inline-block; text-align: center; text-decoration: none;
        border: 1px solid var(--loss); border-radius: 0; padding: 0.5em 0.7em;
        background: var(--loss-softer); color: var(--loss);
        font-size: 8px; font-weight: 700; letter-spacing: 0.12em; line-height: 1.6;
        text-transform: uppercase; transition: all 0.2s;
        font-family: 'Inter', Arial, sans-serif;
    }
    .clear-cache-btn a:hover {
        border-color: var(--loss); background: var(--loss-hover-soft);
    }

    /* ── Main navigation ──
       A segmented control restyled as plain text nav. It replaces st.tabs
       because st.tabs renders every tab's body on every run — all 53 charts,
       however few are visible. Only the selected section is built now. */
    .st-key-main_nav { margin: -4px 0 12px 0; }
    .st-key-main_nav [data-testid="stButtonGroup"] { gap: 22px !important; flex-wrap: wrap; }
    .st-key-main_nav button[data-variant="segmented_control"] + button { margin-left: 22px !important; }
    .st-key-main_nav button[data-variant="segmented_control"] {
        background: transparent !important;
        border: none !important;
        border-bottom: 2px solid transparent !important;
        border-radius: 0 !important;
        padding: 2px 0 5px 0 !important;
        min-height: 0 !important;
        height: auto !important;
        box-shadow: none !important;
        color: var(--secondary-text) !important;
        font-family: 'Inter', Arial, sans-serif !important;
    }
    .st-key-main_nav button[data-variant="segmented_control"] p {
        font-size: 13px !important; font-weight: 500 !important;
        letter-spacing: 0.02em !important; color: inherit !important;
        margin: 0 !important;
    }
    .st-key-main_nav button[data-variant="segmented_control"]:hover { color: var(--primary-text) !important; }
    .st-key-main_nav button[aria-checked="true"] {
        color: var(--primary-text) !important;
        border-bottom-color: var(--accent) !important;
    }
    .st-key-main_nav button[aria-checked="true"] p { font-weight: 600 !important; }

    /* ── Chart cards: restyle Streamlit's bordered container ── */
    [data-testid="stVerticalBlockBorderWrapper"] {
        background: var(--card-bg) !important;
        border: 1px solid var(--card-border) !important;
        border-radius: 0px !important;
        padding: 6px 8px 2px 8px !important;
    }

    /* ── Popover trigger → inline dotted underline label ── */
    [data-testid="stPopover"] button {
        background: transparent !important;
        border: none !important;
        padding: 0 !important;
        color: var(--primary-text) !important;
        font-size: 14px !important;
        font-weight: 500 !important;
        letter-spacing: 0 !important;
        height: auto !important;
        min-height: 0 !important;
        line-height: 1.4 !important;
        border-bottom: 1px dotted var(--tertiary-text) !important;
        box-shadow: none !important;
        cursor: pointer !important;
        font-family: 'Inter', Arial, sans-serif !important;
    }
    [data-testid="stPopover"] button:hover {
        color: var(--primary-text) !important;
        border-bottom-color: var(--secondary-text) !important;
    }

    /* ── Popover panel ── */
    [data-testid="stPopoverBody"] {
        background: var(--canvas) !important;
        border: 1px solid var(--card-border) !important;
        border-radius: 4px !important;
        min-width: 0 !important;
        width: fit-content !important;
    }

    /* ── Segmented control ── */
    [data-testid="stSegmentedControl"] { gap: 2px; }

    /* ── Remove button ── */
    [data-testid="stBaseButton-tertiary"] {
        color: var(--secondary-text) !important;
        font-size: 11px !important;
        padding: 0 4px !important;
        height: auto !important;
    }
    [data-testid="stBaseButton-tertiary"]:hover { color: var(--loss) !important; }

    /* ── Inputs and buttons ── */
    [data-testid="stTextInput"] input {
        background: var(--card-bg) !important;
        border-color: var(--card-border) !important;
        color: var(--primary-text) !important;
        font-family: 'Inter', Arial, sans-serif !important;
    }
    [data-testid="stBaseButton-secondary"] {
        background: var(--card-bg) !important;
        border-color: var(--card-border) !important;
        color: var(--primary-text) !important;
        font-family: 'Inter', Arial, sans-serif !important;
    }

    /* ── Divider ── */
    hr { border-color: var(--card-border) !important; }

    /* ── Watchlist: compact tiles that wrap and hug their content ──
       Built from native containers so the remove control can be a real button
       instead of a link that reloads the page. */
    .st-key-wl_tiles { gap: 10px !important; margin: 6px 0; }
    /* The tile itself. st.container(border=True) puts the border on the
       stVerticalBlock, so that is what has to carry the old .wl-cell look —
       including min-width, which is what kept tiles on a uniform 132px grid
       instead of letting each one shrink to its name. */
    .st-key-wl_tiles > div > [data-testid="stVerticalBlock"] {
        background: var(--card-bg) !important;
        border: 1px solid var(--card-border) !important;
        border-radius: 0 !important;
        padding: 9px 13px !important;
        min-width: 132px !important;
        gap: 5px !important;          /* the old header margin-bottom */
    }
    /* Streamlit stacks these as flex children with a zero basis, so they shrink
       below their own text and spill past the bottom border. Size them to content. */
    .st-key-wl_tiles > div > [data-testid="stVerticalBlock"] > * {
        flex: 0 0 auto !important;
    }
    /* Streamlit hangs margin-bottom:-16px off markdown to cancel the bottom
       margin of the <p> it normally wraps content in. Some of these tiles emit
       bare <div>s with no such margin, so the negative value had nothing to
       cancel and pulled the border up through the price. Zero both instead, and
       restore the 1.6 line-height the old .wl-cell inherited from the page. */
    .st-key-wl_tiles [data-testid="stMarkdownContainer"] {
        margin-bottom: 0 !important;
        line-height: 1.6 !important;
        font-size: 13px !important;   /* the name's line box; values set their own */
    }
    .st-key-wl_tiles [data-testid="stMarkdownContainer"] p { margin: 0 !important; }
    /* No stray vertical gaps inside a tile */
    .st-key-wl_tiles [data-testid="stVerticalBlock"] { gap: 0 !important; }
    .st-key-wl_tiles [data-testid="stHorizontalBlock"] { gap: 10px !important; }
    .st-key-wl_tiles [data-testid="stElementContainer"] { margin: 0 !important; }
    /* Remove control: sized and coloured like the × it replaces */
    .st-key-wl_tiles [data-testid="stBaseButton-tertiary"] {
        font-size: 17px !important; line-height: 1 !important;
        color: var(--tertiary-text) !important; padding: 0 2px !important;
        min-height: 0 !important; height: auto !important;
        background: transparent !important; border: none !important;
    }
    .st-key-wl_tiles [data-testid="stBaseButton-tertiary"]:hover { color: var(--loss) !important; }
    .st-key-wl_tiles [data-testid="stBaseButton-tertiary"] p {
        font-size: 17px !important; line-height: 1 !important; margin: 0 !important;
        color: inherit !important;
    }

    /* Streamlit colours bare anchors blue; keep our own links neutral */
    .clear-cache-btn a, .clear-cache-btn a:link, .clear-cache-btn a:visited {
        text-decoration: none !important;
    }

    /* ── View selector chips ──
       st.pills restyled as the square chips the design calls for. Native
       widgets, so switching view is a rerun rather than a page navigation. */
    .chip-label {
        font-size: 12px; color: var(--secondary-text); text-transform: uppercase;
        letter-spacing: 0.08em; margin-bottom: 4px;
    }
    .st-key-wl_idx button, .st-key-wl_cst button {
        background: transparent !important;
        border: 1px solid var(--card-border) !important;
        border-radius: 0 !important;
        color: var(--secondary-text) !important;
        padding: 0.34em 0.8em !important;
        min-height: 0 !important; height: auto !important;
        box-shadow: none !important;
        font-family: 'Inter', Arial, sans-serif !important;
    }
    .st-key-wl_idx button p, .st-key-wl_cst button p {
        font-size: 13px !important; font-weight: 500 !important;
        color: inherit !important; margin: 0 !important;
    }
    .st-key-wl_idx button:hover, .st-key-wl_cst button:hover {
        border-color: var(--secondary-text) !important; color: var(--primary-text) !important;
    }
    .st-key-wl_idx button[aria-checked="true"], .st-key-wl_cst button[aria-checked="true"] {
        background: var(--selected) !important; border-color: var(--selected) !important;
        color: var(--canvas) !important;
    }
    .st-key-wl_idx button[aria-checked="true"] p,
    .st-key-wl_cst button[aria-checked="true"] p { font-weight: 600 !important; }

    /* ── Inline confirm buttons: sized to their text ── */
    .confirm-row { display: flex; gap: 8px; margin: 6px 0 2px 0; }
    .confirm-row a, .confirm-row a:link, .confirm-row a:visited {
        display: inline-block;
        text-decoration: none !important;
        font-family: 'Inter', Arial, sans-serif;
        font-size: 13px; font-weight: 600;
        padding: 0.42em 0.95em;
        border-radius: 0;
    }
    a.btn-danger, a.btn-danger:link, a.btn-danger:visited {
        border: 1px solid var(--loss); color: var(--loss) !important;
        background: var(--loss-soft);
    }
    a.btn-danger:hover { background: var(--loss-hover); }
    a.btn-quiet, a.btn-quiet:link, a.btn-quiet:visited {
        border: 1px solid var(--card-border); color: var(--secondary-text) !important;
        background: transparent;
    }
    a.btn-quiet:hover { border-color: var(--secondary-text); color: var(--primary-text) !important; }

    /* the theme watcher's iframe and button: no height, no slot in the layout */
    iframe[height="0"] { display: none !important; }
    [data-testid="stElementContainer"]:has(iframe[height="0"]) { display: none !important; }
    .st-key-theme_sync { position: absolute; width: 0; height: 0; overflow: hidden;
                         opacity: 0; pointer-events: none; }

</style>
""", unsafe_allow_html=True)

PERIODS = {"1W": "5d", "1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "3Y": "3y"}
PERIOD_LABELS = {v: k for k, v in PERIODS.items()}

# Chart tab presets, and how far back each one reaches.
CHART_PRESETS = {"1M": 30, "3M": 90, "6M": 182, "1Y": 365, "3Y": 1095}

# How many calendar days of history each loader period is good for. Used to pick
# the cheapest period that still reaches back to a chosen start date.
PERIOD_COVERS = (("1mo", 31), ("3mo", 92), ("6mo", 184), ("1y", 368), ("3y", 1100))

# 3Y is the furthest back the loader will fetch — its NSE path maps anything it
# doesn't recognise to 35 days, so asking for more would quietly return less.
MAX_HISTORY_DAYS = 1095


def range_history(ticker, start, end):
    """Price history between two dates, inclusive.

    get_price_history only takes preset periods and SQLite caches on them, so
    fetch the smallest preset that still reaches `start` and slice it down.
    That reuses the existing cache instead of adding a cache key per date pair.
    """
    days = (date.today() - start).days
    period = "3y"
    for name, covers in PERIOD_COVERS:
        if days <= covers:
            period = name
            break

    df = get_price_history(ticker, period=period).dropna(subset=["Close"])
    if df.empty:
        return df
    on = pd.to_datetime(df.index).date
    return df[(on >= start) & (on <= end)]

PRESET_NAMES = ["Nifty 50", "Nifty Midcap", "Nifty Auto", "Nifty IT", "Bank Nifty"]
# Index-level ticker for each read-only view's own 1D/1W return, shown beside
# the Change window toggle. Nifty Midcap has no dedicated index ticker in the
# app, so it reuses Nifty Mid Select (Adarsh's call, 5 Oct 2026).
PRESET_INDEX_TICKER = {
    "Nifty 50": "^NSEI",
    "Nifty Midcap": "NSE:NIFTY MID SELECT",
    "Nifty Auto": "NSE:NIFTY AUTO",
    "Nifty IT": "^CNXIT",
    "Bank Nifty": "^NSEBANK",
}

TABULAR = "font-variant-numeric:tabular-nums lining-nums;"


def signed_pct(pct):
    """Format a percentage change with an explicit sign; never show -0.00."""
    if round(pct, 2) == 0:
        return "+0.00%"
    return f"{pct:+.2f}%"


def format_inr(value, decimals=2):
    """Indian digit grouping: 1234567.89 -> 12,34,567.89"""
    whole, _, frac = f"{value:.{decimals}f}".partition(".")
    neg = whole.startswith("-")
    whole = whole.lstrip("-")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return ("-" if neg else "") + whole + ("." + frac if frac else "")


def format_price(ticker, value, decimals=2):
    """Format a price with currency where the ticker makes it unambiguous."""
    if ticker.endswith((".NS", ".BO")):
        return "₹" + format_inr(value, decimals)
    return f"{value:,.{decimals}f}"


def price_decimals(ticker):
    """FX quotes keep four decimals and trailing zeros (spec); everything else two."""
    return 4 if ticker.endswith("=X") else 2


def signed_price_change(ticker, value, decimals=2):
    """Signed absolute change with the instrument's currency/unit; never -0.00."""
    if round(value, decimals) == 0:
        return "+" + format_price(ticker, 0, decimals)
    sign = "-" if value < 0 else "+"
    return sign + format_price(ticker, abs(value), decimals)


def elapsed_label(days):
    """Calendar days elapsed; weeks+days once it crosses two weeks (spec)."""
    if days <= 14:
        return f"{days}d"
    weeks, rem = divmod(days, 7)
    return f"{weeks}w {rem}d" if rem else f"{weeks}w"

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
    f"<div style='font-size:38px;font-weight:700;color:{T1};letter-spacing:-0.02em;"
    f"line-height:1.1;padding-top:2px;'>Trader Desk</div>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<div style='border-top:2px solid {T1};margin:-8px 0 0 0;'></div>",
    unsafe_allow_html=True,
)

# ── Constituents ──────────────────────────────────────────────────────────────

# Each entry: list of (name, weight%) tuples — approximate, for reference only
CONSTITUENTS = {
    "^GSPC": [
        ("Apple", 7.0), ("Microsoft", 6.5), ("Nvidia", 6.0), ("Amazon", 3.8),
        ("Meta", 2.5), ("Alphabet", 4.0), ("Berkshire", 1.7), ("Broadcom", 1.8),
        ("Tesla", 1.5), ("JPMorgan", 1.4),
    ],
    "^IXIC": [
        ("Apple", 8.5), ("Microsoft", 8.0), ("Nvidia", 7.5), ("Amazon", 5.0),
        ("Meta", 4.5), ("Alphabet", 5.0), ("Broadcom", 3.5), ("Tesla", 2.5),
        ("Costco", 2.0), ("ASML", 1.8),
    ],
    "^NSEI": [
        ("HDFC Bank", 13.0), ("Reliance", 10.0), ("ICICI Bank", 8.0), ("Infosys", 6.0),
        ("TCS", 4.0), ("Bharti Airtel", 4.0), ("ITC", 3.0), ("L&T", 3.0),
        ("Axis Bank", 3.0), ("HUL", 2.0),
    ],
    "^KS11": [
        ("Samsung Electronics", 22.0), ("SK Hynix", 6.0), ("LG Energy", 3.0), ("Hyundai Motor", 3.0),
        ("Kakao", 2.0), ("Naver", 2.0), ("Samsung SDI", 2.0), ("Posco", 1.5),
        ("KB Financial", 1.5), ("Shinhan", 1.5),
    ],
    "^FTSE": [
        ("Shell", 8.0), ("AstraZeneca", 8.0), ("HSBC", 6.0), ("Unilever", 4.0),
        ("BP", 3.0), ("Rio Tinto", 3.0), ("Diageo", 3.0), ("GSK", 3.0),
        ("BHP", 2.5), ("Rolls-Royce", 2.5),
    ],
    "^GDAXI": [
        ("SAP", 14.0), ("Siemens", 8.0), ("Allianz", 7.0), ("Deutsche Telekom", 6.0),
        ("Infineon", 4.0), ("BASF", 3.0), ("BMW", 3.0), ("Bayer", 3.0),
        ("Deutsche Bank", 3.0), ("Volkswagen", 2.5),
    ],
    "^STOXX50E": [
        ("ASML", 8.0), ("SAP", 5.0), ("LVMH", 5.0), ("Schneider Electric", 4.0),
        ("Siemens", 4.0), ("Sanofi", 3.5), ("TotalEnergies", 3.5), ("Airbus", 3.5),
        ("BNP Paribas", 3.0), ("Stellantis", 2.0),
    ],
    "^N225": [
        ("Fast Retailing", 10.0), ("Tokyo Electron", 6.0), ("SoftBank", 5.0), ("Keyence", 4.0),
        ("Fanuc", 3.0), ("Toyota", 2.5), ("Sony", 2.0), ("Mitsubishi UFJ", 1.5),
        ("Nintendo", 1.5), ("Honda", 1.0),
    ],
    "^NSEBANK": [
        ("HDFC Bank", 30.0), ("ICICI Bank", 24.0), ("Kotak Bank", 10.0), ("Axis Bank", 10.0),
        ("SBI", 9.0), ("IndusInd Bank", 4.0), ("Bank of Baroda", 2.0), ("PNB", 2.0),
        ("IDFC First", 2.0), ("Federal Bank", 1.5),
    ],
    "^CNXIT": [
        ("TCS", 24.0), ("Infosys", 23.0), ("HCL Tech", 14.0), ("Wipro", 9.0),
        ("Tech Mahindra", 6.0), ("LTIMindtree", 6.0), ("Persistent", 4.0), ("Mphasis", 3.0),
        ("Coforge", 2.0), ("KPIT Tech", 2.0),
    ],
    "NSE:NIFTY AUTO": [
        ("Maruti", 22.0), ("M&M", 20.0), ("Tata Motors", 12.0), ("Bajaj Auto", 10.0),
        ("Hero MotoCorp", 8.0), ("Eicher Motors", 7.0), ("TVS Motor", 6.0), ("Bosch", 4.0),
        ("MRF", 3.0), ("Ashok Leyland", 2.0),
    ],
    "NSE:NIFTY ENERGY": [
        ("Reliance", 35.0), ("ONGC", 15.0), ("NTPC", 12.0), ("PowerGrid", 8.0),
        ("BPCL", 5.0), ("IOC", 5.0), ("Tata Power", 4.0), ("Adani Green", 4.0),
        ("Coal India", 4.0), ("GAIL", 3.0),
    ],
    "NSE:NIFTY MID SELECT": [
        ("Trent", 5.0), ("Persistent", 4.5), ("Tube Investments", 4.0), ("Kalyan Jewellers", 3.5),
        ("Oberoi Realty", 3.5), ("Bharat Forge", 3.0), ("Cummins India", 3.0), ("JSW Energy", 3.0),
        ("KPIT Tech", 3.0), ("Voltas", 2.5),
    ],
    "NSE:NIFTY INDIA DEFENCE": [
        ("HAL", 30.0), ("BEL", 20.0), ("Bharat Dynamics", 10.0), ("Mazagon Dock", 8.0),
        ("BEML", 7.0), ("Garden Reach", 7.0), ("Cochin Shipyard", 6.0), ("Data Patterns", 4.0),
        ("Paras Defence", 2.0), ("DCX Systems", 2.0),
    ],
}

# ── Asset definitions ─────────────────────────────────────────────────────────

def instrument_options():
    """(labels, label -> ticker) for the instrument dropdowns."""
    # Count names first so duplicates get a ticker suffix to disambiguate.
    all_syms = get_all_symbols()
    from collections import Counter
    name_counts = Counter(s["name"] for s in all_syms)
    labels, by_label = [], {}
    for s in all_syms:
        label = s["name"] if name_counts[s["name"]] == 1 else f"{s['name']} ({s['ticker']})"
        labels.append(label)
        by_label[label] = s["ticker"]
    return labels, by_label


def display_name(ticker):
    """Friendly instrument name for display, falling back to the raw ticker
    (e.g. a US ticker typed outside the local NSE/curated table)."""
    for s in get_all_symbols():
        if s["ticker"] == ticker:
            return s["name"]
    return ticker


def pick_instrument(key, placeholder, disabled=False, default=None):
    """Searchable dropdown over the instrument table. Returns a ticker, or None.

    `accept_new_options` keeps anything outside the table reachable — the table
    is NSE plus curated entries, so a US ticker is typed rather than picked.
    `filter_mode="contains"` keeps our ordering instead of re-scoring matches.
    """
    labels, by_label = instrument_options()
    index = None
    if default:
        match = next((l for l, t in by_label.items() if t == default), None)
        if match:
            index = labels.index(match)
        else:                       # a raw ticker that is not in the table
            labels = [default] + labels
            index = 0
    choice = st.selectbox(
        key, labels, index=index, placeholder=placeholder,
        label_visibility="collapsed", key=key, disabled=disabled,
        accept_new_options=True, filter_mode="contains",
    )
    if not choice:
        return None
    return by_label.get(choice, choice.strip().upper())


def _group(category):
    return [(name, ticker) for cat, name, ticker in CURATED_INSTRUMENTS
            if cat == category]


INDICES     = _group("Indices")
INDIA       = _group("India")
FX          = _group("FX")
COMMODITIES = _group("Commodities")
VOLATILITY  = _group("Volatility")

# ── Chart builder ─────────────────────────────────────────────────────────────

def price_chart(label, ticker, period, height=220):
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
        line=dict(color=(UP if pct >= 0 else DOWN), width=1.5),
        hovertemplate="<b>%{x|%d %b %Y}</b><br>%{y:,.2f}<extra></extra>",
        name=label,
    ))
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family="Inter, Arial, sans-serif"),
        xaxis=dict(showgrid=False, showline=False, tickfont=dict(size=12, color=T2),
                   tickformat=tickformat),
        yaxis=dict(showgrid=True, gridcolor=GRID, gridwidth=1, showline=False, zeroline=False,
                   tickfont=dict(size=12, color=T2), side="right", range=[ymin, ymax]),
        showlegend=False,
        hoverlabel=dict(bgcolor=TOOLTIP_BG, bordercolor=BORDER,
                        font=dict(size=12, color=T1, family="Inter, Arial, sans-serif")),
    )
    return fig, last, pct, latest_date


def _constituents_popover(label, members):
    with st.popover(label):
        rows = "".join(
            f"<tr style='border-bottom:1px solid {BORDER};'>"
            f"<td style='padding:4px 18px 4px 0;color:{T1};font-size:13px;"
            f"white-space:nowrap;border:none;'>{name}</td>"
            f"<td style='padding:4px 0;color:{T2};font-size:13px;"
            f"text-align:right;border:none;"
            f"font-variant-numeric:tabular-nums lining-nums;'>{w:.1f}%</td>"
            f"</tr>"
            for name, w in members
        )
        st.markdown(
            f"<div style='font-size:12px;color:{T2};text-transform:uppercase;"
            f"letter-spacing:0.05em;margin-bottom:6px;"
            f"padding-bottom:4px;border-bottom:1px solid {BORDER};'>Approximate weights</div>"
            f"<table style='border-collapse:collapse;border:none;'>{rows}</table>",
            unsafe_allow_html=True,
        )


def asset_card(label, ticker, period, decimal=2, height=220, value_size=28):
    fig, last, pct, latest_date = price_chart(label, ticker, period, height)
    if fig is None:
        st.warning(f"No data — {label}")
        return

    color = UP if pct >= 0 else DOWN
    members = CONSTITUENTS.get(ticker)
    period_label = PERIOD_LABELS.get(period, period)

    with st.container(border=True):
        c_label, c_pct = st.columns([5, 3])
        with c_label:
            if members:
                _constituents_popover(label, members)
            else:
                st.markdown(
                    f"<span style='font-size:14px;color:{T1};font-weight:500;'>{label}</span>",
                    unsafe_allow_html=True,
                )
        with c_pct:
            st.markdown(
                f"<div style='text-align:right;padding-top:1px;'>"
                f"<span style='font-size:14px;color:{color};font-weight:600;{TABULAR}'>"
                f"{signed_pct(pct)}</span>"
                f"<span style='font-size:12px;color:{T2};padding-left:5px;'>{period_label}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        st.markdown(
            f"<div style='display:flex;align-items:baseline;gap:10px;margin:2px 0 4px 0;'>"
            f"<span style='font-size:{value_size}px;font-weight:600;color:{T1};"
            f"line-height:1.1;{TABULAR}'>{last:,.{decimal}f}</span>"
            f"<span style='font-size:12px;color:{T2};'>{latest_date}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def render_grid(assets, period, cols=3, decimal=2, height=220, value_size=28):
    columns = st.columns(cols)
    for i, (label, ticker) in enumerate(assets):
        with columns[i % cols]:
            asset_card(label, ticker, period, decimal, height, value_size)

def _watchlist_grid(watchlist, show_1w, list_id=None):
    """Render instrument tiles. `list_id` is the editable list these belong to;
    pass None for read-only index views so no remove control is drawn.

    Tiles are native containers rather than one HTML block so that the remove
    control can be a real button. As a link it was a page navigation, which
    reloaded the whole app — blanking the page and resetting scroll.
    """
    with st.container(horizontal=True, wrap=True, gap="small", key="wl_tiles"):
        for item in watchlist:
            ticker = item["ticker"]
            label  = item["label"]

            with st.container(border=True, width="content", key=f"wl_{ticker}"):
                head = st.container(horizontal=True, gap="small",
                                    vertical_alignment="center")
                with head:
                    st.markdown(
                        f"<span style='font-size:13px;color:{T2};font-weight:600;"
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
                        f"<div style='font-size:13px;color:{DOWN};line-height:1.5;'>"
                        f"Couldn't load</div>",
                        unsafe_allow_html=True,
                    )
                    continue

                last    = df["Close"].iloc[-1]
                decimal = 4 if ticker.endswith("=X") else 2
                pct = ((last / df["Close"].iloc[0]) - 1) * 100 if show_1w else (
                      ((last / df["Close"].iloc[-2]) - 1) * 100 if len(df) >= 2 else 0.0)
                color = UP if pct >= 0 else DOWN

                st.markdown(
                    f"<div style='font-size:22px;font-weight:700;color:{color};"
                    f"line-height:1.1;margin-bottom:3px;{TABULAR}'>{signed_pct(pct)}</div>"
                    f"<div style='font-size:14px;font-weight:600;color:{T1};{TABULAR}'>"
                    f"{format_price(ticker, last, decimal)}</div>",
                    unsafe_allow_html=True,
                )


# ── Tabs ──────────────────────────────────────────────────────────────────────

# Only the selected section is rendered. st.tabs would run every tab's body on
# every interaction — 53 charts rebuilt to show at most 8 — which cost ~650ms a
# click regardless of what was clicked.
NAV = ["Watchlist", "Chart", "Indices", "India", "FX", "Commodities", "Volatility", "Events"]


# `required` keeps a section selected: without it, clicking the active item
# deselects it and the app falls back to the first section.
nav = st.segmented_control(
    "nav", NAV, default=NAV[0], required=True,
    label_visibility="collapsed", key="main_nav",
)

_period_lbl = (f"<div style='font-size:12px;color:{T2};text-transform:uppercase;"
               f"letter-spacing:0.08em;margin:0 0 2px 0;'>History</div>")

if nav == "Watchlist":
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
            f"<div style='color:{T2};font-size:12px;margin:10px 0 6px 0;'>{status}</div>",
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
                f"<div style='color:{DOWN};font-size:12px;margin:2px 0 4px 0;'>{err}</div>",
                unsafe_allow_html=True,
            )

        if is_full:
            note = f"This list is full ({LIST_MAX} of {LIST_MAX}). Remove one to add another."
            if failed:
                note += f" Entries that couldn't load still count ({failed})."
            st.markdown(
                f"<div style='color:{DOWN};font-size:12px;margin:-2px 0 4px 0;'>{note}</div>",
                unsafe_allow_html=True,
            )
        elif picked:
            if picked in {i["ticker"] for i in items}:
                st.markdown(
                    f"<div style='color:{T2};font-size:12px;margin:2px 0 4px 0;'>"
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
        f"<div style='font-size:12px;color:{T2};text-transform:uppercase;"
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
            idx_color = UP if idx_pct >= 0 else DOWN
            idx_date = idf.index[-1].strftime("%d %b %Y")
            idx_price = format_price(idx_ticker, idx_last, price_decimals(idx_ticker))
            with c_idx:
                st.markdown(
                    f"<div style='display:flex;align-items:center;gap:8px;'>"
                    f"<span style='font-size:13px;color:{T2};font-weight:600;"
                    f"white-space:nowrap;'>{active_name}</span>"
                    f"<span style='font-size:19px;font-weight:700;color:{T1};"
                    f"{TABULAR}'>{idx_price}</span>"
                    f"<span style='font-size:19px;font-weight:700;color:{idx_color};"
                    f"{TABULAR}'>{signed_pct(idx_pct)}</span>"
                    f"<span style='font-size:12px;color:{T2};'>{wl_period}</span>"
                    f"<span style='font-size:12px;color:{T2};'>as of {idx_date}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    if not items:
        st.markdown(
            f"<div style='color:{T2};padding:40px 0;'>"
            f"{active_name} is empty — add an instrument above.</div>",
            unsafe_allow_html=True,
        )
    else:
        _watchlist_grid(items, show_1w, list_id if is_custom else None)


elif nav == "Chart":
    # Remember the plotted instruments across tab switches
    for k in ("_ct1", "_ct2"):
        if k not in st.session_state:
            st.session_state[k] = ""

    lbl = (f"<div style='font-size:12px;color:{T2};text-transform:uppercase;"
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
            C1, C2 = T1, T2
            name1, name2 = t1.strip(), (t2.strip() if t2 else "")
            disp1, disp2 = display_name(name1), (display_name(name2) if name2 else "")
            m_dec = price_decimals(name1)
            m2_dec = price_decimals(name2) if has_t2 else None
            pct1 = (df1["Close"].iloc[-1] / df1["Close"].iloc[0] - 1) * 100
            pct1_color = UP if pct1 >= 0 else DOWN

            cp_label = span_label

            # ── Summary bar ───────────────────────────────────────────────────
            summary = (
                f"<div style='display:flex;gap:24px;margin-bottom:12px;align-items:center;"
                f"flex-wrap:wrap;'>"
                f"<span style='display:inline-flex;align-items:center;gap:8px;'>"
                f"<span style='width:16px;height:0;border-top:2px solid {C1};"
                f"display:inline-block;'></span>"
                f"<span style='font-size:14px;color:{T1};font-weight:500;'>{disp1}</span>"
                f"<span style='font-size:14px;color:{pct1_color};font-weight:600;{TABULAR}'>"
                f"{signed_pct(pct1)}</span>"
                f"<span style='font-size:12px;color:{T2};'>{cp_label}</span>"
                f"</span>"
            )
            if has_t2:
                pct2 = (df2["Close"].iloc[-1] / df2["Close"].iloc[0] - 1) * 100
                pct2_color = UP if pct2 >= 0 else DOWN
                summary += (
                    f"<span style='display:inline-flex;align-items:center;gap:8px;'>"
                    f"<span style='width:16px;height:0;border-top:2px dashed {C2};"
                    f"display:inline-block;'></span>"
                    f"<span style='font-size:14px;color:{T1};font-weight:500;'>{disp2}</span>"
                    f"<span style='font-size:14px;color:{pct2_color};font-weight:600;{TABULAR}'>"
                    f"{signed_pct(pct2)}</span>"
                    f"<span style='font-size:12px;color:{T2};'>{cp_label}</span>"
                    f"</span>"
                )
            summary += "</div>"
            st.markdown(summary, unsafe_allow_html=True)

            # ── Measure readout ───────────────────────────────────────────────
            if measure_ready:
                m_chg = mb["close"] - ma["close"]
                m_pct = ((mb["close"] / ma["close"]) - 1) * 100 if ma["close"] else 0.0
                m_color = UP if m_pct >= 0 else DOWN
                m_dir = "up" if m_pct >= 0 else "down"
                pos_a, pos_b = df1.index.get_loc(ma["date"]), df1.index.get_loc(mb["date"])
                m_bars = pos_b - pos_a + 1
                m_days = (mb["date"] - ma["date"]).days

                row2 = ""
                if has_t2 and "close2" in ma and "close2" in mb:
                    m_chg2 = mb["close2"] - ma["close2"]
                    m_pct2 = (((mb["close2"] / ma["close2"]) - 1) * 100
                              if ma["close2"] else 0.0)
                    m_color2 = UP if m_pct2 >= 0 else DOWN
                    row2 = (
                        f"<span style='font-size:12px;color:{T2};'>{disp2}</span>"
                        f"<span style='font-size:19px;font-weight:700;color:{m_color2};{TABULAR}'>"
                        f"{signed_pct(m_pct2)}</span>"
                        f"<span style='font-size:14px;font-weight:600;color:{m_color2};{TABULAR}'>"
                        f"{signed_price_change(name2, m_chg2, m2_dec)}</span>"
                    )

                st.markdown(
                    f"<div style='display:flex;gap:26px;align-items:center;flex-wrap:wrap;"
                    f"border:1px solid {BORDER};padding:10px 14px;margin-bottom:12px;'>"
                    f"<span style='font-size:11px;color:{T2};text-transform:uppercase;"
                    f"letter-spacing:0.08em;'>Measured, {m_dir}</span>"
                    f"<span style='font-size:12px;color:{T2};'>{disp1}</span>"
                    f"<span style='font-size:19px;font-weight:700;color:{m_color};{TABULAR}'>"
                    f"{signed_pct(m_pct)}</span>"
                    f"<span style='font-size:14px;font-weight:600;color:{m_color};{TABULAR}'>"
                    f"{signed_price_change(t1.strip(), m_chg, m_dec)}</span>"
                    f"{row2}"
                    f"<span style='font-size:13px;color:{T2};{TABULAR}'>{m_bars} bars</span>"
                    f"<span style='font-size:13px;color:{T2};{TABULAR}'>{elapsed_label(m_days)}</span>"
                    f"<span style='font-size:12px;color:{T2};{TABULAR}'>"
                    f"{ma['date']:%d %b %Y} → {mb['date']:%d %b %Y}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            elif measure_armed:
                st.markdown(
                    f"<div style='font-size:12px;color:{T2};margin-bottom:12px;'>"
                    f"Measure — hover {disp1} to preview the change, click to set "
                    f"the end point.</div>",
                    unsafe_allow_html=True,
                )
            elif measure_on:
                st.markdown(
                    f"<div style='font-size:12px;color:{T2};margin-bottom:12px;'>"
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
                    fillcolor=(T1 if m_pct >= 0 else DOWN),
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
                    title=dict(text=disp2, font=dict(size=12, color=T2)),
                    tickfont=dict(size=12, color=T2), showgrid=False,
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
                fig.add_vline(x=ma["date"], line=dict(color=SELECTED, width=1, dash="dot"))
                fig.add_trace(go.Scatter(
                    x=[ma["date"]], y=[ma["close"]],
                    mode="markers", marker=dict(size=9, color=SELECTED,
                                                 line=dict(color=BG, width=1.5)),
                    hoverinfo="skip", showlegend=False, name="",
                ))

            fig.update_layout(
                height=460,
                margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor=BG, plot_bgcolor=BG,
                font=dict(family="Inter, Arial, sans-serif"),
                xaxis=dict(showgrid=False, showline=False,
                           tickfont=dict(size=12, color=T2), tickformat=tickformat_x),
                yaxis=dict(title=dict(text=disp1, font=dict(size=12, color=T2)),
                           tickfont=dict(size=12, color=T2), showgrid=True,
                           gridcolor=GRID, zeroline=False, side="left",
                           tickformat=",.0f"),
                yaxis2=yaxis2_cfg,
                hoverlabel=dict(bgcolor=TOOLTIP_BG, bordercolor=BORDER,
                                font=dict(size=12, color=T1,
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
            f"<div style='color:{T2};text-align:center;padding:80px 0;'>"
            f"Enter a ticker above to plot it.</div>",
            unsafe_allow_html=True,
        )

elif nav == "Indices":
    st.markdown(_period_lbl, unsafe_allow_html=True)
    period_label = st.segmented_control("pi", list(PERIODS.keys()), default="1M", label_visibility="collapsed", key="period_indices")
    render_grid(INDICES, PERIODS[period_label or "1M"], cols=3, decimal=2)

elif nav == "India":
    st.markdown(_period_lbl, unsafe_allow_html=True)
    period_label = st.segmented_control("pi", list(PERIODS.keys()), default="1M", label_visibility="collapsed", key="period_india")
    render_grid(INDIA, PERIODS[period_label or "1M"], cols=3, decimal=2)

elif nav == "FX":
    st.markdown(_period_lbl, unsafe_allow_html=True)
    period_label = st.segmented_control("pi", list(PERIODS.keys()), default="1M", label_visibility="collapsed", key="period_fx")
    render_grid(FX, PERIODS[period_label or "1M"], cols=2, decimal=4)

elif nav == "Commodities":
    st.markdown(_period_lbl, unsafe_allow_html=True)
    period_label = st.segmented_control("pi", list(PERIODS.keys()), default="1M", label_visibility="collapsed", key="period_commodities")
    render_grid(COMMODITIES, PERIODS[period_label or "1M"], cols=3, decimal=2)

elif nav == "Volatility":
    st.markdown(_period_lbl, unsafe_allow_html=True)
    period_label = st.segmented_control("pi", list(PERIODS.keys()), default="1M", label_visibility="collapsed", key="period_volatility")
    render_grid(VOLATILITY, PERIODS[period_label or "1M"], cols=1, decimal=2, height=420, value_size=40)

elif nav == "Events":
    from datetime import datetime, timezone
    from events_db import get_upcoming_events, get_last_success, IST

    SUBTYPE_LABELS = {
        "dividend": "Dividend", "split": "Split", "bonus": "Bonus",
        "rights": "Rights", "buyback": "Buyback", "fundraise": "Fundraise",
        "agm": "AGM", "results": "Results", "other": "Other",
    }

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
        f"<div style='color:{T2};font-size:12px;margin-bottom:10px;'>Last updated: {updated_label}</div>",
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
            f"style='color:inherit;text-decoration:none;border-bottom:1px dotted {T3};'>{text}</a>"
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
            f"<div style='color:{T2};font-size:13px;margin-top:14px;'>{empty_label}</div>",
            unsafe_allow_html=True,
        )
    elif subtab == "Calendar":
        rows = "".join(
            f"<tr style='border-bottom:1px solid {BORDER};'>"
            f"<td style='padding:6px 16px 6px 0;color:{T2};font-size:13px;white-space:nowrap;"
            f"border:none;{TABULAR}'>{_fmt_date(r.event_date)}</td>"
            f"<td style='padding:6px 16px 6px 0;color:{T1};font-size:13px;white-space:nowrap;"
            f"border:none;'>{r.symbol}</td>"
            f"<td style='padding:6px 0;color:{T2};font-size:13px;border:none;'>"
            f"{_agenda_link(r.symbol, r.purpose, 'board_meeting')}</td>"
            f"</tr>"
            for r in type_df.itertuples()
        )
        header = (
            f"<tr style='border-bottom:1px solid {BORDER};'>"
            f"<th style='padding:4px 16px 6px 0;color:{T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Board meeting</th>"
            f"<th style='padding:4px 16px 6px 0;color:{T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Symbol</th>"
            f"<th style='padding:4px 0 6px 0;color:{T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Agenda</th>"
            f"</tr>"
        )
        st.markdown(
            f"<table style='border-collapse:collapse;border:none;width:100%;margin-top:10px;'>{header}{rows}</table>",
            unsafe_allow_html=True,
        )
    else:
        rows = "".join(
            f"<tr style='border-bottom:1px solid {BORDER};'>"
            f"<td style='padding:6px 16px 6px 0;color:{T2};font-size:13px;white-space:nowrap;"
            f"border:none;{TABULAR}'>{_fmt_date(r.event_date)}</td>"
            f"<td style='padding:6px 16px 6px 0;color:{T1};font-size:13px;white-space:nowrap;"
            f"border:none;'>{r.symbol}</td>"
            f"<td style='padding:6px 16px 6px 0;color:{T2};font-size:13px;white-space:nowrap;"
            f"border:none;{TABULAR}'>{_fmt_date(r.record_date)}</td>"
            f"<td style='padding:6px 16px 6px 0;color:{T2};font-size:13px;white-space:nowrap;"
            f"border:none;'>{SUBTYPE_LABELS.get(r.subtype, r.subtype.title())}</td>"
            f"<td style='padding:6px 0;color:{T2};font-size:13px;border:none;'>"
            f"{_agenda_link(r.symbol, r.purpose, 'corp_action')}</td>"
            f"</tr>"
            for r in type_df.itertuples()
        )
        header = (
            f"<tr style='border-bottom:1px solid {BORDER};'>"
            f"<th style='padding:4px 16px 6px 0;color:{T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Ex-date</th>"
            f"<th style='padding:4px 16px 6px 0;color:{T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Symbol</th>"
            f"<th style='padding:4px 16px 6px 0;color:{T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Record date</th>"
            f"<th style='padding:4px 16px 6px 0;color:{T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Type</th>"
            f"<th style='padding:4px 0 6px 0;color:{T3};font-size:12px;text-align:left;"
            f"text-transform:uppercase;letter-spacing:0.05em;border:none;'>Details</th>"
            f"</tr>"
        )
        st.markdown(
            f"<table style='border-collapse:collapse;border:none;width:100%;margin-top:10px;'>{header}{rows}</table>",
            unsafe_allow_html=True,
        )

    st.markdown(
        f"<div style='color:{T3};font-size:12px;margin-top:16px;'>"
        f"Results dates appear only about 1-3 weeks ahead, after the company files "
        f"with the exchange. Dates can be revised. This list updates daily and is "
        f"not a complete calendar.</div>",
        unsafe_allow_html=True,
    )

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
""".replace("__THEME__", THEME),
    height=0,
)
