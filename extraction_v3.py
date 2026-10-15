"""Two-stage LLM extraction over an earnings-call transcript (spec section 2,
with the frame block from 3.2 and the sector slots from 3.3/3.6 added to
stage 2).

    stage 1  structural segmentation -> numbered exchanges
    stage 2  extraction over those exchanges, every judgement citing them
    verify   every GIVEN quote must be an exact substring of the cleaned text

LLM-ONLY EXTRACTION (spec section 1). No regex or keyword layer decides any
value. The deterministic code here does three things only, all of which the
spec explicitly allows as plumbing: it numbers lines before the call, it
locates a returned quote inside the text afterwards, and it checks that the
quote is really there.

WHY OFFSETS ARE COMPUTED, NOT ASKED FOR. Spec 2.2 and 2.3 want character
offsets on every exchange and every cite. Asking a model for character offsets
into a 50,000-character document does not work - models cannot count
characters, and a confidently wrong offset is worse than none because it looks
verified. So the mechanism is inverted while the requirement is kept:

  - stage 1 reads a line-numbered transcript and returns LINE ranges, which it
    can see rather than count; lines map to character offsets arithmetically.
  - stage 2 returns the verbatim quote text; the quote is then located in the
    cleaned text to get its offsets.

The spec's actual requirement - a cite that provably points at real text -
comes out stronger this way, because locating the quote IS the substring check
from 2.3. A quote that cannot be located fails the row, which is exactly what
"a quote that is not a substring fails the row" asks for.

NO EXAMPLE STOCK IS BAKED INTO THE PROMPTS (spec 4.4). The prompts never
mention TCS or any other company, so the TCS reference set stays an honest
test rather than something the prompt was fitted to.
"""

import json
import os
import re

from google import genai
from google.genai import types

import sector_cards

#: Bumped whenever a prompt, schema or rubric changes in a way that could
#: change an extracted value. Part of the idempotency key (spec 7.4): a new
#: version re-extracts, an unchanged one is skipped.
EXTRACTOR_VERSION = "v3.0.0"

#: Model settings live in the environment, not in app logic (CLAUDE.md repo
#: convention). Stage 1 is mechanical segmentation; stage 2 carries every
#: judgement, so the spec warns against changing it without re-running the
#: TCS reference check.
#: Stage 1 is mechanical segmentation and flash-lite does it correctly
#: (verified: 11/11 exchanges with the right analyst names and firms on the
#: TCS Q2 FY27 reference call). Stage 2 carries every judgement and flash-lite
#: measurably cannot do the evasiveness rubric - on that same call it labelled
#: all 11 questions "open-ended, answered", finding 0 evasive against the
#: reference's 8, while non-lite flash found 5 of those 8 with no false
#: positives. Hence the split. Spec 7.7 anticipates exactly this ("a smaller
#: model for stage 1 only") and warns against changing the stage 2 model
#: without re-running the TCS reference check.
STAGE1_MODEL = os.environ.get("SENTIMENT_STAGE1_MODEL", "gemini-3.5-flash-lite")

