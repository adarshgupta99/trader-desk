"""Offline tests for extraction_v3.py's deterministic parts: line numbering,
quote location, the 40-word cap, and the cite verification that implements
spec 2.3.

No network. The LLM stages are exercised live against the TCS reference set
(spec 8.1), not here - what is pinned here is the plumbing that decides
whether a model's cite is trustworthy, which is the part that must never
silently pass something unverifiable.
"""

import extraction_v3 as ex


# --- line numbering ----------------------------------------------------------

def test_line_numbers_map_to_real_offsets():
    text = "first line\nsecond line\nthird line"
    numbered, offsets = ex.number_lines(text)
    assert numbered.splitlines()[0] == "L0001: first line"
    assert numbered.splitlines()[2] == "L0003: third line"
    # The offset must point at the real start of that line in the ORIGINAL.
    assert text[offsets[1]:].startswith("first line")
    assert text[offsets[2]:].startswith("second line")
    assert text[offsets[3]:].startswith("third line")


def test_line_span_covers_the_right_text():
    text = "alpha\nbravo\ncharlie\ndelta"
    _, offsets = ex.number_lines(text)
    start, end = ex._line_span_to_chars(offsets, 2, 3, len(text))
    assert text[start:end] == "bravo\ncharlie"


def test_line_span_to_end_of_document():
    text = "alpha\nbravo"
    _, offsets = ex.number_lines(text)
    start, end = ex._line_span_to_chars(offsets, 2, 2, len(text))
    assert text[start:end] == "bravo"


# --- quote location (the spec 2.3 substring check) ---------------------------

def test_locates_an_exact_quote_and_offsets_point_at_it():
    text = "Moderator: Welcome.\nCFO: Margins improved by 40 basis points.\n"
    found = ex.locate_quote("Margins improved by 40 basis points.", text)
    assert found is not None
    start, end = found
    assert text[start:end] == "Margins improved by 40 basis points."


def test_locates_a_quote_across_pdf_line_wrapping():
    # PDF text wraps mid-sentence constantly. A quote that is otherwise exact
    # must not fail just because the transcript broke the line - but the
    # offsets must still bracket the real span.
    text = "CFO: We expect demand to\nimprove in the second half of the year."
    found = ex.locate_quote("We expect demand to improve in the second half", text)
    assert found is not None
    start, end = found
    assert text[start:end] == "We expect demand to\nimprove in the second half"


def test_rejects_a_quote_that_is_not_in_the_transcript():
    # The whole point: an invented or paraphrased quote must fail, because a
    # cite that cannot be located is not evidence.
    text = "CFO: Margins improved by 40 basis points."
    assert ex.locate_quote("Margins improved by 90 basis points.", text) is None
    assert ex.locate_quote("We expect a strong second half.", text) is None


def test_paraphrase_fails_even_when_meaning_matches():
    text = "CEO: We are not providing revenue guidance this year."
    assert ex.locate_quote("We will not give revenue guidance this year.", text) is None


def test_empty_quote_is_not_located():
    assert ex.locate_quote("", "some text") is None
    assert ex.locate_quote(None, "some text") is None


def test_normalise_map_round_trips_every_position():
    text = "  alpha   bravo \n\n charlie  "
    norm, index_map = ex.normalise_with_map(text)
    assert norm == "alpha bravo charlie"
    assert len(index_map) == len(norm)
    # Every non-space normalised char must map back to the same character.
    for i, ch in enumerate(norm):
        if ch != " ":
            assert text[index_map[i]] == ch


# --- quote trimming ----------------------------------------------------------

def test_trim_quote_enforces_the_word_cap():
    quote = " ".join(f"word{i}" for i in range(60))
    trimmed = ex.trim_quote(quote)
    assert len(trimmed.split(" ")) == ex.MAX_QUOTE_WORDS


def test_trim_quote_collapses_whitespace_but_keeps_short_quotes_whole():
    assert ex.trim_quote("margins   improved\nsharply") == "margins improved sharply"


# --- cite verification -------------------------------------------------------

