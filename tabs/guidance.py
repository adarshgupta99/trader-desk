"""
Guidance tab: per-stock formal guidance, soft outlook, sector metrics, and
promised-vs-delivered (spec section 6).

Read-only. Reads the v3 extraction tables, written only by
jobs/refresh_extraction_v3.py. This tab never writes.

DESIGN NOTES that are requirements, not preferences:
- NOT GIVEN is a designed state, rendered at the same size as a value, never
  a blank cell. A company that was asked and declined is different
  information from a company nobody looked at, and the UI has to say which.
- NOT VERIFIED renders distinctly from NOT GIVEN. The first means the
  evidence did not check out; the second means the call was silent. Collapsing
  them would hide exactly the failure the cite check exists to catch.
- Formal guidance and soft outlook never share a panel. The whole reason the
  extraction keeps them apart is that a soft "we feel good about H2" must not
  read as a commitment.
- Every value can show its evidence: the verbatim quote, the exchange it came
  from, and a link to the NSE PDF. Provenance is visible, not buried.
"""

from datetime import datetime

import streamlit as st

import fno
import sector_cards
from events_db import (get_estimate_surprise, get_extraction_quarters,
                       get_extraction_symbols, get_guidance_formal,
                       get_guidance_soft, get_sector_slots)
from theme import TABULAR, palette

_FORMAL_FIELDS = [
    ("guidance_revenue", "Revenue"),
    ("guidance_margin", "Operating margin"),
    ("guidance_capex", "Capex"),
    ("guidance_orderbook", "Order book"),
]

_DIRECTION_MARK = {"positive": "+", "negative": "−", "mixed": "~",
                   "neutral": "·"}


def _fmt_quarter(row):
    if row.get("fiscal_quarter"):
        return row["fiscal_quarter"]
    d = datetime.strptime(row["quarter_end"], "%Y-%m-%d")
    return f"QE {d.strftime('%b %Y')}"


def _status_chip(P, label, value, good=None):
    colour = P.T1 if good is None else (P.UP if good else P.T2)
    return (
        f"<span style='display:inline-flex;align-items:center;gap:6px;"
        f"border:1px solid {P.BORDER};padding:3px 10px;margin-right:8px;'>"
        f"<span style='font-size:11px;color:{P.T3};text-transform:uppercase;"
        f"letter-spacing:0.06em;'>{label}</span>"
        f"<span style='font-size:12px;color:{colour};font-weight:600;'>{value}</span>"
        f"</span>"
    )


def _value_or_state(P, field, large=True):
    """A guidance value, or a deliberately designed NOT GIVEN / NOT VERIFIED
    state at the same visual weight - never an empty cell."""
    status = (field or {}).get("status", "NOT_GIVEN")
    size = "22px" if large else "15px"
    if status == "GIVEN" and field.get("value_text"):
        return (f"<div style='font-size:{size};color:{P.T1};font-weight:600;"
                f"line-height:1.25;{TABULAR}'>{field['value_text']}</div>")
    if status == "WITHDRAWN_OR_CHANGED":
        return (f"<div style='font-size:{size};color:{P.T1};font-weight:600;"
                f"line-height:1.25;'>{field.get('value_text') or 'CHANGED'}"
                f"<span style='font-size:11px;color:{P.DOWN};margin-left:8px;'>"
                f"CHANGED</span></div>")
    if status == "NOT_VERIFIED":
        # Visually distinct from NOT GIVEN: the evidence failed its check,
        # which is a different fact from the call being silent.
        return (f"<div style='font-size:{size};color:{P.T3};font-weight:600;"
                f"line-height:1.25;border-bottom:2px dotted {P.T3};"
                f"display:inline-block;'>NOT VERIFIED</div>")
    return (f"<div style='font-size:{size};color:{P.T3};font-weight:600;"
            f"line-height:1.25;'>— <span style='font-size:12px;"
            f"letter-spacing:0.04em;'>NOT GIVEN</span></div>")


