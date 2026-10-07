"""
Design tokens, and the stylesheets built from them.

Two palettes with identical roles: Swiss Editorial (light) and Charcoal Warm
(dark). Colours only - geometry, type and spacing are shared.

The palette is resolved per rerun, never in a module body. Python caches an
imported module for the life of the process while Streamlit reruns only the
top-level script, so a token computed at import time would freeze on whichever
theme the first visitor happened to have. Everything that needs a colour calls
palette() instead.

Add a role to both _LIGHT and _DARK rather than writing a raw hex at a call
site - that is the rule the design language asks for, and it is the only way
both themes stay in step.
"""

import streamlit as st

# `accent` and `selected` are split from `down` on purpose: in dark they are
# accent_selected, never loss_color, so a blind hex swap would be wrong.
#
# `corr_pos`/`corr_neg`/`corr_mid` are a second scoped exception, agreed with
# Adarsh 7 Oct 2026: the Correlation tab's heatmap cells (sector matrix,
# drill-down, stock matrix) are data ink, not chrome, so they are exempt from
# the matte/single-accent rule the same way sector_colors() already is. A
# richer blue-white-red diverging scale replaces the old approach of reusing
# UP/DOWN (muted green/terracotta) for r-values, which read as mud at low
# saturation. Blue = positive correlation, red = negative - the opposite of
# this app's price-direction convention (green up / red down) everywhere
# else, which is deliberate: correlation isn't a gain or a loss, and reusing
# green/red here previously implied one wrongly. Used ONLY inside the
# correlation-value colorscale (see _value_colorscale in tabs/correlation.py)
# - never for sector identity (that stays sector_colors()), navigation, or
# anywhere else.
_LIGHT = dict(
    canvas="#DFDACB", card_bg="#D7D2C1", card_border="#BDB7A5",
    t1="#1F1D19", t2="#6B675F", t3="#9C988D",
    up="#35875B", down="#C25C36",
    accent="#C25C36", selected="#1F1D19",
    rule="#BDB7A5", tooltip_bg="#FFFFFF",
    down_rgb="194,92,54",
    corr_pos="#1D5B8A", corr_neg="#B3261E", corr_mid="#F2EFE4",
)
_DARK = dict(
    canvas="#24211E", card_bg="#302B27", card_border="#554A40",
    t1="#ECE5DA", t2="#AAA197", t3="#AAA197",
    up="#9AC69C", down="#E8A078",
    accent="#D8B58A", selected="#D8B58A",
    rule="#96816A", tooltip_bg="#302B27",
    down_rgb="232,160,120",
    corr_pos="#6FA8D8", corr_neg="#E2654C", corr_mid="#3A352F",
)


class Palette:
    """The named tokens for one theme.

    Attribute names are the roles used across the app; `raw` keeps the
    underlying dict for the CSS custom properties below.
    """

    def __init__(self, name, raw):
        self.name       = name
        self.raw        = raw
        self.UP         = raw["up"]          # positive change
        self.DOWN       = raw["down"]        # negative change
        self.BG         = raw["canvas"]      # page canvas
        self.SURFACE    = raw["card_bg"]     # card surface
        self.BORDER     = raw["card_border"] # rule / border
        self.GRID       = raw["card_border"] # chart grid
        self.T1         = raw["t1"]          # text primary
        self.T2         = raw["t2"]          # text secondary
        self.T3         = raw["t3"]          # text tertiary
        self.ACCENT     = raw["accent"]      # active navigation
        self.SELECTED   = raw["selected"]    # filled selected control, pinned mark
        self.RULE       = raw["rule"]        # editorial rules, chart axis lines
        self.TOOLTIP_BG = raw["tooltip_bg"]
        self.CORR_POS   = raw["corr_pos"]    # correlation heatmap cells only, see below
        self.CORR_NEG   = raw["corr_neg"]    # correlation heatmap cells only, see below
        self.CORR_MID   = raw["corr_mid"]    # correlation heatmap cells only, see below


# Palettes are immutable, so one instance per theme can be reused for the life
# of the process; only the *choice* between them is per-run.
_CACHE = {}


def palette():
    """The tokens for the theme this rerun is rendering with."""
    name = "dark" if getattr(st.context.theme, "type", "light") == "dark" else "light"
    if name not in _CACHE:
        _CACHE[name] = Palette(name, _DARK if name == "dark" else _LIGHT)
    return _CACHE[name]


