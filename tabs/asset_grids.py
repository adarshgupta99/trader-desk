"""
Indices, India, FX, Commodities and Volatility: the same small-multiples grid
over five different asset lists, so they share one renderer rather than five
near-identical blocks.
"""

import streamlit as st

from components import period_label, render_grid
from instruments import COMMODITIES, FX, INDIA, INDICES, VOLATILITY
from periods import PERIODS

# Per tab: the assets, how many columns, decimals, chart height, value size.
GRIDS = {
    "Indices":     (INDICES,     3, 2, 220, 28),
    "India":       (INDIA,       3, 2, 220, 28),
    "FX":          (FX,          2, 4, 220, 28),
    "Commodities": (COMMODITIES, 3, 2, 220, 28),
    "Volatility":  (VOLATILITY,  1, 2, 420, 40),
}


def render(name):
    assets, cols, decimal, height, value_size = GRIDS[name]
    st.markdown(period_label("History"), unsafe_allow_html=True)
    # Keyed per tab so each one keeps its own selection across switches.
    choice = st.segmented_control(
        "pi", list(PERIODS.keys()), default="1M",
        label_visibility="collapsed", key=f"period_{name.lower()}",
    )
    render_grid(assets, PERIODS[choice or "1M"], cols=cols, decimal=decimal,
                height=height, value_size=value_size)
