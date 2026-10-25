"""
Guidance tab: per-stock formal guidance, soft outlook, sector metrics,
promised-vs-delivered, risks, analyst pushback, and a synthesized verdict.

Read-only. Reads the v3 extraction tables, written only by
jobs/refresh_extraction_v3.py. This tab never writes.

VISUAL REDESIGN (21 Oct 2026, guidance-claude-prompt-light-dark-7b65efc4.txt):
this tab intentionally runs its OWN colour palette and font pair, independent
of the rest of the app's Swiss Editorial / Charcoal Warm design language in
theme.py. Adarsh's explicit call when the two conflicted: follow the new
spec for this tab, documented as a deliberate, scoped divergence rather than
a drift. Every colour below is a CSS custom property on the `.g-redesign`
wrapper (not :root) so it cannot leak into any other tab, and the palette
switches on the browser's own `prefers-color-scheme`, not Streamlit's
`st.context.theme.type` - this is what lets the spec's "do not pin a base"
intent apply to just this tab without touching .streamlit/config.toml (which
other tabs' palette.py already depends on for its own, separate dual-theme
mechanism - changing it was out of scope per the spec's own "no other tab
changes" rule, so it is left alone; see ARCHITECTURE.md).

DATA DESIGN NOTES that are requirements, not preferences:
- NOT GIVEN / NOT VERIFIED fields are never rendered as cards in this view
  (spec 5: "Only render a slot if it has a value"). The OLD guidance.py
  showed every field including empty ones; this version hides them and
  shows a single muted note line instead - a deliberate behaviour change
  the new spec asked for.
- Formal guidance and soft outlook still never share a panel/row.
- A section with no backing data is hidden outright, never an empty card or
  placeholder dash (spec 5).
- VERDICT, RISKS and PUSHBACK summary are LLM-derived (stage 2 risks list,
  stage-2-derived pushback counts, stage-3 verdict synthesis) but every one
  is grounded in already-verified extracted facts, never the raw transcript
  re-interpreted at render time. FORWARD CALENDAR and LANGUAGE DELTA need no
  new extraction: the calendar is a plain read of events_db.get_upcoming_events,
  and the delta is pure composition over two quarters' already-stored soft
  outlook rows.
"""

import html
from datetime import datetime

import streamlit as st

import sector_cards
import guidance_logic
from events_db import (get_commitments, get_estimate_surprise,
                       get_extraction_quarters, get_extraction_symbols,
                       get_guidance_formal, get_guidance_soft,
                       get_promise_checks, get_risks, get_sector_slots,
                       get_upcoming_events, get_verdict)

_FORMAL_FIELDS = guidance_logic.FORMAL_FIELDS

_DIRECTION_MARK = {"positive": "+", "negative": "−", "mixed": "~",
                   "neutral": "·"}

#: role -> the CSS colour-role class each chip/accent uses. Collapsed to the
#: spec's five hues (green/red/blue/purple/ochre); teal is accent-only (the
#: Segment Scorecard section label), never a chip role.
_ROLE_CLASS = {
    "kept": "green", "beat": "green", "met": "green",
    "raised": "blue",
    "new": "purple",
    "stated": "ochre", "maintained": "ochre", "partial": "ochre",
    "lowered": "red", "dodged": "red", "risk": "red", "missed": "red",
}

#: Light/dark token sets. Which one is used is decided in PYTHON, from
#: st.context.theme.type - the SAME mechanism theme.py's palette() uses for
#: every other tab - NOT a `prefers-color-scheme` media query.
#:
#: The new spec asked for `prefers-color-scheme`, and that is what this tab
#: first shipped with. It is wrong HERE, and produced a real visible bug
#: (found live by Adarsh, 25 Oct 2026): `prefers-color-scheme` reads the
#: OS/browser setting, while everything else on the page - the canvas, the
#: nav, the title, every other tab - follows Streamlit's own theme setting
#: (its toolbar Appearance toggle, which may simply follow the system, but
#: does not have to). They are two independent settings. When they
#: disagreed, this tab rendered its dark cards on the app's light canvas: a
#: dark slab sitting in a beige page. Reading the same setting the rest of
#: the app reads makes a mismatch impossible by construction.
_LIGHT_TOKENS = {
    "bg": "#DDD6C4", "card": "#F8F5EC", "card_hi": "#F3EFE4",
    "border": "#B3AC9A", "text": "#2A251F", "muted": "#4F493E",
    "accent": "#8A6A3B", "green": "#2F7A4B", "red": "#C0502E",
    "blue": "#2F6690", "purple": "#7B5694", "ochre": "#A8741A",
    "teal": "#2A7F7A",
    "green_rgb": "47,122,75", "red_rgb": "192,80,46", "blue_rgb": "47,102,144",
    "purple_rgb": "123,86,148", "ochre_rgb": "168,116,26",
    "teal_rgb": "42,127,122",
    "card_shadow": "0 1px 3px rgba(60,50,30,.18)",
    "quote_bg": "rgba(0,0,0,.05)",
}
_DARK_TOKENS = {
    "bg": "#24211E", "card": "#302B27", "card_hi": "#38322C",
    "border": "#554A40", "text": "#ECE5DA", "muted": "#AAA197",
    "accent": "#D8B58A", "green": "#9AC69C", "red": "#E8A078",
    "blue": "#8FB4D4", "purple": "#B99ACB", "ochre": "#D4A95C",
    "teal": "#7FBDB5",
    "green_rgb": "154,198,156", "red_rgb": "232,160,120",
    "blue_rgb": "143,180,212", "purple_rgb": "185,154,203",
    "ochre_rgb": "212,169,92", "teal_rgb": "127,189,181",
    "card_shadow": "0 1px 3px rgba(0,0,0,.35)",
    "quote_bg": "rgba(0,0,0,.22)",
}

