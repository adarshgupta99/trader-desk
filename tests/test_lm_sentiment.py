"""Fixture tests for analytics/lm_sentiment.py - pure, no network.

Uses real words confirmed present in data/lm_dictionary.json rather than
invented ones, so a test failure means the counting logic broke, not
that a guessed word was never in the list to begin with.
"""

import pytest

from analytics.lm_sentiment import CATEGORIES, count_words, per_1000_words, _wordlists


def test_known_words_are_actually_in_their_categories():
    # Sanity check on the data file itself before trusting any count
    # built on top of it.
    lists = _wordlists()
    assert "STRONG" in lists["positive"]
    assert "LOSS" in lists["negative"]
    assert "UNCERTAIN" in lists["uncertainty"]
    assert "LITIGATION" in lists["litigious"]


def test_count_words_tokenizes_and_uppercases():
    # Mixed case, punctuation - must still match.
    text = "Our results were strong, strong, and the outlook is UNCERTAIN."
    counts = count_words(text)
    assert counts["positive"] == 2    # "strong" twice
    assert counts["uncertainty"] == 1  # "UNCERTAIN"
    assert counts["total_words"] == 10  # Our/results/were/strong/strong/and/the/outlook/is/UNCERTAIN


def test_count_words_on_empty_text():
    counts = count_words("")
    assert counts["total_words"] == 0
    assert all(counts[c] == 0 for c in CATEGORIES)


def test_count_words_on_none_is_safe():
    # A chunk with nothing extracted (e.g. the Q&A split found no match)
    # must not crash the pipeline.
    counts = count_words(None)
    assert counts["total_words"] == 0


def test_per_1000_words_normalizes():
    # 2 positive words in a 100-word text -> 20 per 1000.
    text = "strong " * 2 + "word " * 98
    counts = count_words(text)
    assert counts["total_words"] == 100
    norm = per_1000_words(counts)
    assert norm["positive"] == pytest.approx(20.0)


def test_per_1000_words_handles_zero_total_without_division_error():
    counts = {"positive": 0, "negative": 0, "uncertainty": 0, "litigious": 0, "total_words": 0}
    norm = per_1000_words(counts)
    assert all(v == 0.0 for v in norm.values())


def test_a_word_in_two_categories_counts_in_both():
    # The dictionary allows overlap (rare but real) - counting logic
    # must not assume mutual exclusivity.
    lists = _wordlists()
    overlap = lists["negative"] & lists["uncertainty"]
    if overlap:
        word = next(iter(overlap))
        counts = count_words(word)
        assert counts["negative"] == 1
        assert counts["uncertainty"] == 1