#: Stage 2 is a CHAIN, not one model. Pinning it to a single model means one
#: model's capacity decides whether a 213 x 6 backfill completes, which is
#: exactly what happened on 16 Oct 2026: gemini-3.5-flash hit 429
#: RESOURCE_EXHAUSTED mid-run while 3.7-flash was 503 and only 3.8-flash and
#: flash-lite were answering. Each entry is tried in order; a transient
#: failure moves to the next rather than failing the quarter.
#:
#: Order is quality-first. flash-lite is LAST and is a genuine degradation,
#: not an equal option - it cannot do the evasiveness rubric (measured: 0
#: evasive found against the reference's 8, labelling all 11 exchanges
#: "open-ended, answered"). It is in the chain so a backfill completes with a
#: labelled-weaker row rather than a hole, and every row records the model
#: that produced it so the difference is visible in the data.
#: Adarsh's call, 16 Oct 2026: flash-lite for everything. It is the only tier
#: that answered reliably all session (the others returned 429/503 in bursts
#: that cost ~6 minutes of backoff per quarter), and once the prompt forced a
#: per-exchange verdict its evasiveness output stopped being the outlier it
#: first appeared to be. The heavier models stay in the chain behind it so a
#: lite outage still completes rather than halting a backfill.
STAGE2_MODEL_CHAIN = [
    m.strip() for m in os.environ.get(
        "SENTIMENT_STAGE2_MODELS",
        "gemini-3.5-flash-lite,gemini-3.8-flash,gemini-3.5-flash,gemini-3.7-flash",
    ).split(",") if m.strip()
]
#: Kept for callers and tests that want to force one model.
STAGE2_MODEL = os.environ.get("SENTIMENT_STAGE2_MODEL") or STAGE2_MODEL_CHAIN[0]

#: Spec 2.3: a cite quote is "up to ~40 words". Enforced on the way in so an
#: over-long quote is trimmed to a checkable span rather than rejected.
MAX_QUOTE_WORDS = 40

_STATUSES = ["GIVEN", "NOT_GIVEN", "NOT_VERIFIED", "NOT_APPLICABLE"]
_FORMAL_STATUSES = ["GIVEN", "NOT_GIVEN", "WITHDRAWN_OR_CHANGED", "NOT_VERIFIED"]
_FRAME_FIELDS = [
    ("mgmt_margin_commentary",
     "What management says about margin direction and its drivers."),
    ("one_offs_disclosed",
     "Exceptional items, tax-rate quirks and other-income items management "
     "names, each with the amount if one was stated."),
    ("cash_flow_commentary",
     "Working-capital or operating-cash-flow remarks, if any."),
    ("riders_announced",
     "Dividend, buyback, fundraise, acquisition or management change "
     "mentioned on the call."),
]


class ExtractionFailed(RuntimeError):
    """Stage output did not validate, or a cite failed the substring check.
    The caller writes an EXTRACTION_FAILED row with the reason rather than a
    partially valid one (spec 2.2: never write a partially valid row
    silently)."""


# ── line numbering (plumbing) ────────────────────────────────────────────────

def number_lines(cleaned_text):
    """The transcript with a stable 'L0001: ' prefix per line, plus the map
    from line number to that line's character offset in the ORIGINAL cleaned
    text. Stage 1 cites lines it can see; we convert to offsets here."""
    offsets, pos = {}, 0
    numbered = []
    for i, line in enumerate(cleaned_text.split("\n"), start=1):
        offsets[i] = pos
        numbered.append(f"L{i:04d}: {line}")
        pos += len(line) + 1          # +1 for the newline split consumed
    return "\n".join(numbered), offsets


def _line_span_to_chars(line_offsets, start_line, end_line, text_len):
    start = line_offsets.get(start_line, 0)
    nxt = line_offsets.get(end_line + 1)
    return start, (nxt - 1 if nxt is not None else text_len)


# ── stage 1: structural segmentation ─────────────────────────────────────────

_STAGE1_SCHEMA = {
    "type": "object",
    "properties": {
        "prepared": {
            "type": "array",
            "description": "Blocks of management prepared remarks, in order.",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["speaker", "start_line", "end_line"],
            },
        },
        "qa": {
            "type": "array",
            "description": "Q&A exchanges in order: one analyst question plus "
                           "the management answer to it.",
            "items": {
                "type": "object",
                "properties": {
                    "analyst_name": {"type": "string", "nullable": True},
                    "analyst_firm": {"type": "string", "nullable": True},
                    "management_speakers": {
                        "type": "array", "items": {"type": "string"}},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["analyst_name", "analyst_firm",
                             "management_speakers", "start_line", "end_line"],
            },
        },
    },
    "required": ["prepared", "qa"],
}

