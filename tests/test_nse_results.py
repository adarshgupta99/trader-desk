"""Fixture tests for nse_results.py - pure parsing/selection, no network.

Every case here mirrors a real shape observed live in NSE's
integrated-filing feed on 12 Oct 2026, not an invented edge case. The
figures are the real ones (TCS Sep-2026, DMART Sep-2026, HDFCBANK
Jun-2026) so a regression shows up as a number that no longer matches the
actual filing.
"""

from datetime import date

import pytest

from nse_results import (BASIS_CONSOLIDATED, BASIS_STANDALONE, parse_facts,
                         quarter_contexts, select_filing)
import xml.etree.ElementTree as ET


def _row(qe, basis, created, seq="1", type_=None, xbrl="http://x/a.xml"):
    return {
        "qe_Date": qe,
        "consolidated": basis,
        "creation_Date": created,
        "seq_Id": seq,
        "type": type_ or "Integrated Filing- Financials",
        "xbrl": xbrl,
    }


# --- select_filing ---------------------------------------------------------

def test_governance_filing_is_never_selected():
    # DMART's real shape: the Jun-2026 governance filing was broadcast
    # 29 Jul, LATER than the 11 Jul financials for the same quarter, so
    # "most recent row" would pick the governance one, which carries no
    # financial facts at all.
    rows = [
        _row("30-JUN-2026", None, "29-Jul-2026 18:21:46", type_="Integrated Filing- Governance"),
        _row("30-JUN-2026", "Consolidated", "11-Jul-2026 16:11:16"),
    ]
    picked = select_filing(rows, date(2026, 6, 30), BASIS_CONSOLIDATED)
    assert picked["type"] == "Integrated Filing- Financials"
    assert picked["creation_Date"] == "11-Jul-2026 16:11:16"


def test_basis_selects_between_the_two_same_quarter_filings():
    # Every quarter is filed twice, seconds apart, with different numbers.
    rows = [
        _row("30-SEP-2026", "Consolidated", "10-Oct-2026 17:08:17", seq="2"),
        _row("30-SEP-2026", "Standalone", "10-Oct-2026 17:08:14", seq="1"),
    ]
    assert select_filing(rows, date(2026, 9, 30), BASIS_CONSOLIDATED)["seq_Id"] == "2"
    assert select_filing(rows, date(2026, 9, 30), BASIS_STANDALONE)["seq_Id"] == "1"


def test_revision_supersedes_original():
    rows = [
        _row("30-SEP-2026", "Consolidated", "10-Oct-2026 17:08:17", seq="1"),
        _row("30-SEP-2026", "Consolidated", "14-Oct-2026 09:00:00", seq="9"),
    ]
    assert select_filing(rows, date(2026, 9, 30), BASIS_CONSOLIDATED)["seq_Id"] == "9"


def test_quarter_not_filed_yet_returns_none():
    rows = [_row("30-JUN-2026", "Consolidated", "11-Jul-2026 16:11:16")]
    assert select_filing(rows, date(2026, 9, 30), BASIS_CONSOLIDATED) is None


def test_row_without_xbrl_is_skipped():
    rows = [_row("30-SEP-2026", "Consolidated", "10-Oct-2026 17:08:17", xbrl=None)]
    assert select_filing(rows, date(2026, 9, 30), BASIS_CONSOLIDATED) is None


# --- context selection: the year-to-date trap ------------------------------

TCS_DOC = """<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:in-bse-fin="http://www.bseindia.com/xbrl/fin/2020-03-31/in-bse-fin">
  <in-bse-fin:DateOfStartOfReportingPeriod contextRef="OneD">2026-07-01</in-bse-fin:DateOfStartOfReportingPeriod>
  <in-bse-fin:DateOfEndOfReportingPeriod contextRef="OneD">2026-09-30</in-bse-fin:DateOfEndOfReportingPeriod>
  <in-bse-fin:DateOfStartOfReportingPeriod contextRef="FourD">2026-04-01</in-bse-fin:DateOfStartOfReportingPeriod>
  <in-bse-fin:DateOfEndOfReportingPeriod contextRef="FourD">2026-09-30</in-bse-fin:DateOfEndOfReportingPeriod>
  <in-bse-fin:DescriptionOfPresentationCurrency contextRef="OneD">INR</in-bse-fin:DescriptionOfPresentationCurrency>
  <in-bse-fin:RevenueFromOperations contextRef="OneD">731880000000</in-bse-fin:RevenueFromOperations>
  <in-bse-fin:RevenueFromOperations contextRef="FourD">1454630000000</in-bse-fin:RevenueFromOperations>
  <in-bse-fin:ProfitLossForPeriod contextRef="OneD">139340000000</in-bse-fin:ProfitLossForPeriod>
  <in-bse-fin:ProfitLossForPeriod contextRef="FourD">273540000000</in-bse-fin:ProfitLossForPeriod>
  <in-bse-fin:BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations contextRef="OneD">38.37</in-bse-fin:BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations>
  <in-bse-fin:BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations contextRef="FourD">75.27</in-bse-fin:BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations>
</xbrli:xbrl>
"""


