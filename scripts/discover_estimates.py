"""
Step 0 discovery run for the analyst-estimates feature (see
analyst_estimates_spec-c3606434.txt, section 3).

Calls everything yfinance offers for analyst estimates and actuals, for a
handful of F&O names, and prints the real shape: which periods are present,
which columns exist, what's empty, and what currency revenue comes in.
yfinance's own estimate methods aren't documented in detail anywhere
reliable, so we look at them directly instead of assuming the schema in
the spec is already right.

Saves the full output to docs/estimates_discovery.txt as well as stdout.
"""

import io
import os
import sys
from contextlib import redirect_stdout

import yfinance as yf

DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
os.makedirs(DOCS_DIR, exist_ok=True)
OUT_PATH = os.path.join(DOCS_DIR, "estimates_discovery.txt")

SYMBOLS = ["RELIANCE.NS", "HDFCBANK.NS", "TCS.NS", "INDUSINDBK.NS", "PIIND.NS"]


def inspect(label, fn):
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    try:
        result = fn()
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")
        return None

    if result is None:
        print("Returned None")
        return None
    if hasattr(result, "empty") and result.empty:
        print(f"Returned an empty {type(result).__name__}")
        return result
    if hasattr(result, "columns"):
        print(f"shape = {result.shape}")
        print(f"columns = {list(result.columns)}")
        print(f"index = {list(result.index)}")
        print("\ndtypes:")
        print(result.dtypes)
        print("\nfull content:")
        print(result.to_string())
    else:
        print(f"type = {type(result)}")
        print(result)
    return result


def discover_symbol(sym):
    print(f"\n\n{'#' * 70}\n{sym}\n{'#' * 70}")
    t = yf.Ticker(sym)

    if hasattr(t, "get_earnings_estimate"):
        inspect(f"{sym} get_earnings_estimate()", t.get_earnings_estimate)
    else:
        inspect(f"{sym} earnings_estimate (attribute, older yfinance)",
                lambda: t.earnings_estimate)

    if hasattr(t, "get_revenue_estimate"):
        inspect(f"{sym} get_revenue_estimate()", t.get_revenue_estimate)
    else:
        inspect(f"{sym} revenue_estimate (attribute, older yfinance)",
                lambda: t.revenue_estimate)

    inspect(f"{sym} get_earnings_dates()", lambda: t.get_earnings_dates(limit=12))
    inspect(f"{sym} quarterly_income_stmt", lambda: t.quarterly_income_stmt)


if __name__ == "__main__":
    buf = io.StringIO()
    real_stdout = sys.stdout

    class Tee:
        def write(self, s):
            buf.write(s)
            real_stdout.write(s)

        def flush(self):
            real_stdout.flush()

    with redirect_stdout(Tee()):
        print(f"yfinance version: {yf.__version__}")
        for sym in SYMBOLS:
            discover_symbol(sym)

    with open(OUT_PATH, "w") as f:
        f.write(buf.getvalue())
    print(f"\n\nSaved full output to {OUT_PATH}")
