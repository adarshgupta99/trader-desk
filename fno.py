"""The F&O universe: 213 NSE stocks, their display names and their sectors.

Reads fno_sectors.json (generated 5 Oct 2026, committed to the repo) and
nothing else. The fetch job and the Correlation tab both need this list,
and neither should be parsing the JSON itself.

Symbols are bare NSE symbols as the exchange writes them ("M&M",
"BAJAJ-AUTO"). Append ".NS" for Yahoo via `yahoo_ticker`; step 0 verified
on 6 Oct 2026 that all 213 resolve that way, so there is no special-case
mapping table and there should not be one.
"""

import functools
import json
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "fno_sectors.json")


@functools.lru_cache(maxsize=1)
def _data():
    with open(_PATH) as f:
        return json.load(f)


def symbols():
    """All F&O symbols, sorted. 213 of them as of 5 Oct 2026."""
    return sorted(_data()["stocks"].keys())


def yahoo_ticker(symbol):
    return f"{symbol}.NS"


def display_name(symbol):
    """Company name, falling back to the symbol itself."""
    return _data()["stocks"].get(symbol, {}).get("name") or symbol


def sectors_of(symbol):
    """Every sector the symbol belongs to. Empty list if unknown."""
    return list(_data()["stocks"].get(symbol, {}).get("sectors") or [])


def primary_sector(symbol):
    """The first sector, which is what a single cell shows. '' if unknown."""
    found = sectors_of(symbol)
    return found[0] if found else ""


def generated_on():
    """The date fno_sectors.json was built, for the UI footer."""
    return _data().get("generated", "unknown")


#: Display grouping for the sector matrix (spec section 5B, 6 Oct 2026).
#: fno_sectors.json carries 22 raw labels with near-duplicates (Bank /
#: Private Bank / PSU Bank, Energy / Oil & Gas, ...); a 22x22 matrix is not
#: more readable than the 213x213 stock grid it replaces. This is the ONE
#: place the 22-to-15 merge happens; raw labels stay untouched in the data.
#: Order is fixed and is the order the matrix and legend render in — do not
#: sort it alphabetically, and do not recluster between reruns.
DISPLAY_GROUPS = [
    "Banks", "Financial Services", "Energy", "Healthcare & Pharma", "IT",
    "Auto", "FMCG", "Capital Goods & Infra", "Metals", "Consumer Durables",
    "Consumer Services", "Realty & Construction", "Chemicals", "Telecom",
    "Textiles",
]

_RAW_TO_GROUP = {
    "Bank": "Banks", "Private Bank": "Banks", "PSU Bank": "Banks",
    "Financial Services": "Financial Services",
    "Energy": "Energy", "Oil & Gas": "Energy",
    "Healthcare": "Healthcare & Pharma", "Pharma": "Healthcare & Pharma",
    "IT": "IT",
    "Auto": "Auto",
    "FMCG": "FMCG",
    "Capital Goods": "Capital Goods & Infra", "Construction": "Capital Goods & Infra",
    "Metal": "Metals",
    "Consumer Durables": "Consumer Durables",
    "Consumer Services": "Consumer Services", "Services": "Consumer Services",
    "Realty": "Realty & Construction", "Construction Materials": "Realty & Construction",
    "Chemicals": "Chemicals",
    "Telecommunication": "Telecom",
    "Textiles": "Textiles",
}


def display_group_of(raw_sector):
    """One raw sector label -> its display group. '' if unmapped."""
    return _RAW_TO_GROUP.get(raw_sector, "")


def display_groups_of(symbol):
    """Every display group this symbol maps into, DISPLAY_GROUPS-ordered.

    A stock whose raw sectors all merge into one group appears once; a
    stock spanning two merged groups (multi-group) appears in both, which
    is what the sector matrix's same-symbol exclusion (spec 5/5A) exists
    to handle.
    """
    found = {display_group_of(s) for s in sectors_of(symbol)} - {""}
    return [g for g in DISPLAY_GROUPS if g in found]


@functools.lru_cache(maxsize=1)
def _group_membership():
    membership = {g: [] for g in DISPLAY_GROUPS}
    for symbol in symbols():
        for g in display_groups_of(symbol):
            membership[g].append(symbol)
    return membership


def symbols_in_display_group(group):
    """Sorted symbols mapping into this display group. [] if none."""
    return sorted(_group_membership().get(group, []))
