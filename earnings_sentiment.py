"""Track B of the Week 5-6 earnings-call sentiment tracker: Gemini
structured-output extraction of trader-relevant signals from a
transcript (see Adarsh's 14 Oct 2026 spec in the chat history, not
CLAUDE.md - the schema below is its exact shape).

ONE CALL PER TRANSCRIPT, NOT TWO. The earlier version ran two calls
(management, then Q&A) with a role-specific instruction telling Gemini
which half of the transcript to attend to. This version does the
prepared-remarks/Q&A split INSIDE a single call instead, because the
new schema needs both halves read together to answer its own
cross-cutting fields - qa_vs_prepared_divergence and
analyst_tone.sharpest_challenge.management_answered both require
comparing what management said earlier in the SAME call against how
Q&A went, which a role-scoped call literally cannot see.

EVIDENCE-OR-NULL, ENFORCED BY THE SCHEMA: every judgment field is
paired with an "evidence" string that must be a verbatim quote, or
null if the transcript doesn't support a quote - per Adarsh's explicit
rule, nothing is invented to fill a gap. Every "evidence"/nullable
field below is marked nullable in the schema for exactly that reason.

Verified live 14 Oct 2026: response_schema + response_mime_type=
"application/json" (google-genai 2.27.0) reliably returns a dict
matching the schema, including nested objects/arrays and nullable
leaf fields.
"""

import json

from google import genai
from google.genai import types

MODEL = "gemini-3.5-flash-lite"

_EVIDENCE = {
    "type": "string",
    "nullable": True,
    "description": "An exact quoted line from the transcript supporting this "
                   "field, or null if the transcript doesn't cover it.",
}

CALL_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "guidance": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["raised", "maintained", "lowered", "withheld", "not_given"],
                },
                "metrics_touched": {"type": "array", "items": {"type": "string"}},
                "delta_vs_prior_language": {"type": "string", "nullable": True,
                    "description": "One sentence on how guidance language shifted, "
                                   "judged only from language within this transcript."},
                "evidence": _EVIDENCE,
            },
            "required": ["direction", "metrics_touched", "delta_vs_prior_language", "evidence"],
        },
        "margin_commentary": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["improving", "stable", "under_pressure", "unclear"],
                },
                "drivers_named": {"type": "array", "items": {"type": "string"}},
                "evidence": _EVIDENCE,
            },
            "required": ["direction", "drivers_named", "evidence"],
        },
        "demand_outlook": {
            "type": "object",
            "properties": {
                "tone": {
                    "type": "string",
                    "enum": ["confident", "neutral", "cautious", "deteriorating"],
                },
                "segments_or_geographies_named": {"type": "array", "items": {"type": "string"}},
                "evidence": _EVIDENCE,
            },
            "required": ["tone", "segments_or_geographies_named", "evidence"],
        },
        "hedging": {
            "type": "object",
            "properties": {
                "phrases": {"type": "array", "items": {"type": "string"},
                    "description": "Exact uncertainty phrases, e.g. 'watching the situation'."},
                "count": {"type": "integer"},
            },
            "required": ["phrases", "count"],
        },
        "capital_allocation": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"},
                    "description": "Buyback/dividend/capex/debt changes mentioned."},
                "evidence": _EVIDENCE,
            },
            "required": ["items", "evidence"],
        },
        "one_offs": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"},
                    "description": "Anything management flags as non-recurring."},
                "evidence": _EVIDENCE,
            },
            "required": ["items", "evidence"],
        },
        "management_confidence": {
            "type": "object",
            "properties": {
                "score": {"type": "number", "description": "-2 to +2."},
                "rationale": {"type": "string"},
                "evidence": _EVIDENCE,
            },
            "required": ["score", "rationale", "evidence"],
        },
        "analyst_tone": {
            "type": "object",
            "properties": {
                "overall": {"type": "string", "enum": ["skeptical", "neutral", "constructive"]},
                "sharpest_challenge": {
                    "type": "object",
                    "properties": {
                        "question_summary": {"type": "string", "nullable": True},
                        "management_answered": {
                            "type": "string",
                            "enum": ["yes", "partially", "dodged"],
                            "nullable": True,
                        },
                        "evidence": _EVIDENCE,
                    },
                    "required": ["question_summary", "management_answered", "evidence"],
                },
            },
            "required": ["overall", "sharpest_challenge"],
        },
        "evasiveness": {
            "type": "object",
            "properties": {
                "dodged_questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question_summary": {"type": "string"},
                            "what_was_not_answered": {"type": "string"},
                        },
                        "required": ["question_summary", "what_was_not_answered"],
                    },
                },
                "count": {"type": "integer"},
            },
            "required": ["dodged_questions", "count"],
        },
        "qa_vs_prepared_divergence": {
            "type": "object",
            "properties": {
                "flag": {"type": "boolean"},
                "note": {"type": "string", "nullable": True,
                    "description": "Where Q&A tone or facts contradict prepared remarks."},
                "evidence": _EVIDENCE,
            },
            "required": ["flag", "note", "evidence"],
        },
        "overall_tone": {
            "type": "object",
            "properties": {
                "score": {"type": "number", "description": "-2 to +2."},
                "one_paragraph_justification": {"type": "string"},
                "top_3_quotes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["score", "one_paragraph_justification", "top_3_quotes"],
        },
    },
    "required": [
        "guidance", "margin_commentary", "demand_outlook", "hedging",
        "capital_allocation", "one_offs", "management_confidence",
        "analyst_tone", "evasiveness", "qa_vs_prepared_divergence", "overall_tone",
    ],
}