def test_quarter_context_excludes_the_year_to_date_one():
    # Both contexts END on 30 Sep. Only OneD (1 Jul-30 Sep) is the
    # quarter; FourD (1 Apr-30 Sep) is the half-year. They share concept
    # names, so only the declared span distinguishes them.
    ctxs = quarter_contexts(ET.fromstring(TCS_DOC), date(2026, 9, 30))
    assert ctxs == ["OneD"]


def test_parse_facts_reads_the_quarter_not_the_half_year():
    facts = parse_facts(TCS_DOC.encode(), date(2026, 9, 30))
    assert facts["revenue"] == pytest.approx(731880000000)   # NOT 1454630000000
    assert facts["pat"] == pytest.approx(139340000000)       # NOT 273540000000
    assert facts["eps_basic"] == pytest.approx(38.37)        # NOT 75.27
    assert facts["revenue_concept"] == "RevenueFromOperations"
    assert facts["currency"] == "INR"


def test_parse_facts_raises_when_quarter_absent():
    # Asking for a quarter the document doesn't describe must fail loudly
    # rather than silently returning some other period's numbers.
    with pytest.raises(ValueError):
        parse_facts(TCS_DOC.encode(), date(2026, 6, 30))


# --- banking taxonomy ------------------------------------------------------

BANK_DOC = """<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:in-bse-fin="http://www.bseindia.com/xbrl/fin/2020-03-31/in-bse-fin">
  <in-bse-fin:DateOfStartOfReportingPeriod contextRef="OneD">2026-04-01</in-bse-fin:DateOfStartOfReportingPeriod>
  <in-bse-fin:DateOfEndOfReportingPeriod contextRef="OneD">2026-06-30</in-bse-fin:DateOfEndOfReportingPeriod>
  <in-bse-fin:Income contextRef="OneD">1331103600000</in-bse-fin:Income>
  <in-bse-fin:InterestEarned contextRef="OneD">905753300000</in-bse-fin:InterestEarned>
  <in-bse-fin:ProfitLossForThePeriod contextRef="OneD">203826900000</in-bse-fin:ProfitLossForThePeriod>
  <in-bse-fin:BasicEarningsPerShareAfterExtraordinaryItems contextRef="OneD">12.5</in-bse-fin:BasicEarningsPerShareAfterExtraordinaryItems>
  <in-bse-fin:DilutedEarningsPerShareAfterExtraordinaryItems contextRef="OneD">12.47</in-bse-fin:DilutedEarningsPerShareAfterExtraordinaryItems>
</xbrli:xbrl>
"""


def test_bank_taxonomy_resolves_via_fallback_concepts():
    # HDFCBANK Jun-2026. None of the standard concepts exist here -
    # notably its profit concept is ProfitLossForThePeriod, one word off
    # from the standard ProfitLossForPeriod. Before these fallbacks
    # existed every bank and NBFC in F&O silently returned NULL.
    facts = parse_facts(BANK_DOC.encode(), date(2026, 6, 30))
    assert facts["revenue"] == pytest.approx(1331103600000)
    assert facts["revenue_concept"] == "Income"
    assert facts["pat"] == pytest.approx(203826900000)
    assert facts["pat_concept"] == "ProfitLossForThePeriod"
    assert facts["eps_basic"] == pytest.approx(12.5)
    assert facts["eps_diluted"] == pytest.approx(12.47)


def test_concept_priority_prefers_standard_over_banking_fallback():
    # A filer carrying BOTH must resolve to the standard concept, so a
    # non-financial company never gets measured on a bank's definition.
    doc = TCS_DOC.replace(
        "</xbrli:xbrl>",
        '  <in-bse-fin:Income contextRef="OneD">999</in-bse-fin:Income>\n</xbrli:xbrl>',
    )
    facts = parse_facts(doc.encode(), date(2026, 9, 30))
    assert facts["revenue_concept"] == "RevenueFromOperations"
    assert facts["revenue"] == pytest.approx(731880000000)


# --- quarter derivation (jobs/fill_actuals.py) -----------------------------

def test_quarter_end_for_event_date():
    from jobs.fill_actuals import quarter_end_for_event_date as qe
    # TCS's real 8 Oct 2026 board meeting reported the Sep-2026 quarter.
    assert qe(date(2026, 10, 8)) == date(2026, 9, 30)
    assert qe(date(2026, 7, 30)) == date(2026, 6, 30)   # VEDL's Q1 print
    assert qe(date(2026, 5, 2)) == date(2026, 3, 31)
    # A January meeting reports the PREVIOUS calendar year's Dec quarter.
    assert qe(date(2026, 1, 10)) == date(2025, 12, 31)


def test_quarter_end_is_strictly_before_the_meeting():
    from jobs.fill_actuals import quarter_end_for_event_date as qe
    # A meeting ON a quarter end cannot be reporting that quarter.
    assert qe(date(2026, 9, 30)) == date(2026, 6, 30)


# --- bank revenue is derived, not read off the Income line ----------------

