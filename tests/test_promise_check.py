"""Tests for promise_check: the cite-verification side is pure and offline
(same locate/trim helpers extraction_v3's own verify_and_locate uses), and
check()'s call-shaping is tested with the Gemini client mocked out - no
network, no API key, same pattern as test_verdict_synthesis.py.
"""

import json
from unittest.mock import MagicMock, patch

import promise_check as pc

TRANSCRIPT = (
    "Management said exports run-rate reached two hundred forty four "
    "thousand per month this quarter, a new record for the company."
)


def _item(metric="Exports run-rate", quote="exports run-rate reached two "
          "hundred forty four thousand per month this quarter",
          verdict="BEAT", delivered="244k/mo", exchange_ref="P1"):
    return {"metric": metric, "promised_value": "220k/mo",
            "delivered_value": delivered, "quote": quote,
            "exchange_ref": exchange_ref, "verdict": verdict}


def test_verify_keeps_a_check_whose_quote_is_a_real_substring():
    kept, failures = pc.verify([_item()], TRANSCRIPT)
    assert len(kept) == 1
    assert failures == []
    assert kept[0]["char_start"] is not None
    assert kept[0]["char_end"] is not None


def test_verify_drops_a_check_whose_quote_is_not_in_the_transcript():
    bad = _item(quote="this exact sentence never appears anywhere")
    kept, failures = pc.verify([bad], TRANSCRIPT)
    assert kept == []
    assert len(failures) == 1
    assert "not found verbatim" in failures[0]


def test_verify_keeps_not_addressed_with_no_quote_required():
    item = _item(verdict="NOT_ADDRESSED", delivered=None, quote=None,
                 exchange_ref=None)
    kept, failures = pc.verify([item], TRANSCRIPT)
    assert len(kept) == 1
    assert kept[0]["delivered_value"] is None
    assert kept[0]["quote"] is None
    assert failures == []


def test_verify_drops_a_non_not_addressed_check_with_no_quote():
    item = _item(quote=None)
    kept, failures = pc.verify([item], TRANSCRIPT)
    assert kept == []
    assert len(failures) == 1
    assert "no quote" in failures[0]


def test_verify_mixed_batch_keeps_only_the_verifiable_ones():
    good = _item(metric="A")
    bad = _item(metric="B", quote="nonsense not in the text")
    not_addressed = _item(metric="C", verdict="NOT_ADDRESSED", delivered=None,
                          quote=None, exchange_ref=None)
    kept, failures = pc.verify([good, bad, not_addressed], TRANSCRIPT)
    assert {k["metric"] for k in kept} == {"A", "C"}
    assert len(failures) == 1


def _mock_response(payload):
    resp = MagicMock()
    resp.text = json.dumps(payload)
    resp.usage_metadata = None
    return resp


def test_check_returns_empty_with_no_api_call_when_no_prior_commitments():
    with patch("promise_check.genai.Client") as MockClient:
        checks, usage = pc.check("fake-key", [], "P1: prepared", TRANSCRIPT)
    assert checks == []
    MockClient.assert_not_called()


def test_check_passes_prior_commitments_into_the_prompt():
    prior = [{"metric": "Exports run-rate", "value": "220k/mo",
             "support": "was 200k before", "status": "raised"}]
    payload = {"checks": [_item()]}
    with patch("promise_check.genai.Client") as MockClient:
        mock_generate = MockClient.return_value.models.generate_content
        mock_generate.return_value = _mock_response(payload)
        checks, usage = pc.check("fake-key", prior, "P1: prepared", TRANSCRIPT)

    assert len(checks) == 1
    prompt_sent = mock_generate.call_args.kwargs["contents"]
    assert "Exports run-rate" in prompt_sent
    assert "220k/mo" in prompt_sent
    assert "was 200k before" in prompt_sent
    assert mock_generate.call_args.kwargs["model"] == pc.MODEL
