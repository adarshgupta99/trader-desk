-- Schema for the Week 5-6 Earnings call sentiment tracker (see
-- CLAUDE.md and earnings_sentiment.py). One row per (ticker, quarter):
-- Track A's deterministic Loughran-McDonald word counts plus Track B's
-- Gemini-extracted call analysis, both computed from the same
-- NSE-filed transcript (see nse_transcripts.py).
--
-- REBUILT 14 Oct 2026 for Adarsh's richer Track B spec (evidence-quoted
-- extraction: guidance/margin/demand/hedging/capital-allocation/
-- one-offs/confidence/analyst-tone/evasiveness/divergence/overall-tone,
-- all from ONE Gemini call per transcript instead of two role-scoped
-- calls). Only 8 RELIANCE rows existed under the old shape and were
-- test data from the first live run, not anything downstream depends
-- on, so the table is redefined rather than migrated in place.
--
-- REBUILT AGAIN 15 Oct 2026: `qa_tone` replaces `qa_tone_proxy`.
-- qa_tone_proxy was a fixed lookup (constructive=+2/neutral=0/
-- skeptical=-2) derived from the 3-value analyst_tone category, added
-- because the spec only asked for that category, not a number. It
-- looked wrong once real data came in: every "constructive" quarter
-- plotted at the identical +2.0, flattening what should have been a
-- varying line. Fixed at the source instead - Gemini is now asked for
-- `analyst_tone.score` directly, same -2..+2 scale as
-- management_confidence (see earnings_sentiment.py's schema) - so
-- `qa_tone` is a real model judgment now, not a derived constant.
--
-- DEVIATIONS FROM THE SPEC'S LITERAL STORAGE LIST, both deliberate:
--
-- 1. `quarter_end` (ISO date), not a free-text "quarter" label - same
--    reasoning as before: every other table this app writes keys on an
--    ISO quarter-end date, so this stays joinable against them.
--
-- 2. `lm_litigious` is added despite the earlier spec's storage section
--    listing only lm_positive/negative/uncertainty - the Method section
--    requires computing all four LM categories, so dropping litigious
--    from storage would silently throw away a number already computed.
--
-- `management_confidence`, `overall_tone`, and `qa_tone` are all
-- Track B's own -2..+2 scores - management commentary, the whole call,
-- and the Q&A session respectively.
--
-- `guidance` and `analyst_tone` (the category, not the score) are
-- single columns pulled from the extraction for the table/filtering;
-- dodged-question detail and every quote/evidence/rationale field
-- lives only in extraction_json.

CREATE TABLE IF NOT EXISTS earnings_call_sentiment (
    id                     INTEGER PRIMARY KEY,
    ticker                 TEXT NOT NULL,         -- bare NSE symbol, no .NS suffix
    quarter_end            TEXT NOT NULL,         -- YYYY-MM-DD, the quarter the call discusses
    call_date              TEXT NOT NULL,         -- YYYY-MM-DD, when the call/transcript was filed
    transcript_source_url  TEXT NOT NULL,         -- the exact NSE filing parsed, for audit
    lm_positive            REAL,                  -- Track A, per 1000 words
    lm_negative            REAL,
    lm_uncertainty         REAL,
    lm_litigious           REAL,
    lm_total_words         INTEGER,               -- the word count the per-1000 figures are normalized against
    management_confidence  REAL,                  -- Track B, -2..+2
    overall_tone           REAL,                  -- Track B, -2..+2, whole call
    qa_tone                REAL,                  -- Track B, -2..+2, the Q&A session itself
    guidance               TEXT,                  -- "raised" | "maintained" | "lowered" | "withheld" | "not_given"
    analyst_tone           TEXT,                  -- "skeptical" | "neutral" | "constructive"
    evasiveness_count      INTEGER,               -- number of dodged questions Gemini identified
    qa_divergence_flag     INTEGER,               -- 0/1, prepared-remarks vs Q&A contradiction flagged
    extraction_json        TEXT NOT NULL,         -- full Track B structured output, for audit
    created_at             TEXT NOT NULL,         -- full ISO timestamp, IST
    UNIQUE (ticker, quarter_end)
);

CREATE INDEX IF NOT EXISTS idx_earnings_call_sentiment_ticker
    ON earnings_call_sentiment (ticker, quarter_end);
