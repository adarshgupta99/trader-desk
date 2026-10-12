-- Schema for expected_move_spec-3c050a43.txt, section 8.
-- Applied to the existing events.db (same file scripts/fetch_events.py and
-- db/schema_estimates.sql already own), not a separate database. The
-- existing `events` table is left untouched - these are purely additive
-- new tables, linked to it loosely by (symbol, event_date) the same way
-- earnings_estimates already does, not a real FK (none are enforced
-- elsewhere in this repo).
--
-- This file is the reference copy. The actual tables are created at
-- runtime by the owning job's get_connection(), same CREATE TABLE IF NOT
-- EXISTS + ALTER TABLE ADD COLUMN pattern as jobs/snapshot_estimates.py -
-- see that file before changing this one.

-- Targeted hourly-bar cache for the reaction windows around prints only
-- (NOT a general-purpose 1h tier - data_layer/intraday_store.py's 15m/1d
-- tiers are a separate system and out of scope here).
CREATE TABLE IF NOT EXISTS earnings_move_prices (
    id                INTEGER PRIMARY KEY,
    symbol            TEXT NOT NULL,        -- NSE symbol, no .NS suffix
    interval          TEXT NOT NULL,        -- '1h' for v1
    bar_start_utc     TEXT NOT NULL,        -- ISO timestamp, UTC
    bar_end_utc       TEXT NOT NULL,        -- ISO timestamp, UTC; clipped for the shortened final bar
    open              REAL NOT NULL,
    high              REAL NOT NULL,
    low               REAL NOT NULL,
    close             REAL NOT NULL,
    volume            INTEGER,
    adjustment_basis  TEXT NOT NULL,        -- e.g. 'split_adjusted' - verified, not assumed
    source            TEXT NOT NULL,        -- 'yfinance'
    fetched_at        TEXT NOT NULL,        -- full ISO timestamp, IST
    provider_version  TEXT,                 -- yfinance version string, for reproducing a mapping
    UNIQUE (symbol, interval, bar_start_utc, adjustment_basis)
);

CREATE INDEX IF NOT EXISTS idx_earnings_move_prices_symbol_bar
    ON earnings_move_prices (symbol, bar_start_utc);

-- One row per distinct actual result filing (print identity = symbol +
-- financial_period, per spec section 3). filing_ts_* and reference/
-- calculation fields are frozen once computed for a given
-- calculation_version - see trg_earnings_move_prints_frozen below. A real
-- recompute (changed bars, fixed reference logic) inserts a NEW row under
-- a new calculation_version; it never overwrites an old one.
CREATE TABLE IF NOT EXISTS earnings_move_prints (
    id                       INTEGER PRIMARY KEY,
    symbol                   TEXT NOT NULL,        -- NSE symbol, no .NS suffix
    financial_period         TEXT NOT NULL,        -- e.g. '2024-Q2', from filing text, never inferred from calendar date
    event_date               TEXT,                 -- loose link to events.event_date (planned board-meeting date), NULL if no match found
    filing_ts_raw            TEXT NOT NULL,         -- exact string as returned by NSE (an_dt)
    filing_ts_utc            TEXT NOT NULL,         -- normalized UTC instant
    filing_ts_ist            TEXT NOT NULL,         -- IST display conversion
    timestamp_source_field   TEXT NOT NULL,         -- e.g. 'an_dt'
    announcement_id          TEXT,                  -- NSE's own id for the row, if available (e.g. seq_id)
    attachment_url           TEXT,                  -- attchmntFile PDF link
    retrieved_at             TEXT NOT NULL,          -- full ISO timestamp, IST, when this job fetched it
    release_mode             TEXT NOT NULL,          -- 'intraday' | 'post_close' | 'pre_open' | 'non_session'
    reference_price          REAL,
    reference_bar_ts         TEXT,                  -- bar_start_utc of the chosen reference bar, NULL for post-close modes using daily close
    reference_offset_minutes INTEGER,               -- signed; NULL when reference uses a daily close
    reference_method         TEXT NOT NULL,         -- 'nearest_bar_open' | 'reference_next_bar' | 'daily_close' | 'unavailable'
    session_a_date           TEXT,                  -- NULL for post-close/pre-open/non-session (window A not applicable)
    session_b_date           TEXT NOT NULL,
    high_a                   REAL,
    low_a                    REAL,
    high_b                   REAL,
    low_b                    REAL,
    move_a_pct               REAL,
    move_b_pct               REAL,
    print_move_pct           REAL,                  -- NULL unless all required windows are complete and usable
    status                   TEXT NOT NULL,         -- 'usable' | 'partial' | 'excluded' | 'pending'
    reason_codes             TEXT,                  -- JSON array, spec section 7 vocabulary (e.g. ["boundary_hour_contamination"])
    results_cluster          TEXT,                  -- JSON array of every results-evening announcement row for this event
                                                     -- (desc, an_dt, attchmntFile), incl. non-qualifying rows (board-outcome,
                                                     -- press release, dividend dup). The audit trail - see TCS 16:06:39/16:09:39.
                                                     -- Append-only: a later revision filing for this period is recorded HERE,
                                                     -- not by overwriting filing_ts_* - see trigger below.
    price_basis              TEXT NOT NULL,         -- provider price-basis label, not a claim of raw exchange quotes unless verified
    calculation_version      TEXT NOT NULL,
    source_provenance        TEXT,                  -- JSON: which fetch adapter/endpoint produced filing vs. price data
    inputs_cutoff            TEXT,                  -- ISO timestamp; data observed as of this instant
    computed_at              TEXT NOT NULL,
    UNIQUE (symbol, financial_period, calculation_version)
);