_INSTRUCTIONS = """\
TASK: Extract trader-relevant signals from an earnings call transcript. \
Stock-agnostic - works for any company, any quarter. Output one JSON object \
matching the given schema. Never invent content; everything traces to a \
quoted line.

PROCESS
1. Split the transcript into: (a) prepared remarks, (b) Q&A. Tag each \
speaker as management or analyst. Ignore operator boilerplate.
2. Run the extraction on each section separately, then produce the overall \
call fields (management_confidence, analyst_tone, evasiveness, \
qa_vs_prepared_divergence, overall_tone) by reading both sections together.

RULES
- Evidence quotes must be verbatim from the transcript. If you can't quote \
it, the field is null.
- Numbers: capture them exactly as stated (units included). No conversion, \
no rounding.
- Judge language, not your outside knowledge of the company or sector. No \
facts from beyond the transcript.
- "not_given"/null is a valid and useful answer. Mark it; don't fill gaps.
- Compare language WITHIN this transcript only (e.g. guidance section vs \
Q&A) - you have no prior-quarter transcript unless one is provided.
"""


def extract_call_analysis(api_key, transcript_text):
    """Gemini structured extraction over a full transcript in one call.

    Returns (parsed_dict, usage) where usage is {prompt_tokens,
    candidates_tokens, total_tokens} for the spec's "log token usage"
    cost guard. Raises on an HTTP/API failure or on a response that
    doesn't parse as valid JSON - callers decide how to log/skip, same
    division of labour as nse_results.py and nse_transcripts.py.
    """
    client = genai.Client(api_key=api_key)
    prompt = f"{_INSTRUCTIONS}\n\n---TRANSCRIPT---\n{transcript_text}"
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CALL_ANALYSIS_SCHEMA,
        ),
    )
    parsed = json.loads(response.text)

    usage = {"prompt_tokens": None, "candidates_tokens": None, "total_tokens": None}
    if response.usage_metadata is not None:
        usage = {
            "prompt_tokens": response.usage_metadata.prompt_token_count,
            "candidates_tokens": response.usage_metadata.candidates_token_count,
            "total_tokens": response.usage_metadata.total_token_count,
        }
    return parsed, usage
