import streamlit as st
import plotly.graph_objects as go

from data_loader import (get_price_history, get_watchlist, add_to_watchlist,
                         remove_from_watchlist, seed_watchlist_if_empty,
                         reset_watchlist_to_preset, count_watchlist_with_data,
                         clear_price_cache)

seed_watchlist_if_empty()

st.set_page_config(page_title="Trader Desk", layout="wide", page_icon="📊", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    /* ── Layout ── */
    .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
    footer { visibility: hidden; }
    [data-testid="stSidebarCollapsedControl"] { display: none !important; }
    section[data-testid="stSidebar"] { display: none !important; }
    .clear-cache-btn {
        position: fixed; bottom: 24px; right: 24px; z-index: 999;
    }
    .clear-cache-btn a {
        display: inline-block; text-align: center; text-decoration: none;
        border: 1px solid #3d1a1a; border-radius: 6px; padding: 0.5em 0.7em;
        background: rgba(248,81,73,0.04); color: #5c1a1a;
        font-size: 8px; font-weight: 700; letter-spacing: 0.12em; line-height: 1.6;
        text-transform: uppercase; transition: all 0.2s;
    }
    .clear-cache-btn a:hover {
        border-color: #f85149; color: #f85149;
        background: rgba(248,81,73,0.08);
        box-shadow: 0 0 12px rgba(248,81,73,0.15);
    }

    /* ── Tabs ── */
    .stTabs [data-baseweb="tab"] {
        font-size: 13px; font-weight: 500; letter-spacing: 0.02em;
        color: #636e7b;
    }
    .stTabs [aria-selected="true"] { color: #e6edf3 !important; }
    .stTabs [data-baseweb="tab-border"] { background: #21262d !important; }

    /* ── Chart cards: restyle Streamlit's bordered container ── */
    [data-testid="stVerticalBlockBorderWrapper"] {
        background: #161b22 !important;
        border: 1px solid #21262d !important;
        border-radius: 10px !important;
        padding: 6px 8px 2px 8px !important;
    }

    /* ── Popover trigger → inline dotted underline label ── */
    [data-testid="stPopover"] button {
        background: transparent !important;
        border: none !important;
        padding: 0 !important;
        color: #8b949e !important;
        font-size: 10px !important;
        font-weight: 500 !important;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        height: auto !important;
        min-height: 0 !important;
        line-height: 1.4 !important;
        border-bottom: 1px dotted #444 !important;
        box-shadow: none !important;
        cursor: pointer !important;
    }
    [data-testid="stPopover"] button:hover {
        color: #c9d1d9 !important;
        border-bottom-color: #8b949e !important;
    }

    /* ── Popover panel ── */
    [data-testid="stPopoverBody"] {
        background: #1c2128 !important;
        border: 1px solid #30363d !important;
        border-radius: 8px !important;
        min-width: 0 !important;
        width: fit-content !important;
    }

    /* ── Segmented control ── */
    [data-testid="stSegmentedControl"] { gap: 2px; }

    /* ── Remove button: small muted link ── */
    [data-testid="stBaseButton-tertiary"] {
        color: #636e7b !important;
        font-size: 11px !important;
        padding: 0 4px !important;
        height: auto !important;
    }
    [data-testid="stBaseButton-tertiary"]:hover { color: #f85149 !important; }

</style>
""", unsafe_allow_html=True)

# ── Design tokens ─────────────────────────────────────────────────────────────
UP      = "#00d4aa"   # positive / teal
DOWN    = "#f85149"   # negative / red
BG      = "#0e1117"   # page background
SURFACE = "#161b22"   # card surface
BORDER  = "#21262d"   # subtle border
GRID    = "#21262d"   # chart grid
T1      = "#e6edf3"   # text primary
T2      = "#8b949e"   # text secondary
T3      = "#636e7b"   # text tertiary

PERIODS = {"1W": "5d", "1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "3Y": "3y"}

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
        f"<div style='font-size:20px;font-weight:700;color:{T1};letter-spacing:-0.01em;"
        f"padding-top:4px;'>📊 Trader Desk</div>",
        unsafe_allow_html=True,
    )
with col_period:
    st.markdown("<div style='padding-top:6px'>", unsafe_allow_html=True)
    period_label = st.segmented_control(
        "Period", list(PERIODS.keys()), default="1M", label_visibility="collapsed"
    )
    st.markdown("</div>", unsafe_allow_html=True)

period = PERIODS[period_label or "1M"]
st.divider()

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

INDICES = [
    ("S&P 500",        "^GSPC"),
    ("Nasdaq",         "^IXIC"),
    ("Nifty 50",       "^NSEI"),
    ("KOSPI (Korea)",  "^KS11"),
    ("FTSE 100 (UK)",  "^FTSE"),
    ("DAX (Germany)",  "^GDAXI"),
    ("Euro Stoxx 50",  "^STOXX50E"),
    ("Nikkei (Japan)", "^N225"),
]

INDIA = [
    ("Bank Nifty",       "^NSEBANK"),
    ("Nifty IT",         "^CNXIT"),
    ("Nifty Auto",       "NSE:NIFTY AUTO"),
    ("Nifty Energy",     "NSE:NIFTY ENERGY"),
    ("Nifty Mid Select", "NSE:NIFTY MID SELECT"),
    ("Nifty Defence",    "NSE:NIFTY INDIA DEFENCE"),
]

FX = [
    ("USD / INR", "USDINR=X"),
    ("EUR / USD", "EURUSD=X"),
    ("GBP / USD", "GBPUSD=X"),
    ("EUR / INR", "EURINR=X"),
]

COMMODITIES = [
    ("WTI Crude Oil", "CL=F"),
    ("Gold",          "GC=F"),
    ("Silver",        "SI=F"),
]

VOLATILITY = [
    ("VIX (US)", "^VIX"),
]

# ── Chart builder ─────────────────────────────────────────────────────────────

def price_chart(label, ticker, period):
    df = get_price_history(ticker, period=period)
    df = df.dropna(subset=["Close"])
    if df.empty:
        return None, None, None, None

    last  = df["Close"].iloc[-1]
    first = df["Close"].iloc[0]
    pct   = (last / first - 1) * 100
    latest_date = df.index[-1].strftime("%d %b %Y")
    color = UP if pct >= 0 else DOWN

    spread = df["Close"].max() - df["Close"].min()
    ymin = df["Close"].min() - spread * 0.1
    ymax = df["Close"].max() + spread * 0.1

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df.index, y=df["Close"],
        mode="lines",
        fill="tozeroy",
        fillcolor=f"rgba({'0,212,170' if pct >= 0 else '248,81,73'},0.10)",
        line=dict(color=color, width=1.8),
        hovertemplate="<b>%{x|%d %b %Y}</b><br>%{y:,.2f}<extra></extra>",
        name=label,
    ))
    fig.update_layout(
        height=220,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=BG,
        plot_bgcolor=BG,
        xaxis=dict(showgrid=False, showline=False, tickfont=dict(size=10, color="#8b949e"), tickformat="%d %b"),
        yaxis=dict(showgrid=True, gridcolor=GRID, gridwidth=1, showline=False, zeroline=False,
                   tickfont=dict(size=10, color="#8b949e"), side="right", range=[ymin, ymax]),
        showlegend=False,
        hoverlabel=dict(bgcolor="#1e2530", font_size=12),
    )
    return fig, last, pct, latest_date


def _constituents_popover(label, members):
    with st.popover(label):
        rows = "".join(
            f"<tr>"
            f"<td style='padding:2px 14px 2px 0;color:#c9d1d9;font-size:12.5px;"
            f"white-space:nowrap;border:none;'>· {name}</td>"
            f"<td style='padding:2px 0;color:#8b949e;font-size:12.5px;"
            f"text-align:right;border:none;'>{w:.1f}%</td>"
            f"</tr>"
            for name, w in members
        )
        st.markdown(
            f"<div style='font-size:10px;color:#8b949e;text-transform:uppercase;"
            f"letter-spacing:0.05em;margin-bottom:4px;'>Approx. weights</div>"
            f"<table style='border-collapse:collapse;border:none;'>{rows}</table>",
            unsafe_allow_html=True,
        )


def asset_card(label, ticker, period, decimal=2):
    fig, last, pct, latest_date = price_chart(label, ticker, period)
    if fig is None:
        st.warning(f"No data — {label}")
        return

    sign  = "+" if pct >= 0 else ""
    color = UP if pct >= 0 else DOWN
    members = CONSTITUENTS.get(ticker)

    with st.container(border=True):
        c_label, c_pct = st.columns([5, 2])
        with c_label:
            if members:
                _constituents_popover(label, members)
            else:
                st.markdown(
                    f"<span style='font-size:10px;color:{T2};font-weight:500;"
                    f"text-transform:uppercase;letter-spacing:0.08em;'>{label}</span>",
                    unsafe_allow_html=True,
                )
        with c_pct:
            st.markdown(
                f"<div style='text-align:right;font-size:12px;color:{color};"
                f"font-weight:600;padding-top:1px;'>{sign}{pct:.2f}%</div>",
                unsafe_allow_html=True,
            )
        st.markdown(
            f"<div style='display:flex;align-items:baseline;gap:10px;margin:-2px 0 2px 0;'>"
            f"<span style='font-size:20px;font-weight:700;color:{T1};line-height:1.1;'>"
            f"{last:,.{decimal}f}</span>"
            f"<span style='font-size:10px;color:{T3};'>{latest_date}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def render_grid(assets, period, cols=3, decimal=2):
    columns = st.columns(cols)
    for i, (label, ticker) in enumerate(assets):
        with columns[i % cols]:
            asset_card(label, ticker, period, decimal)

def _watchlist_grid(watchlist, show_1w):
    cards_html = ""
    for item in watchlist:
        ticker = item["ticker"]
        label  = item["label"]
        df = get_price_history(ticker, period="5d")
        df = df.dropna(subset=["Close"])
        if df.empty:
            continue
        last    = df["Close"].iloc[-1]
        decimal = 4 if ticker.endswith("=X") else 2
        pct = ((last / df["Close"].iloc[0]) - 1) * 100 if show_1w else (
              ((last / df["Close"].iloc[-2]) - 1) * 100 if len(df) >= 2 else 0.0)
        sign  = "+" if pct >= 0 else ""
        color = UP if pct >= 0 else DOWN

        cards_html += (
            f"<div style='background:{SURFACE};border:1px solid {BORDER};"
            f"border-radius:8px;padding:10px 14px;min-width:140px;'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;"
            f"margin-bottom:6px;'>"
            f"<span style='font-size:13px;color:{T2};font-weight:600;'>{label}</span>"
            f"<a href='?remove={ticker}' style='font-size:11px;color:{T3};text-decoration:none;"
            f"line-height:1;padding-left:10px;' title='Remove'>×</a>"
            f"</div>"
            f"<div style='font-size:22px;font-weight:700;color:{color};line-height:1;margin-bottom:4px;'>"
            f"{sign}{pct:.2f}%</div>"
            f"<div style='font-size:14px;font-weight:600;color:{T1};'>"
            f"{last:,.{decimal}f}</div>"
            f"</div>"
        )

    st.markdown(
        f"<div style='display:flex;flex-wrap:wrap;gap:12px;padding:4px 0;'>{cards_html}</div>",
        unsafe_allow_html=True,
    )


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_watchlist, tab_chart, tab_indices, tab_india, tab_fx, tab_commodities, tab_volatility = st.tabs(
    ["Watchlist", "Chart", "Indices", "India", "FX", "Commodities", "Volatility"]
)

with tab_watchlist:
    # Handle remove via query param (set by the × link in _watchlist_grid)
    to_remove = st.query_params.get("remove")
    if to_remove:
        remove_from_watchlist(to_remove)
        st.query_params.clear()
        st.rerun()

    watchlist = get_watchlist()
    count = len(watchlist)
    visible = count_watchlist_with_data()

    # ── Add instrument ────────────────────────────────────────────────────────
    st.markdown(
        f"<div style='color:#8b949e;font-size:12px;margin-bottom:6px;'>"
        f"{visible} / 30 instruments</div>",
        unsafe_allow_html=True,
    )
    col_input, col_btn = st.columns([6, 1])
    with col_input:
        new_ticker = st.text_input(
            "ticker",
            placeholder="e.g. RELIANCE.NS · AAPL · ^GSPC · NSE:NIFTY AUTO",
            label_visibility="collapsed",
            key="wl_input",
        )
    with col_btn:
        add_clicked = st.button("Add", width="stretch", disabled=(count >= 30))

    # ── Preset reset buttons ──────────────────────────────────────────────────
    st.markdown(
        f"<div style='font-size:10px;color:{T3};text-transform:uppercase;"
        f"letter-spacing:0.08em;margin:6px 0 4px 0;'>Load preset</div>",
        unsafe_allow_html=True,
    )
    p1, p2, p3, p4, p5 = st.columns(5)
    for col, preset in zip([p1, p2, p3, p4, p5],
                           ["Nifty 50", "Nifty Midcap", "Nifty Auto", "Nifty IT", "Bank Nifty"]):
        with col:
            if st.button(preset, width="stretch", key=f"preset_{preset}"):
                reset_watchlist_to_preset(preset)
                st.rerun()

    if add_clicked and new_ticker:
        ok, msg = add_to_watchlist(new_ticker.strip())
        if ok:
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)

    # ── Return toggle + grid ──────────────────────────────────────────────────
    wl_period = st.segmented_control(
        "wl_period", ["1D", "1W"], default="1D", label_visibility="collapsed", key="wl_period"
    )
    show_1w = (wl_period == "1W")

    if not watchlist:
        st.markdown(
            f"<div style='color:{T2};text-align:center;padding:48px 0;'>"
            "Your watchlist is empty — add tickers above.</div>",
            unsafe_allow_html=True,
        )
    else:
        _watchlist_grid(watchlist, show_1w)


with tab_chart:
    # Persist ticker values across tab switches via explicit session state
    for k in ("_ct1", "_ct2"):
        if k not in st.session_state:
            st.session_state[k] = ""

    def _save_t1(): st.session_state["_ct1"] = st.session_state["chart_t1"]
    def _save_t2(): st.session_state["_ct2"] = st.session_state["chart_t2"]

    c1, c2, c3 = st.columns([3, 3, 2])
    with c1:
        t1 = st.text_input("Ticker 1", placeholder="e.g. RELIANCE.NS", key="chart_t1",
                           value=st.session_state["_ct1"],
                           label_visibility="collapsed", on_change=_save_t1)
    with c2:
        t2 = st.text_input("Ticker 2 (optional)", placeholder="e.g. HDFCBANK.NS",
                           key="chart_t2", value=st.session_state["_ct2"],
                           label_visibility="collapsed", on_change=_save_t2)
    with c3:
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
            C1 = "#00d4aa"   # teal — always ticker 1
            C2 = "#e8b84b"   # amber — always ticker 2
            pct1 = (df1["Close"].iloc[-1] / df1["Close"].iloc[0] - 1) * 100
            sign1 = "+" if pct1 >= 0 else ""
            pct1_color = UP if pct1 >= 0 else DOWN

            has_t2 = df2 is not None and not df2.empty

            # ── Summary bar ───────────────────────────────────────────────────
            summary = (
                f"<div style='display:flex;gap:20px;margin-bottom:10px;align-items:center;'>"
                f"<span style='display:inline-flex;align-items:center;gap:6px;'>"
                f"<span style='width:10px;height:2px;background:{C1};display:inline-block;border-radius:2px;'></span>"
                f"<span style='font-size:12px;color:{T2};font-weight:500;'>{t1.strip()}</span>"
                f"<span style='font-size:14px;color:{pct1_color};font-weight:700;'>{sign1}{pct1:.2f}%</span>"
                f"</span>"
            )
            if has_t2:
                pct2 = (df2["Close"].iloc[-1] / df2["Close"].iloc[0] - 1) * 100
                sign2 = "+" if pct2 >= 0 else ""
                pct2_color = UP if pct2 >= 0 else DOWN
                summary += (
                    f"<span style='display:inline-flex;align-items:center;gap:6px;'>"
                    f"<span style='width:10px;height:2px;background:{C2};display:inline-block;border-radius:2px;'></span>"
                    f"<span style='font-size:12px;color:{T2};font-weight:500;'>{t2.strip()}</span>"
                    f"<span style='font-size:14px;color:{pct2_color};font-weight:700;'>{sign2}{pct2:.2f}%</span>"
                    f"</span>"
                )
            summary += "</div>"
            st.markdown(summary, unsafe_allow_html=True)

            # ── Chart ─────────────────────────────────────────────────────────
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df1.index, y=df1["Close"],
                name=t1.strip(), mode="lines",
                line=dict(color=C1, width=2),
                yaxis="y1",
                hovertemplate="<b>%{x|%d %b %Y}</b><br>%{y:,.2f}<extra>" + t1.strip() + "</extra>",
            ))

            yaxis2_cfg = {}
            if has_t2:
                fig.add_trace(go.Scatter(
                    x=df2.index, y=df2["Close"],
                    name=t2.strip(), mode="lines",
                    line=dict(color=C2, width=2),
                    yaxis="y2",
                    hovertemplate="<b>%{x|%d %b %Y}</b><br>%{y:,.2f}<extra>" + t2.strip() + "</extra>",
                ))
                yaxis2_cfg = dict(
                    tickfont=dict(size=10, color=C2), showgrid=False,
                    zeroline=False, overlaying="y", side="right",
                    tickformat=",.0f",
                )

            fig.update_layout(
                height=460,
                margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor=BG, plot_bgcolor=BG,
                xaxis=dict(showgrid=False, showline=False,
                           tickfont=dict(size=10, color=T2), tickformat="%d %b"),
                yaxis=dict(tickfont=dict(size=10, color=C1), showgrid=True,
                           gridcolor=GRID, zeroline=False, side="left",
                           tickformat=",.0f"),
                yaxis2=yaxis2_cfg if yaxis2_cfg else None,
                hoverlabel=dict(bgcolor="#1e2530", font_size=12),
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
    render_grid(FX, period, cols=3, decimal=4)

with tab_commodities:
    render_grid(COMMODITIES, period, cols=3, decimal=2)

with tab_volatility:
    render_grid(VOLATILITY, period, cols=3, decimal=2)

