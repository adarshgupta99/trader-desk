"""Verdict synthesis for the Guidance tab's top card (spec 4a).

A SEPARATE, cheap stage-3 LLM call over ALREADY-EXTRACTED structured facts
for one quarter - never the raw transcript. This is the only way to satisfy
both halves of what the Guidance redesign spec asks for: a synthesized 1-2
line headline (which structured fields alone can't produce - that needs
language), while staying inside this build's evidence-grounded discipline
(spec section 1: no invention). Grounding it in already-verified facts rather
than the transcript means the model can only restate what already passed
stage 2's evidence-or-null check; it cannot introduce a claim the transcript
never supported, because it never sees the transcript at all.

Deliberately small: the input is a compact JSON summary (tones, divergence,
guidance status, promised-vs-delivered results, risks, pushback counts), not
15k words of transcript - this keeps it cheap even as a third call per
quarter, and keeps the prompt's only possible source of truth narrow enough
to audit by reading it.
"""

import json

from google import genai
from google.genai import types

#: Same tier as everything else in this build (Adarsh, 16 Oct 2026). The
#: input here is a few hundred tokens of structured facts, not a transcript,
#: so even the weakest tier has everything it needs - there is no "find a
#: number in 15k words" step for a weak model to fail at here.
MODEL = "gemini-3.5-flash-lite"

#: role must be one of these three - the UI colours a chip purely from this,
#: never by re-reading the label text, so a role outside this set would
#: render uncoloured rather than guessed-at.
_CHIP_ROLES = ["kept", "raised", "risk"]

_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {
            "type": "string",
            "description": "1-2 sentences, the single most important "
                           "takeaway from this quarter's call.",
        },
        "supporting_line": {
            "type": "string", "nullable": True,
            "description": "One more sentence of context, or null if the "
                           "headline already says enough.",
        },
        "chips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string",
                             "description": "Very short, e.g. '3 of 4 "
                                            "promises kept', 'Margin raised', "
                                            "'FX risk not sized'."},
                    "role": {"type": "string", "enum": _CHIP_ROLES},
                },
                "required": ["label", "role"],
            },
            "description": "Up to 3 chips: role=kept for promises kept "
                           "(green), role=raised for guidance/targets raised "
                           "(blue), role=risk for a risk not quantified (red).",
        },
    },
    "required": ["headline", "supporting_line", "chips"],
}

_PROMPT = """\
You are writing a one-glance verdict for a trading-desk UI, summarising ONE
quarter's earnings call. You are given only the structured facts already
extracted from that call below - you have NOT seen the transcript and must
not claim anything beyond what these facts state. If the facts are thin
(mostly null/empty), say so plainly rather than padding the headline.

FACTS
{facts}

Write:
- headline: 1-2 sentences, the single most important takeaway. Plain,
  specific, no hedging filler ("overall", "it seems"). Name the actual
  numbers or statuses given in the facts where that is the takeaway.
- supporting_line: one more sentence of context, or null if the headline
  already covers it.
- chips: up to 3, each a very short label plus a role (kept / raised / risk).
  Only create a chip the facts actually support - e.g. a "kept" chip needs
  promises_tested to show IN_BAND results, a "raised" chip needs a
  WITHDRAWN_OR_CHANGED or an upward guidance move in the facts, a "risk" chip
  needs a risk in the facts with sized=false. Do not invent a chip to reach
  three.
"""


def synthesize(api_key, facts):
    """facts: a JSON-serialisable dict of already-extracted, already-verified
    data for one quarter (see jobs/refresh_extraction_v3.py's
    _build_verdict_facts). Returns (verdict_dict, usage)."""
    client = genai.Client(api_key=api_key)
    prompt = _PROMPT.format(facts=json.dumps(facts, indent=2, default=str))
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_SCHEMA,
        ),
    )
    parsed = json.loads(response.text)
    parsed["chips"] = [c for c in parsed.get("chips") or [] if c.get("role") in _CHIP_ROLES]

    usage = {"prompt_tokens": None, "candidates_tokens": None, "total_tokens": None}
    if response.usage_metadata is not None:
        usage = {
            "prompt_tokens": response.usage_metadata.prompt_token_count,
            "candidates_tokens": response.usage_metadata.candidates_token_count,
            "total_tokens": response.usage_metadata.total_token_count,
        }
    return parsed, usage
