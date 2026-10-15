"""Tests for the estimates-sheet importer (spec 5A).

Offline: a synthetic workbook built in-memory, so nothing depends on the
uploaded file being present or on its contents staying fixed.

The surprise percentage is the SHEET's own `spread_pct` (Adarsh's call,
15 Oct 2026), not recomputed here - so what these tests pin is the mapping,
the quarter-label convention, and the fact that the source's caveats survive
the import rather than being quietly dropped.
"""

from datetime import date

import openpyxl
import pytest

import sentiment_store_v3 as store
from jobs.import_estimates_sheet import (file_sha256, parse_quarter_label,
                                         read_rows, write_rows)

HEADER = ["symbol", "isin", "fiscal_quarter", "metric", "unit", "released",
          "forecast", "spread_pct", "ms_page_id", "page_url", "fetched_at",
          "basis", "snapshot_status", "flags"]


def _sheet(tmp_path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Estimates vs Actuals"
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    path = str(tmp_path / "estimates.xlsx")
    wb.save(path)
    return path


def _row(symbol="TCS", quarter="2027 Q2", metric="PAT", released=138840,
         forecast=137328, spread=1.1, flags=None):
    return [symbol, "INE467B01029", quarter, metric, "Million INR", released,
            forecast, spread, "12345", "https://example.invalid/x",
            "2026-10-14T17:23:48", "probable consolidated (per 14 Oct probe)",
            "unverified", flags]


# --- quarter labels ----------------------------------------------------------

@pytest.mark.parametrize("label,expected", [
    # The source labels by fiscal-year END: FY2027 Q2 is Jul-Sep 2026.
    ("2027 Q2", date(2026, 9, 30)),
    ("2027 Q1", date(2026, 6, 30)),
    ("2027 Q3", date(2026, 12, 31)),
    ("2027 Q4", date(2027, 3, 31)),   # Jan-Mar sits in the FY's own year
    ("2026 Q2", date(2025, 9, 30)),
])
def test_quarter_labels_follow_the_fiscal_year_end_convention(label, expected):
    assert parse_quarter_label(label) == expected


@pytest.mark.parametrize("label", ["2024 S1", "2026 S2", "", None, "garbage",
                                   "2027 Q5", "2027"])
def test_non_quarter_labels_are_rejected(label):
    # Half-year columns are excluded at source, but a label that is not a
    # plain fiscal quarter must never be guessed into a date.
    assert parse_quarter_label(label) is None


# --- reading -----------------------------------------------------------------

def test_reads_rows_onto_the_storage_contract(tmp_path):
    path = _sheet(tmp_path, [_row()])
    rows, skipped = read_rows(path)
    assert skipped == []
    row = rows[0]
    assert row["symbol"] == "TCS"
    assert row["period_end_date"] == "2026-09-30"
    assert row["source_quarter_label"] == "2027 Q2"
    assert row["estimate_value"] == 137328      # the sheet's `forecast`
    assert row["actual_value"] == 138840        # the sheet's `released`
    assert row["surprise_pct"] == pytest.approx(1.1)   # the sheet's own figure


def test_every_row_is_provisional(tmp_path):
    # The source's basis is "probable consolidated", unconfirmed per stock,
    # so nothing imported here may present as a verified comparison.
    path = _sheet(tmp_path, [_row(), _row(metric="sales", released=731880,
                                          forecast=730938, spread=0.13)])
    rows, _ = read_rows(path)
    assert {r["comparability"] for r in rows} == {"PROVISIONAL"}


def test_missing_actual_is_a_named_pending_state_not_zero(tmp_path):
    path = _sheet(tmp_path, [_row(released=None, spread=None)])
    rows, _ = read_rows(path)
    assert rows[0]["comparability"] == "ACTUAL_PENDING"
    assert rows[0]["actual_value"] is None       # never 0
    assert rows[0]["surprise_pct"] is None


def test_source_caveats_survive_the_import(tmp_path):
    # snapshot_status 'unverified' and the eps_rounded flag are the reasons a
    # reader must not treat these as frozen pre-print consensus. Losing them
    # in the import would be worse than not importing at all.
    path = _sheet(tmp_path, [_row(metric="EPS", released=38.4, forecast=37.7,
                                  spread=1.69, flags="eps_rounded")])
    rows, _ = read_rows(path)
    assert rows[0]["snapshot_status"] == "unverified"
    assert rows[0]["flags"] == "eps_rounded"
    assert "probable consolidated" in rows[0]["basis"]


def test_half_year_rows_are_skipped_and_reported(tmp_path):
    path = _sheet(tmp_path, [_row(), _row(quarter="2026 S1")])
    rows, skipped = read_rows(path)
    assert len(rows) == 1
    assert len(skipped) == 1 and skipped[0][1] == "2026 S1"


def test_a_sheet_without_the_expected_tab_is_an_error(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "Something Else"
    path = str(tmp_path / "wrong.xlsx")
    wb.save(path)
    with pytest.raises(ValueError, match="Estimates vs Actuals"):
        read_rows(path)


# --- writing and versioning --------------------------------------------------

def test_write_then_reimport_is_a_no_op(tmp_path):
    path = _sheet(tmp_path, [_row(), _row(metric="sales", released=731880,
                                          forecast=730938, spread=0.13)])
    conn = store.get_connection(str(tmp_path / "events_test.db"))
    rows, _ = read_rows(path)
    sha = file_sha256(path)
    write_rows(conn, rows, sha)
    write_rows(conn, rows, sha)
    count = conn.execute(
        "SELECT COUNT(*) FROM earnings_estimate_surprise").fetchone()[0]
    assert count == 2       # same file re-imported, no duplicates
    conn.close()


def test_a_changed_upload_is_a_new_version_not_an_overwrite(tmp_path):
    # Spec 5A.1: "a changed upload makes a new version rather than
    # overwriting an old input", so a corrected sheet never silently erases
    # what an earlier analysis was based on.
    conn = store.get_connection(str(tmp_path / "events_test.db"))
    first = _sheet(tmp_path, [_row(spread=1.1)])
    rows, _ = read_rows(first)
    write_rows(conn, rows, "sha_version_one")

    rows[0]["surprise_pct"] = 2.2
    write_rows(conn, rows, "sha_version_two")

    stored = conn.execute(
        "SELECT source_file_sha256, surprise_pct FROM earnings_estimate_surprise "
        "ORDER BY source_file_sha256").fetchall()
    assert len(stored) == 2
    assert dict(stored) == {"sha_version_one": 1.1, "sha_version_two": 2.2}
    conn.close()
