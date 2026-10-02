import streamlit as st
import plotly.graph_objects as go

from data_loader import get_price_history

st.set_page_config(page_title="Trader Desk", layout="wide", page_icon="📊")

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
    .stTabs [data-baseweb="tab"] { font-size: 15px; font-weight: 500; }
    footer { visibility: hidden; }

    /* Dark card: target bordered containers */
    [data-testid="stVerticalBlockBorderWrapper"] {
        background: #161b22 !important;
        border-color: #2d333b !important;
        border-radius: 10px !important;
        padding: 4px 6px 0 6px !important;
    }

    /* Popover trigger → plain label style */
    [data-testid="stPopover"] button {
        background: transparent !important;
        border: none !important;
        padding: 0 2px !important;
        color: #8b949e !important;
        font-size: 13px !important;
        font-weight: 500 !important;
        height: auto !important;
        min-height: 0 !important;
        line-height: 1.4 !important;
        border-bottom: 1px dotted #555 !important;
        box-shadow: none !important;
        cursor: pointer !important;
    }
    [data-testid="stPopover"] button:hover {
        color: #e6edf3 !important;
        border-bottom-color: #8b949e !important;
    }

    /* Popover panel */
    [data-testid="stPopoverBody"] {
        background: #21262d !important;
        border: 1px solid #30363d !important;
        border-radius: 8px !important;
        min-width: 0 !important;
        width: fit-content !important;
    }
</style>
""", unsafe_allow_html=True)

PERIODS = {"1W": "5d", "1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "3Y": "3y"}

col_title, col_period = st.columns([2, 2])
with col_title:
    st.markdown("## 📊 Trader Desk")
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

UP   = "#00d4aa"
DOWN = "#ff4d4d"
BG   = "#0e1117"
GRID = "#1e2530"


def price_chart(label, ticker, period):
    df = get_price_history(ticker, period=period)
    df = df.dropna(subset=["Close"])
    if df.empty:
        return None, None, None

    last  = df["Close"].iloc[-1]
    first = df["Close"].iloc[0]
    pct   = (last / first - 1) * 100
    color = UP if pct >= 0 else DOWN

    spread = df["Close"].max() - df["Close"].min()
    ymin = df["Close"].min() - spread * 0.1
    ymax = df["Close"].max() + spread * 0.1

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df.index, y=df["Close"],
        mode="lines",
        fill="tozeroy",
        fillcolor=f"rgba({'0,212,170' if pct >= 0 else '255,77,77'},0.10)",
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
    return fig, last, pct


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
    fig, last, pct = price_chart(label, ticker, period)
    if fig is None:
        st.warning(f"No data — {label}")
        return

    sign  = "+" if pct >= 0 else ""
    color = UP if pct >= 0 else DOWN
    members = CONSTITUENTS.get(ticker)

    with st.container(border=True):
        # Header row: label/popover | spacer | pct
        c_label, c_pct = st.columns([5, 2])
        with c_label:
            if members:
                _constituents_popover(label, members)
            else:
                st.markdown(
                    f"<span style='font-size:13px;color:#8b949e;font-weight:500;'>{label}</span>",
                    unsafe_allow_html=True,
                )
        with c_pct:
            st.markdown(
                f"<div style='text-align:right;font-size:13px;color:{color};"
                f"font-weight:600;padding-top:2px;'>{sign}{pct:.2f}%</div>",
                unsafe_allow_html=True,
            )

        # Price
        st.markdown(
            f"<div style='font-size:22px;font-weight:700;color:#e6edf3;"
            f"margin:-4px 0 4px 0;'>{last:,.{decimal}f}</div>",
            unsafe_allow_html=True,
        )

        # Chart
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def render_grid(assets, period, cols=3, decimal=2):
    columns = st.columns(cols)
    for i, (label, ticker) in enumerate(assets):
        with columns[i % cols]:
            asset_card(label, ticker, period, decimal)

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_indices, tab_india, tab_fx, tab_commodities, tab_volatility = st.tabs(
    ["Indices", "India", "FX", "Commodities", "Volatility"]
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
