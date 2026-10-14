-- Schema for the Week 5-6 Earnings call sentiment tracker (see
-- CLAUDE.md). One row per (ticker, quarter): Track A's deterministic
-- Loughran-McDonald word counts plus Track B's Gemini-extracted
-- management/Q&A tone, both computed from the same NSE-filed transcript
-- (see nse_transcripts.py).
--
-- TWO DEVIATIONS FROM THE SPEC'S LITERAL STORAGE LIST, both deliberate:
--
-- 1. `quarter_end` (ISO date), not a free-text "quarter" label. Every
--    other table this app writes (earnings_estimates, earnings_history_
--    quarters) keys on an ISO quarter-end date, not a human label like
--    "Q1 FY2027" - using the same convention here keeps this table
--    joinable against those without a label-parsing step. If a display
--    label is wanted later, it is one `strftime` away from this value;
--    going the other direction (parsing "Q1 FY2027" back into a date)
--    is the harder, lossier conversion, so the date is the right thing
--    to store.
--
-- 2. `lm_litigious` is added despite the spec's storage section listing
--    only lm_positive/negative/uncertainty - its own Method section
--    requires computing all four LM categories ("positive, negative,
--    uncertainty, litigious wordlists"), so dropping litigious from
--    storage would silently throw away a number the spec itself asked
--    for.
--
-- mgmt_tone/qa_tone are Track B's separately-scored tone_score
-- (-2..+2) for management commentary and the analyst Q&A - the spec's
-- explicit point that "Q&A tone divergence from management tone is
-- itself a signal worth storing" is exactly why these are two columns,
-- not one. guidance is a single column (raised/maintained/lowered/none)
-- sourced from the management-commentary extraction, where guidance
-- language actually lives in these transcripts (see nse_transcripts.py's
-- Q&A-format discovery notes) - not duplicated per track.

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
    mgmt_tone              REAL,                  -- Track B, -2..+2, management commentary
    qa_tone                REAL,                  -- Track B, -2..+2, analyst Q&A
    guidance               TEXT,                  -- "raised" | "maintained" | "lowered" | "none"
    extraction_json        TEXT NOT NULL,          -- full Track B structured output, both tracks, for audit
    created_at             TEXT NOT NULL,         -- full ISO timestamp, IST
    UNIQUE (ticker, quarter_end)
);

CREATE INDEX IF NOT EXISTS idx_earnings_call_sentiment_ticker
    ON earnings_call_sentiment (ticker, quarter_end);