TABULAR = "font-variant-numeric:tabular-nums lining-nums;"

#: Scoped exception to the single-accent rule, agreed with Adarsh 6 Oct
#: 2026: the sector matrix (tabs/correlation.py) needs 15 sector identities
#: distinguishable at a glance, and a 15-cell grid with no colour is the
#: unreadable wall the design review already complained about once. These
#: muted, desaturated hues are used ONLY for sector identity there (chips,
#: legend swatches, drill-down column grouping) - never for navigation,
#: controls, or anywhere else in the app, and never for correlation VALUES
#: (those use CORR_POS/CORR_NEG/CORR_MID below, a separate scoped exception).
#: Centralised here, in fno.DISPLAY_GROUPS order, so it is the one place to
#: revise.
_SECTOR_HUES_LIGHT = [
    "#A75947", "#A77F47", "#A7A547", "#83A747", "#5DA747",
    "#47A759", "#47A77F", "#47A7A5", "#4783A7", "#475DA7",
    "#5947A7", "#7F47A7", "#A547A7", "#A74783", "#A7475D",
]
_SECTOR_HUES_DARK = [
    "#C5958A", "#C5AD8A", "#C5C48A", "#AFC58A", "#97C58A",
    "#8AC595", "#8AC5AD", "#8AC5C4", "#8AAFC5", "#8A97C5",
    "#958AC5", "#AD8AC5", "#C48AC5", "#C58AAF", "#C58A97",
]


def sector_colors():
    """{display group: hex} for the current theme, fno.DISPLAY_GROUPS order."""
    import fno
    P = palette()
    hues = _SECTOR_HUES_DARK if P.name == "dark" else _SECTOR_HUES_LIGHT
    return dict(zip(fno.DISPLAY_GROUPS, hues))


# Static sheet: every colour in it reads a custom property set by _root_css, so
# this never has to be rebuilt per theme.
_MAIN_CSS = """
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
    .st-key-wl_idx button, .st-key-wl_cst button, .st-key-corr_section button {
        background: transparent !important;
        border: 1px solid var(--card-border) !important;
        border-radius: 0 !important;
        color: var(--secondary-text) !important;
        padding: 0.34em 0.8em !important;
        min-height: 0 !important; height: auto !important;
        box-shadow: none !important;
        font-family: 'Inter', Arial, sans-serif !important;
    }
    .st-key-wl_idx button p, .st-key-wl_cst button p, .st-key-corr_section button p {
        font-size: 13px !important; font-weight: 500 !important;
        color: inherit !important; margin: 0 !important;
    }
    .st-key-wl_idx button:hover, .st-key-wl_cst button:hover,
    .st-key-corr_section button:hover {
        border-color: var(--secondary-text) !important; color: var(--primary-text) !important;
    }
    .st-key-wl_idx button[aria-checked="true"], .st-key-wl_cst button[aria-checked="true"],
    .st-key-corr_section button[aria-checked="true"] {
        background: var(--selected) !important; border-color: var(--selected) !important;
        color: var(--canvas) !important;
    }
    .st-key-wl_idx button[aria-checked="true"] p,
    .st-key-wl_cst button[aria-checked="true"] p,
    .st-key-corr_section button[aria-checked="true"] p { font-weight: 600 !important; }

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
"""


def _root_css(P):
    """The palette as CSS custom properties, which _MAIN_CSS then references."""
    return f"""
<style>
    :root {{
        --canvas: {P.raw['canvas']};
        --card-bg: {P.raw['card_bg']};
        --card-border: {P.raw['card_border']};
        --primary-text: {P.raw['t1']};
        --secondary-text: {P.raw['t2']};
        --tertiary-text: {P.raw['t3']};
        --loss: {P.raw['down']};
        --accent: {P.raw['accent']};
        --selected: {P.raw['selected']};
        --rule: {P.raw['rule']};
        --loss-soft: rgba({P.raw['down_rgb']}, 0.07);
        --loss-softer: rgba({P.raw['down_rgb']}, 0.04);
        --loss-hover: rgba({P.raw['down_rgb']}, 0.16);
        --loss-hover-soft: rgba({P.raw['down_rgb']}, 0.08);
    }}
</style>
"""


def inject_css(P):
    st.markdown(_root_css(P), unsafe_allow_html=True)
    st.markdown(_MAIN_CSS, unsafe_allow_html=True)