def _result(**overrides):
    base = {
        "guidance_formal": {
            k: {"status": "NOT_GIVEN", "value_text": None, "quote": None,
                "exchange_ref": None}
            for k in ("guidance_revenue", "guidance_margin",
                      "guidance_capex", "guidance_orderbook")
        },
        "frame": {
            k: {"status": "NOT_GIVEN", "value_text": None, "quote": None,
                "exchange_ref": None}
            for k, _ in ex._FRAME_FIELDS
        },
        "guidance_soft": [],
        "qa_divergence": {"grade": "N", "note": "", "cites": [], "quote": None},
    }
    base.update(overrides)
    return base


TEXT = "CEO: We expect the margin to be 24 to 26% for FY27.\nCFO: No change."


def test_verified_quote_gets_offsets_attached():
    result = _result()
    result["guidance_formal"]["guidance_margin"] = {
        "status": "GIVEN", "value_text": "24-26% FY27",
        "quote": "We expect the margin to be 24 to 26% for FY27.",
        "exchange_ref": "P1",
    }
    out, failures = ex.verify_and_locate(result, TEXT)
    assert failures == []
    field = out["guidance_formal"]["guidance_margin"]
    assert TEXT[field["char_start"]:field["char_end"]] == field["quote"]


def test_unverifiable_quote_demotes_that_field_only():
    # Spec 2.3 at field granularity: the bad field is demoted to
    # NOT_VERIFIED and its unverifiable quote stripped, while everything
    # else in the same extraction survives. Voiding the whole quarter for
    # one paraphrase is what the live TCS run hit on 16 Oct 2026.
    result = _result()
    result["guidance_formal"]["guidance_margin"] = {
        "status": "GIVEN", "value_text": "30% FY27",
        "quote": "We expect the margin to be 30% for FY27.",
        "exchange_ref": "P1",
    }
    result["guidance_formal"]["guidance_revenue"] = {
        "status": "GIVEN", "value_text": "no change",
        "quote": "No change.", "exchange_ref": "P2",
    }
    out, failures = ex.verify_and_locate(result, TEXT)
    assert len(failures) == 1
    assert "not found verbatim" in failures[0]

    bad = out["guidance_formal"]["guidance_margin"]
    assert bad["status"] == "NOT_VERIFIED"
    assert bad["quote"] is None            # nothing unverifiable is kept
    assert "char_start" not in bad

    good = out["guidance_formal"]["guidance_revenue"]
    assert good["status"] == "GIVEN"       # survived intact
    assert TEXT[good["char_start"]:good["char_end"]] == good["quote"]


def test_nothing_unverified_remains_given():
    # The property QA 8.2 actually asks for: every stored GIVEN value has a
    # quote that passes the substring test.
    result = _result()
    result["frame"]["mgmt_margin_commentary"] = {
        "status": "GIVEN", "value_text": "x",
        "quote": "A sentence that is not in the transcript at all.",
        "exchange_ref": "P1",
    }
    out, _ = ex.verify_and_locate(result, TEXT)
    for group in (out["guidance_formal"], out["frame"]):
        for field in group.values():
            if field.get("status") == "GIVEN":
                assert field.get("quote")
                assert ex.locate_quote(field["quote"], TEXT) is not None


def test_unverifiable_soft_item_is_dropped_not_demoted():
    # A soft-outlook item IS its quote, so there is nothing left to store.
    result = _result(guidance_soft=[
        {"topic": "demand", "direction": "positive",
         "quote": "No change.", "exchange_ref": "P2"},
        {"topic": "margin", "direction": "positive",
         "quote": "Demand will be spectacular.", "exchange_ref": "P2"},
    ])
    out, failures = ex.verify_and_locate(result, TEXT)
    assert len(failures) == 1
    assert len(out["guidance_soft"]) == 1
    assert out["guidance_soft"][0]["topic"] == "demand"


