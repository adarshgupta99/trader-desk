"""
The history ranges the app offers, and how to turn a date pair into prices.
"""

from datetime import date

import pandas as pd

from data_loader import get_price_history

PERIODS = {"1W": "5d", "1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "3Y": "3y"}
PERIOD_LABELS = {v: k for k, v in PERIODS.items()}

# Chart tab presets, and how far back each one reaches.
CHART_PRESETS = {"1M": 30, "3M": 90, "6M": 182, "1Y": 365, "3Y": 1095}

# How many calendar days of history each loader period is good for. Used to pick
# the cheapest period that still reaches back to a chosen start date.
PERIOD_COVERS = (("1mo", 31), ("3mo", 92), ("6mo", 184), ("1y", 368), ("3y", 1100))

# 3Y is the furthest back the loader will fetch — its NSE path maps anything it
# doesn't recognise to 35 days, so asking for more would quietly return less.
MAX_HISTORY_DAYS = 1095


def range_history(ticker, start, end):
    """Price history between two dates, inclusive.

    get_price_history only takes preset periods and SQLite caches on them, so
    fetch the smallest preset that still reaches `start` and slice it down.
    That reuses the existing cache instead of adding a cache key per date pair.
    """
    days = (date.today() - start).days
    period = "3y"
    for name, covers in PERIOD_COVERS:
        if days <= covers:
            period = name
            break

    df = get_price_history(ticker, period=period).dropna(subset=["Close"])
    if df.empty:
        return df
    on = pd.to_datetime(df.index).date
    return df[(on >= start) & (on <= end)]