def _evidence_block(P, quote, exchange_ref, url):
    if not quote:
        return ""
    ref = f" · {exchange_ref}" if exchange_ref else ""
    link = (f" · <a href='{url}' target='_blank' rel='noopener' "
            f"style='color:{P.T3};'>NSE PDF</a>") if url else ""
    return (
        f"<details style='margin-top:6px;'>"
        f"<summary style='font-size:11px;color:{P.T3};cursor:pointer;"
        f"list-style:none;'>evidence{ref}{link}</summary>"
        f"<div style='font-size:12px;color:{P.T2};margin-top:4px;"
        f"border-left:2px solid {P.BORDER};padding-left:8px;font-style:italic;'>"
        f"“{quote}”</div></details>"
    )


def _promised_vs_delivered(P, symbol, quarters, current_index):
    """Spec 6.5: the PRIOR quarter's formal guidance against what this
    quarter actually shows. Only a numeric promise with a comparable actual
    can get an in-band verdict; everything else is a named state."""
    if current_index + 1 >= len(quarters):
        return ("<div style='color:%s;font-size:13px;'>No earlier quarter "
                "stored yet — nothing to test against.</div>" % P.T2)

    prior = quarters[current_index + 1]
    prior_formal = get_guidance_formal(symbol, prior["quarter_end"])
    current = quarters[current_index]
    current_slots = {s["slot_key"]: s for s in
                     get_sector_slots(symbol, current["quarter_end"])}

    rows = []
    for key, label in _FORMAL_FIELDS:
        promise = prior_formal.get(key) or {}
        if promise.get("status") not in ("GIVEN", "WITHDRAWN_OR_CHANGED"):
            verdict, delivered, source = "NO PROMISE TO TEST", "—", ""
        else:
            # Margin is the one field with a routinely comparable spoken
            # actual (the sector cards carry an ebit_margin slot). Anything
            # else needs a basis match this desk cannot assert yet, so it is
            # NOT COMPARABLE rather than a guessed verdict.
            slot = current_slots.get("ebit_margin") if key == "guidance_margin" else None
            if slot and slot.get("status") == "GIVEN" and slot.get("value_num") is not None:
                low, high = promise.get("value_num_low"), promise.get("value_num_high")
                actual = slot["value_num"]
                delivered = slot.get("value_text") or f"{actual}"
                source = "this quarter's call"
                if low is not None and high is not None:
                    verdict = ("IN BAND" if low <= actual <= high
                               else "ABOVE" if actual > high else "BELOW")
                else:
                    verdict = "NOT COMPARABLE"
            else:
                verdict, delivered, source = "NOT COMPARABLE", "—", ""

        colour = {"IN BAND": P.UP, "ABOVE": P.UP, "BELOW": P.DOWN}.get(verdict, P.T3)
        promised_text = promise.get("value_text") or "—"
        period = promise.get("applies_to_period") or ""
        rows.append(
            f"<tr style='border-bottom:1px solid {P.BORDER};'>"
            f"<td style='padding:7px 16px 7px 0;color:{P.T2};font-size:13px;'>{label}</td>"
            f"<td style='padding:7px 16px 7px 0;color:{P.T1};font-size:13px;{TABULAR}'>"
            f"{promised_text}"
            f"<span style='color:{P.T3};font-size:11px;'> {period}</span></td>"
            f"<td style='padding:7px 16px 7px 0;color:{P.T3};font-size:11px;'>"
            f"{_fmt_quarter(prior)}</td>"
            f"<td style='padding:7px 16px 7px 0;color:{P.T1};font-size:13px;{TABULAR}'>"
            f"{delivered}</td>"
            f"<td style='padding:7px 16px 7px 0;color:{colour};font-size:12px;"
            f"font-weight:600;'>{verdict}</td>"
            f"<td style='padding:7px 0;color:{P.T3};font-size:11px;'>{source}</td>"
            f"</tr>"
        )

    header = "".join(
        f"<th style='padding:4px 16px 6px 0;color:{P.T3};font-size:11px;"
        f"text-align:left;text-transform:uppercase;letter-spacing:0.05em;"
        f"border:none;'>{h}</th>"
        for h in ("Field", "Promised", "On call", "Delivered", "Verdict", "Source"))
    return (f"<table style='border-collapse:collapse;border:none;width:100%;'>"
            f"<tr style='border-bottom:1px solid {P.BORDER};'>{header}</tr>"
            f"{''.join(rows)}</table>")


