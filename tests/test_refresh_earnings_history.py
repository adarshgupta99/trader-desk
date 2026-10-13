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
