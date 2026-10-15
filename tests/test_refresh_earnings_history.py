"""Regression test for jobs/refresh_earnings_history.py's call_with_retry:
a ValueError from nse_results.parse_facts (a deterministic "this filing
has no 3-month context" result, not a network blip) must fail immediately,
not burn through the full retry/backoff schedule.

Real incident, 13 Oct 2026: ICICIPRULI (a life insurer) files under a
different XBRL taxonomy whose declared contexts span ~356/~362 days -
never a quarter. Before this fix, the identical blind-retry bug in both
this job and jobs/fill_actuals.py meant every insurer quarter checked
burned ~75 seconds retrying something retrying could never fix.
"""

import jobs.refresh_earnings_history as reh


def test_value_error_fails_fast_without_retry_delay(tmp_path, monkeypatch):
    # log() writes to LOG_PATH regardless of this test - isolate it so a
    # test run never appends into the real job's log file (it otherwise
    # interleaves with a concurrently-running real backfill, confusing but
    # harmless - still worth avoiding).
    monkeypatch.setattr(reh, "LOG_PATH", str(tmp_path / "test.log"))
    sleeps = []
    monkeypatch.setattr(reh.time, "sleep", lambda s: sleeps.append(s))

    def always_raises_value_error():
        raise ValueError("no ~3-month context ending 2025-12-31 in document")

    try:
        reh.call_with_retry("test", always_raises_value_error)
        assert False, "expected ValueError to propagate"
    except ValueError:
        pass

    # No backoff sleep at all - a ValueError must not enter the retry loop.
    assert sleeps == []


def test_other_errors_still_retry_with_backoff(tmp_path, monkeypatch):
    monkeypatch.setattr(reh, "LOG_PATH", str(tmp_path / "test.log"))
    sleeps = []
    monkeypatch.setattr(reh.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def fails_twice_then_succeeds():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient NSE fetch error")
        return "ok"

    result = reh.call_with_retry("test", fails_twice_then_succeeds)
    assert result == "ok"
    assert sleeps == list(reh.RETRY_DELAYS[:2])


# --- due_symbols: the lookback window must match the Calendar table's own ---

def test_due_symbols_includes_a_recently_reported_event(tmp_path, monkeypatch):
    # Real bug, 13 Oct 2026: due_symbols only looked forward (event_date
    # >= today), but the Calendar subtab shows results back to
    # lookback_days=14 so a just-reported row's actual/surprise stays
    # visible for a while. TCS/DMART/HCLTECH etc, all reported in the
    # last few days and still shown in Calendar, were never backfilled -
    # permanently "no history" despite being exactly the rows a reader
    # would want it for.
    #
    # Widened 15 Oct 2026: due_symbols now returns EVERY symbol in the
    # universe (fno.symbols(), monkeypatched here to a small fixed set so
    # this test stays fast and isolated from the real 213-symbol list,
    # per the test suite's existing network-free discipline) - a symbol
    # outside the lookback window, or with no events row at all, is no
    # longer dropped, it's included with today's date as its window
    # anchor instead (same fallback an explicit --symbols call already
    # used for refresh_symbol's quarter-window calc).
    import sqlite3
    from datetime import date, timedelta

    monkeypatch.setattr(
        reh.fno, "symbols", lambda: ["RECENTCO", "STALECO", "UPCOMINGCO", "NEVERCO"],
    )
    db_path = str(tmp_path / "events_test.db")
    monkeypatch.setattr(reh, "DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE events (symbol TEXT, event_type TEXT, event_date TEXT, purpose TEXT)"
    )
    recent = (date.today() - timedelta(days=5)).isoformat()   # within the 14-day window
    stale = (date.today() - timedelta(days=20)).isoformat()   # outside it
    upcoming = (date.today() + timedelta(days=3)).isoformat()
    conn.executemany(
        "INSERT INTO events VALUES (?, 'board_meeting', ?, 'Financial Results')",
        [("RECENTCO", recent), ("STALECO", stale), ("UPCOMINGCO", upcoming)],
    )
    conn.commit()

    today_iso = date.today().isoformat()
    by_symbol = dict(reh.due_symbols(conn))
    assert by_symbol == {
        "RECENTCO": recent,
        "UPCOMINGCO": upcoming,
        "STALECO": today_iso,   # outside the window - falls back to today, not dropped
        "NEVERCO": today_iso,   # no events row at all - still included, same fallback
    }
    conn.close()