_STAGE1_PROMPT = """\
You are segmenting an earnings-call transcript. The transcript below has a
line number prefix on every line, like "L0042: ".

Split it into two parts:

1. PREPARED REMARKS - management's scripted opening commentary, before the
   question-and-answer session begins. Return one entry per distinct
   management speaker block, in order.
2. Q&A EXCHANGES - after the Q&A session opens, one entry per exchange. An
   exchange is a single analyst's question (or group of questions asked
   together) plus the management answer that follows it. Return them in order.

For each entry give the first and last line number it spans, using the line
numbers printed in the transcript. Do not guess line numbers: read them.

Ignore operator and moderator boilerplate (call instructions, "please press
star one", hand-overs) - it belongs to no exchange. Do not include the
moderator as a management speaker. If an analyst's name or firm is not stated,
return null for it rather than inventing one.

---TRANSCRIPT---
{transcript}
"""


def _call_llm(api_key, model, prompt, schema):
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    usage = {"prompt_tokens": None, "candidates_tokens": None, "total_tokens": None}
    if response.usage_metadata is not None:
        usage = {
            "prompt_tokens": response.usage_metadata.prompt_token_count,
            "candidates_tokens": response.usage_metadata.candidates_token_count,
            "total_tokens": response.usage_metadata.total_token_count,
        }
    try:
        return json.loads(response.text), usage
    except (json.JSONDecodeError, TypeError) as e:
        raise ExtractionFailed(f"model returned non-JSON: {e}") from e


def segment(api_key, cleaned_text):
    """Stage 1. Returns (exchanges, usage) where each exchange is a dict with
    exchange_ref ('P1'/'Q1'), kind, seq, speakers and character offsets."""
    numbered, line_offsets = number_lines(cleaned_text)
    parsed, usage = _call_llm(
        api_key, STAGE1_MODEL,
        _STAGE1_PROMPT.format(transcript=numbered), _STAGE1_SCHEMA,
    )

    exchanges = []
    for i, block in enumerate(parsed.get("prepared", []), start=1):
        start, end = _line_span_to_chars(
            line_offsets, block["start_line"], block["end_line"], len(cleaned_text))
        exchanges.append({
            "exchange_ref": f"P{i}", "kind": "prepared", "seq": i,
            "analyst_name": None, "analyst_firm": None,
            "management_speakers": [block.get("speaker")] if block.get("speaker") else [],
            "char_start": start, "char_end": end,
        })
    for i, block in enumerate(parsed.get("qa", []), start=1):
        start, end = _line_span_to_chars(
            line_offsets, block["start_line"], block["end_line"], len(cleaned_text))
        exchanges.append({
            "exchange_ref": f"Q{i}", "kind": "qa", "seq": i,
            "analyst_name": block.get("analyst_name"),
            "analyst_firm": block.get("analyst_firm"),
            "management_speakers": block.get("management_speakers") or [],
            "char_start": start, "char_end": end,
        })

    if not exchanges:
        raise ExtractionFailed("stage 1 returned no prepared remarks and no Q&A")
    return exchanges, usage


# ── stage 2: extraction ──────────────────────────────────────────────────────

def _cited(description):
    """A scored judgement: -2..+2 plus the exchanges that justify it."""
    return {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "description": description},
            "cites": {"type": "array", "items": {"type": "string"},
                      "description": "Exchange refs, e.g. ['P1','Q3']."},
        },
        "required": ["score", "cites"],
    }


def _evidenced(extra_properties=None, required=None):
    """A field that must carry a verbatim quote when GIVEN."""
    props = {
        "status": {"type": "string", "enum": _STATUSES},
        "value_text": {"type": "string", "nullable": True},
        "quote": {"type": "string", "nullable": True,
                  "description": "Verbatim from the transcript, <= 40 words. "
                                 "Null unless status is GIVEN."},
        "exchange_ref": {"type": "string", "nullable": True},
    }
    props.update(extra_properties or {})
    return {
        "type": "object",
        "properties": props,
        "required": ["status", "value_text", "quote", "exchange_ref"] + (required or []),
    }


