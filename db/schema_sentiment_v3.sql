-- Storage for the sector-aware extraction / guidance / surprise build
-- (spec: trader-desk-sector-aware-extraction-guidance-backfill-surprise-spec,
-- section 9 step 1). Applied to the existing events.db, same file every other
-- events feature already owns. Purely additive: nothing here touches `events`,
-- `earnings_estimates`, `earnings_move_*`, `earnings_history_quarters`, or the
-- existing `earnings_call_sentiment`.
--
-- WHY NEW TABLES INSTEAD OF MUTATING earnings_call_sentiment: that table holds
-- 265 rows the live Sentiment tab reads right now, written by the current
-- single-call extractor. The v3 pipeline is a different extraction (two-stage,
-- numbered exchanges, graded divergence, numeric analyst tone, cite-grounded
-- everything) and the spec's own build order proves it on TCS (step 5) and
-- builds the UI (step 6) before any backfill (step 7). Running the two side by
-- side until v3 is proven is what keeps the app working the whole way through;
-- the old table is retired in a later step, not in this one.
--
-- LONG FORMAT for frame / sector slots / guidance: spec 3.7 asks for it on the
-- sector block ("so adding a sector card never changes the schema") and the
-- same reasoning applies to the frame and guidance fields - a new field is a
-- config addition, not a migration. One row per (symbol, quarter, key).
--
-- WHAT LIVES WHERE: structured rows live here, in the committed events.db,
-- same as every other feature in this app (Adarsh's call, 15 Oct 2026,
-- overriding the spec's "dataset outside the repo" default). The raw PDF bytes
-- and the cleaned transcript text do NOT - they go to a gitignored local
-- archive directory (spec 7.5: "save the original PDF bytes untouched to a
-- local archive directory (not the repo)"), and only their SHA-256s and paths
-- are stored here. That keeps the committed binary from growing by ~100MB of
-- transcript text across a 213 x 6 backfill while leaving every stored quote
-- self-sufficient for display (the quote text itself is on the row; the
-- offsets exist for verification at extraction time, not for rendering).
--
-- TIMESTAMP DISCIPLINE (spec section 5): every feature row carries
-- announcement_at / source_published_at / feature_available_at plus
-- extracted_at, and QA check 6 fails the build if any row is missing one.
-- They are stored, never derived at read time, and source_published_at is
-- NEVER defaulted to the announcement time - a row whose transcript publish
-- time could not be established carries timestamp_quality='UNKNOWN' and is
-- excluded from any time-sensitive use.

-- ── Source provenance ────────────────────────────────────────────────────────
-- One row per transcript document actually fetched. The SHA-256 of the
-- original bytes is the identity of the source for the whole pipeline (spec
-- 7.5) and is half of the idempotency key in 7.4.
CREATE TABLE IF NOT EXISTS earnings_transcript_sources (
    id                      INTEGER PRIMARY KEY,
    symbol                  TEXT NOT NULL,          -- bare NSE symbol, no .NS
    quarter_end             TEXT NOT NULL,          -- YYYY-MM-DD, the quarter the call discusses
    fiscal_quarter          TEXT,                   -- e.g. 'Q2 FY27', as stated in the filing (spec 7.5)
    pdf_url                 TEXT NOT NULL,          -- the NSE attachment URL actually fetched
    pdf_sha256              TEXT NOT NULL,          -- of the original bytes, untouched
    cleaned_text_sha256     TEXT,                   -- of the cover-stripped text the LLM saw
    pdf_archive_path        TEXT,                   -- local archive path (gitignored), not a repo path
    cleaned_text_path       TEXT,
    pdf_bytes               INTEGER,
    http_status             INTEGER,
    fetched_at              TEXT NOT NULL,          -- ISO IST, when we pulled it
    transcript_available    INTEGER NOT NULL DEFAULT 1,  -- 0 when NSE has no transcript filing for this quarter
    pdf_unreadable          INTEGER NOT NULL DEFAULT 0,  -- 1 when the PDF fetched but could not be parsed
    announcement_at         TEXT,                   -- results filing instant (spec 5)
    announcement_at_precision TEXT,                 -- 'instant' | 'date'
    source_published_at     TEXT,                   -- when NSE published THIS transcript
    feature_available_at    TEXT,                   -- earliest a participant could have known it
    timestamp_quality       TEXT NOT NULL DEFAULT 'OK',  -- 'OK' | 'UNKNOWN'
    UNIQUE (symbol, quarter_end, pdf_sha256)
);

CREATE INDEX IF NOT EXISTS idx_transcript_sources_symbol
    ON earnings_transcript_sources (symbol, quarter_end);

-- ── Stage 1: structural segmentation ─────────────────────────────────────────
-- The numbered exchanges every stage-2 judgement cites (spec 2.2). Kept so a
-- cite can be resolved back to its speaker and offsets without re-running the
-- segmentation, and so the evidence drill-down in the UI has something to
-- point at. Text itself is NOT stored here (it is in the archived cleaned
-- text); offsets locate it.
CREATE TABLE IF NOT EXISTS earnings_call_exchanges (
    id                  INTEGER PRIMARY KEY,
    symbol              TEXT NOT NULL,
    quarter_end         TEXT NOT NULL,
    exchange_ref        TEXT NOT NULL,      -- 'P1', 'P2' (prepared) or 'Q1', 'Q2' (Q&A), stable per call
    kind                TEXT NOT NULL CHECK (kind IN ('prepared', 'qa')),
    seq                 INTEGER NOT NULL,   -- ordering within the call
    analyst_name        TEXT,               -- Q&A only
    analyst_firm        TEXT,               -- Q&A only
    management_speakers TEXT,               -- JSON array of names answering
    char_start          INTEGER NOT NULL,
    char_end            INTEGER NOT NULL,
    extractor_version   TEXT NOT NULL,
    source_pdf_sha256   TEXT NOT NULL,
    cleaned_text_sha256 TEXT NOT NULL,
    extracted_at        TEXT NOT NULL,
    UNIQUE (symbol, quarter_end, exchange_ref, extractor_version)
);

CREATE INDEX IF NOT EXISTS idx_call_exchanges_symbol
    ON earnings_call_exchanges (symbol, quarter_end);

-- ── The 10-column sheet (v3) ─────────────────────────────────────────────────
-- Schema-locked per spec section 1: QUARTER | PRICE MOVE | MGMT CONFIDENCE |
-- OVERALL TONE | Q&A TONE | GUIDANCE | ANALYST TONE | EVASIVE QS |
-- QA DIVERGENCE | SOURCE. The LM lexicon columns are gone (Adarsh, 15 Oct
-- 2026) and nothing sector-aware is ever added into this sheet - the frame and
-- sector blocks are separate tables joined on (symbol, quarter_end).
--
-- Every tone is the -2..+2 integer scale WITH the exchange numbers that
-- justify it, so each score is stored as a value plus its cites. ANALYST TONE
-- is numeric here, not the old skeptical/neutral/constructive category.
CREATE TABLE IF NOT EXISTS earnings_call_extraction (
    id                      INTEGER PRIMARY KEY,
    symbol                  TEXT NOT NULL,
    quarter_end             TEXT NOT NULL,          -- YYYY-MM-DD
    fiscal_quarter          TEXT,                   -- 'Q2 FY27' display label
    call_date               TEXT,                   -- YYYY-MM-DD

    -- PRICE MOVE: next-session close vs results-day close (spec 2.2 / 7.6).
    -- Computed from price data, never by the LLM; both dates stored.
    price_move_pct          REAL,
    price_move_from_date    TEXT,
    price_move_to_date      TEXT,

    -- Tones: -2..+2 with cites (JSON array of exchange_ref strings).
    mgmt_confidence         INTEGER CHECK (mgmt_confidence BETWEEN -2 AND 2),
    mgmt_confidence_cites   TEXT,
    overall_tone            INTEGER CHECK (overall_tone BETWEEN -2 AND 2),
    overall_tone_cites      TEXT,
    qa_tone                 INTEGER CHECK (qa_tone BETWEEN -2 AND 2),
    qa_tone_cites           TEXT,
    analyst_tone            INTEGER CHECK (analyst_tone BETWEEN -2 AND 2),
    analyst_tone_cites      TEXT,

    -- GUIDANCE cell: 'FORMAL: <...> | SOFT: <...>' (spec 4.4). The structured
    -- parts live in earnings_guidance_formal / earnings_guidance_soft; this is
    -- the display string for the sheet.
    guidance_cell           TEXT,

    -- EVASIVE QS: count AND the list of evasive exchange numbers (spec 2.2).
    evasive_count           INTEGER,
    evasive_exchanges       TEXT,                   -- JSON array of exchange_ref

    -- QA DIVERGENCE: graded, not boolean (spec 2.2).
    qa_divergence           TEXT CHECK (qa_divergence IN ('N', 'MILD', 'STRONG')),
    qa_divergence_note      TEXT,
    qa_divergence_cites     TEXT,

    -- SOURCE + row status
    transcript_url          TEXT,
    source_pdf_sha256       TEXT,
    cleaned_text_sha256     TEXT,
    row_status              TEXT NOT NULL DEFAULT 'OK'
                            CHECK (row_status IN ('OK', 'EXTRACTION_FAILED', 'NOT_VERIFIED')),
    failure_reason          TEXT,
    sector                  TEXT,                   -- resolved fno display group
    sector_card             TEXT,                   -- slot-card key, or NULL when unmapped
    sector_unmapped         INTEGER NOT NULL DEFAULT 0,

    extractor_version       TEXT NOT NULL,
    announcement_at         TEXT,
    announcement_at_precision TEXT,
    source_published_at     TEXT,
    feature_available_at    TEXT,
    timestamp_quality       TEXT NOT NULL DEFAULT 'OK',
    extracted_at            TEXT NOT NULL,
    UNIQUE (symbol, quarter_end, extractor_version)
);

CREATE INDEX IF NOT EXISTS idx_call_extraction_symbol
    ON earnings_call_extraction (symbol, quarter_end);

-- ── Universal frame block (spec 3.2) ─────────────────────────────────────────
-- The cheat sheet's 8 checks, as far as a transcript can answer them. Filled
-- ONLY from what management says on the call. Checks 1 (vs consensus) and 8
-- (what was priced in) are deliberately absent: they are not transcript
-- questions and live in the surprise / price features instead.
CREATE TABLE IF NOT EXISTS earnings_frame (
    id                  INTEGER PRIMARY KEY,
    symbol              TEXT NOT NULL,
    quarter_end         TEXT NOT NULL,
    field_key           TEXT NOT NULL,      -- mgmt_margin_commentary | one_offs_disclosed |
                                            -- cash_flow_commentary | riders_announced
    status              TEXT NOT NULL CHECK (status IN
                            ('GIVEN', 'NOT_GIVEN', 'NOT_VERIFIED', 'NOT_APPLICABLE')),
    value_text          TEXT,
    value_json          TEXT,               -- list-valued fields (one-offs, riders) keep structure
    quote               TEXT,
    exchange_ref        TEXT,
    char_start          INTEGER,
    char_end            INTEGER,
    extractor_version   TEXT NOT NULL,
    source_pdf_sha256   TEXT,
    cleaned_text_sha256 TEXT,
    announcement_at     TEXT,
    announcement_at_precision TEXT,
    source_published_at TEXT,
    feature_available_at TEXT,
    timestamp_quality   TEXT NOT NULL DEFAULT 'OK',
    extracted_at        TEXT NOT NULL,
    UNIQUE (symbol, quarter_end, field_key, extractor_version)
);

CREATE INDEX IF NOT EXISTS idx_earnings_frame_symbol
    ON earnings_frame (symbol, quarter_end);

-- ── Sector slots (spec 3.3 / 3.7) ────────────────────────────────────────────
-- One row per (symbol, quarter, slot_key). Four statuses, never a blank.
-- Slots come from the TRANSCRIPT ONLY (spec 3.6): a TCV figure that is in the
-- press release but not said on the call is NOT_GIVEN here, never filled from
-- the release.
CREATE TABLE IF NOT EXISTS earnings_sector_slots (
    id                  INTEGER PRIMARY KEY,
    symbol              TEXT NOT NULL,
    quarter_end         TEXT NOT NULL,
    sector              TEXT NOT NULL,      -- slot-card key the slot list came from
    slot_key            TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN
                            ('GIVEN', 'NOT_GIVEN', 'NOT_VERIFIED', 'NOT_APPLICABLE')),
    value_text          TEXT,               -- as spoken
    value_num           REAL,               -- parsed, only when the speaker gave a number
    unit                TEXT,
    period_basis        TEXT,               -- QoQ | YoY | FY | LTM, as stated
    quote               TEXT,
    exchange_ref        TEXT,
    char_start          INTEGER,
    char_end            INTEGER,
    extractor_version   TEXT NOT NULL,
    source_pdf_sha256   TEXT,
    cleaned_text_sha256 TEXT,
    announcement_at     TEXT,
    announcement_at_precision TEXT,
    source_published_at TEXT,
    feature_available_at TEXT,
    timestamp_quality   TEXT NOT NULL DEFAULT 'OK',
    extracted_at        TEXT NOT NULL,
    UNIQUE (symbol, quarter_end, slot_key, extractor_version)
);

CREATE INDEX IF NOT EXISTS idx_sector_slots_symbol
    ON earnings_sector_slots (symbol, quarter_end);

-- ── Formal guidance (spec 4.1) ───────────────────────────────────────────────
-- Four generic fields, one row each. NOT_GIVEN is first-class and carries the
-- quote in which the company said it does not guide. A changed guidance keeps
-- BOTH the old and new value (WITHDRAWN_OR_CHANGED).
CREATE TABLE IF NOT EXISTS earnings_guidance_formal (
    id                  INTEGER PRIMARY KEY,
    symbol              TEXT NOT NULL,
    quarter_end         TEXT NOT NULL,
    field_key           TEXT NOT NULL CHECK (field_key IN
                            ('guidance_revenue', 'guidance_margin',
                             'guidance_capex', 'guidance_orderbook')),
    status              TEXT NOT NULL CHECK (status IN
                            ('GIVEN', 'NOT_GIVEN', 'WITHDRAWN_OR_CHANGED', 'NOT_VERIFIED')),
    value_text          TEXT,
    value_num_low       REAL,               -- parsed band, when a number/range was stated
    value_num_high      REAL,
    unit                TEXT,
    applies_to_period   TEXT,               -- the period the target covers, e.g. 'FY27'
    prior_value_text    TEXT,               -- populated for WITHDRAWN_OR_CHANGED
    quote               TEXT,
    exchange_ref        TEXT,
    char_start          INTEGER,
    char_end            INTEGER,
    extractor_version   TEXT NOT NULL,
    source_pdf_sha256   TEXT,
    cleaned_text_sha256 TEXT,
    announcement_at     TEXT,
    announcement_at_precision TEXT,
    source_published_at TEXT,
    feature_available_at TEXT,
    timestamp_quality   TEXT NOT NULL DEFAULT 'OK',
    extracted_at        TEXT NOT NULL,
    UNIQUE (symbol, quarter_end, field_key, extractor_version)
);

CREATE INDEX IF NOT EXISTS idx_guidance_formal_symbol
    ON earnings_guidance_formal (symbol, quarter_end);

-- ── Soft outlook (spec 4.2) ──────────────────────────────────────────────────
-- Qualitative forward statements. NEVER promoted into a formal field - the
-- whole point of the split. A company can be formal NOT_GIVEN with a rich soft
-- outlook (the TCS pattern across all six reference quarters).
CREATE TABLE IF NOT EXISTS earnings_guidance_soft (
    id                  INTEGER PRIMARY KEY,
    symbol              TEXT NOT NULL,
    quarter_end         TEXT NOT NULL,
    item_no             INTEGER NOT NULL,
    topic               TEXT,               -- which formal field or sector slot it informs
    direction           TEXT CHECK (direction IN ('positive', 'neutral', 'negative', 'mixed')),
    quote               TEXT NOT NULL,
    exchange_ref        TEXT,
    char_start          INTEGER,
    char_end            INTEGER,
    extractor_version   TEXT NOT NULL,
    source_pdf_sha256   TEXT,
    cleaned_text_sha256 TEXT,
    announcement_at     TEXT,
    announcement_at_precision TEXT,
    source_published_at TEXT,
    feature_available_at TEXT,
    timestamp_quality   TEXT NOT NULL DEFAULT 'OK',
    extracted_at        TEXT NOT NULL,
    UNIQUE (symbol, quarter_end, item_no, extractor_version)
);

CREATE INDEX IF NOT EXISTS idx_guidance_soft_symbol
    ON earnings_guidance_soft (symbol, quarter_end);

-- ── Estimate vs actual: surprise (spec 5A) ───────────────────────────────────
-- Imported from the sheet Adarsh uploads; NO fetching or scraping is part of
-- this build. Adarsh's call (15 Oct 2026): the surprise percentage is taken
-- from the sheet's own `spread_pct` rather than recomputed against NSE XBRL
-- actuals, so `actual_value` here is the sheet's `released` column, not the
-- desk's own XBRL actual. The sheet's own caveats travel with the row -
-- basis is unconfirmed ("probable consolidated") and snapshot_status is
-- unverified on every row, so nothing here may be displayed as a verified
-- point-in-time consensus.
CREATE TABLE IF NOT EXISTS earnings_estimate_surprise (
    id                  INTEGER PRIMARY KEY,
    symbol              TEXT NOT NULL,
    period_end_date     TEXT NOT NULL,      -- ISO, derived from the sheet's fiscal_quarter label
    source_quarter_label TEXT NOT NULL,     -- as printed in the sheet, e.g. '2027 Q2'
    metric              TEXT NOT NULL,      -- sales | EBITDA | EBT | PAT | EPS
    estimate_value      REAL,               -- sheet's `forecast`
    actual_value        REAL,               -- sheet's `released`
    surprise_pct        REAL,               -- sheet's `spread_pct`, taken as-is
    unit                TEXT,
    basis               TEXT,               -- sheet's `basis`, carried verbatim
    snapshot_status     TEXT,               -- sheet's `snapshot_status`
    comparability       TEXT NOT NULL DEFAULT 'PROVISIONAL'
                        CHECK (comparability IN
                            ('PROVISIONAL', 'NOT_COMPARABLE', 'NEAR_ZERO_ESTIMATE',
                             'SIGN_FLIP', 'ACTUAL_PENDING')),
    flags               TEXT,               -- sheet's row flags, e.g. eps_rounded
    source_file_sha256  TEXT NOT NULL,      -- identity of the uploaded sheet version
    source_row_ref      INTEGER,            -- row number in that sheet, for traceability
    imported_at         TEXT NOT NULL,
    UNIQUE (symbol, period_end_date, metric, source_file_sha256)
);

CREATE INDEX IF NOT EXISTS idx_estimate_surprise_symbol
    ON earnings_estimate_surprise (symbol, period_end_date);
