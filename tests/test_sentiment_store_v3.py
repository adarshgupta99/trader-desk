"""Storage tests for the v3 pipeline: idempotency, the guidance cell format,
fiscal-quarter labelling, and the spec's QA check 6 - every row in every
table carries the three section-5 timestamps plus extracted_at.

Offline: a temp SQLite file, no network and no LLM. The extraction payloads
here are hand-built to the shape stage 2 returns.
"""

import json
from datetime import date

import pytest

import sentiment_store_v3 as store
from jobs.refresh_extraction_v3 import _fiscal_quarter


TIMESTAMPS = {
    "announcement_at": "2026-10-08T19:00:00+05:30",
    "announcement_at_precision": "instant",
    "source_published_at": "2026-10-14T15:59:41+05:30",
    "feature_available_at": "2026-10-14T15:59:41+05:30",
    "timestamp_quality": "OK",
    "extracted_at": "2026-10-16T02:00:00+05:30",
}
PROVENANCE = {
    "extractor_version": "v3.0.0",
    "source_pdf_sha256": "a" * 64,
    "cleaned_text_sha256": "b" * 64,
}
PREPARED = {
    "url": "https://nsearchives.nseindia.com/corporate/TCS_x.pdf",
    "pdf_sha256": "a" * 64, "cleaned_text_sha256": "b" * 64,
    "pdf_archive_path": "/tmp/x.pdf", "cleaned_text_path": "/tmp/x.txt",
    "pdf_bytes": 723462, "http_status": 200,
    "fetched_at": "2026-10-16T01:00:00+05:30",
}
SECTOR = {"sector": "IT", "sector_card": "it_services"}


def _parsed(**overrides):
    base = {
        "mgmt_confidence": {"score": 0, "cites": ["P1"]},
        "overall_tone": {"score": 1, "cites": ["P1", "Q2"]},
        "qa_tone": {"score": 0, "cites": ["Q1"]},
        "analyst_tone": {"score": -1, "cites": ["Q5"]},
        "qa_divergence": {"grade": "MILD", "note": "margin framing softened",
                          "cites": ["P2", "Q3"], "quote": None},
        "evasive_exchanges": ["Q3", "Q4"],
        "qa_assessments": [],
        "guidance_formal": {
            k: {"status": "NOT_GIVEN", "value_text": None, "quote": None,
                "exchange_ref": None, "value_num_low": None,
                "value_num_high": None, "unit": None,
                "applies_to_period": None, "prior_value_text": None}
            for k in ("guidance_revenue", "guidance_margin",
                      "guidance_capex", "guidance_orderbook")
        },
        "guidance_soft": [
            {"topic": "demand", "direction": "positive",
             "quote": "demand is improving", "exchange_ref": "P1",
             "char_start": 10, "char_end": 30},
        ],
        "frame": {
            "mgmt_margin_commentary": {
                "status": "GIVEN", "value_text": "margins under pressure",
                "quote": "margins were under pressure", "exchange_ref": "P2",
                "char_start": 5, "char_end": 32},
            "one_offs_disclosed": {"status": "NOT_GIVEN", "value_text": None,
                                   "quote": None, "exchange_ref": None},
            "cash_flow_commentary": {"status": "NOT_GIVEN", "value_text": None,
                                     "quote": None, "exchange_ref": None},
            "riders_announced": {"status": "GIVEN", "value_text": "Rs 12 dividend",
                                 "quote": "interim dividend of Rs 12",
                                 "exchange_ref": "P1", "char_start": 1,
                                 "char_end": 25},
        },
        "sector_slots": {
            "tcv_deal_wins": {"status": "GIVEN", "value_text": "$9.6bn",
                              "value_num": 9.6, "unit": "USD bn",
                              "period_basis": None, "quote": "TCV of 9.6 billion",
                              "exchange_ref": "P1", "char_start": 2, "char_end": 20},
            "attrition_ltm": {"status": "NOT_GIVEN", "value_text": None,
                              "value_num": None, "unit": None,
                              "period_basis": None, "quote": None,
                              "exchange_ref": None},
        },
        "commitments": [
            {"metric": "Headcount growth", "value": "10,000 net adds",
             "support": "was 8,000 last quarter", "status": "raised",
             "quote": "we expect to add ten thousand net this year",
             "exchange_ref": "P1", "char_start": 40, "char_end": 90},
        ],
    }
    base.update(overrides)
    return base


