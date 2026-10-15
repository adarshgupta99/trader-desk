"""
Imports the uploaded estimate-vs-actual sheet into earnings_estimate_surprise
(spec section 5A).

    python -m jobs.import_estimates_sheet --file "F&O-estimates-....xlsx"

NO FETCHING, NO SCRAPING (spec 5A.1). This job reads a file Adarsh put in the
project folder and nothing else. Nothing here contacts the estimate source.

THE SURPRISE COMES FROM THE SHEET. Adarsh's call, 15 Oct 2026: use the
sheet's own `spread_pct` and `released` rather than recomputing the comparison
against the desk's NSE XBRL actuals. The spec's 5A.3 formula and its
basis-matching guards are therefore NOT applied here - the arithmetic is the
source's, and so are its caveats. Those caveats travel with every row instead
of being dropped:

  - basis is "probable consolidated", unconfirmed per stock, by the sheet's
    own admission. Every row is therefore stored PROVISIONAL.
  - snapshot_status is "unverified" on every row: whether a past quarter's
    forecast is the frozen pre-result consensus or was revised after the
    result is unproven. It is explicitly NOT point-in-time data, so nothing
    here may be displayed as a verified pre-print consensus.
  - EPS is rounded at source (flagged eps_rounded on every EPS row).
  - EBITDA is deliberately absent for lenders, which matches spec 5A.3's
    "bank EBITDA is always NULL" rule rather than contradicting it.

`snapshot_at` is NOT populated. The sheet carries `fetched_at`, which is when
the page was read - a different thing from when the consensus was captured,
which is unknown. Spec 5A.1 says never default it to the upload time, and
Adarsh confirmed the column is not worth chasing, so it stays absent rather
than being filled with something misleading.

QUARTER LABELS: the source labels by fiscal-year END, so "2027 Q2" is the
July-September 2026 quarter for a March-year-end company. That is the
convention for the great majority of NSE names but not a universal law; a
company on a different fiscal calendar would be mislabelled, which is a known
and accepted limitation rather than a verified fact.

REIMPORT IS A NO-OP, a changed file is a new version: rows are keyed by the
sheet's SHA-256, so re-running over the same file rewrites identical rows and
a newer upload lands alongside rather than overwriting the older input.
"""

import argparse
import hashlib
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import openpyxl  # noqa: E402

import sentiment_store_v3 as store  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
SHEET_NAME = "Estimates vs Actuals"

#: Quarter-end month/day by fiscal quarter, Indian FY (April-March).
_FQ_END = {1: (6, 30), 2: (9, 30), 3: (12, 31), 4: (3, 31)}


def parse_quarter_label(label):
    """'2027 Q2' -> date(2026, 9, 30).

    The source labels by fiscal-year end: FY2027 Q2 is Jul-Sep 2026. Q1-Q3
    fall in the previous calendar year, Q4 (Jan-Mar) in the FY's own year.
    Returns None for a label that is not a plain fiscal quarter - the sheet
    also carries half-year columns (S1/S2), which are excluded at source but
    guarded here anyway.
    """
    if not label or not isinstance(label, str):
        return None
    parts = label.strip().split()
    if len(parts) != 2 or not parts[1].upper().startswith("Q"):
        return None
    try:
        fy = int(parts[0])
        quarter = int(parts[1][1:])
    except ValueError:
        return None
    if quarter not in _FQ_END:
        return None
    month, day = _FQ_END[quarter]
    year = fy if quarter == 4 else fy - 1
    return date(year, month, day)


def file_sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def read_rows(path):
    """(rows, skipped) from the sheet, mapped onto the storage contract."""
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    if SHEET_NAME not in workbook.sheetnames:
        raise ValueError(f"{path} has no {SHEET_NAME!r} sheet "
                         f"(found {workbook.sheetnames})")
    sheet = workbook[SHEET_NAME]
    header = [c.value for c in next(sheet.iter_rows(min_row=1, max_row=1))]
    index = {name: i for i, name in enumerate(header)}
    for required in ("symbol", "fiscal_quarter", "metric", "forecast",
                     "released", "spread_pct"):
        if required not in index:
            raise ValueError(f"sheet is missing the {required!r} column; "
                             f"found {header}")

    rows, skipped = [], []
    for row_no, raw in enumerate(sheet.iter_rows(min_row=2, values_only=True),
                                 start=2):
        if raw is None or raw[index["symbol"]] is None:
            continue
        label = raw[index["fiscal_quarter"]]
        period_end = parse_quarter_label(label)
        if period_end is None:
            skipped.append((row_no, label))
            continue
        released = raw[index["released"]]
        rows.append({
            "symbol": str(raw[index["symbol"]]).strip().upper(),
            "period_end_date": period_end.isoformat(),
            "source_quarter_label": label,
            "metric": raw[index["metric"]],
            "estimate_value": raw[index["forecast"]],
            "actual_value": released,
            "surprise_pct": raw[index["spread_pct"]],
            "unit": raw[index["unit"]] if "unit" in index else None,
            "basis": raw[index["basis"]] if "basis" in index else None,
            "snapshot_status": (raw[index["snapshot_status"]]
                                if "snapshot_status" in index else None),
            # Every row is PROVISIONAL: the source's own basis is unconfirmed
            # per stock. A quarter with no released figure yet is a named
            # pending state, never a zero.
            "comparability": "ACTUAL_PENDING" if released is None else "PROVISIONAL",
            "flags": raw[index["flags"]] if "flags" in index else None,
            "source_row_ref": row_no,
        })
    return rows, skipped


def write_rows(conn, rows, source_sha):
    imported_at = datetime.now(IST).isoformat()
    with conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO earnings_estimate_surprise
                (symbol, period_end_date, source_quarter_label, metric,
                 estimate_value, actual_value, surprise_pct, unit, basis,
                 snapshot_status, comparability, flags, source_file_sha256,
                 source_row_ref, imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [(r["symbol"], r["period_end_date"], r["source_quarter_label"],
              r["metric"], r["estimate_value"], r["actual_value"],
              r["surprise_pct"], r["unit"], r["basis"], r["snapshot_status"],
              r["comparability"], r["flags"], source_sha, r["source_row_ref"],
              imported_at) for r in rows],
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="the uploaded .xlsx")
    args = ap.parse_args()
    if not os.path.exists(args.file):
        print(f"AWAITING_UPLOAD: {args.file} not found")
        sys.exit(1)

    source_sha = file_sha256(args.file)
    rows, skipped = read_rows(args.file)
    conn = store.get_connection()
    try:
        write_rows(conn, rows, source_sha)
        symbols = len({r["symbol"] for r in rows})
        pending = sum(r["comparability"] == "ACTUAL_PENDING" for r in rows)
        print(f"imported {len(rows)} rows across {symbols} symbols "
              f"from {os.path.basename(args.file)}")
        print(f"  source sha256: {source_sha[:16]}...")
        print(f"  actual pending: {pending}")
        print(f"  skipped (non-quarter labels): {len(skipped)}")
        for row_no, label in skipped[:5]:
            print(f"    row {row_no}: {label!r}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
