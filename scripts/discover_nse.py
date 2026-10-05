"""
Step 1 discovery run for the F&O events feature (see fetch_events.txt).

Calls the three nselib functions we plan to use, and prints everything we
need to know about their real output: row count, columns, dtypes, and a
sample of rows. nselib's own column names/contents are not documented
anywhere reliable, so we look at them directly instead of guessing.

Also saves each result to scratch/ as CSV so Adarsh can open it in a
spreadsheet. scratch/ is gitignored - these dumps are for inspection only,
never committed.
"""

import os
from datetime import date, timedelta

import pandas as pd
from nselib import capital_market

SCRATCH_DIR = os.path.join(os.path.dirname(__file__), "..", "scratch")
os.makedirs(SCRATCH_DIR, exist_ok=True)

TODAY = date.today()
TO_DATE = TODAY + timedelta(days=60)
FROM_STR = TODAY.strftime("%d-%m-%Y")
TO_STR = TO_DATE.strftime("%d-%m-%Y")


def inspect(label, fn, *args, **kwargs):
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    try:
        df = fn(*args, **kwargs)
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")
        return None

    if not isinstance(df, pd.DataFrame):
        print(f"Returned a {type(df)}, not a DataFrame: {df!r}")
        return None

    print(f"len(df) = {len(df)}")
    print(f"columns = {list(df.columns)}")
    print("\ndtypes:")
    print(df.dtypes)
    print("\nhead(10):")
    print(df.head(10).to_string())

    csv_path = os.path.join(SCRATCH_DIR, f"{label}.csv")
    df.to_csv(csv_path, index=False)
    print(f"\nSaved full result to {csv_path}")
    return df


if __name__ == "__main__":
    print(f"Window: {FROM_STR} to {TO_STR}")

    fno_df = inspect("fno_equity_list", capital_market.fno_equity_list)
    if fno_df is not None:
        print(f"\nF&O stock count: {len(fno_df)}")

    inspect(
        "corporate_actions_for_equity",
        capital_market.corporate_actions_for_equity,
        from_date=FROM_STR,
        to_date=TO_STR,
        fno_only=True,
    )

    inspect(
        "event_calendar_for_equity",
        capital_market.event_calendar_for_equity,
        from_date=FROM_STR,
        to_date=TO_STR,
        fno_only=True,
    )