CREATE INDEX IF NOT EXISTS idx_earnings_move_prints_symbol_filing
    ON earnings_move_prints (symbol, filing_ts_utc);
CREATE INDEX IF NOT EXISTS idx_earnings_move_prints_status
    ON earnings_move_prints (status);

-- Re-filing rule (one print per period is the invariant): if a revised
-- results filing arrives for a period that already has a print row, do
-- NOT upsert over the FILING IDENTITY - the earliest filing stays the
-- print of record. This freezes only which filing the print points to
-- (symbol/period/timestamps/announcement/attachment/release_mode), same
-- IS-comparison pattern as trg_earnings_estimates_immutable in
-- db/schema_estimates.sql.
--
-- Everything else (reference_*, session/high/low/move columns, status,
-- reason_codes, results_cluster, price_basis, calculation_version,
-- source_provenance, inputs_cutoff, computed_at) stays freely updatable
-- in place: a 'pending' print completing once its next session closes,
-- or a recompute under corrected bar data, is expected and is NOT a
-- re-filing - only an actual later announcement row for the SAME period
-- is. The job layer is responsible for appending that later row to
-- results_cluster and adding 'source_conflict' to reason_codes; the
-- trigger only guarantees it can't silently move the print to a
-- different filing.
CREATE TRIGGER IF NOT EXISTS trg_earnings_move_prints_identity_frozen
BEFORE UPDATE ON earnings_move_prints
WHEN NOT (
    NEW.symbol IS OLD.symbol
    AND NEW.financial_period IS OLD.financial_period
    AND NEW.filing_ts_raw IS OLD.filing_ts_raw
    AND NEW.filing_ts_utc IS OLD.filing_ts_utc
    AND NEW.filing_ts_ist IS OLD.filing_ts_ist
    AND NEW.timestamp_source_field IS OLD.timestamp_source_field
    AND NEW.announcement_id IS OLD.announcement_id
    AND NEW.attachment_url IS OLD.attachment_url
    AND NEW.release_mode IS OLD.release_mode
)
BEGIN
    SELECT RAISE(ABORT, 'earnings_move_prints: filing identity (symbol/financial_period/filing_ts/announcement/release_mode) is frozen once inserted - the earliest filing stays the print of record; record a later filing in results_cluster with source_conflict instead');
END;

-- One row per upcoming event's rolling 8-print cohort.
CREATE TABLE IF NOT EXISTS earnings_move_summaries (
    id                   INTEGER PRIMARY KEY,
    symbol                TEXT NOT NULL,
    event_date            TEXT NOT NULL,        -- the upcoming event this summary is for (events.event_date)
    lookback_target       INTEGER NOT NULL DEFAULT 8,
    selected_print_ids    TEXT NOT NULL,        -- JSON array of earnings_move_prints.id - the contributing cohort, for audit
    found_count           INTEGER NOT NULL,     -- distinct prints discovered before this event, any status
    selected_count        INTEGER NOT NULL,     -- min(found_count, lookback_target)
    usable_count          INTEGER NOT NULL,     -- status='usable' within selected
    excluded_count        INTEGER NOT NULL,     -- selected_count - usable_count
    expected_move_pct     REAL,                 -- NULL if usable_count=0 - never 0.00
    worst_case_pct        REAL,                 -- NULL if usable_count=0
    calculation_version   TEXT NOT NULL,
    inputs_cutoff          TEXT NOT NULL,        -- ISO timestamp; data observed as of this instant
    last_success_at        TEXT,                 -- last time this summary recomputed cleanly
    last_attempt_at         TEXT,                 -- last time a recompute was attempted, success or not
    refresh_status          TEXT NOT NULL,        -- 'ok' | 'stale' | 'failed'
    UNIQUE (symbol, event_date, calculation_version)
);

CREATE INDEX IF NOT EXISTS idx_earnings_move_summaries_event
    ON earnings_move_summaries (symbol, event_date);
