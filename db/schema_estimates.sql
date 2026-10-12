-- Schema for analyst_estimates_spec-c3606434.txt, section 4.
-- Applied to the existing events.db (same file scripts/fetch_events.py
-- already owns), not a separate database.
--
-- KEY DESIGN RULE: a snapshot row is immutable once inserted. Analysts
-- revise estimates after the print, so a late or re-fetched snapshot would
-- compare actuals against a contaminated number. The trigger below blocks
-- any UPDATE that touches an estimate column; only the post-release
-- "actual" columns may ever change, and application code should still only
-- set them once (see jobs/fill_actuals.py).

CREATE TABLE IF NOT EXISTS earnings_estimates (
    id                INTEGER PRIMARY KEY,
    symbol            TEXT NOT NULL,        -- NSE symbol, no .NS suffix
    event_date        TEXT NOT NULL,        -- earnings date from events table, YYYY-MM-DD
    snapshot_date     TEXT NOT NULL,        -- date the snapshot was taken, YYYY-MM-DD (IST)
    eps_estimate      REAL,                 -- NULL if Yahoo has none
    revenue_estimate  REAL,                 -- NULL if none, plain INR
    n_analysts_eps    INTEGER,              -- NULL if none
    n_analysts_rev    INTEGER,
    eps_low           REAL,
    eps_high          REAL,
    period_label      TEXT NOT NULL,        -- yfinance period used, e.g. "0q"
    source            TEXT NOT NULL,        -- "yfinance"
    fetched_at        TEXT NOT NULL,        -- full ISO timestamp, IST
    -- post-release, filled once by jobs/fill_actuals.py:
    eps_actual        REAL,
    revenue_actual    REAL,
    surprise_pct      REAL,                 -- EPS surprise only
    revenue_surprise_pct REAL,              -- same formula, against revenue
    actual_source     TEXT,                 -- "nselib" or "yfinance"
    actual_filled_at  TEXT,
    UNIQUE (symbol, event_date, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_earnings_estimates_symbol
    ON earnings_estimates (symbol, event_date);

-- Blocks any UPDATE that changes an estimate column. SQLite trigger
-- conditions can't be parameterised, so this compares OLD/NEW directly;
-- IS keeps the comparison NULL-safe (two NULLs count as "unchanged").
CREATE TRIGGER IF NOT EXISTS trg_earnings_estimates_immutable
BEFORE UPDATE ON earnings_estimates
WHEN NOT (
    NEW.eps_estimate IS OLD.eps_estimate
    AND NEW.revenue_estimate IS OLD.revenue_estimate
    AND NEW.n_analysts_eps IS OLD.n_analysts_eps
    AND NEW.n_analysts_rev IS OLD.n_analysts_rev
    AND NEW.eps_low IS OLD.eps_low
    AND NEW.eps_high IS OLD.eps_high
    AND NEW.period_label IS OLD.period_label
    AND NEW.source IS OLD.source
    AND NEW.fetched_at IS OLD.fetched_at
)
BEGIN
    SELECT RAISE(ABORT, 'earnings_estimates: estimate columns are immutable once snapshotted');
END;
