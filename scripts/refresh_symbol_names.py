"""Upgrade the NSE equity names in data/symbols.csv to NSE's full company names.

Why this exists
---------------
The instrument table was originally built from NSE's daily bhavcopy, which
truncates company names to about 25 characters. That makes some instruments
impossible to find: MOTHERSON arrives as "Samvrdhna Mthrsn Intl Ltd", which
contains neither "motherson" nor the ticker, and the dropdown filters only on
the name it displays.

NSE publishes the untruncated names separately in EQUITY_L.csv. This script
merges them in, so the same row becomes "Samvardhana Motherson International
Ltd" and a search for "motherson" finds it.

This is a maintenance script, not app code. data/symbols.csv stays the single
source of truth that the app reads at startup — run this by hand after an
index reshuffle or when NSE lists new companies, then commit the CSV.

    .venv/bin/python scripts/refresh_symbol_names.py

EQUITY_L.csv only covers the EQ series, so ETFs and SM-series instruments in
our table have no full name available and keep their bhavcopy name.
"""

import io
import os
import sys

import pandas as pd
import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOLS = os.path.join(REPO, "data", "symbols.csv")
EQUITY_L = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"

# NSE's archive host rejects requests without a browser-like User-Agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def fetch_full_names():
    """symbol -> full company name, from NSE's equity list."""
    reply = requests.get(EQUITY_L, headers=HEADERS, timeout=60)
    reply.raise_for_status()
    frame = pd.read_csv(io.StringIO(reply.text))
    frame.columns = [c.strip() for c in frame.columns]
    return {
        str(sym).strip(): str(name).strip()
        for sym, name in zip(frame["SYMBOL"], frame["NAME OF COMPANY"])
    }


def tidy(name):
    """Match the "Ltd" spelling the untouched bhavcopy rows already use."""
    for suffix in (" Limited", " Ltd."):
        if name.endswith(suffix):
            return name[: -len(suffix)] + " Ltd"
    return name


def main():
    symbols = pd.read_csv(SYMBOLS)
    full_names = fetch_full_names()
    print(f"EQUITY_L: {len(full_names)} companies")

    changed = missing = 0
    names = []
    for ticker, name, kind in zip(symbols["ticker"], symbols["name"], symbols["kind"]):
        if kind != "NSE equity":
            names.append(name)
            continue
        full = full_names.get(str(ticker).replace(".NS", ""))
        if full is None:
            missing += 1
            names.append(name)
            continue
        full = tidy(full)
        changed += full != name
        names.append(full)

    symbols["name"] = names
    symbols.to_csv(SYMBOLS, index=False)
    print(f"names updated: {changed}")
    print(f"no full name available (ETF / non-EQ series): {missing}")
    print(f"wrote {SYMBOLS} ({len(symbols)} rows)")


if __name__ == "__main__":
    sys.exit(main())