def test_bank_revenue_is_nii_plus_other_income_not_total_income():
    # HDFCBANK Jun-2025, the quarter that pinned this down: the derivation
    # equals yfinance's reported Total Revenue of 85,346.46cr to the paisa,
    # while the filing's own `Income` line is 133,054.97cr - 56% higher.
    # The estimate is set against the former, so using `Income` made bank
    # revenue surprise % meaningless (RBLBANK showed +64%).
    doc = """<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:in-bse-fin="http://www.bseindia.com/xbrl/fin/2020-03-31/in-bse-fin">
  <in-bse-fin:DateOfStartOfReportingPeriod contextRef="OneD">2025-04-01</in-bse-fin:DateOfStartOfReportingPeriod>
  <in-bse-fin:DateOfEndOfReportingPeriod contextRef="OneD">2025-06-30</in-bse-fin:DateOfEndOfReportingPeriod>
  <in-bse-fin:Income contextRef="OneD">1330549700000</in-bse-fin:Income>
  <in-bse-fin:InterestEarned contextRef="OneD">873718700000</in-bse-fin:InterestEarned>
  <in-bse-fin:InterestExpended contextRef="OneD">477085100000</in-bse-fin:InterestExpended>
  <in-bse-fin:OtherIncome contextRef="OneD">456831000000</in-bse-fin:OtherIncome>
  <in-bse-fin:ProfitLossForThePeriod contextRef="OneD">100000000</in-bse-fin:ProfitLossForThePeriod>
</xbrli:xbrl>
"""
    facts = parse_facts(doc.encode(), date(2025, 6, 30))
    assert facts["revenue"] == pytest.approx(853464600000)   # 85,346.46 cr
    assert facts["revenue"] != pytest.approx(1330549700000)  # NOT the Income line
    assert facts["revenue_concept"] == "InterestEarned-InterestExpended+OtherIncome"


def test_non_bank_never_uses_the_banking_derivation():
    # A filer with RevenueFromOperations must take it even if banking-ish
    # concepts also appear, so a normal company is never rerouted.
    doc = TCS_DOC.replace(
        "</xbrli:xbrl>",
        '  <in-bse-fin:InterestEarned contextRef="OneD">500</in-bse-fin:InterestEarned>\n'
        '  <in-bse-fin:InterestExpended contextRef="OneD">100</in-bse-fin:InterestExpended>\n'
        "</xbrli:xbrl>",
    )
    facts = parse_facts(doc.encode(), date(2026, 9, 30))
    assert facts["revenue_concept"] == "RevenueFromOperations"
    assert facts["revenue"] == pytest.approx(731880000000)


def test_bank_with_no_other_income_derives_from_nii_alone():
    doc = """<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:in-bse-fin="http://www.bseindia.com/xbrl/fin/2020-03-31/in-bse-fin">
  <in-bse-fin:DateOfStartOfReportingPeriod contextRef="OneD">2026-04-01</in-bse-fin:DateOfStartOfReportingPeriod>
  <in-bse-fin:DateOfEndOfReportingPeriod contextRef="OneD">2026-06-30</in-bse-fin:DateOfEndOfReportingPeriod>
  <in-bse-fin:Income contextRef="OneD">900</in-bse-fin:Income>
  <in-bse-fin:InterestEarned contextRef="OneD">800</in-bse-fin:InterestEarned>
  <in-bse-fin:InterestExpended contextRef="OneD">300</in-bse-fin:InterestExpended>
</xbrli:xbrl>
"""
    facts = parse_facts(doc.encode(), date(2026, 6, 30))
    assert facts["revenue"] == pytest.approx(500)  # 800 - 300, no other income
    assert facts["revenue_concept"] == "InterestEarned-InterestExpended+OtherIncome"


def test_bank_missing_interest_expended_falls_back_and_says_so():
    # Can't compute NII without both legs. Falling back to the `Income`
    # line is the wrong MEASURE, so the only acceptable behaviour is that
    # the row says `Income` and the mismatch stays diagnosable rather than
    # masquerading as a derived topline. BANK_DOC has no InterestExpended.
    facts = parse_facts(BANK_DOC.encode(), date(2026, 6, 30))
    assert facts["revenue"] == pytest.approx(1331103600000)
    assert facts["revenue_concept"] == "Income"


# --- quarter_window / previous_quarter_end ---------------------------------

def test_previous_quarter_end_steps_back_one_quarter():
    from nse_results import previous_quarter_end
    assert previous_quarter_end(date(2026, 9, 30)) == date(2026, 6, 30)
    assert previous_quarter_end(date(2026, 6, 30)) == date(2026, 3, 31)
    assert previous_quarter_end(date(2026, 3, 31)) == date(2025, 12, 31)
    assert previous_quarter_end(date(2025, 12, 31)) == date(2025, 9, 30)


def test_quarter_window_is_oldest_first_and_inclusive():
    from nse_results import quarter_window
    window = quarter_window(date(2026, 9, 30), 6)
    assert window == [
        date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31),
        date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30),
    ]
    assert len(window) == 6