_CSS_TEMPLATE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');

/* Variables live on :root, not a wrapping .g-redesign div: Streamlit renders
   each st.markdown()/st.expander()/st.columns() call as an INDEPENDENT
   sibling block in the DOM, not nested inside whatever HTML a previous call
   emitted. A div opened in one call and closed in a much later, separate
   call never actually wraps anything in between - every var(--card) etc.
   used by content from a different call was resolving to nothing, which is
   why the tab rendered flat and colourless (found live, 25 Oct 2026). Safe
   at :root scope: this <style> block only exists in the DOM while this tab
   is the active one (app.py builds only the selected section per rerun),
   and no other tab's CSS references these variable names. */
:root {
    --bg: {bg}; --card: {card}; --card-hi: {card_hi}; --border: {border};
    --text: {text}; --muted: {muted}; --accent: {accent};
    --green: {green}; --red: {red}; --blue: {blue}; --purple: {purple};
    --ochre: {ochre}; --teal: {teal};
    --green-rgb: {green_rgb}; --red-rgb: {red_rgb}; --blue-rgb: {blue_rgb};
    --purple-rgb: {purple_rgb}; --ochre-rgb: {ochre_rgb}; --teal-rgb: {teal_rgb};
    --card-shadow: {card_shadow};
    --quote-bg: {quote_bg};
}
/* The page canvas is deliberately NOT repainted here. Streamlit's own theme
   (.streamlit/config.toml's [theme]/[theme.dark]) already paints it, and its
   values match these within a shade. Overriding it hit the whole shared
   .block-container - which holds the title and the nav, not just this tab's
   body - and darkened the app's chrome around a tab that is only supposed to
   restyle itself. Cards carry their own surface instead; the "page" behind
   them is simply the app's own canvas. */
[data-testid="stAppViewContainer"] .block-container {
    max-width: 1100px;
    margin: 0 auto;
}
* { box-sizing: border-box; }
h1, h2, h3, .g-num, .g-headline, .g-ticker {
    font-family: 'Source Sans 3', sans-serif;
}

.g-section { margin-bottom: 30px; }
.g-label-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.g-dot { width: 10px; height: 10px; border-radius: 3px; display: inline-block; flex-shrink: 0; }
.g-label {
    font-family: 'Inter', sans-serif; font-size: 12px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.12em; color: var(--text);
}

.g-card {
    background: var(--card); border: 1px solid var(--border); border-radius: 8px;
    box-shadow: var(--card-shadow);
}
.g-card-hi {
    background: var(--card-hi); border: 1px solid var(--border); border-radius: 8px;
    box-shadow: var(--card-shadow); border-left: 5px solid var(--accent);
    padding: 16px 20px;
}

.g-row { padding: 14px 20px; border-bottom: 1px solid var(--border); }
.g-row:last-child { border-bottom: none; }
.g-row-grid { display: grid; grid-template-columns: 230px 1fr 110px; gap: 12px; align-items: center; }
@media (max-width: 800px) { .g-row-grid { grid-template-columns: 1fr; } }

.g-chip {
    display: inline-flex; align-items: center; font-family: 'Inter', sans-serif;
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.06em; border-radius: 999px; padding: 3px 9px;
    border: 1px solid; white-space: nowrap; width: fit-content;
}
.g-chip-green { color: var(--green); border-color: var(--green); background: rgba(var(--green-rgb),.12); }
.g-chip-red { color: var(--red); border-color: var(--red); background: rgba(var(--red-rgb),.12); }
.g-chip-blue { color: var(--blue); border-color: var(--blue); background: rgba(var(--blue-rgb),.12); }
.g-chip-purple { color: var(--purple); border-color: var(--purple); background: rgba(var(--purple-rgb),.12); }
.g-chip-ochre { color: var(--ochre); border-color: var(--ochre); background: rgba(var(--ochre-rgb),.12); }

