"""Transcript source handling for the v3 extraction pipeline: fetch the NSE
PDF, hash it, archive the original bytes, and turn it into the cleaned text
the LLM reads (spec sections 2.1 and 7.5).

NSE-ONLY, ORIGINALS ONLY (spec section 1). The only URL this module will
fetch is an NSE corporate-filing attachment. A transcript that cannot be
obtained from NSE is NOT_VERIFIED - never filled from QuarterMark,
AlphaStreet, a company IR site or a news copy.

WHY THE PDF HASH MATTERS: it is the identity of the source for the whole
pipeline. It is half the idempotency key in spec 7.4 ((symbol, quarter,
source_pdf_sha256, extractor_version)), so a re-run over an unchanged
document costs nothing, while a document NSE has quietly replaced produces a
different hash and genuinely re-extracts.

DISCOVERY IS REUSED, NOT REBUILT. Finding *which* filing is the transcript
already exists and is live-verified in nse_transcripts.py (desc category plus
the "transcript" test on the filing's own text, with AGM/Investor-Day
exclusions). Spec 7.5 says to treat NSE's endpoint shapes as unverified and
check them against live responses; that check was already done in this repo,
so this module composes with that code instead of re-deriving it. Note the
spec's "no regex or keyword layers" rule is about EXTRACTION - deciding which
of several filings is the transcript, and stripping a cover letter, are input
plumbing, which the same rule explicitly permits.

PyMuPDF, not pypdf (Adarsh's call, 16 Oct 2026). Both read these files, but
the spec names PyMuPDF and it preserves reading order more reliably on the
two-column layouts some filers use.
"""

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone

import pymupdf

from nselib.libutil import nse_urlfetch

IST = timezone(timedelta(hours=5, minutes=30))

#: Raw PDFs and cleaned text live OUTSIDE the repo (spec 7.5). Only hashes and
#: paths go into the committed events.db - a 213 x 6 backfill of transcript
#: text would otherwise add ~100MB to a binary file tracked by git.
ARCHIVE_ROOT = os.environ.get(
    "TRADER_DESK_ARCHIVE",
    os.path.join(os.path.expanduser("~"), ".trader_desk", "transcript_archive"),
)

#: Only an NSE host is ever fetched. Enforced, not assumed - the source lock
#: in spec section 1 is the one rule most likely to be eroded by a convenient
#: mirror link, so it fails loudly instead.
_ALLOWED_HOSTS = ("nsearchives.nseindia.com", "www.nseindia.com", "nseindia.com")

#: A real transcript runs to thousands of words. Below this, after the cover
#: letter is removed, the filing is almost always a cover-letter-only PDF or a
#: scanned image with no text layer (confirmed live on AXISBANK, INDUSINDBK,
#: KOTAKBANK and MARUTI, every quarter). That is `pdf_unreadable`, per spec
#: 7.5 - NOT_VERIFIED for the quarter, never filled from another source.
MIN_TRANSCRIPT_WORDS = 200

#: Markers of an exchange cover letter. Scoring, not a single trigger, so an
#: ordinary transcript page that happens to mention "BSE Limited" is not
#: mistaken for boilerplate.
_COVER_MARKERS = (
    "dear sir", "dear sirs", "dear madam", "sub:", "subject:",
    "pursuant to regulation", "yours faithfully", "yours sincerely",
    "bse limited", "national stock exchange", "scrip code",
    "corporate identity no", "encl", "thanking you",
)

#: Markers that a page is already transcript body. If a leading page carries
#: these it is kept, whatever boilerplate it also contains.
_TRANSCRIPT_MARKERS = (
    "moderator", "ladies and gentlemen", "good morning", "good evening",
    "good day", "thank you for standing by", "question-and-answer",
    "first question", "management team",
)

#: A cover letter is one page, occasionally two. Never strip deeper than this:
#: on a filing with no cover at all, over-stripping would silently delete real
#: prepared remarks.
MAX_COVER_PAGES = 2


class TranscriptUnreadable(ValueError):
    """The PDF fetched but yields no usable transcript text (scanned image,
    cover-letter-only filing, or a corrupt file). A deterministic content
    problem: retrying cannot change what is in the document, so callers must
    mark the quarter NOT_VERIFIED rather than retry."""


def _assert_nse_url(url):
    host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
    if host not in _ALLOWED_HOSTS:
        raise ValueError(
            f"refusing to fetch {host!r}: transcripts come from NSE only "
            "(spec section 1 source lock)"
        )


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_pdf(url):
    """Fetch an NSE attachment. Returns a dict with the raw bytes and their
    provenance. Raises for a non-NSE host or a non-200 response; a caller
    decides whether that is a retry (network) or a gap (missing filing)."""
    _assert_nse_url(url)
    resp = nse_urlfetch(url)
    if resp.status_code != 200:
        raise RuntimeError(f"NSE returned {resp.status_code} for {url}")
    content = resp.content
    return {
        "url": url,
        "content": content,
        "pdf_sha256": sha256_bytes(content),
        "pdf_bytes": len(content),
        "http_status": resp.status_code,
        "fetched_at": datetime.now(IST).isoformat(),
    }


