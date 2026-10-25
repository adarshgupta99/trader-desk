"""Tests for guidance_logic.promised_vs_delivered - pure, no DB, no network.

Covers the five verdict states and the reasons each one is reached, since
this function's whole point is to never guess a verdict it can't support.
"""

import guidance_logic as gl


def _promise(status="GIVEN", value_text=None, low=None, high=None, period="FY27"):
    return {"status": status, "value_text": value_text, "value_num_low": low,
            "value_num_high": high, "applies_to_period": period}


def _slot(status="GIVEN", value_num=None, value_text=None):
    return {"status": status, "value_num": value_num, "value_text": value_text}


def _result_for(results, field_key):
    return next(r for r in results if r["field_key"] == field_key)


def test_no_promise_to_test_when_prior_quarter_never_guided():
    results = gl.promised_vs_delivered({}, {}, "Q1 FY27")
    for r in results:
        assert r["verdict"] == "NO_PROMISE_TO_TEST"
        assert r["delivered"] is None


def test_no_promise_to_test_when_status_is_not_given_or_withdrawn():
    prior = {"guidance_margin": _promise(status="NOT_GIVEN")}
    r = _result_for(gl.promised_vs_delivered(prior, {}, "Q1 FY27"), "guidance_margin")
    assert r["verdict"] == "NO_PROMISE_TO_TEST"


def test_withdrawn_or_changed_status_still_counts_as_a_promise_to_test():
    prior = {"guidance_margin": _promise(status="WITHDRAWN_OR_CHANGED", low=24, high=26)}
    current = {"ebit_margin": _slot(value_num=25.0, value_text="25%")}
    r = _result_for(gl.promised_vs_delivered(prior, current, "Q1 FY27"), "guidance_margin")
    assert r["verdict"] == "IN_BAND"


def test_only_margin_field_is_checked_against_a_sector_slot():
    # guidance_revenue has no comparable spoken actual this desk can assert,
    # so even a GIVEN promise with a plausible-looking current slot stays
    # NOT_COMPARABLE rather than being guessed from an unrelated slot.
    prior = {"guidance_revenue": _promise(low=10, high=12)}
    current = {"ebit_margin": _slot(value_num=11.0)}
    r = _result_for(gl.promised_vs_delivered(prior, current, "Q1 FY27"), "guidance_revenue")
    assert r["verdict"] == "NOT_COMPARABLE"
    assert r["delivered"] is None


def test_not_comparable_when_current_slot_missing_or_not_given():
    prior = {"guidance_margin": _promise(low=24, high=26)}
    r = _result_for(gl.promised_vs_delivered(prior, {}, "Q1 FY27"), "guidance_margin")
    assert r["verdict"] == "NOT_COMPARABLE"

    current = {"ebit_margin": _slot(status="NOT_GIVEN", value_num=None)}
    r = _result_for(gl.promised_vs_delivered(prior, current, "Q1 FY27"), "guidance_margin")
    assert r["verdict"] == "NOT_COMPARABLE"


def test_not_comparable_when_promise_has_no_numeric_band():
    prior = {"guidance_margin": _promise(value_text="healthy margins", low=None, high=None)}
    current = {"ebit_margin": _slot(value_num=25.0, value_text="25%")}
    r = _result_for(gl.promised_vs_delivered(prior, current, "Q1 FY27"), "guidance_margin")
    assert r["verdict"] == "NOT_COMPARABLE"
    assert r["delivered"] == "25%"


def test_in_band_above_below():
    prior = {"guidance_margin": _promise(low=24.0, high=26.0)}

    in_band = _result_for(gl.promised_vs_delivered(
        prior, {"ebit_margin": _slot(value_num=25.0)}, "Q1 FY27"), "guidance_margin")
    assert in_band["verdict"] == "IN_BAND"

    above = _result_for(gl.promised_vs_delivered(
        prior, {"ebit_margin": _slot(value_num=27.0)}, "Q1 FY27"), "guidance_margin")
    assert above["verdict"] == "ABOVE"

    below = _result_for(gl.promised_vs_delivered(
        prior, {"ebit_margin": _slot(value_num=23.0)}, "Q1 FY27"), "guidance_margin")
    assert below["verdict"] == "BELOW"


def test_band_edges_are_inclusive():
    prior = {"guidance_margin": _promise(low=24.0, high=26.0)}
    low_edge = _result_for(gl.promised_vs_delivered(
        prior, {"ebit_margin": _slot(value_num=24.0)}, "Q1 FY27"), "guidance_margin")
    high_edge = _result_for(gl.promised_vs_delivered(
        prior, {"ebit_margin": _slot(value_num=26.0)}, "Q1 FY27"), "guidance_margin")
    assert low_edge["verdict"] == "IN_BAND"
    assert high_edge["verdict"] == "IN_BAND"


def test_delivered_falls_back_to_str_of_value_num_when_no_value_text():
    prior = {"guidance_margin": _promise(low=24.0, high=26.0)}
    current = {"ebit_margin": {"status": "GIVEN", "value_num": 25.0, "value_text": None}}
    r = _result_for(gl.promised_vs_delivered(prior, current, "Q1 FY27"), "guidance_margin")
    assert r["delivered"] == "25.0"


def test_source_is_this_quarters_call_only_when_comparable():
    prior = {"guidance_margin": _promise(low=24.0, high=26.0)}
    comparable = _result_for(gl.promised_vs_delivered(
        prior, {"ebit_margin": _slot(value_num=25.0)}, "Q1 FY27"), "guidance_margin")
    assert comparable["source"] == "this quarter's call"

    not_comparable = _result_for(gl.promised_vs_delivered(prior, {}, "Q1 FY27"), "guidance_margin")
    assert not_comparable["source"] is None


def test_prior_quarter_label_and_promised_text_are_carried_through():
    prior = {"guidance_capex": _promise(value_text="Rs 500cr", period="FY27")}
    r = _result_for(gl.promised_vs_delivered(prior, {}, "Q4 FY26"), "guidance_capex")
    assert r["promised_text"] == "Rs 500cr"
    assert r["period"] == "FY27"
    assert r["prior_quarter_label"] == "Q4 FY26"


def test_returns_all_four_formal_fields_in_order():
    results = gl.promised_vs_delivered({}, {}, "Q1 FY27")
    assert [r["field_key"] for r in results] == [k for k, _ in gl.FORMAL_FIELDS]