.g-headline { font-size: 26px; font-weight: 700; line-height: 1.25; color: var(--text); }
.g-support { font-family: 'Inter', sans-serif; font-size: 13.5px; color: var(--muted); margin-top: 4px; }
.g-row-title { font-size: 16px; font-weight: 600; color: var(--text); }
.g-value { font-size: 24px; font-weight: 700; color: var(--text); }
.g-kpi-value { font-size: 30px; font-weight: 700; color: var(--text); }
.g-hitrate-value { font-size: 34px; font-weight: 700; }
.g-muted { font-family: 'Inter', sans-serif; font-size: 13.5px; color: var(--muted); }
.g-ticker { font-size: 28px; font-weight: 700; color: var(--text); }

.g-receipt { margin-top: 3px; }
.g-receipt-link {
    color: var(--blue); font-size: 12px; cursor: pointer; list-style: none;
}
.g-receipt-link::-webkit-details-marker { display: none; }
.g-receipt-link::before { content: '\25b8  '; }
.g-receipt[open] > .g-receipt-link::before { content: '\25be  '; }
.g-quote-block {
    border-left: 3px solid var(--red); background: var(--quote-bg);
    border-radius: 0 6px 6px 0; padding: 8px 12px; margin-top: 6px;
}
.g-quote-text { font-family: 'Inter', sans-serif; font-size: 13.5px; font-style: italic; color: var(--muted); }
.g-quote-src { font-weight: 600; font-style: normal; color: var(--text); }

.g-kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px; }
.g-kpi-grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }
@media (max-width: 800px) { .g-kpi-grid, .g-kpi-grid-3 { grid-template-columns: 1fr 1fr; } }
/* No justify-content: space-between. With tiles stretched to a tall
   sibling's height it pushed the label to the top and the value to the
   bottom, leaving a hole in the middle of every shorter tile - exactly the
   empty space inside a card this desk does not allow. Content stacks from
   the top instead and the tile simply ends where its content does. */
.g-kpi-tile {
    background: var(--card); border: 1px solid var(--border); border-radius: 8px;
    box-shadow: var(--card-shadow); border-top: 3px solid var(--accent-tile, var(--accent));
    padding: 16px 18px; min-height: 128px; display: flex; flex-direction: column;
    gap: 6px;
}
/* A slot value is supposed to be a short number or phrase, but extraction
   sometimes returns a whole sentence (DRREDDY's india_formulations_vs_ipm
   came back 143 characters). At headline size that wraps to ten lines and
   drags every sibling tile's height with it, so the size steps down with
   length and anything genuinely long is clamped, with the full text on
   hover via the app's existing data-tip popup. */
.g-kpi-value-long {
    display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical;
    overflow: hidden; line-height: 1.3;
}
.g-kpi-label {
    font-family: 'Inter', sans-serif; font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted);
}

.g-two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
@media (max-width: 800px) { .g-two-col { grid-template-columns: 1fr; } }

.g-table { width: 100%; border-collapse: collapse; font-family: 'Inter', sans-serif; }
.g-table th {
    text-align: left; font-size: 12px; font-weight: 700; text-transform: uppercase;
    color: var(--text); padding: 10px 14px 8px 0; border-bottom: 1px solid var(--border);
}
.g-table td { padding: 10px 14px 10px 0; border-bottom: 1px solid var(--border); font-size: 13.5px; }
.g-table tr:last-child td { border-bottom: none; }

.g-progress-track { width: 100%; height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; }
.g-progress-fill { height: 8px; background: var(--red); border-radius: 4px; }

.g-cal-cells { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); }
.g-cal-cell { padding: 14px 16px; border-right: 1px solid var(--border); }
.g-cal-cell:last-child { border-right: none; }
.g-cal-date { font-size: 18px; font-weight: 700; }
.g-cal-event { font-family: 'Inter', sans-serif; font-size: 13px; color: var(--muted); margin-top: 4px; }