EXCHANGES = [
    {"exchange_ref": "P1", "kind": "prepared", "seq": 1, "analyst_name": None,
     "analyst_firm": None, "management_speakers": ["CEO"],
     "char_start": 0, "char_end": 100},
    {"exchange_ref": "Q1", "kind": "qa", "seq": 1, "analyst_name": "A Analyst",
     "analyst_firm": "Some Broker", "management_speakers": ["CFO"],
     "char_start": 100, "char_end": 200},
]


@pytest.fixture
def conn(tmp_path):
    c = store.get_connection(str(tmp_path / "events_test.db"))
    yield c
    c.close()


def _write(conn, symbol="TCS", quarter="2026-09-30", parsed=None):
    store.write_all(
        conn, symbol, quarter, parsed or _parsed(), EXCHANGES, PREPARED,
        PROVENANCE, TIMESTAMPS, SECTOR,
        price_move={"pct": 3.85, "from_date": "2026-10-08",
                    "to_date": "2026-10-09"},
        fiscal_quarter="Q2 FY27", call_date="2026-10-08",
    )


# --- round trip --------------------------------------------------------------

def test_write_all_populates_every_table(conn):
    _write(conn)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("earnings_transcript_sources", "earnings_call_exchanges",
                        "earnings_call_extraction", "earnings_frame",
                        "earnings_guidance_formal", "earnings_guidance_soft",
                        "earnings_sector_slots", "earnings_commitments")}
    assert counts["earnings_transcript_sources"] == 1
    assert counts["earnings_call_exchanges"] == 2
    assert counts["earnings_call_extraction"] == 1
    assert counts["earnings_frame"] == 5        # the four frame fields + risks_disclosed
    assert counts["earnings_guidance_formal"] == 4   # the four generic fields
    assert counts["earnings_guidance_soft"] == 1
    assert counts["earnings_sector_slots"] == 2
    assert counts["earnings_commitments"] == 1


def test_sheet_row_holds_the_v3_shape(conn):
    _write(conn)
    row = conn.execute(
        "SELECT mgmt_confidence, overall_tone, qa_tone, analyst_tone, "
        "evasive_count, evasive_exchanges, qa_divergence, price_move_pct, "
        "sector, sector_card, sector_unmapped FROM earnings_call_extraction"
    ).fetchone()
    assert row[0] == 0 and row[1] == 1 and row[2] == 0 and row[3] == -1
    assert row[4] == 2
    assert json.loads(row[5]) == ["Q3", "Q4"]
    assert row[6] == "MILD"                     # graded, not boolean
    assert row[7] == pytest.approx(3.85)
    assert row[8] == "IT" and row[9] == "it_services" and row[10] == 0


def test_not_given_is_stored_not_skipped(conn):
    # NOT_GIVEN is first-class (spec 4.1): a quarter where the company guides
    # nothing must still produce four formal rows, or the UI cannot tell
    # "asked and refused" from "never looked".
    _write(conn)
    statuses = [r[0] for r in conn.execute(
        "SELECT status FROM earnings_guidance_formal ORDER BY field_key")]
    assert statuses == ["NOT_GIVEN"] * 4


def test_unmapped_symbol_writes_no_sector_rows(conn):
    store.write_all(
        conn, "RELIANCE", "2026-09-30", _parsed(), EXCHANGES, PREPARED,
        PROVENANCE, TIMESTAMPS, {"sector": "Energy", "sector_card": None},
        fiscal_quarter="Q2 FY27",
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM earnings_sector_slots WHERE symbol='RELIANCE'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT sector_unmapped FROM earnings_call_extraction "
        "WHERE symbol='RELIANCE'").fetchone()[0] == 1


# --- QA check 6: timestamps on every row of every table ----------------------

