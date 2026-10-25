"""Promised-vs-delivered comparison logic for the Guidance tab (spec 6c /
6.5), factored out of tabs/guidance.py so the SAME computation feeds both the
UI's "Promises Tested" table and the verdict-synthesis stage's input facts
(jobs/refresh_extraction_v3.py). Two call sites computing this independently
would drift the moment one of them changed; this is the single source of
truth.

Pure, deliberately: takes already-fetched dicts, does no I/O itself. The job
and the UI fetch those dicts through different paths (the job reads
events.db directly mid-backfill, when a write earlier in the same run may not
be visible through events_db's @st.cache_data layer yet; the UI goes through
events_db's cached reads as usual) - this function doesn't need to know or
care which. Returns plain dicts, not HTML; the UI decides how to render a
verdict string into a colour, this module only decides what the verdict IS.
"""

#: The four generic guidance fields, in display order (spec 6b).
FORMAL_FIELDS = [
    ("guidance_revenue", "Revenue"),
    ("guidance_margin", "Operating margin"),
    ("guidance_capex", "Capex"),
    ("guidance_orderbook", "Order book"),
]


def promised_vs_delivered(prior_formal, current_slots, prior_quarter_label):
    """Checks the PRIOR quarter's formal guidance against the CURRENT
    quarter's sector slots.

    prior_formal: {field_key: row} from the prior quarter's
        earnings_guidance_formal, as events_db.get_guidance_formal returns.
    current_slots: {slot_key: row} from the CURRENT quarter's
        earnings_sector_slots, as events_db.get_sector_slots returns
        (keyed by slot_key - the caller builds this dict from the list).
    prior_quarter_label: display label for the prior quarter (e.g. its
        fiscal_quarter), carried through for the UI/facts to cite.

    Returns a list of dicts, one per formal field:
        {field_key, label, promised_text, period, prior_quarter_label,
         delivered, verdict, source}
    verdict is one of: NO_PROMISE_TO_TEST, NOT_COMPARABLE, IN_BAND, ABOVE,
    BELOW. Only a numeric promise with a comparable spoken actual gets
    IN_BAND/ABOVE/BELOW - everything else is a named state, never guessed.
    """
    results = []
    for key, label in FORMAL_FIELDS:
        promise = prior_formal.get(key) or {}
        if promise.get("status") not in ("GIVEN", "WITHDRAWN_OR_CHANGED"):
            results.append({
                "field_key": key, "label": label,
                "promised_text": None, "period": None,
                "prior_quarter_label": prior_quarter_label,
                "delivered": None, "verdict": "NO_PROMISE_TO_TEST",
                "source": None,
            })
            continue

        # Margin is the one field with a routinely comparable spoken actual
        # (the sector cards carry an ebit_margin slot for most companies
        # that give margin guidance). Anything else needs a basis match this
        # desk cannot assert yet, so it stays NOT_COMPARABLE rather than a
        # guessed verdict.
        slot = current_slots.get("ebit_margin") if key == "guidance_margin" else None
        if slot and slot.get("status") == "GIVEN" and slot.get("value_num") is not None:
            low, high = promise.get("value_num_low"), promise.get("value_num_high")
            actual = slot["value_num"]
            delivered = slot.get("value_text") or str(actual)
            source = "this quarter's call"
            if low is not None and high is not None:
                verdict = ("IN_BAND" if low <= actual <= high
                          else "ABOVE" if actual > high else "BELOW")
            else:
                verdict = "NOT_COMPARABLE"
        else:
            delivered, source, verdict = None, None, "NOT_COMPARABLE"

        results.append({
            "field_key": key, "label": label,
            "promised_text": promise.get("value_text"),
            "period": promise.get("applies_to_period"),
            "prior_quarter_label": prior_quarter_label,
            "delivered": delivered, "verdict": verdict, "source": source,
        })
    return results
