"""Offline tests for transcript_source.py - the source lock, cover-page
stripping and the unreadable-filing guard (spec 2.1, 7.5, and the section-1
NSE-only rule).

No network and no PDFs: every function under test here is pure or takes bytes,
which is why strip_cover_pages takes a list of page strings rather than a
document. The live six-URL proof is a separate manual step (spec 9 step 3),
already run; these tests pin the behaviour it demonstrated.
"""

import os

import pytest

import transcript_source as ts


# --- source lock (spec section 1) --------------------------------------------

def test_nse_hosts_are_allowed():
    ts._assert_nse_url("https://nsearchives.nseindia.com/corporate/TCS_x.pdf")
    ts._assert_nse_url("https://www.nseindia.com/api/corporate-announcements")


def test_non_nse_hosts_are_refused():
    # The source lock is the rule most likely to be eroded by a convenient
    # mirror, so it must fail loudly rather than quietly fetching a copy.
    for url in (
        "https://quartr.com/transcripts/tcs.pdf",
        "https://www.alphastreet.com/tcs-q2.pdf",
        "https://www.tcs.com/investor-relations/transcript.pdf",
        "https://nseindia.com.evil.example/corporate/fake.pdf",
    ):
        with pytest.raises(ValueError, match="NSE only"):
            ts._assert_nse_url(url)


# --- cover-page stripping (spec 2.1) -----------------------------------------

_COVER = (
    "BSE Limited\nP. J. Towers, Dalal Street, Mumbai - 400001\n"
    "National Stock Exchange of India Limited\nScrip Code No. 532540\n"
    "Dear Sirs,\nSub: Transcript of the earnings conference call\n"
    "Pursuant to Regulation 30 of SEBI LODR\nYours faithfully,\n"
)
_BODY_1 = (
    "Page 1 of 28\nTranscript for Earnings Conference Call\nModerator:\n"
    "Ladies and gentlemen, good day and welcome to the earnings call.\n"
)
_BODY_2 = "Analyst: What drove the margin this quarter?\nCFO: Wage revision.\n"


def test_strips_a_single_cover_page():
    kept, stripped = ts.strip_cover_pages([_COVER, _BODY_1, _BODY_2])
    assert stripped == 1
    assert kept == [_BODY_1, _BODY_2]


def test_keeps_everything_when_there_is_no_cover():
    kept, stripped = ts.strip_cover_pages([_BODY_1, _BODY_2])
    assert stripped == 0
    assert kept == [_BODY_1, _BODY_2]


def test_transcript_markers_protect_a_page_that_also_has_boilerplate():
    # Some filers put the cover letter and the first transcript page on one
    # page. Stripping it would delete real prepared remarks, so transcript
    # markers win over cover markers.
    mixed = _COVER + _BODY_1
    kept, stripped = ts.strip_cover_pages([mixed, _BODY_2])
    assert stripped == 0
    assert kept[0] is mixed


def test_never_strips_deeper_than_max_cover_pages():
    # Over-stripping is worse than under-stripping: the word-count guard
    # catches a surviving cover letter, but deleted prepared remarks are
    # invisible.
    pages = [_COVER] * 5 + [_BODY_1]
    kept, stripped = ts.strip_cover_pages(pages)
    assert stripped == ts.MAX_COVER_PAGES
    assert kept[-1] is _BODY_1


def test_a_single_cover_only_page_is_not_stripped_to_nothing():
    # Leave it and let extract_text's word-count guard classify the filing,
    # rather than returning an empty string that reads like a clean parse.
    kept, stripped = ts.strip_cover_pages([_COVER])
    assert stripped == 0
    assert kept == [_COVER]


def test_one_cover_marker_alone_is_not_enough():
    # Scoring, not a single trigger - a transcript page that merely mentions
    # the exchange must not be mistaken for boilerplate.
    page = "Analyst from BSE Limited asks about margins.\nCFO: Margins improved.\n"
    kept, stripped = ts.strip_cover_pages([page, _BODY_2])
    assert stripped == 0


# --- unreadable filings (spec 7.5) -------------------------------------------

def test_cover_letter_only_pdf_raises_unreadable():
    # The real AXISBANK / KOTAKBANK / MARUTI case: the filing is a one-page
    # intimation letter (or a scanned image), not a transcript. That is
    # NOT_VERIFIED for the quarter, never a retry and never a mirror.
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Dear Sirs, Sub: Transcript. Yours faithfully.")
    content = doc.tobytes()
    doc.close()
    with pytest.raises(ts.TranscriptUnreadable):
        ts.extract_text(content)


def test_corrupt_bytes_raise_unreadable_not_a_crash():
    with pytest.raises(ts.TranscriptUnreadable):
        ts.extract_text(b"this is not a pdf at all")


def test_unreadable_is_a_valueerror_so_retry_loops_fail_fast():
    # Every job in this repo treats ValueError as "deterministic, do not
    # retry" (jobs/fill_actuals.py, refresh_earnings_history.py,
    # refresh_earnings_sentiment.py all share that guard). Retrying cannot
    # change what is inside a document, so this subclass keeps that contract.
    assert issubclass(ts.TranscriptUnreadable, ValueError)


# --- hashing -----------------------------------------------------------------

def test_hashes_are_stable_and_distinguish_content():
    assert ts.sha256_bytes(b"abc") == ts.sha256_bytes(b"abc")
    assert ts.sha256_bytes(b"abc") != ts.sha256_bytes(b"abd")
    assert ts.sha256_text("abc") == ts.sha256_bytes(b"abc")


def test_archive_paths_are_outside_the_repo():
    # Spec 7.5: original bytes go to a local archive directory, not the repo.
    # A 213 x 6 backfill is ~175MB of PDFs and text (measured on the six TCS
    # documents), which must never enter a git-tracked binary.
    pdf_path, text_path = ts.archive_paths("TCS", "2026-09-30", "deadbeef" * 8)
    repo = os.path.dirname(os.path.abspath(ts.__file__))
    assert not pdf_path.startswith(repo)
    assert not text_path.startswith(repo)
    assert pdf_path.endswith(".pdf") and text_path.endswith(".txt")