def _stage2_schema(slot_keys):
    formal = {
        key: _evidenced(
            {
                "value_num_low": {"type": "number", "nullable": True},
                "value_num_high": {"type": "number", "nullable": True},
                "unit": {"type": "string", "nullable": True},
                "applies_to_period": {"type": "string", "nullable": True},
                "prior_value_text": {"type": "string", "nullable": True},
            },
            ["value_num_low", "value_num_high", "unit",
             "applies_to_period", "prior_value_text"],
        )
        for key in ("guidance_revenue", "guidance_margin",
                    "guidance_capex", "guidance_orderbook")
    }
    for field in formal.values():
        field["properties"]["status"] = {"type": "string", "enum": _FORMAL_STATUSES}

    schema = {
        "type": "object",
        "properties": {
            "mgmt_confidence": _cited(
                "Management's confidence in its own prepared remarks, -2 to +2."),
            "overall_tone": _cited("Tone of the whole call, -2 to +2."),
            "qa_tone": _cited("Tone of the Q&A session, -2 to +2."),
            "analyst_tone": _cited(
                "Tone of the analysts' questioning, -2 to +2: negative is "
                "skeptical/pressing, positive is constructive."),
            "qa_divergence": {
                "type": "object",
                "properties": {
                    "grade": {"type": "string", "enum": ["N", "MILD", "STRONG"]},
                    "note": {"type": "string",
                             "description": "One line naming what diverged."},
                    "cites": {"type": "array", "items": {"type": "string"}},
                    "quote": {"type": "string", "nullable": True},
                },
                "required": ["grade", "note", "cites", "quote"],
            },
            # Per-exchange verdicts rather than a bare list of evasive refs.
            # A weak model asked for "the evasive ones" tends to return an
            # empty array without having considered each exchange; made to
            # judge every Q&A exchange in turn it actually does the work, and
            # the per-exchange record is also what the strict rubric needs to
            # be auditable (spec 2.2 wants the list, not just a count).
            "qa_assessments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "exchange_ref": {"type": "string"},
                        "specific_ask": {
                            "type": "string",
                            "description": "The precise thing asked for: a "
                                           "number, a date, a yes/no, or "
                                           "'none - open-ended' if the "
                                           "question asked for no specific.",
                        },
                        "answered": {
                            "type": "string",
                            "enum": ["yes", "declined_plainly", "partial", "no"],
                            "description": "yes = the specific ask was given. "
                                           "declined_plainly = refused openly, "
                                           "e.g. 'we do not guide on that'. "
                                           "partial = some of it. no = not "
                                           "addressed.",
                        },
                        "redirects_or_generalises": {"type": "boolean"},
                        "evasive": {"type": "boolean"},
                    },
                    "required": ["exchange_ref", "specific_ask", "answered",
                                 "redirects_or_generalises", "evasive"],
                },
            },
            "guidance_formal": {
                "type": "object", "properties": formal, "required": list(formal),
            },
            "guidance_soft": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "direction": {"type": "string",
                                      "enum": ["positive", "neutral",
                                               "negative", "mixed"]},
                        "quote": {"type": "string"},
                        "exchange_ref": {"type": "string", "nullable": True},
                    },
                    "required": ["topic", "direction", "quote", "exchange_ref"],
                },
            },
            "frame": {
                "type": "object",
                "properties": {k: _evidenced() for k, _ in _FRAME_FIELDS},
                "required": [k for k, _ in _FRAME_FIELDS],
            },
        },
        "required": ["mgmt_confidence", "overall_tone", "qa_tone", "analyst_tone",
                     "qa_divergence", "qa_assessments", "guidance_formal",
                     "guidance_soft", "frame"],
    }

    if slot_keys:
        schema["properties"]["sector_slots"] = {
            "type": "object",
            "properties": {
                key: _evidenced(
                    {
                        "value_num": {"type": "number", "nullable": True},
                        "unit": {"type": "string", "nullable": True},
                        "period_basis": {"type": "string", "nullable": True,
                                         "description": "QoQ / YoY / FY / LTM, "
                                                        "only as stated."},
                        # Adarsh's call, 16 Oct 2026, overriding spec 3.6's
                        # "management actually said" wording: an analyst
                        # stating a figure uncontested is still that figure
                        # being said on the call, and management correcting
                        # it is the stronger signal. Storing WHO said it keeps
                        # the distinction visible instead of discarding the
                        # value or silently blurring the two.
                        "speaker_role": {
                            "type": "string",
                            "enum": ["management", "analyst"],
                            "nullable": True,
                        },
                    },
                    ["value_num", "unit", "period_basis", "speaker_role"],
                )
                for key in slot_keys
            },
            "required": list(slot_keys),
        }
        schema["required"].append("sector_slots")
    return schema


