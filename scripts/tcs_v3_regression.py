"""TCS six-quarter regression against the v3 reference set (spec QA 8.1).

Reads what the pipeline actually stored in events.db and compares it, field by
field, with TCS-v3-reference-simple.xlsx. Reports every difference; nothing is
silently accepted.

    .venv/bin/python scripts/tcs_v3_regression.py

Read-only. Run it after jobs/refresh_extraction_v3.py --symbols TCS.
"""

import json
import os
import sqlite3
import sys

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB = os.path.join(ROOT, "events.db")
REFERENCE = os.path.join(ROOT, "TCS-v3-reference-simple.xlsx")

#: The reference sheet labels quarters by fiscal period; the pipeline keys on
#: the quarter-end date the filing itself states.
QUARTER_END = {
    "Q1 FY26": "2025-06-30", "Q2 FY26": "2025-09-30", "Q3 FY26": "2025-12-31",
    "Q4 FY26": "2026-03-31", "Q1 FY27": "2026-06-30", "Q2 FY27": "2026-09-30",
}


def _score(cell):
    """'-1 | Q1, Q6' -> -1. The reference stores value and cites in one cell."""
    if cell is None:
        return None
    text = str(cell).split("|")[0].strip()
    try:
        return int(text.replace("+", ""))
    except ValueError:
        return None


def _evasive_count(cell):
    """'8 | Q3, Q4, ...' -> 8."""
    if cell is None:
        return None
    try:
        return int(str(cell).split("|")[0].strip())
    except ValueError:
        return None


def _evasive_refs(cell):
    if cell is None or "|" not in str(cell):
        return []
    return [p.strip() for p in str(cell).split("|", 1)[1].split(",") if p.strip()]


def _grade(cell):
    """'MILD | P7 vs Q2...' -> 'MILD'."""
    if cell is None:
        return None
    return str(cell).split("|")[0].strip()


def read_reference():
    wb = openpyxl.load_workbook(REFERENCE, data_only=True)
    ws = wb["TCS reference"]
    header_row = None
    rows = {}
    for row in ws.iter_rows(values_only=True):
        if row[0] == "QUARTER":
            header_row = row
            continue
        if header_row is None or not row[0]:
            continue
        label = str(row[0]).split(" (")[0]
        if label not in QUARTER_END:
            continue
        # The sheet repeats these same quarter labels lower down, in its
        # SOURCE TRANSCRIPTS block, where the value columns hold a URL rather
        # than a score. First occurrence is the data; later ones would
        # overwrite every field with None.
        if label in rows:
            continue
        rows[label] = {
            "price_move": row[1],
            "mgmt_confidence": _score(row[2]),
            "overall_tone": _score(row[3]),
            "qa_tone": _score(row[4]),
            "guidance": row[5],
            "analyst_tone": _score(row[6]),
            "evasive_count": _evasive_count(row[7]),
            "evasive_refs": _evasive_refs(row[7]),
            "qa_divergence": _grade(row[8]),
        }
    return rows


def read_stored():
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(
            "SELECT quarter_end, mgmt_confidence, overall_tone, qa_tone, "
            "analyst_tone, evasive_count, evasive_exchanges, qa_divergence, "
            "guidance_cell, price_move_pct, row_status, cite_failures "
            "FROM earnings_call_extraction WHERE symbol='TCS'"
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        out[r[0]] = {
            "mgmt_confidence": r[1], "overall_tone": r[2], "qa_tone": r[3],
            "analyst_tone": r[4], "evasive_count": r[5],
            "evasive_refs": json.loads(r[6] or "[]"),
            "qa_divergence": r[7], "guidance_cell": r[8],
            "price_move": r[9], "row_status": r[10], "cite_failures": r[11],
        }
    return out


def main():
    reference = read_reference()
    stored = read_stored()
    if not stored:
        print("No TCS rows stored. Run jobs/refresh_extraction_v3.py --symbols TCS")
        return 1

    fields = ["mgmt_confidence", "overall_tone", "qa_tone", "analyst_tone",
              "evasive_count", "qa_divergence"]
    width = max(len(f) for f in fields) + 2

    agree = total = 0
    print(f"{'quarter':9} " + " ".join(f"{f:>{width}}" for f in fields)
          + "  status      cites")
    print("-" * (9 + (width + 1) * len(fields) + 20))

    for label, qe in QUARTER_END.items():
        ref, got = reference.get(label), stored.get(qe)
        if got is None:
            print(f"{label:9} {'NOT EXTRACTED':>{width*3}}")
            continue
        cells = []
        for field in fields:
            expected, actual = ref[field], got[field]
            ok = expected == actual
            agree += ok
            total += 1
            cells.append(f"{actual}/{expected}" + ("" if ok else " X"))
        print(f"{label:9} " + " ".join(f"{c:>{width}}" for c in cells)
              + f"  {got['row_status']:11} {got['cite_failures']}")

    print(f"\nfield agreement: {agree}/{total} ({100 * agree / total:.0f}%)")

    # Evasiveness is judged on overlap as well as count: finding a subset of
    # the reference's exchanges with no false positives is a very different
    # result from finding a different set of the same size.
    print("\nevasive exchange overlap:")
    for label, qe in QUARTER_END.items():
        ref, got = reference.get(label), stored.get(qe)
        if got is None:
            continue
        expected, actual = set(ref["evasive_refs"]), set(got["evasive_refs"])
        extra = actual - expected
        print(f"  {label:9} ours={sorted(actual) or '[]'} "
              f"ref={sorted(expected) or '[]'} "
              f"hit={len(actual & expected)}/{len(expected)} "
              f"false_positives={sorted(extra) or 'none'}")

    print("\nguidance cell (formal must be NOT GIVEN in all six):")
    for label, qe in QUARTER_END.items():
        got = stored.get(qe)
        if got:
            print(f"  {label:9} {got['guidance_cell'][:96]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
