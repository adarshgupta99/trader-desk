"""
Gate zero, part 2 (expected_move_spec-3c050a43.txt section 2b/2g): does the
raw NSE corporate-announcements archive endpoint - not covered by nselib's
own corporate_actions_for_equity/event_calendar_for_equity functions - work
from wherever this script runs? nselib ships a ready-made cookie-bootstrap
helper (nselib.libutil.nse_urlfetch) built for exactly this kind of raw NSE
API call, so this reuses it rather than hand-rolling a new session dance.

Hits the canonical regression case from the spec (TCS, financial period
ended 30 Sep 2024, filed 10-Oct-2024 16:06:39 IST) plus a recent window for
the same 5 discovery names used in discover_expected_move.py.

Prints results; does not write any file (this is a go/no-go check, not a
data capture pass - that happens once this access question is answered).
"""

import json
import time

from nselib.libutil import nse_urlfetch

NAMES = ["TCS", "RELIANCE", "HDFCBANK", "INFY", "WIPRO"]

BASE = "https://www.nseindia.com/api/corporate-announcements"


def fetch(symbol, from_date, to_date):
    url = f"{BASE}?index=equities&symbol={symbol}&from_date={from_date}&to_date={to_date}"
    try:
        resp = nse_urlfetch(url)
    except Exception as e:
        return None, f"EXCEPTION: {e}"
    status = resp.status_code
    try:
        data = resp.json()
    except Exception:
        return status, f"NOT JSON - body[:200]={resp.text[:200]!r}"
    return status, data


print("=" * 70)
print("Canonical regression case: TCS, 10-Oct-2024")
print("=" * 70)
status, data = fetch("TCS", "10-10-2024", "10-10-2024")
print(f"status={status}")
if isinstance(data, list):
    print(f"{len(data)} rows")
    for row in data[:5]:
        print(json.dumps(row, indent=2)[:600])
else:
    print(data)

time.sleep(2)

print()
print("=" * 70)
print("5-name recent window (last 90 days)")
print("=" * 70)
import datetime
today = datetime.date.today()
from_date = (today - datetime.timedelta(days=90)).strftime("%d-%m-%Y")
to_date = today.strftime("%d-%m-%Y")
print(f"window: {from_date} to {to_date}")

for sym in NAMES:
    status, data = fetch(sym, from_date, to_date)
    if isinstance(data, list):
        print(f"{sym}: status={status}, {len(data)} rows")
        if data:
            print(f"  sample keys: {list(data[0].keys())}")
            print(f"  sample row: {json.dumps(data[0], indent=2)[:500]}")
    else:
        print(f"{sym}: status={status}, {data}")
    time.sleep(2)
