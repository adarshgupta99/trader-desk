"""Fixture tests for nse_transcripts.select_transcript - pure, no
network. Row shapes mirror what NSE's corporate-announcements archive
actually returns, confirmed live 14 Oct 2026 against RELIANCE, TCS,
HDFCBANK and HINDUNILVR - see nse_transcripts.py's module docstring.
"""

from nse_transcripts import TRANSCRIPT_DESC, select_transcript


def _row(an_dt, sort_date, desc, text, url="http://x/a.pdf"):
    return {"an_dt": an_dt, "sort_date": sort_date, "desc": desc,
            "attchmntText": text, "attchmntFile": url}


def test_picks_the_transcript_not_the_recording_or_schedule():
    # Real RELIANCE shape: all three share the same desc category.
    rows = [
        _row("07-Jul-2026", "2026-07-07", TRANSCRIPT_DESC,
             "Schedule of the analyst meet to be held on July 17, 2026"),
        _row("17-Jul-2026", "2026-07-17", "Outcome of Board Meeting",
             "Unaudited Financial Results for the quarter ended June 30, 2026"),
        _row("18-Jul-2026", "2026-07-18", TRANSCRIPT_DESC,
             "Audio recording of the discussion ... is available", url="http://x/audio.pdf"),
        _row("19-Jul-2026", "2026-07-19", TRANSCRIPT_DESC,
             "Transcript of the discussion on the Unaudited Financial Results ...",
             url="http://x/transcript.pdf"),
    ]
    picked = select_transcript(rows)
    assert picked["attchmntFile"] == "http://x/transcript.pdf"


def test_matches_differently_worded_transcript_text():
    # TCS/HDFCBANK's real wording differs from RELIANCE's but both
    # contain "transcript" - case-insensitive match confirmed needed.
    rows = [_row("14-Oct-2026", "2026-10-14", TRANSCRIPT_DESC,
                 "Tata Consultancy Services Limited has informed the Exchange about Transcript")]
    assert select_transcript(rows) is not None


def test_no_transcript_in_window_returns_none():
    rows = [_row("07-Jul-2026", "2026-07-07", TRANSCRIPT_DESC, "Schedule of meet")]
    assert select_transcript(rows) is None


def test_wrong_desc_category_never_matches_even_with_the_word_transcript():
    # Defensive: the desc filter must still apply even if some other
    # announcement type happens to mention "transcript" in passing.
    rows = [_row("01-Jan-2026", "2026-01-01", "Press Release",
                 "A transcript of yesterday's interview is available on our website")]
    assert select_transcript(rows) is None


def test_multiple_transcripts_in_window_picks_most_recent():
    rows = [
        _row("15-Jul-2026", "2026-07-15", TRANSCRIPT_DESC, "... Transcript ...",
             url="http://x/old.pdf"),
        _row("14-Oct-2026", "2026-10-14", TRANSCRIPT_DESC, "... about Transcript",
             url="http://x/new.pdf"),
    ]
    assert select_transcript(rows)["attchmntFile"] == "http://x/new.pdf"
