"""Acceptance checks for analyst estimates (analyst_estimates_spec-c3606434.txt,
section 9). Checks A-G are synthetic: a temp events.db, no network calls -
yfinance/nselib are monkeypatched out. Check H is a manual browser check and
has no automated test; see its docstring below.
"""

import sqlite3
from datetime import date, timedelta

import pytest

import events_db
import jobs.fill_actuals as fa
import jobs.snapshot_estimates as se


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "events_test.db")
    log_path = str(tmp_path / "test.log")
    monkeypatch.setattr(se, "DB_PATH", path)
    monkeypatch.setattr(se, "LOG_PATH", log_path)
    monkeypatch.setattr(se, "INTER_CALL_SLEEP", 0)
    monkeypatch.setattr(fa, "DB_PATH", path)
    monkeypatch.setattr(fa, "LOG_PATH", log_path)
    monkeypatch.setattr(fa, "INTER_CALL_SLEEP", 0)
    monkeypatch.setattr(events_db, "DB_PATH", path)
    return path


def _seed_events(path, rows):
    """rows: list of (symbol, event_type, purpose, event_date)."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            symbol TEXT, event_type TEXT, subtype TEXT, event_date TEXT,
            record_date TEXT, purpose TEXT, source TEXT, fetched_at TEXT,
            raw_json TEXT,
            PRIMARY KEY (symbol, event_type, event_date, purpose)
        )
        """
    )
    conn.executemany(
        "INSERT INTO events (symbol, event_type, purpose, event_date) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _fake_estimate(eps=10.0, rev=1000.0, n_eps=5, n_rev=4):
    return {
        "eps_estimate": eps, "revenue_estimate": rev,
        "n_analysts_eps": n_eps, "n_analysts_rev": n_rev,
        "eps_low": eps - 1 if eps is not None else None,
        "eps_high": eps + 1 if eps is not None else None,
    }


def test_a_immutability(db_path, monkeypatch):
    conn = se.get_connection()
    est = _fake_estimate(eps=16.9)
    ok = se.insert_snapshot(conn, "RELIANCE", "2026-10-10", "2026-10-09", est)
    assert ok

    # Same (symbol, event_date, snapshot_date) with a different eps_estimate
    # must not overwrite the original.
    dup = se.insert_snapshot(conn, "RELIANCE", "2026-10-10", "2026-10-09", _fake_estimate(eps=99.0))
    assert dup is False
    row = conn.execute(
        "SELECT eps_estimate FROM earnings_estimates WHERE symbol='RELIANCE'"
    ).fetchone()
    assert row[0] == 16.9

    # A direct UPDATE of an estimate column must be blocked by the trigger.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE earnings_estimates SET eps_estimate = 99 WHERE symbol='RELIANCE'")
    conn.close()


def test_b_t1_timing(db_path, monkeypatch):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    _seed_events(db_path, [
        ("RELIANCE", "board_meeting", "Financial Results", tomorrow),
        ("TCS", "board_meeting", "Financial Results", today),
    ])
    monkeypatch.setattr(se, "fetch_estimate", lambda symbol: _fake_estimate())

    conn = se.get_connection()
    # Job "run on D-1": event dated tomorrow is due and gets snapshotted.
    counts = se.run(conn, [tomorrow], allow_past=False)
    assert counts["inserted"] == 1
    assert conn.execute("SELECT 1 FROM earnings_estimates WHERE symbol='RELIANCE'").fetchone()

    # Job "run on D": event dated today (already at/past T-1) is skipped.
    counts2 = se.run(conn, [today], allow_past=False)
    assert counts2["missed_t1"] == 1
    assert counts2["inserted"] == 0
    assert not conn.execute("SELECT 1 FROM earnings_estimates WHERE symbol='TCS'").fetchone()
    conn.close()


def test_c_no_estimate_row_is_returned(db_path):
    conn = se.get_connection()
    ok = se.insert_snapshot(
        conn, "PIIND", "2026-10-10", "2026-10-09",
        {"eps_estimate": None, "revenue_estimate": None, "n_analysts_eps": None,
         "n_analysts_rev": None, "eps_low": None, "eps_high": None},
    )
    assert ok
    conn.close()

    df = events_db.get_earnings_estimates(symbols=["PIIND"])
    assert len(df) == 1
    assert df.iloc[0]["eps_estimate"] is None
    assert df.iloc[0]["symbol"] == "PIIND"


@pytest.mark.parametrize("estimate,actual,expected", [
    (10, 12, 20.0),
    (10, 8, -20.0),
    (-5, -4, 20.0),
    (0, 5, None),
    (None, 5, None),
])
def test_d_surprise_math(estimate, actual, expected):
    got = fa.compute_surprise(estimate, actual)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


def test_e_actuals_fill_only_touches_actual_columns(db_path):
    conn = se.get_connection()
    est = _fake_estimate(eps=38.0, rev=7e11, n_eps=20, n_rev=15)
    se.insert_snapshot(conn, "TCS", "2026-10-05", "2026-10-04", est)
    before = dict(zip(
        ["eps_estimate", "revenue_estimate", "n_analysts_eps", "n_analysts_rev",
         "eps_low", "eps_high", "period_label", "source", "fetched_at"],
        conn.execute(
            "SELECT eps_estimate, revenue_estimate, n_analysts_eps, n_analysts_rev, "
            "eps_low, eps_high, period_label, source, fetched_at "
            "FROM earnings_estimates WHERE symbol='TCS'"
        ).fetchone(),
    ))

    row_id = conn.execute("SELECT id FROM earnings_estimates WHERE symbol='TCS'").fetchone()[0]
    fa.fill_row(conn, row_id, eps_actual=40.0, revenue_actual=7.2e11, surprise_pct=5.26,
                revenue_surprise_pct=2.86, source="nse_xbrl:Consolidated")

    after = dict(zip(
        before.keys(),
        conn.execute(
            "SELECT eps_estimate, revenue_estimate, n_analysts_eps, n_analysts_rev, "
            "eps_low, eps_high, period_label, source, fetched_at "
            "FROM earnings_estimates WHERE symbol='TCS'"
        ).fetchone(),
    ))
    assert before == after

    actual_row = conn.execute(
        "SELECT eps_actual, revenue_actual, surprise_pct FROM earnings_estimates WHERE symbol='TCS'"
    ).fetchone()
    assert actual_row == (40.0, 7.2e11, 5.26)
    # Provenance is stored, not assumed: actuals can come from NSE's own
    # filing XBRL or from yfinance as fallback, and a row must say which.
    assert conn.execute(
        "SELECT actual_source FROM earnings_estimates WHERE symbol='TCS'"
    ).fetchone()[0] == "nse_xbrl:Consolidated"
    conn.close()


def test_f_idempotence(db_path, monkeypatch):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    _seed_events(db_path, [("RELIANCE", "board_meeting", "Financial Results", tomorrow)])
    monkeypatch.setattr(se, "fetch_estimate", lambda symbol: _fake_estimate())

    conn = se.get_connection()
    counts1 = se.run(conn, [tomorrow], allow_past=False)
    assert counts1["inserted"] == 1

    counts2 = se.run(conn, [tomorrow], allow_past=False)
    assert counts2["inserted"] == 0
    assert counts2["skipped"] == 1

    total = conn.execute("SELECT COUNT(*) FROM earnings_estimates").fetchone()[0]
    assert total == 1
    conn.close()


def test_g_rate_limit_stops_cleanly(db_path, monkeypatch):
    # due_events orders by (event_date, symbol), so alphabetically this
    # processes RELIANCE, then TCS (which 429s), then would reach WIPRO.
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    _seed_events(db_path, [
        ("RELIANCE", "board_meeting", "Financial Results", tomorrow),
        ("TCS", "board_meeting", "Financial Results", tomorrow),
        ("WIPRO", "board_meeting", "Financial Results", tomorrow),
    ])

    def fetch_with_429(symbol):
        if symbol == "TCS":
            raise RuntimeError("429 Too Many Requests")
        return _fake_estimate()

    monkeypatch.setattr(se, "fetch_estimate", fetch_with_429)

    conn = se.get_connection()
    counts = se.run(conn, [tomorrow], allow_past=False)

    # RELIANCE (processed before the 429) got snapshotted; the run stops at
    # TCS and never reaches WIPRO or inserts anything partial for TCS.
    assert counts["inserted"] == 1
    assert conn.execute("SELECT 1 FROM earnings_estimates WHERE symbol='RELIANCE'").fetchone()
    assert not conn.execute("SELECT 1 FROM earnings_estimates WHERE symbol='TCS'").fetchone()
    assert not conn.execute("SELECT 1 FROM earnings_estimates WHERE symbol='WIPRO'").fetchone()
    conn.close()


# H. Manual check by Adarsh, not automatable: open the Events tab, find one
# big name (e.g. RELIANCE or TCS) with a frozen estimate, and one mid-cap
# showing "no estimate". Both rows should be visible, not dropped.
