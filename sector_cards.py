"""Sector slot-cards: which sector-specific metrics an extraction should look
for in a given company's earnings call (spec section 3.3).

Reads data/sector_cards.json and nothing else. The symbol -> sector mapping is
NOT built here: it comes from the app's existing sector data (fno.py /
fno_sectors.json), which the Correlation tab, the Calendar sector filter and
the Sentiment tab's sector tiles already use. Adarsh's call, 16 Oct 2026 -
the spec's own proposal of a fresh LLM-built sector_map was dropped in favour
of the taxonomy already in the repo, so there is exactly one sector truth in
this app.

Resolution order for a symbol:
    fno.display_groups_of(symbol)[0]   -> its primary display group
    group_to_card[group]               -> a card key, or None

A None card is the spec's UNMAPPED path (3.4): extraction still runs, but
universal-only - the 10-column sheet plus the universal frame block, with no
sector slots. That is a first-class outcome, not a failure; six of the app's
fifteen display groups have no cheat-sheet card and Adarsh chose not to invent
any (spec 3.5 default).

Conglomerates take the primary group only - no per-segment slots in v1
(Adarsh, 16 Oct 2026). Reliance resolves to Energy and therefore runs
universal-only.
"""

import functools
import json
import os

import fno

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "data", "sector_cards.json")


@functools.lru_cache(maxsize=1)
def _data():
    with open(_PATH) as f:
        return json.load(f)


def version():
    """Config version string, stored alongside extracted rows so a slot list
    change is traceable in the data."""
    return _data()["version"]


def group_to_card(group):
    """Card key for a display group, or None when the group has no card."""
    return _data()["group_to_card"].get(group)


def card_key_for_symbol(symbol):
    """The slot-card key for a symbol, or None when it runs universal-only.

    Uses the symbol's PRIMARY display group (first entry of
    fno.display_groups_of, which is DISPLAY_GROUPS-ordered). A symbol fno does
    not recognise has no groups at all and is also universal-only.
    """
    groups = fno.display_groups_of(symbol)
    if not groups:
        return None
    return group_to_card(groups[0])


def primary_group(symbol):
    """The symbol's primary display group, or '' if fno has no sector for it."""
    groups = fno.display_groups_of(symbol)
    return groups[0] if groups else ""


def card(card_key):
    """The full card dict ({label, slots}) for a key, or None."""
    if card_key is None:
        return None
    return _data()["cards"].get(card_key)


def slots_for_symbol(symbol):
    """Slot definitions to extract for this symbol: a list of
    {key, label, capture} dicts, or [] when the symbol is universal-only."""
    found = card(card_key_for_symbol(symbol))
    return list(found["slots"]) if found else []


def slot_keys_for_symbol(symbol):
    return [s["key"] for s in slots_for_symbol(symbol)]


def bank_type(symbol):
    """'PSU' / 'PRIVATE' for a bank, else None.

    PSU banks use the private-bank card (same metrics) with this recorded as
    metadata - Adarsh confirmed 16 Oct 2026, matching the spec's 3.4 proposal.
    Derived from the raw sector labels, since the display group merges both.
    """
    raw = fno.sectors_of(symbol)
    if "PSU Bank" in raw:
        return "PSU"
    if "Private Bank" in raw:
        return "PRIVATE"
    return None


def is_unmapped(symbol):
    return card_key_for_symbol(symbol) is None


def all_card_keys():
    return sorted(_data()["cards"].keys())