def _looks_like_cover(page_text):
    low = page_text.lower()
    if any(m in low for m in _TRANSCRIPT_MARKERS):
        return False
    return sum(m in low for m in _COVER_MARKERS) >= 2


def strip_cover_pages(pages):
    """(kept_pages, n_stripped) - drops leading exchange-cover-letter pages.

    Pure, so the boundary is testable without a PDF. Only LEADING pages are
    considered and never more than MAX_COVER_PAGES: the first page that reads
    as transcript body stops the scan, so a cover marker appearing later in a
    call (an analyst saying "scrip code", say) cannot delete anything.
    """
    stripped = 0
    for page in pages[:MAX_COVER_PAGES]:
        if not _looks_like_cover(page):
            break
        stripped += 1
    if stripped >= len(pages):     # nothing would survive - keep it all and
        return list(pages), 0      # let the word-count guard judge it instead
    return list(pages[stripped:]), stripped


def extract_text(pdf_content):
    """Cleaned transcript text from PDF bytes, via PyMuPDF.

    Strips the cover page before anything else (spec 2.1), then guards against
    a filing with no real transcript in it. Returns the text plus the detail
    needed to audit the cleaning: page counts and how many pages were removed.
    """
    try:
        doc = pymupdf.open(stream=pdf_content, filetype="pdf")
    except Exception as e:
        raise TranscriptUnreadable(f"PyMuPDF could not open the PDF: {e}") from e

    with doc:
        pages = [page.get_text() for page in doc]
    page_count = len(pages)

    kept, stripped = strip_cover_pages(pages)
    text = "\n\n".join(kept).strip()
    words = len(re.findall(r"[A-Za-z]+", text))
    if words < MIN_TRANSCRIPT_WORDS:
        raise TranscriptUnreadable(
            f"only {words} words after removing {stripped} cover page(s) from "
            f"{page_count} page(s) - cover-letter-only filing or a scanned PDF "
            "with no text layer, not a usable transcript"
        )

    return {
        "cleaned_text": text,
        "cleaned_text_sha256": sha256_text(text),
        "page_count": page_count,
        "cover_pages_stripped": stripped,
        "word_count": words,
    }


def archive_paths(symbol, quarter_end, pdf_sha256):
    base = os.path.join(ARCHIVE_ROOT, symbol, quarter_end)
    return (
        os.path.join(base, f"{pdf_sha256[:16]}.pdf"),
        os.path.join(base, f"{pdf_sha256[:16]}.txt"),
    )


def archive(symbol, quarter_end, pdf_content, pdf_sha256, cleaned_text):
    """Write the original bytes and the cleaned text to the local archive.

    The PDF is stored untouched (spec 7.5) so any row can be re-derived from
    the exact document it came from. Returns both paths for storage.
    """
    pdf_path, text_path = archive_paths(symbol, quarter_end, pdf_sha256)
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
    if not os.path.exists(pdf_path):
        with open(pdf_path, "wb") as f:
            f.write(pdf_content)
    with open(text_path, "w") as f:
        f.write(cleaned_text)
    return pdf_path, text_path


def load_archived_text(symbol, quarter_end, pdf_sha256):
    """Cleaned text previously archived for this document, or None.

    Lets a re-extraction under a new extractor_version skip the fetch
    entirely, which keeps the backfill polite to NSE on reruns.
    """
    _, text_path = archive_paths(symbol, quarter_end, pdf_sha256)
    if not os.path.exists(text_path):
        return None
    with open(text_path) as f:
        return f.read()


def prepare(symbol, quarter_end, url):
    """Fetch -> hash -> archive -> clean, in one step.

    Returns everything earnings_transcript_sources needs plus the cleaned text
    for the extractor. Raises TranscriptUnreadable for a document with no
    usable transcript in it (caller marks the quarter NOT_VERIFIED).
    """
    fetched = fetch_pdf(url)
    cleaned = extract_text(fetched["content"])
    pdf_path, text_path = archive(
        symbol, quarter_end, fetched["content"],
        fetched["pdf_sha256"], cleaned["cleaned_text"],
    )
    return {
        **{k: v for k, v in fetched.items() if k != "content"},
        **cleaned,
        "pdf_archive_path": pdf_path,
        "cleaned_text_path": text_path,
    }