def test_every_feature_row_carries_all_timestamps(conn):
    _write(conn)
    feature_tables = ("earnings_call_extraction", "earnings_frame",
                      "earnings_guidance_formal", "earnings_guidance_soft",
                      "earnings_sector_slots")
    for table in feature_tables:
        rows = conn.execute(
            f"SELECT announcement_at, source_published_at, "
            f"feature_available_at, extracted_at FROM {table}").fetchall()
        assert rows, f"{table} had no rows to check"
        for row in rows:
            assert all(v is not None for v in row), f"{table} missing a timestamp"


def test_feature_available_at_is_the_transcript_publish_time(conn):
    # Not the announcement: a transcript appears days after the print, and the
    # whole reason all three are stored is to keep that distinction honest.
    _write(conn)
    row = conn.execute(
        "SELECT announcement_at, feature_available_at "
        "FROM earnings_call_extraction").fetchone()
    assert row[0] == TIMESTAMPS["announcement_at"]
    assert row[1] == TIMESTAMPS["source_published_at"]
    assert row[1] > row[0]


# --- idempotency (spec 7.4) --------------------------------------------------

def test_already_extracted_is_keyed_on_document_and_version(conn):
    _write(conn)
    assert store.already_extracted(conn, "TCS", "2026-09-30", "a" * 64, "v3.0.0")
    # A different document for the same quarter is not the same row.
    assert not store.already_extracted(conn, "TCS", "2026-09-30", "c" * 64, "v3.0.0")
    # A new extractor version re-extracts.
    assert not store.already_extracted(conn, "TCS", "2026-09-30", "a" * 64, "v3.1.0")


