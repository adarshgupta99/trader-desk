"""Offline tests for verdict_synthesis: schema shape and the chip-role
filter, with the Gemini client mocked out - no network, no API key needed.
"""

import json
from unittest.mock import MagicMock, patch

import verdict_synthesis as vs


def test_schema_declares_the_three_top_level_fields_as_required():
    assert vs._SCHEMA["required"] == ["headline", "supporting_line", "chips"]


def test_chip_role_enum_matches_the_three_allowed_roles():
    chip_schema = vs._SCHEMA["properties"]["chips"]["items"]
    assert chip_schema["properties"]["role"]["enum"] == ["kept", "raised", "risk"]
    assert vs._CHIP_ROLES == ["kept", "raised", "risk"]


def test_prompt_template_embeds_arbitrary_facts_without_crashing():
    facts = {"tones": {"overall": 1}, "risks": [{"text": "FX", "sized": False}]}
    rendered = vs._PROMPT.format(facts=json.dumps(facts, indent=2, default=str))
    assert "FX" in rendered
    assert "{facts}" not in rendered


def _mock_response(payload, usage=None):
    resp = MagicMock()
    resp.text = json.dumps(payload)
    resp.usage_metadata = usage
    return resp


def test_synthesize_drops_a_chip_with_an_out_of_enum_role():
    payload = {
        "headline": "Margins held steady.",
        "supporting_line": None,
        "chips": [
            {"label": "Margin maintained", "role": "kept"},
            {"label": "Made up chip", "role": "bogus"},
        ],
    }
    with patch("verdict_synthesis.genai.Client") as MockClient:
        MockClient.return_value.models.generate_content.return_value = _mock_response(payload)
        parsed, usage = vs.synthesize("fake-key", {"tones": {}})

    assert [c["role"] for c in parsed["chips"]] == ["kept"]
    assert usage == {"prompt_tokens": None, "candidates_tokens": None, "total_tokens": None}


def test_synthesize_passes_through_usage_metadata_when_present():
    payload = {"headline": "x", "supporting_line": None, "chips": []}
    usage_meta = MagicMock(prompt_token_count=100, candidates_token_count=20,
                           total_token_count=120)
    with patch("verdict_synthesis.genai.Client") as MockClient:
        MockClient.return_value.models.generate_content.return_value = _mock_response(
            payload, usage=usage_meta)
        parsed, usage = vs.synthesize("fake-key", {"tones": {}})

    assert usage == {"prompt_tokens": 100, "candidates_tokens": 20, "total_tokens": 120}


def test_synthesize_calls_the_lite_model():
    payload = {"headline": "x", "supporting_line": None, "chips": []}
    with patch("verdict_synthesis.genai.Client") as MockClient:
        mock_generate = MockClient.return_value.models.generate_content
        mock_generate.return_value = _mock_response(payload)
        vs.synthesize("fake-key", {"tones": {}})

    assert mock_generate.call_args.kwargs["model"] == "gemini-3.5-flash-lite"
    assert mock_generate.call_args.kwargs["model"] == vs.MODEL