_STAGE2_PROMPT = """\
You are extracting structured facts from an earnings-call transcript that has
already been segmented into numbered exchanges.

EXCHANGES
{exchange_map}

RULES - these override any instinct to be helpful:
- Every judgement cites the exchange refs above that justify it.
- A value is either stated by a speaker on this call, with a verbatim quote,
  or its status is NOT_GIVEN. Never infer a number nobody said. Never fill a
  value from your own knowledge of the company.
- Quotes must be copied EXACTLY from the transcript, at most 40 words. Do not
  paraphrase, tidy punctuation, or join separated sentences.
- NOT_GIVEN is a first-class answer and is expected to be common. A company
  that says it does not guide is NOT_GIVEN, with that statement as the quote.

TONES - integer -2 to +2. Calibrate against these anchors, not against how
pleasant the call sounds:
  +2  exceptional: management points to clear outperformance and the evidence
      on the call supports it
  +1  clearly better than steady: a specific, evidenced improvement
   0  steady / in line: an ordinary quarter with no clear directional signal.
      THIS IS THE DEFAULT. Management sounding assured, thanking the team and
      expressing confidence in the long term is the baseline on every
      earnings call, not evidence of a positive quarter. Do not score above 0
      for confident delivery alone - something concrete must be better.
  -1  clearly cautious or weak: specific softness, deferrals, pressure
  -2  severe: material deterioration management cannot explain away

- mgmt_confidence: how confident management is about its OWN business, judged
  on the substance offered, not the warmth of the delivery.
- overall_tone: the call as a whole.
- qa_tone: the Q&A session specifically - how the exchange went, including
  how well pressed questions were handled.
- analyst_tone: how the ANALYSTS came across - negative means skeptical or
  pressing, positive means constructive. This is about the questioners, not
  the answers. Repeated probing on the same soft spot is skeptical.

EVASIVE QUESTIONS - judge EVERY Q&A exchange listed above, one entry each in
qa_assessments, in order. Do not skip any and do not return a short list.
For each one:
  1. specific_ask: what precisely was asked for - a number, a date, a yes/no,
     a named figure? If the question was open-ended, say "none - open-ended".
  2. answered: was that specific thing given?
       yes               - the specific ask was provided
       declined_plainly  - openly refused, e.g. "we do not guide on that"
       partial           - some of it, not all
       no                - not addressed at all
  3. redirects_or_generalises: did the answer move to a different topic or
     retreat into generalities instead of answering?
  4. evasive: TRUE only when answered is "partial" or "no" AND
     redirects_or_generalises is true. A brief answer is not evasive if it
     answered. A plain refusal is NOT evasive - declining openly is an
     answer. An open-ended question with a substantive reply is not evasive.

Q&A DIVERGENCE - graded. Work through it deliberately: take the main claims
the prepared remarks made (on demand, margin, growth, guidance posture) and
check how each one survives the Q&A when analysts press on it.
- N: the claims survive the Q&A intact and consistent.
- MILD: a claim is qualified, hedged, narrowed or quietly reframed under
  questioning without being abandoned - for example an upbeat prepared
  framing that becomes an acknowledged uphill task when pressed, or a target
  mentioned in prepared remarks that is not reaffirmed when asked about.
  MILD is common and is the right answer whenever a claim is softened at all.
- STRONG: an answer contradicts the prepared remarks outright.
Judge prepared remarks against Q&A. A contradiction between two Q&A answers is
not this field. Name what diverged in `note`, citing both sides.

GUIDANCE - two parts, kept strictly separate:
- guidance_formal: an explicit forward TARGET for a defined period - a number,
  a band, or an explicit direction ("FY27 margin of 24 to 26%", "double-digit
  revenue growth for FY27"). Fill value_num_low/high only when a number was
  actually spoken. Set WITHDRAWN_OR_CHANGED, with prior_value_text, only if
  management says on THIS call that earlier guidance changed or was withdrawn.
- guidance_soft: qualitative forward statements that are NOT targets ("demand
  is stabilising", "cautiously optimistic on the second half"). These must
  NEVER be promoted into a formal field. A company can be formal NOT_GIVEN
  with a rich soft outlook; that is a normal result, not a gap.

{slot_instructions}
---TRANSCRIPT---
{transcript}
"""

