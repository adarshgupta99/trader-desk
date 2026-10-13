"""Fixture tests for analytics/sparkline.py - pure, no network."""

import pytest

from analytics.sparkline import BLOCKS, build_blocks, qoq_pct, tooltip_text


def _w(*vals):
    """[(q1, v1), (q2, v2), ...] with throwaway ISO-looking labels,
    oldest first - matches build_blocks' expected shape."""
    return [(f"2025-{i+1:02d}-30", v) for i, v in enumerate(vals)]


def test_lowest_and_highest_map_to_extreme_blocks():
    blocks = build_blocks(_w(10, 20, 30, 40, 50, 60))
    assert blocks[0].char == BLOCKS[1]   # lowest -> shortest
    assert blocks[-1].char == BLOCKS[8]  # highest -> tallest


def test_flat_window_uses_the_middle_block_for_every_value():
    # No variation to show - shouldn't look arbitrarily full or empty.
    blocks = build_blocks(_w(50, 50, 50, 50, 50, 50))
    assert all(b.char == BLOCKS[4] for b in blocks)


def test_negative_values_scale_like_any_other_value():
    # "Signed min-max" in the spec is just min-max over the raw values -
    # the most negative number is the new floor, nothing special.
    blocks = build_blocks(_w(-50, -10, 0, 10, 30, 60))
    assert blocks[0].char == BLOCKS[1]    # -50, the minimum
    assert blocks[-1].char == BLOCKS[8]   # 60, the maximum
    assert blocks[0].is_negative is True
    assert blocks[2].is_negative is False  # 0 is not negative


def test_gap_renders_as_no_block_and_breaks_the_qoq_chain():
    blocks = build_blocks(_w(10, 20, None, 40, 50, 60))
    assert blocks[2].char is None
    assert blocks[2].direction == "none"
    # The quarter AFTER a gap has no real prior value to compare against -
    # it must not silently compare to the quarter before the gap.
    assert blocks[3].direction == "none"


def test_missing_quarters_dont_affect_the_scale():
    # Two gaps either side of a flat trio - min/max come only from the
    # three real values, which are themselves equal (flat).
    blocks = build_blocks(_w(None, 50, 50, 50, None, None))
    present = [b for b in blocks if b.char is not None]
    assert all(b.char == BLOCKS[4] for b in present)


def test_qoq_direction_up_down_flat():
    blocks = build_blocks(_w(100, 150, 150, 120))
    assert blocks[1].direction == "up"
    assert blocks[2].direction == "flat"
    assert blocks[3].direction == "down"


def test_oldest_quarter_has_no_direction():
    blocks = build_blocks(_w(10, 20, 30))
    assert blocks[0].direction == "none"


def test_all_gaps_is_handled_without_crashing():
    blocks = build_blocks(_w(None, None, None, None, None, None))
    assert all(b.char is None for b in blocks)
    assert all(b.direction == "none" for b in blocks)


# --- qoq_pct -----------------------------------------------------------

@pytest.mark.parametrize("current,previous,expected", [
    (110, 100, 10.0),
    (90, 100, -10.0),
    (-80, -100, 20.0),   # loss shrinking is a positive QoQ move
    (100, None, None),
    (None, 100, None),
    (100, 0, None),      # can't express a % change against zero
])
def test_qoq_pct(current, previous, expected):
    got = qoq_pct(current, previous)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


# --- tooltip_text --------------------------------------------------------

def test_tooltip_includes_qoq_when_present():
    text = tooltip_text("QE Jun 2026", 72275, 1.8)
    assert text == "QE Jun 2026: 72,275 cr (+1.8% QoQ)"


def test_tooltip_omits_qoq_clause_when_none():
    text = tooltip_text("QE Mar 2025", 50000, None)
    assert text == "QE Mar 2025: 50,000 cr"
    assert "QoQ" not in text


# --- height_px: the continuous bar-height rendering pass (13 Oct 2026) ----

def test_height_px_uses_the_full_range_at_the_extremes():
    from analytics.sparkline import MAX_PX, MIN_PX
    blocks = build_blocks(_w(10, 20, 30, 40, 50, 60))
    assert blocks[0].height_px == pytest.approx(MIN_PX)
    assert blocks[-1].height_px == pytest.approx(MAX_PX)


def test_height_px_is_the_midpoint_on_a_flat_window():
    from analytics.sparkline import MAX_PX, MIN_PX
    blocks = build_blocks(_w(50, 50, 50, 50, 50, 50))
    mid = (MIN_PX + MAX_PX) / 2
    assert all(b.height_px == pytest.approx(mid) for b in blocks)


def test_height_px_is_none_for_a_gap():
    blocks = build_blocks(_w(10, None, 30))
    assert blocks[1].height_px is None


def test_height_px_and_char_never_disagree_on_direction():
    # Both are derived from the identical lo/hi scale, so a STRICTLY
    # taller bar can never map to a LOWER block level - they may tie at
    # the same level (char quantizes to 8 steps, height_px doesn't), but
    # never invert.
    blocks = build_blocks(_w(5, 40, 15, 60, 25, 10))
    for a in blocks:
        for b in blocks:
            if a.height_px < b.height_px:
                assert BLOCKS.index(a.char) <= BLOCKS.index(b.char)
