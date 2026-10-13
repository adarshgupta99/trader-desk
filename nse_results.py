"""NSE Integrated Filing results -> structured quarterly actuals (EPS,
revenue, PAT) parsed from the filing's own XBRL document.

WHY THIS EXISTS, AND WHAT IT REPLACES (12 Oct 2026, Adarsh's direction)
----------------------------------------------------------------------
Actuals used to come from yfinance only. That was itself a fallback: the
original spec wanted NSE, and `nselib.financial_results_for_equity` was
discovery-tested on 8 Oct 2026 and found structurally unusable - it reads
`/api/corporates-financial-results`, which over a full trailing year
returned 18 distressed/suspended smallcaps and zero mainstream F&O names.
Re-verified live on 12 Oct 2026: that endpoint still returns ZERO rows for
a window containing TCS's real 8 Oct results. It is not used here.

This module uses a DIFFERENT endpoint, `/api/integrated-filing-results`,
which does work. Verified live 12 Oct 2026 against the figures in the
filings themselves: TCS Sep-2026 consolidated revenue 731.88bn / PAT
139.34bn / EPS 38.37; DMART Sep-2026 consolidated 19,644.01cr /
742.98cr / 11.40; RELIANCE Jun-2026 revenue 3,118.5bn. Coverage spot-check
across large caps, a bank, an NBFC, a midcap and a symbol with "&" in its
name: all returned 12 financial filings covering 6 quarters.

yfinance is NOT deleted - jobs/fill_actuals.py still falls back to it.
It is the only cover for a symbol whose XBRL concepts aren't mapped here
(see CONCEPT_CANDIDATES), and a disagreement between the two sources is
how a bad mapping gets noticed instead of silently storing a wrong number.

THREE TRAPS THIS MODULE EXISTS TO HANDLE
----------------------------------------
1. CONTEXT. One XBRL document carries the quarter AND the year-to-date
   figure under the SAME concept name, distinguished only by contextRef.
   TCS Sep-2026: contextRef "OneD" (1 Jul - 30 Sep) is 731.88bn, "FourD"
   (1 Apr - 30 Sep) is 1,454.63bn. Taking the wrong one stores H1 as the
   quarter - a 2x error that looks entirely plausible. So a context is
   only accepted when its own start/end dates prove it is a ~3-month
   period ending on the target quarter end; nothing is read positionally.

2. BANKS USE A DIFFERENT TAXONOMY, and subtly. HDFCBANK has none of
   `RevenueFromOperations`, `ProfitLossForPeriod`, or the standard EPS
   concept. Its profit concept is `ProfitLossForThePeriod` - one word
   different from the standard one. A single-concept parser returns NULL
   for every bank and NBFC in the F&O universe while appearing to work.
   Hence ordered candidate lists, and `*_concept` recorded alongside every
   value so a wrong pick is auditable rather than invisible.

3. BASIS. Every quarter is filed TWICE, standalone and consolidated,
   seconds apart, with genuinely different numbers - and not in a
   predictable direction: DMART Sep-2026 standalone PAT (803.60cr)
   EXCEEDS consolidated (742.98cr) while its revenue is lower. Mixing
   bases silently corrupts a quarter-over-quarter series, so basis is an
   explicit argument and is stored on every row.

Also: `Integrated Filing- Governance` rows are interleaved with the
financial ones and can carry a LATER broadcast date than the financials
for the same quarter (DMART Jun-2026: governance 29 Jul, financials
11 Jul), so filings are selected by type, never by "most recent row".

UNITS: values are absolute INR. Verified across TCS/DMART/RELIANCE; these
documents carry no `LevelOfRoundingUsedInFinancialStatements` element at
all. If one ever appears declaring a non-absolute scale, parse_facts
records it in `rounding` and the caller is expected to treat the row as
suspect rather than silently mis-scale by 10^5.
"""

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from urllib.parse import quote

from nselib.libutil import nse_urlfetch

FILING_INDEX_URL = "https://www.nseindia.com/api/integrated-filing-results"
FINANCIALS_TYPE = "Integrated Filing- Financials"

#: A quarterly context is ~3 months. Real quarters run 89-92 days
#: (Jan-Mar is 90 in a leap year, 89 otherwise; Jul-Sep is 92). The window
#: is widened slightly either side to tolerate a filer stating an
#: off-by-a-day period, while staying far clear of a half-year (181+) or
#: full-year (365) context, which is the distinction that actually matters.
QUARTER_DAYS_MIN = 80
QUARTER_DAYS_MAX = 100

