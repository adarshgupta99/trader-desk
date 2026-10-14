"""Earnings-call transcript discovery from NSE's corporate-announcements
archive (Week 5-6 spec, Earnings call sentiment tracker). Reuses
jobs.refresh_expected_moves.fetch_archive - same endpoint, same
cookie-bootstrap, no new fetch mechanism.

DISCOVERY, verified live 14 Oct 2026 against 4 real transcripts (RELIANCE,
TCS, HDFCBANK, HINDUNILVR), all real extractable text (not scanned
images), via pypdf:

- Transcripts sit under desc == "Analysts/Institutional Investor Meet/
  Con. Call Updates" - the SAME category as the audio recording and the
  advance meeting-schedule notice for the same call, so desc alone can't
  identify the real transcript. The filing's own attchmntText can:
  "transcript" (case-insensitive) appears in it for a real transcript
  and never for the recording/schedule rows, across all 4 companies
  checked, despite each company wording the sentence differently
  (RELIANCE: "Transcript of the discussion on the Unaudited Financial
  Results..."; TCS: "...has informed the Exchange about Transcript";
  HDFCBANK: same shape as TCS; HINDUNILVR: "Transcript of the Earnings
  Conference Call for the quarter ended...").
- Speaker-label format is NOT standardised across companies - RELIANCE
  uses block headers ("Questioner (Name, Firm)" / "Company Speaker
  (Name)"), TCS/HDFCBANK/HINDUNILVR use inline "Name: text" labels with
  a "Moderator:" role too. This is exactly why the spec's management-
  vs-Q&A split is an LLM job (Track B), not a regex: no single pattern
  handles all four conventions seen already, let alone the rest of the
  F&O universe. This module does NOT attempt to split management from
  Q&A - it only finds and extracts the raw transcript text.
"""

import io
import re

from jobs.refresh_expected_moves import fetch_archive
from pypdf import PdfReader
from nselib.libutil import nse_urlfetch

TRANSCRIPT_DESC = "Analysts/Institutional Investor Meet/Con. Call Updates"


def select_transcript(rows):
    """The single most likely transcript filing among already-fetched
    archive `rows`, or None if nothing matches. Pure selection logic,
    no network - split out from find_transcript_filing so it's
    fixture-testable and so a caller already holding rows (fetched for
    another purpose) can reuse them instead of fetching twice.

    When more than one real transcript row matches (rare - would mean
    two calls in range), the most recent one wins; a caller that needs a
    specific quarter's call should narrow the fetch window tightly
    around that quarter's results date instead of relying on this
    tie-break.
    """
    candidates = [
        r for r in rows
        if r.get("desc") == TRANSCRIPT_DESC
        and "transcript" in (r.get("attchmntText") or "").lower()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.get("sort_date") or "")


def find_transcript_filing(symbol, from_date, to_date):
    """select_transcript, fetching the archive window itself."""
    return select_transcript(fetch_archive(symbol, from_date, to_date))


def fetch_transcript_text(url):
    """Full transcript text, pages joined with a blank line. Raises if
    the fetch fails or the PDF can't be parsed - callers decide how to
    log/skip, same division of labour as nse_results.py."""
    resp = nse_urlfetch(url)
    if resp.status_code != 200:
        raise RuntimeError(f"transcript fetch returned {resp.status_code} for {url}")
    reader = PdfReader(io.BytesIO(resp.content))
    pages = [p.extract_text() or "" for p in reader.pages]
    text = "\n\n".join(pages)
    if len(re.findall(r"[A-Za-z]+", text)) < 200:
        # A real transcript is thousands of words; this few suggests a
        # scanned image pypdf couldn't read, or the wrong document -
        # surface it rather than silently storing near-empty text.
        raise ValueError(
            f"extracted suspiciously little text ({len(text)} chars) from {url} - "
            "likely a scanned PDF pypdf can't read, not a real transcript failure"
        )
    return text
