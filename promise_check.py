"""Promise checking for the Guidance tab's "Promises Tested" section (v3.2.0
redesign). Tests each of the PRIOR quarter's stored `commitments` against
what THIS quarter's call actually says.

UNLIKE verdict_synthesis.py, this call IS transcript-grounded - it is not a
facts-only synthesis. A prior commitment's actual outcome has to be located
as real evidence in THIS quarter's transcript (its own wording is rarely a
clean numeric match to the promise, e.g. "cover about half" vs "margin 20.9%,
+10 bps QoQ" - a model has to find and quote the right passage, not just
compare two stored numbers). So this runs the same cite-verification
discipline as extraction_v3's stage 2: every `quote` must be a real substring
of THIS quarter's cleaned transcript text, checked with the same
normalise/locate helpers, and an item whose quote cannot be verified is
dropped rather than kept unverified.

Still grounded in one sense the way verdict_synthesis is: the prior
commitments given as input are themselves already-verified stage-2 output
from a previous run, not re-derived here.
"""

import json

from google import genai
from google.genai import types

from extraction_v3 import locate_quote, normalise_with_map, trim_quote

#: Same tier as the rest of this build (Adarsh, 16 Oct 2026).
MODEL = "gemini-3.5-flash-lite"

_VERDICTS = ["BEAT", "MET", "PARTIAL", "MISSED", "NOT_ADDRESSED"]

_SCHEMA = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string"},
                    "promised_value": {"type": "string"},
                    "delivered_value": {
                        "type": "string", "nullable": True,
                        "description": "The actual figure/outcome for this "
                                       "metric, AS STATED on this call - "
                                       "never inferred or calculated. Null "
                                       "if the call never addresses it.",
                    },
                    "quote": {
                        "type": "string", "nullable": True,
                        "description": "Verbatim evidence from THIS "
                                       "quarter's call, <= 40 words. Null "
                                       "iff delivered_value is null.",
                    },
                    "exchange_ref": {"type": "string", "nullable": True},
                    "verdict": {"type": "string", "enum": _VERDICTS},
                },
                "required": ["metric", "promised_value", "delivered_value",
                             "quote", "exchange_ref", "verdict"],
            },
        },
    },
    "required": ["checks"],
}

_PROMPT = """\
You are checking whether commitments made on a PRIOR earnings call were
delivered, based ONLY on what THIS quarter's call actually says. You were not
on the prior call - you only have its already-recorded commitments below.

PRIOR COMMITMENTS
{commitments}

For EACH prior commitment above, return exactly one check. Read this
quarter's transcript and determine:
- delivered_value: the actual figure or outcome for that SAME metric, AS
  STATED on this call. Never calculate, derive or infer it yourself - if a
  derived figure would be needed (e.g. you would have to compute a
  percentage), treat it as not addressed instead.
- quote + exchange_ref: the verbatim evidence for delivered_value, copied
  exactly from the transcript, <= 40 words, with the exchange ref it came
  from. Both null iff delivered_value is null.
- verdict:
    BEAT           - delivered clearly exceeds what was promised. For a prior
                     commitment whose status was "dodged" (something
                     declined to be quantified), use BEAT if it is now
                     quantified at all.
    MET            - delivered matches or holds the promised target/level.
    PARTIAL        - some progress but short of what was promised.
    MISSED         - delivered falls short of or reverses the promised target.
    NOT_ADDRESSED  - this quarter's call never speaks to this metric again.

Judge only from what is actually said on THIS call. Do not use outside
knowledge of the company. Never invent a delivered_value to make a promise
look kept or broken.

EXCHANGES
{exchange_map}
---TRANSCRIPT---
{transcript}
"""


def _call_llm(api_key, model, prompt, schema):
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model, contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema),
    )
    usage = {"prompt_tokens": None, "candidates_tokens": None, "total_tokens": None}
    if response.usage_metadata is not None:
        usage = {
            "prompt_tokens": response.usage_metadata.prompt_token_count,
            "candidates_tokens": response.usage_metadata.candidates_token_count,
            "total_tokens": response.usage_metadata.total_token_count,
        }
    return json.loads(response.text), usage


def check(api_key, prior_commitments, exchange_map, cleaned_text, model=None):
    """Runs the promise-check call. prior_commitments: list of
    {metric, value, support, status} dicts (as sentiment_store_v3.
    read_commitments returns). exchange_map: the pre-built exchange_map
    string (extraction_v3._exchange_map output) for THIS quarter's
    exchanges. Returns (checks, usage) - checks are NOT yet cite-verified;
    call verify() next."""
    if not prior_commitments:
        return [], {"prompt_tokens": None, "candidates_tokens": None,
                    "total_tokens": None}
    commitments_text = "\n".join(
        f"- {c['metric']}: {c['value']}"
        + (f" (prior context: {c['support']})" if c.get("support") else "")
        + f" [status when made: {c['status']}]"
        for c in prior_commitments
    )
    prompt = _PROMPT.format(
        commitments=commitments_text, exchange_map=exchange_map,
        transcript=cleaned_text,
    )
    parsed, usage = _call_llm(api_key, model or MODEL, prompt, _SCHEMA)
    return parsed.get("checks") or [], usage


def verify(checks, cleaned_text):
    """Cite-verifies every check's quote against THIS quarter's transcript,
    same discipline as extraction_v3.verify_and_locate: trims to the word
    cap, locates it as a real substring, attaches char offsets. A check
    whose quote cannot be located is dropped entirely (list-shaped, same
    convention as risks/guidance_soft/commitments) - unverifiable evidence
    for a promise outcome is not evidence at all. A NOT_ADDRESSED check
    needs no quote and always survives.

    Returns (kept_checks, failures).
    """
    prepared = normalise_with_map(cleaned_text)
    failures = []
    kept = []
    for i, item in enumerate(checks, start=1):
        label = f"promise_check[{i}] ({item.get('metric')})"
        if item.get("verdict") == "NOT_ADDRESSED" or not item.get("quote"):
            if item.get("verdict") != "NOT_ADDRESSED":
                failures.append(f"{label}: no quote for a non-NOT_ADDRESSED verdict")
                continue
            item["quote"] = None
            item["delivered_value"] = None
            item["exchange_ref"] = None
            kept.append(item)
            continue
        quote = trim_quote(item["quote"])
        found = locate_quote(quote, cleaned_text, prepared)
        if found is None:
            failures.append(f"{label}: quote not found verbatim in transcript")
            continue
        item["quote"] = quote
        item["char_start"], item["char_end"] = found
        kept.append(item)
    return kept, failures