#: Ordered by preference. The first concept present in the quarter context
#: wins, and its name is recorded so the choice stays auditable.
#:
#: The banking entries are NOT interchangeable with the standard ones,
#: which is why a bank's revenue is DERIVED (_derive_bank_revenue) rather
#: than read off any single line here.
CONCEPT_CANDIDATES = {
    # Banking entries are a LAST RESORT only - a bank's revenue is derived
    # instead, see _derive_bank_revenue. `Income` is a bank's TOTAL income
    # (interest earned + other income) and is nothing like the topline the
    # analyst estimate measures: RBLBANK Sep-2026 filed Income 5,401.65cr
    # against a 3,291.30cr consensus, a meaningless +64% "surprise".
    "revenue": [
        "RevenueFromOperations",          # standard (non-financial)
        "Income",                         # banking: total income - see above
        "InterestEarned",                 # banking fallback
    ],
    "pat": [
        "ProfitLossForPeriod",            # standard
        "ProfitLossForThePeriod",         # banking - note the extra "The"
        "ProfitOrLossAttributableToOwnersOfParent",
    ],
    # "AfterExtraordinaryItems" is preferred over "Before" because it is
    # the bottom-line figure, the one comparable to a standard filer's
    # reported EPS and to the analyst estimate it gets measured against.
    # (HDFCBANK Jun-2026 reports both, identical at 12.50, having had no
    # extraordinary items - they only diverge when a company does.)
    "eps_basic": [
        "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
        "BasicEarningsLossPerShareFromContinuingOperations",
        "BasicEarningsPerShareAfterExtraordinaryItems",      # banking
        "BasicEarningsPerShareBeforeExtraordinaryItems",     # banking
        "BasicEarningsPerShare",
    ],
    "eps_diluted": [
        "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
        "DilutedEarningsLossPerShareFromContinuingOperations",
        "DilutedEarningsPerShareAfterExtraordinaryItems",    # banking
        "DilutedEarningsPerShareBeforeExtraordinaryItems",   # banking
        "DilutedEarningsPerShare",
    ],
}

BASIS_CONSOLIDATED = "Consolidated"
BASIS_STANDALONE = "Standalone"


def _localname(tag):
    return re.sub(r"^\{.*\}", "", tag)