_SLOT_INSTRUCTIONS = """\
SECTOR METRICS - fill each slot below from what was actually SAID ON THIS
CALL. Give the number as spoken with its unit, and period_basis (QoQ / YoY /
FY / LTM) only if stated. If the call is silent on a slot, return NOT_GIVEN;
if the slot cannot apply to this company at all, NOT_APPLICABLE. A figure
that appears only in a press release or presentation, and was not said on the
call, is NOT_GIVEN here.

WHO SAID IT. A figure counts whether management or an analyst said it - an
analyst quoting a number back ("you added 10,000 people this quarter") is
still that number being stated on the call. Record who said it in
speaker_role:
  - "management" when a company speaker gave the figure.
  - "analyst" when only an analyst stated it and management did not dispute
    or restate it.
If management explicitly corrects, disputes or restates an analyst's figure,
use MANAGEMENT'S version and set speaker_role to "management" - management
addressing the number directly always takes precedence over the analyst's
framing of it.

{slot_list}
"""


def _exchange_map(exchanges):
    lines = []
    for ex in exchanges:
        if ex["kind"] == "prepared":
            who = ", ".join(ex["management_speakers"]) or "management"
            lines.append(f"{ex['exchange_ref']}: prepared remarks - {who}")
        else:
            who = ex.get("analyst_name") or "unnamed analyst"
            firm = f" ({ex['analyst_firm']})" if ex.get("analyst_firm") else ""
            lines.append(f"{ex['exchange_ref']}: Q&A - {who}{firm}")
    return "\n".join(lines)


def derive_evasive(parsed):
    """evasive_exchanges, applied consistently from the model's own
    per-exchange judgements.

    The spec's rubric is mechanical once the components are judged: evasive
    iff the specific ask went unanswered (partial/no) AND the answer
    redirected or generalised. Models apply their own rubric unevenly across
    a dozen exchanges, so the components are the model's call and the
    conclusion is the rule's - which is not a keyword layer deciding a value
    (spec section 1), it is the stated rubric enforced uniformly. The model's
    own `evasive` flag is kept on each assessment for audit, so a divergence
    between the two is visible rather than lost.
    """
    refs = []
    for item in parsed.get("qa_assessments") or []:
        by_rubric = (item.get("answered") in ("partial", "no")
                     and bool(item.get("redirects_or_generalises")))
        item["evasive_by_rubric"] = by_rubric
        if by_rubric:
            refs.append(item.get("exchange_ref"))
    parsed["evasive_exchanges"] = [r for r in refs if r]
    return parsed


