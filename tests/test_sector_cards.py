"""Config-integrity and resolution tests for sector_cards.py (spec 3.3/3.4).

Pure and offline - reads data/sector_cards.json and fno_sectors.json, both
committed. No network, no database.

The integrity tests matter more than usual here because the slot-card config
is edited by hand whenever a sector card is added, and a typo in a card key
would silently turn a mapped sector into the universal-only path rather than
raising - exactly the kind of quiet degradation the spec's NOT_GIVEN-is-
first-class discipline is meant to prevent.
"""

import fno
import sector_cards


# --- config integrity --------------------------------------------------------

def test_every_display_group_has_an_explicit_mapping():
    # Every one of fno's display groups must appear in group_to_card, even if
    # it maps to null. A group missing entirely would resolve to None by
    # accident rather than by decision.
    mapped = set(sector_cards._data()["group_to_card"])
    assert mapped == set(fno.DISPLAY_GROUPS)


def test_every_referenced_card_key_exists():
    cards = sector_cards._data()["cards"]
    for group, key in sector_cards._data()["group_to_card"].items():
        if key is not None:
            assert key in cards, f"{group} points at missing card {key!r}"


def test_every_card_is_reachable_from_some_group():
    # A card nothing maps to is dead config - either a typo or a leftover.
    referenced = {k for k in sector_cards._data()["group_to_card"].values() if k}
    assert referenced == set(sector_cards.all_card_keys())


def test_slot_keys_unique_within_each_card():
    for key in sector_cards.all_card_keys():
        slots = sector_cards.card(key)["slots"]
        keys = [s["key"] for s in slots]
        assert len(keys) == len(set(keys)), f"duplicate slot key in {key}"


def test_every_slot_has_label_and_capture_text():
    # `capture` is injected into the extraction prompt (spec 3.6); a slot
    # without it would ask the model for a bare key name.
    for key in sector_cards.all_card_keys():
        for slot in sector_cards.card(key)["slots"]:
            assert slot.get("label"), f"{key}/{slot['key']} has no label"
            assert slot.get("capture"), f"{key}/{slot['key']} has no capture text"


def test_nine_cards_as_per_cheat_sheet():
    # The cheat sheet defines exactly nine sector cards and Adarsh chose the
    # spec's default of adding none beyond it (3.5). A tenth card appearing
    # here should be a deliberate decision, so this test is the tripwire.
    assert len(sector_cards.all_card_keys()) == 9


# --- resolution --------------------------------------------------------------

def test_mapped_symbols_resolve_to_expected_cards():
    assert sector_cards.card_key_for_symbol("TCS") == "it_services"
    assert sector_cards.card_key_for_symbol("HDFCBANK") == "private_banks"
    assert sector_cards.card_key_for_symbol("MARUTI") == "autos"
    assert sector_cards.card_key_for_symbol("SUNPHARMA") == "pharma"
    assert sector_cards.card_key_for_symbol("TATASTEEL") == "metals"


def test_bank_primary_group_wins_over_financial_services():
    # Banks carry both 'Banks' and 'Financial Services' as display groups.
    # DISPLAY_GROUPS order puts Banks first, so the bank card must win - a
    # bank resolving to the NBFC card would ask for AUM growth instead of NIM.
    assert "Financial Services" in fno.display_groups_of("HDFCBANK")
    assert sector_cards.card_key_for_symbol("HDFCBANK") == "private_banks"


def test_psu_banks_use_the_private_bank_card_with_a_flag():
    # Adarsh, 16 Oct 2026: same metrics, PSU recorded as metadata.
    assert sector_cards.card_key_for_symbol("SBIN") == "private_banks"
    assert sector_cards.bank_type("SBIN") == "PSU"
    assert sector_cards.bank_type("PNB") == "PSU"
    assert sector_cards.bank_type("HDFCBANK") == "PRIVATE"
    assert sector_cards.bank_type("TCS") is None


def test_uncovered_sectors_run_universal_only():
    # Energy has no cheat-sheet card, so Reliance is universal-only. This is
    # the spec's UNMAPPED path (3.4), a first-class outcome, not a failure.
    assert sector_cards.primary_group("RELIANCE") == "Energy"
    assert sector_cards.card_key_for_symbol("RELIANCE") is None
    assert sector_cards.is_unmapped("RELIANCE") is True
    assert sector_cards.slots_for_symbol("RELIANCE") == []


def test_unknown_symbol_is_universal_only_not_an_error():
    assert sector_cards.primary_group("NOT_A_REAL_SYMBOL") == ""
    assert sector_cards.is_unmapped("NOT_A_REAL_SYMBOL") is True
    assert sector_cards.slots_for_symbol("NOT_A_REAL_SYMBOL") == []


def test_it_slots_match_the_cheat_sheet_list():
    # The IT card is the one validated end-to-end against the TCS reference
    # set, so its slot list is pinned.
    assert sector_cards.slot_keys_for_symbol("TCS") == [
        "cc_revenue_growth_qoq", "cc_revenue_growth_yoy", "tcv_deal_wins",
        "book_to_bill", "ebit_margin", "ebit_margin_vs_guided_band",
        "attrition_ltm", "net_headcount_adds", "fy_guidance_band",
    ]


def test_add_question_slots_are_absent():
    # Spec 3.5's ADD? proposals were declined (default: none added).
    assert "ai_revenue_disclosed" not in sector_cards.slot_keys_for_symbol("TCS")
    assert "capital_ratios" not in sector_cards.slot_keys_for_symbol("HDFCBANK")


def test_every_fno_symbol_resolves_without_error():
    # The whole universe must resolve to either a card or universal-only, with
    # no exceptions thrown - the backfill walks all 213.
    mapped = 0
    for symbol in fno.symbols():
        key = sector_cards.card_key_for_symbol(symbol)
        assert key is None or key in sector_cards.all_card_keys()
        mapped += key is not None
    # Sanity floor: the mapped groups cover a large share of the universe.
    assert mapped > 100