.g-divider { border-top: 1px solid var(--border); padding-top: 10px; margin-top: 4px; }
</style>
"""


def _css():
    """The stylesheet with this render's theme tokens substituted in.

    Plain placeholder replacement, not str.format: the sheet below is mostly
    CSS, so every rule's own braces would have to be doubled to survive
    format(), which is a large, silent footgun for anyone editing the CSS
    later.
    """
    dark = getattr(st.context.theme, "type", "light") == "dark"
    tokens = _DARK_TOKENS if dark else _LIGHT_TOKENS
    sheet = _CSS_TEMPLATE
    for key, value in tokens.items():
        sheet = sheet.replace("{" + key + "}", value)
    return sheet


def _dot(role_var):
    return f"<span class='g-dot' style='background:var({role_var});'></span>"


def _section_label(text, role_var):
    return (f"<div class='g-label-row'>{_dot(role_var)}"
            f"<span class='g-label'>{text}</span></div>")


def _chip(label, role):
    cls = _ROLE_CLASS.get(role, "ochre")
    return f"<span class='g-chip g-chip-{cls}'>{label}</span>"


def _quote_block(quote, speaker, call_label):
    if not quote:
        return ""
    src = " · ".join(x for x in (speaker, call_label) if x)
    src_html = f"<div class='g-quote-src'>{src}</div>" if src else ""
    return (f"<div class='g-quote-block'>{src_html}"
            f"<div class='g-quote-text'>“{quote}”</div></div>")


def _receipt_inline(quote, exchange_ref, call_label):
    """A native <details>/<summary> 'Receipt' disclosure, inline inside a
    row's own HTML - collapsed shows a small blue 'Receipt' link, open
    reveals the quote block beneath it, no native st.expander chrome (a
    boxed accordion, closer to a form control than the spec's minimal inline
    link - and a SEPARATE Streamlit widget call besides, which would render
    as its own sibling block rather than nested in this row)."""
    if not quote:
        return ""
    return (f"<details class='g-receipt'><summary class='g-receipt-link'>"
            f"Receipt</summary>{_quote_block(quote, exchange_ref, call_label)}"
            f"</details>")


def _fmt_quarter(row):
    if row.get("fiscal_quarter"):
        return row["fiscal_quarter"]
    d = datetime.strptime(row["quarter_end"], "%Y-%m-%d")
    return f"QE {d.strftime('%b %Y')}"


def _formal_status_role(key, field, prior_field):
    """Derive a Commitments status chip role from GIVEN/WITHDRAWN_OR_CHANGED
    fields only - NOT_GIVEN fields never reach this function (spec 5: never
    render an empty slot). 'Dodged' has no reliable signal on formal fields
    alone (we don't track 'asked, declined' separately from 'never asked'),
    so it is deliberately not produced here."""
    prior_status = (prior_field or {}).get("status")
    if prior_status not in ("GIVEN", "WITHDRAWN_OR_CHANGED"):
        return "new"
    low, high = field.get("value_num_low"), field.get("value_num_high")
    plow, phigh = prior_field.get("value_num_low"), prior_field.get("value_num_high")
    if None not in (low, high, plow, phigh):
        if low >= phigh and (low, high) != (plow, phigh):
            return "raised"
        if high <= plow and (low, high) != (plow, phigh):
            return "lowered"
    return "stated"


_COMMITMENT_CHIP = {
    "new": ("New", "new"), "raised": ("Raised", "raised"),
    "lowered": ("Lowered", "lowered"), "stated": ("Stated", "stated"),
    "dodged": ("Dodged", "dodged"),
}


def _commitments_card(commitments, formal, prior_formal, call_label):
    """Rows come from TWO sources: `commitments` (v3.2.0, company-specific
    named items the model judges each call - "Exports run-rate", "Capacity",
    including a 'dodged' status when management declines to quantify
    something asked of them) and any formal guidance field that IS given
    (GIVEN/WITHDRAWN_OR_CHANGED only - NOT_GIVEN ones never render, spec 5).
    Most companies give no formal numeric guidance at all; that is normal,
    not a gap, and is named in the note line rather than shown as empty rows.
    """
    formal_given = [(k, l) for k, l in _FORMAL_FIELDS
                    if (formal.get(k) or {}).get("status") in
                    ("GIVEN", "WITHDRAWN_OR_CHANGED")]
    not_given_labels = [l for k, l in _FORMAL_FIELDS
                        if (k, l) not in formal_given]

    if not commitments and not formal_given:
        st.markdown(
            f"<div class='g-muted'>No commitments or formal guidance on "
            f"this call.</div>", unsafe_allow_html=True)
        return

    rows_html = []
    for item in commitments:
        chip_label, role = _COMMITMENT_CHIP.get(item.get("status"), ("Stated", "stated"))
        value_colour = "color:var(--red);" if role == "dodged" else ""
        receipt = _receipt_inline(item.get("quote"), item.get("exchange_ref"), call_label)
        rows_html.append(
            f"<div class='g-row'><div class='g-row-grid'>"
            f"{_row_value_html(item.get('value'), value_colour)}"
            f"<div><div class='g-row-title'>{item.get('metric') or ''}</div>"
            f"<div class='g-support'>{item.get('support') or ''}</div>{receipt}</div>"
            f"<div>{_chip(chip_label, role)}</div>"
            f"</div></div>")
    for key, label in formal_given:
        field = formal[key]
        role = _formal_status_role(key, field, prior_formal.get(key) or {})
        chip_label = {"new": "New", "raised": "Raised", "lowered": "Lowered",
                     "stated": "Stated"}[role]
        receipt = _receipt_inline(field.get("quote"), field.get("exchange_ref"), call_label)
        rows_html.append(
            f"<div class='g-row'><div class='g-row-grid'>"
            f"{_row_value_html(field.get('value_text'))}"
            f"<div><div class='g-row-title'>{label}</div>"
            f"<div class='g-support'>{field.get('applies_to_period') or ''}</div>{receipt}</div>"
            f"<div>{_chip(chip_label, role)}</div>"
            f"</div></div>")
    st.markdown(f"<div class='g-card'>{''.join(rows_html)}</div>",
                unsafe_allow_html=True)

    if not_given_labels:
        st.markdown(
            f"<div class='g-muted' style='margin-top:8px;'>No formal numeric "
            f"guidance given on: {', '.join(not_given_labels)}.</div>",
            unsafe_allow_html=True)


def _promises_tested_card(checks, prior_label):
    """checks: events_db.get_promise_checks rows - each tests one of the
    PRIOR quarter's own commitments against what THIS call actually says
    (transcript-grounded, cite-verified; see promise_check.py). NOT_ADDRESSED
    rows are shown, not hidden - a promise the call goes silent on is itself
    a fact worth keeping visible, matching CLAUDE.md's 'report honest sample
    sizes' - but is excluded from the headline hit-rate, which is about
    promises the call actually spoke to."""
    if not checks:
        return False
    addressed = [c for c in checks if c["verdict"] != "NOT_ADDRESSED"]
    beats_or_met = sum(1 for c in addressed if c["verdict"] in ("BEAT", "MET"))
    score_colour = "var(--green)"
    if addressed:
        score_html = f"{beats_or_met} / {len(addressed)}"
        caption = ("Met or beaten, of promises this call actually addressed."
                   if beats_or_met else "Of promises this call actually addressed.")
    else:
        score_html = "0 addressed"
        caption = "This call did not speak to any of last call's commitments."
    header = (
        f"<div style='padding:16px 20px;border-bottom:1px solid var(--border);"
        f"display:flex;align-items:baseline;gap:14px;'>"
        f"<span class='g-hitrate-value' style='color:{score_colour};'>"
        f"{score_html}</span>"
        f"<div><div class='g-row-title'>promises tested · last call "
        f"({prior_label}) vs actuals</div>"
        f"<div class='g-muted'>{caption}</div></div></div>")

    rows = []
    verdict_chip = {"BEAT": ("Beat", "beat"), "MET": ("Met", "met"),
                    "PARTIAL": ("Partial", "partial"),
                    "MISSED": ("Missed", "missed"),
                    "NOT_ADDRESSED": ("Not addressed", "stated")}
    for c in checks:
        label, role = verdict_chip[c["verdict"]]
        rows.append(
            f"<tr><td>{c['metric']}</td>"
            f"<td class='g-muted'>{c['promised_value'] or ''}</td>"
            f"<td style='font-weight:700;'>{c.get('delivered_value') or '—'}</td>"
            f"<td>{_chip(label, role)}</td></tr>")
    table = (
        f"<table class='g-table'><tr><th>Promise</th><th>Said last call</th>"
        f"<th>Delivered</th><th>Result</th></tr>{''.join(rows)}</table>")
    st.markdown(f"<div class='g-card'>{header}"
                f"<div style='padding:14px 20px;'>{table}</div></div>",
                unsafe_allow_html=True)
    return True


def _risks_card(risks):
    if not risks:
        return
    items = []
    for r in risks:
        sized_note = ("sized" if r.get("sized") else "not sized"
                      if r.get("sized") is not None else "")
        items.append(
            f"<div class='g-row'><div class='g-row-title'>{r.get('text') or ''}</div>"
            f"<div class='g-muted'>{sized_note}</div></div>")
    st.markdown(f"<div class='g-card'>{''.join(items)}</div>", unsafe_allow_html=True)


def _pushback_card(row, call_label):
    total = row.get("pushback_total")
    dodged = row.get("pushback_dodged")
    if not total:
        return
    pct = (dodged / total * 100) if total else 0
    quote_html = _quote_block(row.get("pushback_example_quote"),
                              row.get("pushback_example_exchange_ref"), call_label)
    st.markdown(
        f"<div class='g-card' style='padding:16px 18px;'>"
        f"<div class='g-hitrate-value' style='color:var(--red);'>"
        f"{dodged} of {total}</div>"
        f"<div class='g-muted' style='margin-bottom:10px;'>pointed questions "
        f"not directly answered</div>"
        f"<div class='g-progress-track'><div class='g-progress-fill' "
        f"style='width:{pct:.0f}%;'></div></div>"
        f"{quote_html}"
        f"<div class='g-muted' style='margin-top:8px;'>{row.get('pushback_summary') or ''}</div>"
        f"</div>", unsafe_allow_html=True)


def _row_value_html(value, extra_style=""):
    """Same length-aware treatment as _kpi_value_html, for the commitment
    row's fixed 230px value column."""
    text = ("" if value is None else str(value)).strip()
    if len(text) <= 18:
        size = "24px"
    elif len(text) <= 36:
        size = "18px"
    else:
        size = "15px"
    tip = f" data-tip=\"{html.escape(text, quote=True)}\"" if len(text) > 60 else ""
    return (f"<div class='g-value' style='font-size:{size};{extra_style}'{tip}>"
            f"{text}</div>")


def _kpi_value_html(value):
    """A KPI value sized for its own length.

    The design wants a short number or phrase here, and the extraction
    prompt asks for one, but a model will occasionally return a sentence -
    and a sentence rendered at 30px is what broke this section's layout
    live (DRREDDY, 25 Oct 2026). Rather than trusting the data to always be
    short, the size steps down and long text is clamped to four lines, with
    the whole value still reachable on hover through the app's existing
    data-tip popup (theme.py's _MAIN_CSS - the native title= attribute
    renders nothing on this app's markup, confirmed in a browser earlier).
    """
    text = ("" if value is None else str(value)).strip()
    if len(text) <= 16:
        size = "30px"
    elif len(text) <= 32:
        size = "22px"
    elif len(text) <= 64:
        size = "17px"
    else:
        size = "15px"
    long_cls = " g-kpi-value-long" if len(text) > 64 else ""
    tip = f" data-tip=\"{html.escape(text, quote=True)}\"" if len(text) > 64 else ""
    return (f"<div class='g-kpi-value{long_cls}' "
            f"style='font-size:{size};'{tip}>{text}</div>")


def _segment_scorecard(symbol, slots, quarter_end):
    given = [s for s in slots if s.get("status") == "GIVEN" and s.get("value_text")]
    if not given:
        return
    labels_by_key = {s["key"]: s["label"] for s in sector_cards.slots_for_symbol(symbol)}
    accents = ["blue", "green", "purple", "ochre"]
    tiles = []
    for i, slot in enumerate(given[:4]):
        accent = accents[i % len(accents)]
        label = labels_by_key.get(slot["slot_key"], slot["slot_key"])
        context = slot.get("period_basis") or slot.get("unit") or ""
        tiles.append(
            f"<div class='g-kpi-tile' style='--accent-tile:var(--{accent});'>"
            f"<div class='g-kpi-label'>{label}</div>"
            f"{_kpi_value_html(slot.get('value_text'))}"
            f"<div class='g-muted'>{context}</div></div>")
    st.markdown(f"<div class='g-kpi-grid'>{''.join(tiles)}</div>", unsafe_allow_html=True)


def _actuals_vs_estimates(surprise):
    usable = [s for s in surprise if s.get("actual_value") is not None][:3]
    if not usable:
        return
    tiles = []
    for s in usable:
        pct = s.get("surprise_pct")
        delta_colour = "var(--red)" if (pct is not None and pct < 0) else "var(--green)"
        delta_text = "" if pct is None else f"{pct:+.2f}%"
        tiles.append(
            f"<div class='g-kpi-tile' style='--accent-tile:var(--ochre);'>"
            f"<div class='g-kpi-label'>{s['metric']}</div>"
            f"{_kpi_value_html(s.get('actual_value') or '—')}"
            f"<div class='g-muted'>est. {s.get('estimate_value') or '—'}</div>"
            f"<div style='color:{delta_colour};font-weight:600;font-size:13px;'>"
            f"{delta_text}</div></div>")
    st.markdown(f"<div class='g-kpi-grid-3'>{''.join(tiles)}</div>", unsafe_allow_html=True)


def _forward_calendar(symbol):
    events = get_upcoming_events(symbols=[symbol])
    if events.empty:
        return None
    accents = ["blue", "green", "purple", "ochre"]
    cells = []
    for i, (_, ev) in enumerate(events.head(4).iterrows()):
        accent = accents[i % len(accents)]
        d = datetime.strptime(ev["event_date"], "%Y-%m-%d")
        line = ev.get("purpose") or ev.get("subtype") or ev.get("event_type") or ""
        cells.append(
            f"<div class='g-cal-cell'><div class='g-cal-date' "
            f"style='color:var(--{accent});'>{d.strftime('%d %b')}</div>"
            f"<div class='g-cal-event'>{line}</div></div>")
    return f"<div class='g-card g-cal-cells'>{''.join(cells)}</div>"


def _language_delta(current_soft, prior_soft, prior_label, current_label):
    """Pure composition, no new extraction: finds a topic present in both
    quarters' already-stored soft-outlook rows whose direction changed, and
    shows it as an earlier-call/latest-call pair. No new extraction - this
    is a read-only diff over two quarters already fetched for this render."""
    prior_by_topic = {s["topic"]: s for s in prior_soft if s.get("topic")}
    for item in current_soft:
        topic = item.get("topic")
        prior = prior_by_topic.get(topic)
        if not prior or prior.get("direction") == item.get("direction"):
            continue
        return (
            f"<div class='g-card' style='padding:16px 18px;'>"
            f"<div class='g-row-title' style='margin-bottom:10px;'>{topic}</div>"
            f"<div style='display:flex;align-items:center;gap:10px;flex-wrap:wrap;'>"
            f"{_chip(prior.get('direction', '').upper() or 'EARLIER', 'lowered' if item.get('direction')=='positive' else 'raised')}"
            f"<span class='g-muted'>→</span>"
            f"{_chip(item.get('direction', '').upper() or 'LATEST', 'raised' if item.get('direction')=='positive' else 'lowered')}"
            f"</div>"
            f"<div style='display:flex;gap:10px;margin-top:6px;'>"
            f"<div class='g-muted' style='flex:1;'>{prior_label}</div>"
            f"<div class='g-muted' style='flex:1;'>{current_label}</div>"
            f"</div></div>")
    return None


def render():
    st.markdown(_css(), unsafe_allow_html=True)
    symbols = get_extraction_symbols()
    if not symbols:
        st.markdown(
            "<div class='g-muted'>No extractions "
            "stored yet. Run <code>python -m jobs.refresh_extraction_v3 "
            "--symbols TCS</code> to populate this tab.</div>",
            unsafe_allow_html=True)
        return

    st.markdown("<div class='g-muted' style='text-transform:uppercase;"
                "font-size:11px;letter-spacing:0.07em;'>Symbol</div>",
                unsafe_allow_html=True)
    symbol = st.pills("guidance_symbol", symbols, key="guidance_symbol",
                      label_visibility="collapsed", default=symbols[0])
    symbol = symbol or symbols[0]

    quarters = get_extraction_quarters(symbol)
    if not quarters:
        st.markdown(f"<div class='g-muted'>No stored quarters for {symbol}.</div>",
                    unsafe_allow_html=True)
        return

    labels = [_fmt_quarter(q) for q in quarters]
    st.markdown("<div class='g-muted' style='text-transform:uppercase;"
                "font-size:11px;letter-spacing:0.07em;margin-top:8px;'>Quarter</div>",
                unsafe_allow_html=True)
    chosen = st.pills("guidance_quarter", labels, key=f"guidance_q_{symbol}",
                      label_visibility="collapsed", default=labels[0])
    index = labels.index(chosen) if chosen in labels else 0
    row = quarters[index]
    quarter_end = row["quarter_end"]
    call_label = f"{symbol} {_fmt_quarter(row)} call"

    formal = get_guidance_formal(symbol, quarter_end)
    soft = get_guidance_soft(symbol, quarter_end)
    slots = get_sector_slots(symbol, quarter_end)
    risks = get_risks(symbol, quarter_end)
    verdict = get_verdict(symbol, quarter_end)
    commitments = get_commitments(symbol, quarter_end)
    promise_checks = get_promise_checks(symbol, quarter_end)
    has_prior = index + 1 < len(quarters)
    prior_row = quarters[index + 1] if has_prior else None
    prior_formal = get_guidance_formal(symbol, prior_row["quarter_end"]) if has_prior else {}
    prior_soft = get_guidance_soft(symbol, prior_row["quarter_end"]) if has_prior else []

    # ── header ───────────────────────────────────────────────────────────────
    st.markdown(
        f"<div style='display:flex;align-items:baseline;justify-content:"
        f"space-between;flex-wrap:wrap;gap:10px;margin-bottom:18px;'>"
        f"<span class='g-ticker'>{symbol}</span>"
        f"<span class='g-chip g-chip-ochre'>{_fmt_quarter(row)}"
        f"{' · ' + row['call_date'] if row.get('call_date') else ''}</span>"
        f"</div>", unsafe_allow_html=True)

    # a) VERDICT ─────────────────────────────────────────────────────────────
    if verdict and verdict.get("headline"):
        chips_html = "".join(_chip(c["label"], c["role"]) for c in
                             (verdict.get("chips") or []))
        support = (f"<div class='g-support'>{verdict['supporting_line']}</div>"
                  if verdict.get("supporting_line") else "")
        st.markdown(
            f"<div class='g-section'>{_section_label('VERDICT', '--accent')}"
            f"<div class='g-card-hi'>"
            f"<div class='g-headline'>{verdict['headline']}</div>{support}"
            f"<div style='margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;'>"
            f"{chips_html}</div></div></div>", unsafe_allow_html=True)

    # b) COMMITMENTS THIS CALL ───────────────────────────────────────────────
    if commitments or any((formal.get(k) or {}).get("status") in
                          ("GIVEN", "WITHDRAWN_OR_CHANGED") for k, _ in _FORMAL_FIELDS):
        st.markdown(_section_label("COMMITMENTS THIS CALL", "--blue"),
                   unsafe_allow_html=True)
        _commitments_card(commitments, formal, prior_formal, call_label)
        st.markdown("<div class='g-section'></div>", unsafe_allow_html=True)

    # c) PROMISES TESTED ─────────────────────────────────────────────────────
    if promise_checks:
        st.markdown(_section_label("PROMISES TESTED", "--green"),
                   unsafe_allow_html=True)
        shown = _promises_tested_card(promise_checks, _fmt_quarter(prior_row))
        if shown:
            st.markdown("<div class='g-section'></div>", unsafe_allow_html=True)

    # d) RISKS + ANALYST PUSHBACK ────────────────────────────────────────────
    has_risks = bool(risks)
    has_pushback = bool(row.get("pushback_total"))
    if has_risks or has_pushback:
        left_label = _section_label("RISKS MANAGEMENT RAISED", "--red") if has_risks else ""
        right_label = _section_label("ANALYST PUSHBACK", "--purple") if has_pushback else ""
        cols = st.columns(2, gap="medium") if (has_risks and has_pushback) else [st.container()]
        if has_risks and has_pushback:
            with cols[0]:
                st.markdown(left_label, unsafe_allow_html=True)
                _risks_card(risks)
            with cols[1]:
                st.markdown(right_label, unsafe_allow_html=True)
                _pushback_card(row, call_label)
        elif has_risks:
            st.markdown(left_label, unsafe_allow_html=True)
            _risks_card(risks)
        else:
            st.markdown(right_label, unsafe_allow_html=True)
            _pushback_card(row, call_label)
        st.markdown("<div class='g-section'></div>", unsafe_allow_html=True)

    # e) SEGMENT SCORECARD ───────────────────────────────────────────────────
    given_slots = [s for s in slots if s.get("status") == "GIVEN" and s.get("value_text")]
    if given_slots:
        st.markdown(_section_label("SEGMENT SCORECARD", "--teal"),
                   unsafe_allow_html=True)
        _segment_scorecard(symbol, slots, quarter_end)
        st.markdown("<div class='g-section'></div>", unsafe_allow_html=True)

    # f) ACTUALS VS ESTIMATES ────────────────────────────────────────────────
    surprise = get_estimate_surprise(symbol, quarter_end)
    usable_surprise = [s for s in surprise if s.get("actual_value") is not None]
    if usable_surprise:
        st.markdown(_section_label("ACTUALS VS ESTIMATES", "--ochre"),
                   unsafe_allow_html=True)
        _actuals_vs_estimates(surprise)
        st.markdown("<div class='g-section'></div>", unsafe_allow_html=True)

    # g) FORWARD CALENDAR + LANGUAGE DELTA ───────────────────────────────────
    calendar_html = _forward_calendar(symbol)
    delta_html = (_language_delta(soft, prior_soft, _fmt_quarter(prior_row),
                                  _fmt_quarter(row)) if has_prior else None)
    if calendar_html or delta_html:
        if calendar_html and delta_html:
            lcol, rcol = st.columns(2, gap="medium")
            with lcol:
                st.markdown(_section_label("FORWARD CALENDAR", "--blue"),
                           unsafe_allow_html=True)
                st.markdown(calendar_html, unsafe_allow_html=True)
            with rcol:
                st.markdown(_section_label("LANGUAGE DELTA", "--purple"),
                           unsafe_allow_html=True)
                st.markdown(delta_html, unsafe_allow_html=True)
        elif calendar_html:
            st.markdown(_section_label("FORWARD CALENDAR", "--blue"),
                       unsafe_allow_html=True)
            st.markdown(calendar_html, unsafe_allow_html=True)
        else:
            st.markdown(_section_label("LANGUAGE DELTA", "--purple"),
                       unsafe_allow_html=True)
            st.markdown(delta_html, unsafe_allow_html=True)
        st.markdown("<div class='g-section'></div>", unsafe_allow_html=True)

    # h) footer ──────────────────────────────────────────────────────────────
    model = row.get("stage2_model") or "—"
    st.markdown(
        f"<div class='g-divider g-muted'>Extracted by "
        f"{row.get('extractor_version')} via {model}. Transcript published "
        f"{(row.get('source_published_at') or '')[:16]}; results announced "
        f"{(row.get('announcement_at') or '')[:16]}. A transcript is "
        f"published after the print, so nothing on this tab was knowable at "
        f"the moment of the results move.</div>", unsafe_allow_html=True)
