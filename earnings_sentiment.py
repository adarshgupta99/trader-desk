"""Track B of the Week 5-6 earnings-call sentiment tracker: Gemini
structured-output extraction of tone/guidance/risks from a transcript.

WHY TWO CALLS, NOT ONE, AND WHY NO PRE-SPLIT IN PYTHON: management
commentary and analyst Q&A are scored separately per spec ("Q&A tone
divergence from management tone is itself a signal worth storing"), but
nse_transcripts.py's discovery found speaker-label formatting is not
standardised across companies (RELIANCE uses block headers, TCS/
HDFCBANK/HINDUNILVR use inline "Name:" labels with a "Moderator:" role).
A regex pre-split would need per-company tuning and still break on the
next new convention. Instead, both calls receive the SAME full transcript
text, with a role-specific instruction telling Gemini which part to
attend to and which to ignore - the model reads speaker context (job
titles, a "Call Participants" roster most transcripts open with, analyst
firm names) the same way a human would, regardless of the document's
exact formatting.

Verified live 14 Oct 2026: response_schema + response_mime_type=
"application/json" (google-genai 2.27.0) reliably returns a dict matching
the schema, including the guidance enum constraint - confirmed against a
real sample ("raising our full-year guidance" -> {"guidance": "raised"}).
"""

import json

from google import genai
from google.genai import types

MODEL = "gemini-3.5-flash-lite"

#: The spec's exact JSON shape, applied to each track.
TRACK_B_SCHEMA = {
    "type": "object",
    "properties": {
        "tone_score": {
            "type": "number",
            "description": "Overall tone, -2 (very negative) to +2 (very positive).",
        },
        "guidance": {
            "type": "string",
            "enum": ["raised", "maintained", "lowered", "none"],
            "description": "Forward guidance direction stated or implied. "
                           "'none' if guidance wasn't discussed at all.",
        },
        "key_numbers": {
            "type": "array", "items": {"type": "string"},
            "description": "Specific figures called out (revenue growth %, margin, "
                           "order book, etc.), as short strings including their units.",
        },
        "new_risks_vs_prior_quarter": {
            "type": "array", "items": {"type": "string"},
            "description": "Risks or concerns raised that read as NEW versus what a "
                           "prior-quarter call would typically have covered - inferred "
                           "from context, not a literal prior-quarter comparison.",
        },
        "hedging_language_examples": {
            "type": "array", "items": {"type": "string"},
            "description": "Short verbatim phrases showing hedging/uncertainty "
                           "('we believe', 'subject to', 'it's early days').",
        },
    },
    "required": ["tone_score", "guidance", "key_numbers",
                "new_risks_vs_prior_quarter", "hedging_language_examples"],
}

_ROLE_INSTRUCTIONS = {
    "management": (
        "This is a full earnings call transcript. Companies format these "
        "inconsistently - speakers may be labelled as block headers like "
        "'Company Speaker (Name)', or inline as 'Name: text', sometimes with "
        "a 'Moderator:' role for the call host. Use context (job titles, a "
        "participants list near the start of the document, who is asking vs "
        "answering) to identify which portions are MANAGEMENT'S PREPARED "
        "COMMENTARY - the company executives' own remarks about the "
        "quarter's performance and outlook, typically before the Q&A "
        "session begins. Extract the fields below from that portion only. "
        "Ignore the analyst Q&A session and any moderator/operator lines."
    ),
    "qa": (
        "This is a full earnings call transcript. Companies format these "
        "inconsistently - speakers may be labelled as block headers like "
        "'Questioner (Name, Firm)', or inline as 'Name: text', sometimes "
        "with a 'Moderator:' role for the call host. Use context (job "
        "titles, analyst firm names, who is asking vs answering) to "
        "identify the ANALYST Q&A SESSION - the portion where external "
        "analysts ask questions and company management answers them, "
        "typically after prepared remarks. Extract the fields below from "
        "management's ANSWERS during that Q&A session only. Ignore the "
        "prepared commentary that preceded it and any moderator/operator "
        "lines."
    ),
}


def extract_track_b(api_key, transcript_text, role):
    """Gemini structured extraction for one role ('management' or 'qa').

    Returns (parsed_dict, usage) where usage is {prompt_tokens,
    candidates_tokens, total_tokens} for the spec's "log token usage"
    cost guard. Raises on an HTTP/API failure or on a response that
    doesn't parse as valid JSON - callers decide how to log/skip, same
    division of labour as nse_results.py and nse_transcripts.py.
    """
    if role not in _ROLE_INSTRUCTIONS:
        raise ValueError(f"role must be 'management' or 'qa', got {role!r}")

    client = genai.Client(api_key=api_key)
    prompt = f"{_ROLE_INSTRUCTIONS[role]}\n\n---TRANSCRIPT---\n{transcript_text}"
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=TRACK_B_SCHEMA,
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