def render():
    P = palette()
    st.markdown(
        f"<div style='font-size:28px;font-weight:700;color:{P.T1};"
        f"letter-spacing:-0.01em;margin-bottom:2px;'>Guidance</div>"
        f"<div style='font-size:13px;color:{P.T2};margin-bottom:14px;'>"
        f"What management committed to, what it only hinted at, and whether "
        f"last quarter's promise survived contact with this one. Everything "
        f"here is quoted from the company's own NSE-filed transcript.</div>",
        unsafe_allow_html=True,
    )

    symbols = get_extraction_symbols()
    if not symbols:
        st.markdown(
            f"<div style='color:{P.T2};font-size:13px;margin-top:10px;'>"
            f"No extractions stored yet. Run "
            f"<code>python -m jobs.refresh_extraction_v3 --symbols TCS</code> "
            f"to populate this tab.</div>", unsafe_allow_html=True)
        return

    st.markdown("<div class='chip-label'>Symbol</div>", unsafe_allow_html=True)
    symbol = st.pills("guidance_symbol", symbols, key="guidance_symbol",
                      label_visibility="collapsed", default=symbols[0])
    symbol = symbol or symbols[0]

    quarters = get_extraction_quarters(symbol)
    if not quarters:
        st.markdown(f"<div style='color:{P.T2};font-size:13px;'>No stored "
                    f"quarters for {symbol}.</div>", unsafe_allow_html=True)
        return

    labels = [_fmt_quarter(q) for q in quarters]
    st.markdown("<div class='chip-label' style='margin-top:10px;'>Quarter</div>",
                unsafe_allow_html=True)
    chosen = st.pills("guidance_quarter", labels, key=f"guidance_q_{symbol}",
                      label_visibility="collapsed", default=labels[0])
    index = labels.index(chosen) if chosen in labels else 0
    row = quarters[index]
    quarter_end = row["quarter_end"]

    formal = get_guidance_formal(symbol, quarter_end)
    soft = get_guidance_soft(symbol, quarter_end)
    slots = get_sector_slots(symbol, quarter_end)
    prior_formal = (get_guidance_formal(symbol, quarters[index + 1]["quarter_end"])
                    if index + 1 < len(quarters) else {})

    # ── header chips ─────────────────────────────────────────────────────────
    has_formal = any(f.get("status") in ("GIVEN", "WITHDRAWN_OR_CHANGED")
                     for f in formal.values())
    sector_label = (sector_cards.card(row.get("sector_card")) or {}).get(
        "label", "UNIVERSAL ONLY")
    chips = (
        _status_chip(P, "sector", row.get("sector") or "—")
        + _status_chip(P, "formal", "GIVEN" if has_formal else "NOT GIVEN",
                       good=has_formal)
        + _status_chip(P, "soft outlook", "PRESENT" if soft else "ABSENT",
                       good=bool(soft))
        + _status_chip(P, "tone", f"{row['overall_tone']:+d}"
                       if row.get("overall_tone") is not None else "—")
        + _status_chip(P, "divergence", row.get("qa_divergence") or "—",
                       good=(row.get("qa_divergence") == "N"))
    )
    if row.get("cite_failures"):
        chips += _status_chip(P, "cites rejected", str(row["cite_failures"]))
    if row.get("sector_unmapped"):
        chips += _status_chip(P, "", "UNIVERSAL ONLY")
    st.markdown(f"<div style='margin:4px 0 14px 0;'>{chips}</div>",
                unsafe_allow_html=True)

    left, right = st.columns([3, 2], gap="large")

    # ── formal guidance 2x2 ──────────────────────────────────────────────────
    with left:
        st.markdown(f"<div style='font-size:11px;color:{P.T3};"
                    f"text-transform:uppercase;letter-spacing:0.07em;"
                    f"margin-bottom:8px;'>Guidance fields</div>",
                    unsafe_allow_html=True)
        for pair in (_FORMAL_FIELDS[:2], _FORMAL_FIELDS[2:]):
            cols = st.columns(2, gap="medium")
            for col, (key, label) in zip(cols, pair):
                field = formal.get(key) or {}
                prior = prior_formal.get(key) or {}
                prior_text = (prior.get("value_text")
                              if prior.get("status") == "GIVEN"
                              else "NOT GIVEN last quarter")
                with col:
                    st.markdown(
                        f"<div style='border:1px solid {P.BORDER};padding:12px;"
                        f"min-height:120px;'>"
                        f"<div style='font-size:11px;color:{P.T3};"
                        f"text-transform:uppercase;letter-spacing:0.06em;"
                        f"margin-bottom:6px;'>{label}</div>"
                        f"{_value_or_state(P, field)}"
                        f"<div style='font-size:11px;color:{P.T3};margin-top:4px;'>"
                        f"{field.get('applies_to_period') or ''}</div>"
                        f"<div style='font-size:11px;color:{P.T3};margin-top:8px;"
                        f"border-top:1px solid {P.BORDER};padding-top:6px;'>"
                        f"prior: {prior_text}</div>"
                        f"{_evidence_block(P, field.get('quote'), field.get('exchange_ref'), row.get('transcript_url'))}"
                        f"</div>", unsafe_allow_html=True)

        # ── sector metrics ───────────────────────────────────────────────────
        st.markdown(f"<div style='font-size:11px;color:{P.T3};"
                    f"text-transform:uppercase;letter-spacing:0.07em;"
                    f"margin:18px 0 8px 0;'>{sector_label}</div>",
                    unsafe_allow_html=True)
        if row.get("sector_unmapped") or not slots:
            st.markdown(
                f"<div style='color:{P.T2};font-size:13px;'>No sector card for "
                f"this company yet — universal fields only.</div>",
                unsafe_allow_html=True)
        else:
            prior_slots = {}
            if index + 1 < len(quarters):
                prior_slots = {s["slot_key"]: s for s in get_sector_slots(
                    symbol, quarters[index + 1]["quarter_end"])}
            labels_by_key = {s["key"]: s["label"] for s in
                             sector_cards.slots_for_symbol(symbol)}
            body = []
            for slot in slots:
                prior = prior_slots.get(slot["slot_key"]) or {}
                if slot["status"] == "GIVEN":
                    value = (f"<span style='color:{P.T1};{TABULAR}'>"
                             f"{slot.get('value_text') or ''}</span>")
                    if slot.get("speaker_role") == "analyst":
                        # Adarsh's rule: an analyst's uncontested figure counts,
                        # but the reader must be able to see it was theirs.
                        value += (f"<span style='color:{P.T3};font-size:10px;"
                                  f"margin-left:6px;'>analyst</span>")
                elif slot["status"] == "NOT_VERIFIED":
                    value = (f"<span style='color:{P.T3};border-bottom:1px "
                             f"dotted {P.T3};'>NOT VERIFIED</span>")
                elif slot["status"] == "NOT_APPLICABLE":
                    value = f"<span style='color:{P.T3};'>N/A</span>"
                else:
                    value = f"<span style='color:{P.T3};'>NOT GIVEN</span>"
                body.append(
                    f"<tr style='border-bottom:1px solid {P.BORDER};'>"
                    f"<td style='padding:6px 14px 6px 0;color:{P.T2};"
                    f"font-size:12px;'>"
                    f"{labels_by_key.get(slot['slot_key'], slot['slot_key'])}</td>"
                    f"<td style='padding:6px 14px 6px 0;font-size:13px;'>{value}</td>"
                    f"<td style='padding:6px 14px 6px 0;color:{P.T3};"
                    f"font-size:11px;'>{slot.get('period_basis') or ''}</td>"
                    f"<td style='padding:6px 0;color:{P.T3};font-size:12px;"
                    f"{TABULAR}'>{prior.get('value_text') or '—'}</td></tr>")
            head = "".join(
                f"<th style='padding:4px 14px 6px 0;color:{P.T3};font-size:11px;"
                f"text-align:left;text-transform:uppercase;letter-spacing:0.05em;"
                f"border:none;'>{h}</th>"
                for h in ("Metric", "Value", "Basis", "Prior qtr"))
            st.markdown(
                f"<div style='overflow-x:auto;'><table style='border-collapse:"
                f"collapse;border:none;width:100%;'>"
                f"<tr style='border-bottom:1px solid {P.BORDER};'>{head}</tr>"
                f"{''.join(body)}</table></div>", unsafe_allow_html=True)

    # ── soft outlook ─────────────────────────────────────────────────────────
    with right:
        st.markdown(f"<div style='font-size:11px;color:{P.T3};"
                    f"text-transform:uppercase;letter-spacing:0.07em;"
                    f"margin-bottom:8px;'>Soft outlook</div>",
                    unsafe_allow_html=True)
        if not soft:
            st.markdown(f"<div style='color:{P.T3};font-size:13px;'>"
                        f"No forward commentary recorded for this call.</div>",
                        unsafe_allow_html=True)
        else:
            for item in soft:
                mark = _DIRECTION_MARK.get(item.get("direction"), "·")
                st.markdown(
                    f"<div style='border-left:2px solid {P.BORDER};"
                    f"padding:0 0 0 10px;margin-bottom:12px;'>"
                    f"<div style='font-size:11px;color:{P.T3};"
                    f"text-transform:uppercase;letter-spacing:0.05em;'>"
                    f"{mark} {item.get('topic') or ''}"
                    f"<span style='margin-left:6px;'>"
                    f"{item.get('exchange_ref') or ''}</span></div>"
                    f"<div style='font-size:12px;color:{P.T2};font-style:italic;"
                    f"margin-top:3px;line-height:1.45;'>"
                    f"“{item.get('quote') or ''}”</div></div>",
                    unsafe_allow_html=True)

    # ── promised vs delivered ────────────────────────────────────────────────
    st.markdown(f"<div style='font-size:11px;color:{P.T3};"
                f"text-transform:uppercase;letter-spacing:0.07em;"
                f"margin:22px 0 8px 0;'>Promised vs delivered</div>",
                unsafe_allow_html=True)
    st.markdown(_promised_vs_delivered(P, symbol, quarters, index),
                unsafe_allow_html=True)
    st.markdown(
        f"<div style='color:{P.T3};font-size:11px;margin-top:6px;'>"
        f"Compares the previous quarter's formal guidance against what this "
        f"call stated. Only a numeric promise with a comparable spoken actual "
        f"gets an in-band verdict; anything else is named, never guessed. "
        f"Next check runs at the following quarter's results.</div>",
        unsafe_allow_html=True)

    # ── surprise (spec 5A.4) ─────────────────────────────────────────────────
    surprise = get_estimate_surprise(symbol, quarter_end)
    if surprise:
        st.markdown(f"<div style='font-size:11px;color:{P.T3};"
                    f"text-transform:uppercase;letter-spacing:0.07em;"
                    f"margin:22px 0 8px 0;'>Estimate vs actual</div>",
                    unsafe_allow_html=True)
        body = []
        for item in surprise:
            pct = item.get("surprise_pct")
            colour = P.T3 if pct is None else (P.UP if pct >= 0 else P.DOWN)
            pct_text = "—" if pct is None else f"{pct:+.2f}%"
            body.append(
                f"<tr style='border-bottom:1px solid {P.BORDER};'>"
                f"<td style='padding:6px 14px 6px 0;color:{P.T2};font-size:12px;'>"
                f"{item['metric']}</td>"
                f"<td style='padding:6px 14px 6px 0;color:{P.T2};font-size:12px;"
                f"{TABULAR}'>{item.get('estimate_value') or '—'}</td>"
                f"<td style='padding:6px 14px 6px 0;color:{P.T1};font-size:12px;"
                f"{TABULAR}'>{item.get('actual_value') or '—'}</td>"
                f"<td style='padding:6px 14px 6px 0;color:{colour};font-size:12px;"
                f"{TABULAR}'>{pct_text}</td>"
                f"<td style='padding:6px 0;color:{P.T3};font-size:11px;'>"
                f"{item.get('comparability') or ''}</td></tr>")
        head = "".join(
            f"<th style='padding:4px 14px 6px 0;color:{P.T3};font-size:11px;"
            f"text-align:left;text-transform:uppercase;letter-spacing:0.05em;"
            f"border:none;'>{h}</th>"
            for h in ("Metric", "Estimate", "Actual", "Surprise", "Quality"))
        st.markdown(
            f"<div style='overflow-x:auto;'><table style='border-collapse:"
            f"collapse;border:none;width:100%;'>"
            f"<tr style='border-bottom:1px solid {P.BORDER};'>{head}</tr>"
            f"{''.join(body)}</table></div>", unsafe_allow_html=True)
        st.markdown(
            f"<div style='color:{P.T3};font-size:11px;margin-top:6px;'>"
            f"Historical estimate: not verified as frozen before results; may "
            f"have been restated. Basis is the source's own “probable "
            f"consolidated” and is unconfirmed per stock. Separate from "
            f"formal guidance and from promised-vs-delivered — beating "
            f"consensus and keeping your own promise are different tests.</div>",
            unsafe_allow_html=True)

    # ── history strip ────────────────────────────────────────────────────────
    st.markdown(f"<div style='font-size:11px;color:{P.T3};"
                f"text-transform:uppercase;letter-spacing:0.07em;"
                f"margin:22px 0 8px 0;'>History</div>", unsafe_allow_html=True)
    cells = []
    for q in quarters:
        formal_q = get_guidance_formal(symbol, q["quarter_end"])
        given = any(f.get("status") in ("GIVEN", "WITHDRAWN_OR_CHANGED")
                    for f in formal_q.values())
        is_current = q["quarter_end"] == quarter_end
        cells.append(
            f"<td style='padding:7px 14px 7px 0;font-size:12px;"
            f"color:{P.T1 if is_current else P.T2};"
            f"font-weight:{600 if is_current else 400};{TABULAR}'>"
            f"{_fmt_quarter(q)}</td>"
            f"<td style='padding:7px 14px 7px 0;font-size:11px;color:{P.T3};'>"
            f"{'GIVEN' if given else 'NOT GIVEN'}</td>"
            f"<td style='padding:7px 14px 7px 0;font-size:12px;color:{P.T2};"
            f"{TABULAR}'>{q['overall_tone']:+d}</td>"
            f"<td style='padding:7px 14px 7px 0;font-size:11px;color:{P.T2};'>"
            f"{q.get('qa_divergence') or ''}</td>"
            f"<td style='padding:7px 24px 7px 0;font-size:12px;color:{P.T2};"
            f"{TABULAR}'>{q.get('evasive_count') if q.get('evasive_count') is not None else ''}</td>")
    head = "".join(
        f"<th style='padding:4px 14px 6px 0;color:{P.T3};font-size:11px;"
        f"text-align:left;text-transform:uppercase;letter-spacing:0.05em;"
        f"border:none;'>{h}</th>"
        for h in ("Quarter", "Formal", "Tone", "Diverg.", "Evasive"))
    st.markdown(
        f"<div style='overflow-x:auto;'><table style='border-collapse:collapse;"
        f"border:none;'><tr style='border-bottom:1px solid {P.BORDER};'>"
        f"{head * len(quarters)}</tr><tr>{''.join(cells)}</tr></table></div>",
        unsafe_allow_html=True)

    model = row.get("stage2_model") or "—"
    st.markdown(
        f"<div style='color:{P.T3};font-size:11px;margin-top:10px;'>"
        f"Extracted by {row.get('extractor_version')} via {model}. "
        f"Transcript published {(row.get('source_published_at') or '')[:16]}; "
        f"results announced {(row.get('announcement_at') or '')[:16]}. "
        f"A transcript is published after the print, so nothing on this tab "
        f"was knowable at the moment of the results move.</div>",
        unsafe_allow_html=True)
