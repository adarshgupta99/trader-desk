"""Track A of the earnings-call sentiment tracker (Week 5-6 spec): the
Loughran-McDonald deterministic word-count baseline. Pure - no network,
no model, fully reproducible on the same input text.

Wordlists: data/lm_dictionary.json, built 14 Oct 2026 from the LM Master
Dictionary (Notre Dame SRAF, https://sraf.nd.edu/loughranmcdonald-master-
dictionary/, "free for use in academic research" - this is a solo,
learning-focused build, see CLAUDE.md). Updated: March 2026 edition.
2,355 negative / 354 positive / 297 uncertainty / 905 litigious words,
extracted from the published ~86,500-word master list down to just the
four category wordlists the spec calls for - the raw file carries a lot
of other per-word statistics (syllable counts, document frequency, three
more categories) this feature has no use for.
"""

import functools
import json
import os
import re

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                     "data", "lm_dictionary.json")

CATEGORIES = ("positive", "negative", "uncertainty", "litigious")

#: LM's own tokenization convention: uppercase words, letters only (their
#: source corpus is SEC filings, already English prose) - matches how the
#: wordlists themselves are cased, so counting must uppercase the input
#: the same way or every match silently misses.
_WORD_RE = re.compile(r"[A-Za-z]+")


@functools.lru_cache(maxsize=1)
def _wordlists():
    with open(_PATH) as f:
        raw = json.load(f)
    return {cat: frozenset(raw[cat]) for cat in CATEGORIES}


def count_words(text):
    """{category: count} for each of the four LM categories, plus
    'total_words' - the raw word count the counts are normalized against.
    Pure string processing: tokenize on runs of letters, uppercase, look
    up each token in each category's wordlist. A word in more than one
    category (rare, but the dictionary allows it) counts in each."""
    tokens = [t.upper() for t in _WORD_RE.findall(text or "")]
    lists = _wordlists()
    counts = {cat: sum(1 for t in tokens if t in lists[cat]) for cat in CATEGORIES}
    counts["total_words"] = len(tokens)
    return counts


def per_1000_words(counts):
    """Each category count normalized per 1000 words, per the spec
    ("report counts normalized per 1000 words") - makes a short Q&A
    segment and a long management monologue comparable. 0.0 for every
    category when total_words is 0, rather than dividing by zero."""
    total = counts.get("total_words", 0)
    if total == 0:
        return {cat: 0.0 for cat in CATEGORIES}
    return {cat: counts[cat] / total * 1000 for cat in CATEGORIES}