def test_divergence_grade_survives_an_unverifiable_supporting_quote():
    # The grade rests on exchange cites, not on this quote, so losing the
    # quote must not lose the judgement.
    result = _result(qa_divergence={
        "grade": "MILD", "note": "margin framing softened",
        "cites": ["P1", "Q2"], "quote": "Something nobody said."})
    out, failures = ex.verify_and_locate(result, TEXT)
    assert len(failures) == 1
    assert out["qa_divergence"]["grade"] == "MILD"
    assert out["qa_divergence"]["cites"] == ["P1", "Q2"]
    assert out["qa_divergence"]["quote"] is None


def test_given_without_a_quote_is_a_failure():
    # "No invention" (spec section 1): a GIVEN value with no evidence is
    # exactly the thing that must never reach storage.
    result = _result()
    result["frame"]["mgmt_margin_commentary"] = {
        "status": "GIVEN", "value_text": "margins improving",
        "quote": None, "exchange_ref": "P1",
    }
    _, failures = ex.verify_and_locate(result, TEXT)
    assert len(failures) == 1
    assert "no quote" in failures[0]


def test_not_given_without_a_quote_is_fine():
    # NOT_GIVEN is first-class and needs no evidence to be valid.
    _, failures = ex.verify_and_locate(_result(), TEXT)
    assert failures == []


def test_soft_outlook_items_always_need_a_verifiable_quote():
    result = _result(guidance_soft=[
        {"topic": "demand", "direction": "positive",
         "quote": "No change.", "exchange_ref": "P2"},
        {"topic": "margin", "direction": "positive",
         "quote": "Demand will be spectacular.", "exchange_ref": "P2"},
    ])
    _, failures = ex.verify_and_locate(result, TEXT)
    assert len(failures) == 1
    assert "guidance_soft[2]" in failures[0]


def test_sector_slots_are_verified_too():
    result = _result(sector_slots={
        "ebit_margin": {"status": "GIVEN", "value_text": "24-26%",
                        "quote": "We expect the margin to be 24 to 26% for FY27.",
                        "exchange_ref": "P1", "value_num": None,
                        "unit": "%", "period_basis": "FY"},
        "tcv_deal_wins": {"status": "GIVEN", "value_text": "$9.6bn",
                          "quote": "TCV of 9.6 billion dollars.",
                          "exchange_ref": "P1", "value_num": 9.6,
                          "unit": "USD bn", "period_basis": None},
    })
    _, failures = ex.verify_and_locate(result, TEXT)
    assert len(failures) == 1
    assert "sector_slots.tcv_deal_wins" in failures[0]


def test_over_long_quote_is_trimmed_then_verified():
    long_text = " ".join(f"word{i}" for i in range(100))
    result = _result()
    result["frame"]["cash_flow_commentary"] = {
        "status": "GIVEN", "value_text": "x", "quote": long_text,
        "exchange_ref": "Q1",
    }
    out, failures = ex.verify_and_locate(result, long_text)
    assert failures == []
    field = out["frame"]["cash_flow_commentary"]
    assert len(field["quote"].split(" ")) == ex.MAX_QUOTE_WORDS
    assert long_text[field["char_start"]:field["char_end"]] == field["quote"]


# --- schema shape ------------------------------------------------------------

def test_schema_includes_sector_slots_only_when_there_are_slots():
    with_slots = ex._stage2_schema(["nim", "gnpa"])
    assert "sector_slots" in with_slots["properties"]
    assert "sector_slots" in with_slots["required"]

    universal_only = ex._stage2_schema([])
    assert "sector_slots" not in universal_only["properties"]
    assert "sector_slots" not in universal_only["required"]


def test_formal_guidance_statuses_allow_withdrawn_or_changed():
    schema = ex._stage2_schema([])
    field = schema["properties"]["guidance_formal"]["properties"]["guidance_revenue"]
    assert field["properties"]["status"]["enum"] == ex._FORMAL_STATUSES
    assert "WITHDRAWN_OR_CHANGED" in field["properties"]["status"]["enum"]


def test_divergence_is_graded_not_boolean():
    schema = ex._stage2_schema([])
    grade = schema["properties"]["qa_divergence"]["properties"]["grade"]
    assert grade["enum"] == ["N", "MILD", "STRONG"]
