"""
What can be plotted: the curated per-tab asset lists, and the searchable
instrument picker over the local symbol table.
"""

from collections import Counter

import streamlit as st

from symbols import CURATED_INSTRUMENTS, get_all_symbols


def instrument_options():
    """(labels, label -> ticker) for the instrument dropdowns."""
    # Count names first so duplicates get a ticker suffix to disambiguate.
    all_syms = get_all_symbols()
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
