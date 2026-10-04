from urllib.parse import quote

import streamlit as st
import plotly.graph_objects as go

from data_loader import (get_price_history, clear_price_cache, WATCHLIST_PRESETS,
                         LIST_MAX, get_custom_lists, rename_custom_list,
                         get_list_items, add_to_list, remove_from_list,
                         count_list_with_data, get_all_symbols,
                         refresh_symbols, CURATED_INSTRUMENTS)

st.set_page_config(page_title="Trader Desk", layout="wide", page_icon="📊", initial_sidebar_state="collapsed")

refresh_symbols()   # no-op unless the instrument table is missing or stale

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    /* ── Layout ── */
    html, body, [data-testid="stAppViewContainer"], .main {
        background-color: #DFDACB !important;
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
        border: 1px solid #d4a89a; border-radius: 0; padding: 0.5em 0.7em;
        background: rgba(199,68,36,0.04); color: #c74424;
        font-size: 8px; font-weight: 700; letter-spacing: 0.12em; line-height: 1.6;
        text-transform: uppercase; transition: all 0.2s;
        font-family: 'Inter', Arial, sans-serif;
    }
    .clear-cache-btn a:hover {
        border-color: #c74424; background: rgba(199,68,36,0.08);
    }

    /* ── Tabs ── */
    .stTabs [data-baseweb="tab"] {
        font-size: 13px; font-weight: 500; letter-spacing: 0.02em;
        color: #6B675F; font-family: 'Inter', Arial, sans-serif;
    }
    .stTabs [aria-selected="true"] { color: #1F1D19 !important; }
    .stTabs [data-baseweb="tab-border"] { background: #BDB7A5 !important; }
    .stTabs [data-baseweb="tab-highlight"] { background: #C25C36 !important; }

    /* ── Chart cards: restyle Streamlit's bordered container ── */
    [data-testid="stVerticalBlockBorderWrapper"] {
        background: #D7D2C1 !important;
        border: 1px solid #BDB7A5 !important;
        border-radius: 0px !important;
        padding: 6px 8px 2px 8px !important;
    }

    /* ── Popover trigger → inline dotted underline label ── */
    [data-testid="stPopover"] button {
        background: transparent !important;
        border: none !important;
        padding: 0 !important;
        color: #1F1D19 !important;
        font-size: 14px !important;
        font-weight: 500 !important;
        letter-spacing: 0 !important;
        height: auto !important;
        min-height: 0 !important;
        line-height: 1.4 !important;
        border-bottom: 1px dotted #9C988D !important;
        box-shadow: none !important;
        cursor: pointer !important;
        font-family: 'Inter', Arial, sans-serif !important;
    }
    [data-testid="stPopover"] button:hover {
        color: #1F1D19 !important;
        border-bottom-color: #6B675F !important;
    }

    /* ── Popover panel ── */
    [data-testid="stPopoverBody"] {
        background: #DFDACB !important;
        border: 1px solid #BDB7A5 !important;
        border-radius: 4px !important;
        min-width: 0 !important;
        width: fit-content !important;
    }

    /* ── Segmented control ── */
    [data-testid="stSegmentedControl"] { gap: 2px; }

    /* ── Remove button ── */
    [data-testid="stBaseButton-tertiary"] {
        color: #6B675F !important;
        font-size: 11px !important;
        padding: 0 4px !important;
        height: auto !important;
    }
    [data-testid="stBaseButton-tertiary"]:hover { color: #C25C36 !important; }

    /* ── Inputs and buttons ── */
    [data-testid="stTextInput"] input {
        background: #D7D2C1 !important;
        border-color: #BDB7A5 !important;
        color: #1F1D19 !important;
        font-family: 'Inter', Arial, sans-serif !important;
    }
    [data-testid="stBaseButton-secondary"] {
        background: #D7D2C1 !important;
        border-color: #BDB7A5 !important;
        color: #1F1D19 !important;
        font-family: 'Inter', Arial, sans-serif !important;
    }

    /* ── Divider ── */
    hr { border-color: #BDB7A5 !important; }

    /* ── Watchlist: compact tiles that wrap and hug their content ── */
    .wl-grid {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin: 6px 0;
    }
    .wl-cell {
        background: #D7D2C1;
        border: 1px solid #BDB7A5;
        padding: 9px 13px;
        min-width: 132px;
        flex: 0 0 auto;
    }
    a.wl-remove, a.wl-remove:link, a.wl-remove:visited {
        font-size: 16px !important; color: #9C988D !important;
        text-decoration: none !important;
        line-height: 1; padding: 2px 7px; margin: -2px -7px 0 0;
    }
    a.wl-remove:hover { color: #C25C36 !important; }

    /* Streamlit colours bare anchors blue; keep our own links neutral */
    .clear-cache-btn a, .clear-cache-btn a:link, .clear-cache-btn a:visited {
        text-decoration: none !important;
    }

    /* ── View selector chips ── */
    .chip-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 2px 0; }
    .chip-label {
        font-size: 12px; color: #6B675F; text-transform: uppercase;
        letter-spacing: 0.08em; min-width: 72px;
    }
    a.chip, a.chip:link, a.chip:visited {
        display: inline-block; text-decoration: none !important;
        font-family: 'Inter', Arial, sans-serif;
        font-size: 13px; font-weight: 500;
        padding: 0.34em 0.8em; border-radius: 0;
        border: 1px solid #BDB7A5; color: #6B675F !important;
        background: transparent;
    }
    a.chip:hover { border-color: #6B675F; color: #1F1D19 !important; }
    a.chip-on, a.chip-on:link, a.chip-on:visited {
        border-color: #1F1D19; background: #1F1D19;
        color: #DFDACB !important; font-weight: 600;
    }
    a.chip-on:hover { color: #DFDACB !important; }

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
        border: 1px solid #C25C36; color: #C25C36 !important;
        background: rgba(194,92,54,0.07);
    }
    a.btn-danger:hover { background: rgba(194,92,54,0.16); }
    a.btn-quiet, a.btn-quiet:link, a.btn-quiet:visited {
        border: 1px solid #BDB7A5; color: #6B675F !important;
        background: transparent;
    }
    a.btn-quiet:hover { border-color: #6B675F; color: #1F1D19 !important; }

</style>
""", unsafe_allow_html=True)

# ── Design tokens ─────────────────────────────────────────────────────────────
UP      = "#35875B"   # positive — green
DOWN    = "#C25C36"   # negative — terracotta
BG      = "#DFDACB"   # canvas — matte, low-glare
SURFACE = "#D7D2C1"   # card surface
BORDER  = "#BDB7A5"   # rule / border
GRID    = "#BDB7A5"   # chart grid
T1      = "#1F1D19"   # text primary
T2      = "#6B675F"   # text secondary
T3      = "#9C988D"   # text tertiary

PERIODS = {"1W": "5d", "1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "3Y": "3y"}
PERIOD_LABELS = {v: k for k, v in PERIODS.items()}

PRESET_NAMES = ["Nifty 50", "Nifty Midcap", "Nifty Auto", "Nifty IT", "Bank Nifty"]

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

col_title, col_period = st.columns([2, 2])
with col_title:
    st.markdown(
        f"<div style='font-size:38px;font-weight:700;color:{T1};letter-spacing:-0.02em;"
        f"line-height:1.1;padding-top:2px;'>Trader Desk</div>",
        unsafe_allow_html=True,
    )
with col_period:
    st.markdown(
        f"<div style='font-size:12px;color:{T2};text-transform:uppercase;"
        f"letter-spacing:0.08em;margin:6px 0 2px 0;'>History — panel tabs</div>",
        unsafe_allow_html=True,
    )
    period_label = st.segmented_control(
        "Period", list(PERIODS.keys()), default="1M", label_visibility="collapsed"
    )

period = PERIODS[period_label or "1M"]
st.markdown(
    f"<div style='border-top:2px solid {T1};margin:14px 0 0 0;'></div>",
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
    labels, by_label = [], {}
    for s in get_all_symbols():
        label = f"{s['name']} — {s['ticker']}"
        labels.append(label)
        by_label[label] = s["ticker"]
    return labels, by_label


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
        hoverlabel=dict(bgcolor="#FFFFFF", bordercolor=BORDER,
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

def _watchlist_grid(watchlist, show_1w, view=None):
    """Render instrument tiles. `view` is the editable list this belongs to;
    pass None for read-only index views so no remove control is drawn."""
    window = "1W" if show_1w else "1D"
    cells = ""
    for item in watchlist:
        ticker = item["ticker"]
        label  = item["label"]
        remove = (
            f"<a href='?view={quote(view)}&remove={quote(ticker)}' target='_self' "
            f"class='wl-remove' title='Remove {label}'>&times;</a>"
            if view else ""
        )
        header = (
            f"<div style='display:flex;justify-content:space-between;align-items:center;"
            f"gap:10px;margin-bottom:5px;'>"
            f"<span style='font-size:13px;color:{T2};font-weight:600;"
            f"white-space:nowrap;'>{label}</span>{remove}</div>"
        )

        df = get_price_history(ticker, period="5d")
        df = df.dropna(subset=["Close"])

        if df.empty:
            # Keep failed instruments visible and removable (spec, high severity)
            cells += (
                f"<div class='wl-cell'>{header}"
                f"<div style='font-size:13px;color:{DOWN};line-height:1.5;'>"
                f"Couldn't load</div></div>"
            )
            continue

        last    = df["Close"].iloc[-1]
        decimal = 4 if ticker.endswith("=X") else 2
        pct = ((last / df["Close"].iloc[0]) - 1) * 100 if show_1w else (
              ((last / df["Close"].iloc[-2]) - 1) * 100 if len(df) >= 2 else 0.0)
        color = UP if pct >= 0 else DOWN

        cells += (
            f"<div class='wl-cell'>{header}"
            f"<div style='font-size:22px;font-weight:700;color:{color};"
            f"line-height:1.1;margin-bottom:3px;{TABULAR}'>{signed_pct(pct)}</div>"
            f"<div style='font-size:14px;font-weight:600;color:{T1};{TABULAR}'>"
            f"{format_price(ticker, last, decimal)}</div>"
            f"</div>"
        )

    st.markdown(f"<div class='wl-grid'>{cells}</div>", unsafe_allow_html=True)


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_watchlist, tab_chart, tab_indices, tab_india, tab_fx, tab_commodities, tab_volatility = st.tabs(
    ["Watchlist", "Chart", "Indices", "India", "FX", "Commodities", "Volatility"]
)

with tab_watchlist:
    lists = get_custom_lists()

    # The active view lives in the URL, not session state: the remove control
    # is a link, and following one is a page navigation that session state
    # does not reliably survive.
    view = st.query_params.get("view") or f"p:{PRESET_NAMES[0]}"
    is_custom = view.startswith("c:")
    list_id = int(view[2:]) if is_custom and view[2:].isdigit() else None
    if is_custom and list_id not in {l["id"] for l in lists}:
        view, is_custom, list_id = f"p:{PRESET_NAMES[0]}", False, None

    to_remove = st.query_params.get("remove")
    if to_remove and is_custom:
        remove_from_list(list_id, to_remove)
        st.query_params.clear()
        st.query_params["view"] = view
        st.rerun()

    # Adding happens through a link too, so a failure message has to travel
    # back in the URL — session state does not survive the navigation.
    to_add = st.query_params.get("add")
    if to_add and is_custom:
        ok, msg = add_to_list(list_id, to_add)
        st.query_params.clear()
        st.query_params["view"] = view
        if not ok:
            st.query_params["err"] = msg
        st.rerun()

    err = st.query_params.get("err")

    # ── View selector ─────────────────────────────────────────────────────────
    def _chips(label, items):
        chips = "".join(
            f"<a href='?view={quote(key)}' target='_self' "
            f"class='chip{' chip-on' if key == view else ''}'>{name}</a>"
            for key, name in items
        )
        return (
            f"<div class='chip-row'>"
            f"<span class='chip-label'>{label}</span>{chips}</div>"
        )

    st.markdown(
        _chips("Indices", [(f"p:{n}", n) for n in PRESET_NAMES])
        + _chips("My lists", [(f"c:{l['id']}", l["name"]) for l in lists]),
        unsafe_allow_html=True,
    )

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
                    st.query_params["err"] = msg
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
    wl_period = st.segmented_control(
        "wl_period", ["1D", "1W"], default="1D", label_visibility="collapsed", key="wl_period"
    )
    show_1w = (wl_period == "1W")

    if not items:
        st.markdown(
            f"<div style='color:{T2};padding:40px 0;'>"
            f"{active_name} is empty — add an instrument above.</div>",
            unsafe_allow_html=True,
        )
    else:
        _watchlist_grid(items, show_1w, view if is_custom else None)


with tab_chart:
    # Remember the plotted instruments across tab switches
    for k in ("_ct1", "_ct2"):
        if k not in st.session_state:
            st.session_state[k] = ""

    lbl = (f"<div style='font-size:12px;color:{T2};text-transform:uppercase;"
           f"letter-spacing:0.08em;margin:0 0 4px 0;'>{{}}</div>")
    c1, c2, c3 = st.columns([3, 3, 2])
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
    with c3:
        # This control, not the global one, governs the chart below (spec)
        st.markdown(lbl.format("History — this chart"), unsafe_allow_html=True)
        chart_period_label = st.segmented_control(
            "cp", ["1M", "3M", "6M", "1Y", "3Y"], default="3M",
            label_visibility="collapsed", key="chart_period"
        )
    chart_period = PERIODS.get(chart_period_label or "3M", "3mo")

    if t1:
        df1 = get_price_history(t1.strip(), period=chart_period)
        df1 = df1.dropna(subset=["Close"])

        df2 = None
        if t2:
            df2 = get_price_history(t2.strip(), period=chart_period)
            df2 = df2.dropna(subset=["Close"])
            if df2.empty:
                st.warning(f"No data for '{t2.strip()}'")
                df2 = None

        if df1.empty:
            st.warning(f"No data for '{t1.strip()}'")
        else:
            # Comparison identity: series 1 black solid, series 2 gray dashed
            C1, C2 = T1, T2
            name1, name2 = t1.strip(), (t2.strip() if t2 else "")
            pct1 = (df1["Close"].iloc[-1] / df1["Close"].iloc[0] - 1) * 100
            pct1_color = UP if pct1 >= 0 else DOWN

            has_t2 = df2 is not None and not df2.empty
            cp_label = chart_period_label or "3M"

            # ── Summary bar ───────────────────────────────────────────────────
            summary = (
                f"<div style='display:flex;gap:24px;margin-bottom:12px;align-items:center;"
                f"flex-wrap:wrap;'>"
                f"<span style='display:inline-flex;align-items:center;gap:8px;'>"
                f"<span style='width:16px;height:0;border-top:2px solid {C1};"
                f"display:inline-block;'></span>"
                f"<span style='font-size:14px;color:{T1};font-weight:500;'>{name1}</span>"
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
                    f"<span style='font-size:14px;color:{T1};font-weight:500;'>{name2}</span>"
                    f"<span style='font-size:14px;color:{pct2_color};font-weight:600;{TABULAR}'>"
                    f"{signed_pct(pct2)}</span>"
                    f"<span style='font-size:12px;color:{T2};'>{cp_label}</span>"
                    f"</span>"
                )
            summary += "</div>"
            st.markdown(summary, unsafe_allow_html=True)

            # ── Chart ─────────────────────────────────────────────────────────
            tickformat_x = "%b %Y" if chart_period in ("1y", "3y") else "%d %b"

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df1.index, y=df1["Close"],
                name=name1, mode="lines",
                line=dict(color=C1, width=1.8),
                yaxis="y1",
                hovertemplate="<b>%{x|%d %b %Y}</b><br>%{y:,.4f}<extra>" + name1 + "</extra>",
            ))

            yaxis2_cfg = None
            if has_t2:
                fig.add_trace(go.Scatter(
                    x=df2.index, y=df2["Close"],
                    name=name2, mode="lines",
                    line=dict(color=C2, width=1.8, dash="dash"),
                    yaxis="y2",
                    hovertemplate="<b>%{x|%d %b %Y}</b><br>%{y:,.4f}<extra>" + name2 + "</extra>",
                ))
                yaxis2_cfg = dict(
                    title=dict(text=name2, font=dict(size=12, color=T2)),
                    tickfont=dict(size=12, color=T2), showgrid=False,
                    zeroline=False, overlaying="y", side="right",
                    tickformat=",.0f",   # match left axis; never mix raw with k
                )

            fig.update_layout(
                height=460,
                margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor=BG, plot_bgcolor=BG,
                font=dict(family="Inter, Arial, sans-serif"),
                xaxis=dict(showgrid=False, showline=False,
                           tickfont=dict(size=12, color=T2), tickformat=tickformat_x),
                yaxis=dict(title=dict(text=name1, font=dict(size=12, color=T2)),
                           tickfont=dict(size=12, color=T2), showgrid=True,
                           gridcolor=GRID, zeroline=False, side="left",
                           tickformat=",.0f"),
                yaxis2=yaxis2_cfg,
                hoverlabel=dict(bgcolor="#FFFFFF", bordercolor=BORDER,
                                font=dict(size=12, color=T1,
                                          family="Inter, Arial, sans-serif")),
                showlegend=False,
            )
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    else:
        st.markdown(
            f"<div style='color:{T2};text-align:center;padding:80px 0;'>"
            f"Enter a ticker above to plot it.</div>",
            unsafe_allow_html=True,
        )

with tab_indices:
    render_grid(INDICES, period, cols=3, decimal=2)

with tab_india:
    render_grid(INDIA, period, cols=3, decimal=2)

with tab_fx:
    render_grid(FX, period, cols=2, decimal=4)

with tab_commodities:
    render_grid(COMMODITIES, period, cols=3, decimal=2)

with tab_volatility:
    # Single instrument: dominant value, larger chart (spec)
    render_grid(VOLATILITY, period, cols=1, decimal=2, height=420, value_size=40)
