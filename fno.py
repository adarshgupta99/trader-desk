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