def _parse_qe(text):
    """'30-SEP-2026' (NSE's qe_Date form) -> date. Case-insensitive on the
    month because NSE sends uppercase here and title case elsewhere."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip().title(), "%d-%b-%Y").date()
    except ValueError:
        return None


def select_filing(rows, quarter_end, basis):
    """Pick the one filing row to parse for `quarter_end` on `basis`.

    Filters to financial filings only (governance filings share the feed
    and can be broadcast later for the same quarter), matches the quarter
    end exactly, and matches the basis. Among survivors the most recently
    created row wins, so a re-filed/revised document supersedes the
    original rather than being ignored.

    Returns None when the quarter simply isn't filed yet - a normal state
    in the days after a board meeting, not an error.
    """
    candidates = []
    for row in rows or []:
        if row.get("type") != FINANCIALS_TYPE:
            continue
        if _parse_qe(row.get("qe_Date")) != quarter_end:
            continue
        if (row.get("consolidated") or "").strip().lower() != basis.lower():
            continue
        if not row.get("xbrl"):
            continue
        candidates.append(row)
    if not candidates:
        return None
    return max(candidates, key=lambda r: (r.get("creation_Date") or "", r.get("seq_Id") or ""))


def quarter_contexts(root, quarter_end):
    """contextRef ids whose OWN declared period is a ~3-month span ending
    on `quarter_end`. This is the guard against reading the year-to-date
    figure, which sits in the same document under the same concept name.
    """
    spans = {}
    for el in root.iter():
        name = _localname(el.tag)
        if name in ("DateOfStartOfReportingPeriod", "DateOfEndOfReportingPeriod"):
            ctx = el.get("contextRef")
            if ctx and el.text:
                spans.setdefault(ctx, {})[name] = el.text.strip()

    matching = []
    for ctx, span in spans.items():
        start_text = span.get("DateOfStartOfReportingPeriod")
        end_text = span.get("DateOfEndOfReportingPeriod")
        if not start_text or not end_text:
            continue
        try:
            start = date.fromisoformat(start_text)
            end = date.fromisoformat(end_text)
        except ValueError:
            continue
        if end != quarter_end:
            continue
        if not (QUARTER_DAYS_MIN <= (end - start).days + 1 <= QUARTER_DAYS_MAX):
            continue
        matching.append(ctx)
    return matching


def _derive_bank_revenue(by_concept):
    """(value, concept_label) for a bank's topline, or (None, None).

    Banks don't file `RevenueFromOperations`. The measure analysts quote -
    and the one yfinance reports as a bank's "Total Revenue", which is what
    the stored estimate was taken from - is net interest income plus other
    income, NOT the `Income` (total income) line the filing leads with.

    Verified exactly, not inferred: HDFCBANK Jun-2025 derives to
    85,346.46cr here and yfinance reports Total Revenue of 85,346.46cr for
    the same quarter - equal to the paisa. Its `Income` line for that
    quarter is 133,054.97cr, 56% higher, which is what made bank revenue
    surprise % nonsense before this existed.
    """
    try:
        interest_earned = float(by_concept["InterestEarned"])
        interest_expended = float(by_concept["InterestExpended"])
    except (KeyError, TypeError, ValueError):
        return None, None
    # OtherIncome is genuinely optional; a bank with none still has a
    # valid topline of just net interest income.
    try:
        other_income = float(by_concept.get("OtherIncome", 0.0) or 0.0)
    except (TypeError, ValueError):
        other_income = 0.0
    net_interest_income = interest_earned - interest_expended
    return (net_interest_income + other_income,
            "InterestEarned-InterestExpended+OtherIncome")


def parse_facts(xbrl_bytes, quarter_end):
    """Pull the quarter's facts out of one XBRL document.

    Returns a dict with a value and the concept name that produced it for
    each of revenue/pat/eps_basic/eps_diluted (None when no mapped concept
    is present), plus `currency`, `rounding` and the `contexts` used.

    Raises ValueError when no context in the document describes a quarter
    ending on `quarter_end` - better to fail loudly than to fall back to
    whatever period happens to be present.
    """
    root = ET.fromstring(xbrl_bytes)
    contexts = quarter_contexts(root, quarter_end)
    if not contexts:
        raise ValueError(f"no ~3-month context ending {quarter_end} in document")

    by_concept = {}
    currency, rounding = set(), set()
    for el in root.iter():
        name = _localname(el.tag)
        text = (el.text or "").strip()
        if not text:
            continue
        if name == "DescriptionOfPresentationCurrency":
            currency.add(text)
        elif name == "LevelOfRoundingUsedInFinancialStatements":
            rounding.add(text)
        if el.get("contextRef") in contexts:
            by_concept.setdefault(name, text)

    out = {
        "currency": sorted(currency)[0] if len(currency) == 1 else (sorted(currency) or None),
        "rounding": sorted(rounding)[0] if len(rounding) == 1 else (sorted(rounding) or None),
        "contexts": contexts,
    }
    for field, candidates in CONCEPT_CANDIDATES.items():
        value, concept = None, None
        # A bank's topline is derived, not read off one line. Only when
        # the filer has no RevenueFromOperations - so a non-financial
        # company is never rerouted through the banking derivation.
        if field == "revenue" and "RevenueFromOperations" not in by_concept:
            value, concept = _derive_bank_revenue(by_concept)
        if value is None:
            for candidate in candidates:
                if candidate in by_concept:
                    try:
                        value = float(by_concept[candidate])
                    except ValueError:
                        continue
                    concept = candidate
                    break
        out[field] = value
        out[f"{field}_concept"] = concept
    return out


def fetch_filing_index(symbol):
    """All integrated-filing rows NSE lists for `symbol` (financials and
    governance both; caller filters). The feed returns the most recent
    page, which covers roughly six quarters - far more than the one
    quarter this module is normally asked for."""
    url = f"{FILING_INDEX_URL}?index=equities&symbol={quote(symbol, safe='')}"
    resp = nse_urlfetch(url)
    if resp.status_code != 200:
        raise RuntimeError(f"NSE filing index returned {resp.status_code} for {symbol}")
    return (resp.json() or {}).get("data") or []


def fetch_xbrl(url):
    resp = nse_urlfetch(url)
    if resp.status_code != 200:
        raise RuntimeError(f"NSE XBRL fetch returned {resp.status_code} for {url}")
    return resp.content


def get_quarter_actuals(symbol, quarter_end, basis=BASIS_CONSOLIDATED, rows=None):
    """Actuals for one symbol/quarter/basis, or None if not filed yet.

    `rows` lets a caller reuse an already-fetched index rather than
    hitting NSE again - the repo's polite-fetching rule.
    """
    if rows is None:
        rows = fetch_filing_index(symbol)
    filing = select_filing(rows, quarter_end, basis)
    if filing is None:
        return None
    facts = parse_facts(fetch_xbrl(filing["xbrl"]), quarter_end)
    facts.update({
        "symbol": symbol,
        "quarter_end": quarter_end.isoformat(),
        "basis": basis,
        "xbrl_url": filing["xbrl"],
        "broadcast_date": filing.get("broadcast_Date"),
        "seq_id": filing.get("seq_Id"),
    })
    return facts
