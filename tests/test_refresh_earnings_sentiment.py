"""Regression test for jobs/refresh_earnings_sentiment.py's due_quarters:
a quarter already stored in earnings_call_sentiment must never be
reprocessed. Network (fetch_archive) is monkeypatched out - this tests
the dedup logic only, not live NSE access.
"""

from datetime import date, datetime

import jobs.refresh_earnings_sentiment as res


def test_due_quarters_skips_already_stored(tmp_path, monkeypatch):
    db_path = str(tmp_path / "events_test.db")
    monkeypatch.setattr(res, "DB_PATH", db_path)
    conn = res.get_connection()

    fake_filings = [
        {"financial_period": "2026-06-30",
         "filing_dt": datetime(2026, 7, 19, 18, 6, 37)},
        {"financial_period": "2026-03-31",
         "filing_dt": datetime(2026, 4, 26, 12, 0, 0)},
    ]
    monkeypatch.setattr(res, "fetch_archive", lambda *a, **k: [])
    monkeypatch.setattr(res, "identify_filings", lambda *a, **k: fake_filings)

    # Nothing stored yet - both quarters are due.
    due = res.due_quarters(conn, "TESTCO")
    assert {qe.isoformat() for _, qe in due} == {"2026-06-30", "2026-03-31"}

    # Store one, then only the other should still be due.
    conn.execute(
        "INSERT INTO earnings_call_sentiment "
        "(ticker, quarter_end, call_date, transcript_source_url, extraction_json, created_at) "
        "VALUES ('TESTCO', '2026-06-30', '2026-07-19', 'http://x', '{}', '2026-07-19T00:00:00')"
    )
    conn.commit()

    due = res.due_quarters(conn, "TESTCO")
    assert [qe.isoformat() for _, qe in due] == ["2026-03-31"]
    conn.close()