def test_rewriting_the_same_quarter_does_not_duplicate(conn):
    _write(conn)
    _write(conn)
    for table in ("earnings_call_extraction", "earnings_frame",
                  "earnings_guidance_formal", "earnings_guidance_soft",
                  "earnings_sector_slots", "earnings_call_exchanges",
                  "earnings_commitments"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        expected = {"earnings_call_extraction": 1, "earnings_frame": 5,
                    "earnings_guidance_formal": 4, "earnings_guidance_soft": 1,
                    "earnings_sector_slots": 2, "earnings_call_exchanges": 2,
                    "earnings_commitments": 1}[table]
        assert count == expected, f"{table} duplicated on rewrite"


def test_failed_row_is_recorded_not_dropped(conn):
    # Spec 2.2: never write a partially valid row silently - but also never
    # just skip, or a re-run cannot tell a failure from an untried quarter.
    store.write_failed_row(conn, "TCS", "2026-06-30", PROVENANCE, TIMESTAMPS,
                           "quote not found verbatim", SECTOR)
    conn.commit()
    row = conn.execute(
        "SELECT row_status, failure_reason FROM earnings_call_extraction "
        "WHERE quarter_end='2026-06-30'").fetchone()
    assert row[0] == "EXTRACTION_FAILED"
    assert "verbatim" in row[1]


# --- the GUIDANCE cell (spec 4.4) --------------------------------------------

def test_guidance_cell_keeps_formal_and_soft_separate(conn):
    cell = store._guidance_cell(_parsed())
    assert cell.startswith("FORMAL: NOT GIVEN | SOFT: ")
    assert "positive on demand" in cell


def test_guidance_cell_shows_a_given_target_with_its_period():
    parsed = _parsed()
    parsed["guidance_formal"]["guidance_margin"] = {
        "status": "GIVEN", "value_text": "24-26%", "applies_to_period": "FY27",
        "quote": "margin of 24 to 26%", "exchange_ref": "P1",
        "value_num_low": 24.0, "value_num_high": 26.0, "unit": "%",
        "prior_value_text": None,
    }
    cell = store._guidance_cell(parsed)
    assert "FORMAL: margin 24-26% (FY27)" in cell


def test_guidance_cell_with_no_soft_outlook():
    cell = store._guidance_cell(_parsed(guidance_soft=[]))
    assert cell.endswith("| SOFT: none")


# --- fiscal quarter labelling ------------------------------------------------

@pytest.mark.parametrize("quarter_end,label", [
    (date(2026, 9, 30), "Q2 FY27"),
    (date(2026, 6, 30), "Q1 FY27"),
    (date(2026, 3, 31), "Q4 FY26"),   # Jan-Mar is Q4 of the FY it ends
    (date(2025, 12, 31), "Q3 FY26"),
])
def test_fiscal_quarter_labels(quarter_end, label):
    assert _fiscal_quarter(quarter_end) == label


# --- commitments / promise checks (v3.2.0) -----------------------------------

def test_read_commitments_round_trips_through_write_all(conn):
    _write(conn)
    commitments = store.read_commitments(conn, "TCS", "2026-09-30")
    assert len(commitments) == 1
    assert commitments[0]["metric"] == "Headcount growth"
    assert commitments[0]["value"] == "10,000 net adds"
    assert commitments[0]["status"] == "raised"


def test_read_commitments_empty_when_none_stored(conn):
    _write(conn, parsed=_parsed(commitments=[]))
    assert store.read_commitments(conn, "TCS", "2026-09-30") == []


def test_write_commitments_does_not_duplicate_on_rewrite(conn):
    _write(conn)
    _write(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM earnings_commitments WHERE symbol='TCS'"
    ).fetchone()[0]
    assert count == 1


def test_write_commitments_shrinking_item_count_does_not_leave_stale_rows(conn):
    # A re-run can legitimately produce FEWER commitments than before (one
    # dropped by cite verification) - delete-then-insert, not INSERT OR
    # REPLACE, so item_no 2 from an earlier run can't survive as a ghost row.
    two = _parsed(commitments=[
        {"metric": "A", "value": "1", "support": None, "status": "new",
         "quote": "a quote", "exchange_ref": "P1"},
        {"metric": "B", "value": "2", "support": None, "status": "new",
         "quote": "b quote", "exchange_ref": "P1"},
    ])
    _write(conn, parsed=two)
    assert len(store.read_commitments(conn, "TCS", "2026-09-30")) == 2

    one = _parsed(commitments=[
        {"metric": "A", "value": "1", "support": None, "status": "new",
         "quote": "a quote", "exchange_ref": "P1"},
    ])
    _write(conn, parsed=one)
    assert len(store.read_commitments(conn, "TCS", "2026-09-30")) == 1


def test_write_promise_checks_round_trips(conn):
    checks = [
        {"metric": "Exports run-rate", "promised_value": "220k/mo",
         "delivered_value": "244k/mo", "quote": "demand is improving",
         "exchange_ref": "P1", "char_start": 10, "char_end": 30,
         "verdict": "BEAT"},
        {"metric": "Margin", "promised_value": "cover about half",
         "delivered_value": None, "quote": None, "exchange_ref": None,
         "verdict": "NOT_ADDRESSED"},
    ]
    with conn:
        store.write_promise_checks(conn, "TCS", "2026-12-31", "2026-09-30",
                                   checks, PROVENANCE, TIMESTAMPS)
    rows = conn.execute(
        "SELECT metric, delivered_value, verdict, prior_quarter_end "
        "FROM earnings_promise_checks WHERE symbol='TCS' ORDER BY item_no"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0] == ("Exports run-rate", "244k/mo", "BEAT", "2026-09-30")
    assert rows[1] == ("Margin", None, "NOT_ADDRESSED", "2026-09-30")


def test_write_promise_checks_does_not_duplicate_on_rewrite(conn):
    checks = [{"metric": "A", "promised_value": "1", "delivered_value": "1",
              "quote": "q", "exchange_ref": "P1", "verdict": "MET"}]
    with conn:
        store.write_promise_checks(conn, "TCS", "2026-12-31", "2026-09-30",
                                   checks, PROVENANCE, TIMESTAMPS)
        store.write_promise_checks(conn, "TCS", "2026-12-31", "2026-09-30",
                                   checks, PROVENANCE, TIMESTAMPS)
    count = conn.execute(
        "SELECT COUNT(*) FROM earnings_promise_checks WHERE symbol='TCS'"
    ).fetchone()[0]
    assert count == 1