def _is_transient_model_error(exc):
    """A capacity or quota problem, where another model may well answer. A
    schema or content failure is NOT transient and must not silently walk
    down the chain to a weaker model."""
    text = str(exc).lower()
    return any(s in text for s in (
        "429", "resource_exhausted", "rate limit", "too many requests",
        "503", "unavailable", "high demand", "500", "internal error",
        "timeout", "deadline", "overloaded",
    ))


def extract(api_key, cleaned_text, exchanges, symbol, models=None):
    """Stage 2. Returns (parsed, usage). Slot list comes from the symbol's
    sector card; a universal-only symbol gets no sector_slots key at all.

    Walks STAGE2_MODEL_CHAIN until one answers. usage carries the model that
    actually produced the result, which the caller stores on the row - a
    quarter extracted by the last-resort model is measurably weaker on
    evasiveness and must be identifiable as such afterwards.
    """
    slots = sector_cards.slots_for_symbol(symbol)
    slot_keys = [s["key"] for s in slots]
    slot_instructions = ""
    if slots:
        slot_list = "\n".join(f"- {s['key']}: {s['capture']}" for s in slots)
        slot_instructions = _SLOT_INSTRUCTIONS.format(slot_list=slot_list)

    prompt = _STAGE2_PROMPT.format(
        exchange_map=_exchange_map(exchanges),
        slot_instructions=slot_instructions,
        transcript=cleaned_text,
    )
    schema = _stage2_schema(slot_keys)
    chain = models or STAGE2_MODEL_CHAIN
    last_error = None
    for model in chain:
        try:
            parsed, usage = _call_llm(api_key, model, prompt, schema)
        except Exception as e:
            if not _is_transient_model_error(e):
                raise
            last_error = e
            continue
        usage["model"] = model
        usage["model_was_fallback"] = model != chain[0]
        return derive_evasive(parsed), usage
    raise ExtractionFailed(
        f"every stage-2 model was unavailable ({', '.join(chain)}): {last_error}"
    ) from last_error


# ── cite verification (spec 2.3) ─────────────────────────────────────────────

def _normalise(text):
    """Collapse whitespace so a quote that differs from the transcript only by
    line wrapping still verifies. Nothing else is altered: wording,
    punctuation and numbers must match exactly."""
    return re.sub(r"\s+", " ", text or "").strip()


def normalise_with_map(text):
    """(normalised_text, index_map) where index_map[i] is the offset in the
    ORIGINAL text of normalised character i.

    Built in one pass alongside the normalisation itself, so mapping a match
    back to real offsets is a lookup rather than a second walk. PDF text wraps
    mid-sentence constantly, so quotes are matched whitespace-insensitively;
    the map is what keeps the stored offsets pointing at the real document.
    """
    out, index_map = [], []
    prev_space = True                      # leading whitespace is dropped
    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                index_map.append(i)
                prev_space = True
        else:
            out.append(ch)
            index_map.append(i)
            prev_space = False
    while out and out[-1] == " ":          # and trailing
        out.pop()
        index_map.pop()
    return "".join(out), index_map


def trim_quote(quote, max_words=MAX_QUOTE_WORDS):
    words = _normalise(quote).split(" ")
    return " ".join(words[:max_words]) if len(words) > max_words else " ".join(words)


def locate_quote(quote, cleaned_text, prepared=None):
    """(char_start, char_end) of a quote in the cleaned text, or None.

    This IS the spec 2.3 substring check: a quote that cannot be located is
    not in the document, so the caller fails the row. `prepared` is the
    (normalised, index_map) pair from normalise_with_map, passed in to avoid
    rebuilding it for every quote in a result.
    """
    if not quote:
        return None
    needle = _normalise(quote)
    if not needle:
        return None
    hay, index_map = prepared if prepared is not None else normalise_with_map(cleaned_text)
    at = hay.find(needle)
    if at < 0:
        return None
    return index_map[at], index_map[at + len(needle) - 1] + 1


