"""Pure logic for the Calendar subtab's HISTORY 6Q sparkline (13 Oct 2026
spec). Takes a 6-quarter value window, returns block levels, QoQ colours
and a single-value tooltip per quarter - no HTML, no Streamlit, no network,
so it's fixture-testable and the HTML-building code in tabs/events.py stays
thin.

SCALING: min-max over whatever values are present in the window, oldest to
newest. "Signed min-max" in the spec is just this - plain min()/max() over
the raw (possibly negative) values already puts the most negative at the
bottom block and the largest at the top; there is no separate negative-
handling branch needed. A flat window (every present value equal) has no
shape to show, so every block renders at the middle level rather than
arbitrarily full or empty.

Missing quarters (None in the input) never interpolate and never affect
the min/max - a gap is absent from the window's story, not a zero in it.
"""

from dataclasses import dataclass

#: Oldest (level 1) to newest/tallest (level 8). Index 0 unused - levels
#: are 1-indexed to read naturally against "8 levels" in the spec.
BLOCKS = (None, "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")
MID_LEVEL = 4  # used for every block when the window has no variation at all


@dataclass
class Block:
    quarter_end: str      # ISO date
    value: float | None   # None = gap, render blank
    char: str | None      # one of BLOCKS, or None for a gap
    direction: str        # "up" | "down" | "flat" | "none" (none = no prior to compare, or a gap on either side)
    is_negative: bool     # this quarter's own value is < 0


def _level_for(value, lo, hi):
    """value -> block level (1-8). Flat window (lo == hi) maps every
    present value to MID_LEVEL - no variation to show, so no block should
    look extremal by accident."""
    if lo == hi:
        return MID_LEVEL
    frac = (value - lo) / (hi - lo)
    return 1 + round(frac * 7)  # 1..8


def build_blocks(window):
    """window: list of (quarter_end_iso, value_or_None), oldest first.
    Returns a list[Block], same order and length."""
    present = [v for _, v in window if v is not None]
    lo, hi = (min(present), max(present)) if present else (None, None)

    blocks = []
    prev_value = None
    for qe, value in window:
        if value is None:
            blocks.append(Block(qe, None, None, "none", False))
            prev_value = None  # a gap breaks the QoQ chain, doesn't fake-carry
            continue
        level = _level_for(value, lo, hi)
        char = BLOCKS[level]
        if prev_value is None:
            direction = "none"
        elif value > prev_value:
            direction = "up"
        elif value < prev_value:
            direction = "down"
        else:
            direction = "flat"
        blocks.append(Block(qe, value, char, direction, value < 0))
        prev_value = value
    return blocks


def qoq_pct(current, previous):
    """(current - previous) / |previous| * 100, or None if either is
    missing or previous is zero (nothing to express the change as a
    fraction of)."""
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous) * 100


def tooltip_text(quarter_label, value, qoq, unit="cr"):
    """'QE Jun 2026: 72,275 cr (+1.8% QoQ)' - or without the QoQ clause
    when there's no prior quarter to compare against (oldest in the
    window, or a gap immediately before this one)."""
    value_text = f"{value:,.0f} {unit}"
    if qoq is None:
        return f"{quarter_label}: {value_text}"
    return f"{quarter_label}: {value_text} ({qoq:+.1f}% QoQ)"