def verify_and_locate(parsed, cleaned_text):
    """Walks every quote in a stage-2 result, trims it to the word cap,
    verifies it is really in the transcript, and attaches char offsets.

    Returns (parsed, failures).

    WHAT A FAILED CITE COSTS. Spec 2.3 says a quote that is not a substring
    "fails the row", and QA 8.2 requires that 100% of stored GIVEN values have
    a passing quote. Both are honoured here at FIELD granularity rather than
    by discarding the whole quarter: a field whose quote cannot be located is
    demoted to NOT_VERIFIED (spec 3.3's own meaning - "nothing can be
    asserted") with its unverifiable quote stripped, and a soft-outlook item
    without a verifiable quote is dropped from the list.

    The alternative - one paraphrase anywhere voiding the entire extraction -
    was tried first and is what the live TCS Q2 FY27 run hit on 16 Oct 2026:
    a single bad soft-outlook quote threw away the tones, the divergence
    grade, the guidance and nine verified sector slots, all of which had
    passed. That trades a small, correctly-labelled gap for a large silent
    one, and across a 213 x 6 backfill it would void quarters wholesale.
    Nothing unverified is stored as GIVEN either way, which is the property
    8.2 actually asks for.
    """
    prepared = normalise_with_map(cleaned_text)
    failures = []

    def check(obj, label, required):
        """True if the field survives verification, False if it was demoted."""
        quote = obj.get("quote")
        if not quote:
            if required:
                failures.append(f"{label}: status GIVEN with no quote")
                obj["status"] = "NOT_VERIFIED"
                return False
            return True
        obj["quote"] = trim_quote(quote)
        found = locate_quote(obj["quote"], cleaned_text, prepared)
        if found is None:
            failures.append(f"{label}: quote not found verbatim in transcript")
            obj["status"] = "NOT_VERIFIED"
            obj["quote"] = None
            obj.pop("char_start", None)
            obj.pop("char_end", None)
            return False
        obj["char_start"], obj["char_end"] = found
        return True

    for key in ("guidance_revenue", "guidance_margin",
                "guidance_capex", "guidance_orderbook"):
        field = parsed.get("guidance_formal", {}).get(key)
        if field:
            check(field, f"guidance_formal.{key}",
                  required=field.get("status") in ("GIVEN", "WITHDRAWN_OR_CHANGED"))

    for key, _ in _FRAME_FIELDS:
        field = parsed.get("frame", {}).get(key)
        if field:
            check(field, f"frame.{key}", required=field.get("status") == "GIVEN")

    for key, field in (parsed.get("sector_slots") or {}).items():
        check(field, f"sector_slots.{key}", required=field.get("status") == "GIVEN")

    # A soft-outlook item IS its quote (spec 4.2 stores them as quotes with a
    # topic and direction), so one that cannot be verified is dropped rather
    # than demoted - there would be nothing left to store.
    kept_soft = []
    for i, item in enumerate(parsed.get("guidance_soft") or [], start=1):
        if check(item, f"guidance_soft[{i}]", required=True):
            kept_soft.append(item)
    parsed["guidance_soft"] = kept_soft

    # The divergence GRADE rests on exchange cites, not on this quote, so an
    # unverifiable supporting quote clears the quote and keeps the grade.
    divergence = parsed.get("qa_divergence") or {}
    if divergence.get("quote"):
        if locate_quote(trim_quote(divergence["quote"]), cleaned_text, prepared) is None:
            failures.append("qa_divergence: quote not found verbatim in transcript")
            divergence["quote"] = None
        else:
            divergence["quote"] = trim_quote(divergence["quote"])
            divergence["char_start"], divergence["char_end"] = locate_quote(
                divergence["quote"], cleaned_text, prepared)

    return parsed, failures
