-- Schema for analyst_estimates_spec-c3606434.txt, section 4.
-- Applied to the existing events.db (same file scripts/fetch_events.py
-- already owns), not a separate database.
--
-- KEY DESIGN RULE: a snapshot row is immutable once inserted. Analysts
-- revise estimates after the print, so a late or re-fetched snapshot would
-- compare actuals against a contaminated number. The trigger below blocks
-- any UPDATE that touches an estimate column; only the post-release
-- "actual" columns may ever change, and application code should still only
-- set them once per column (see jobs/fill_actuals.py).
--
-- SUPERSEDED 13 Oct 2026 (Adarsh's direction): actuals no longer compute a
-- surprise % against the estimate at all - "let's not compare on our own,
-- just give the analyst estimate and the NSE numbers." The single
-- eps_actual/revenue_actual/surprise_pct/revenue_surprise_pct columns are
-- replaced by six actual columns, standalone and consolidated for each of
-- revenue, PAT and EPS (diluted only - see nse_results.py's three traps
-- for why a single "actual EPS" was never safe to compare against a
-- Yahoo-sourced estimate: different dilution bases, and for several
-- symbols different standalone/consolidated bases too - see
-- scripts/discover_yahoo_basis.py). The estimate stays a single
-- yfinance-sourced value; it is displayed beside the actuals, not matched
-- against one of them.

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
    -- post-release, filled once per column by jobs/fill_actuals.py, from
    -- NSE's own filing XBRL only (nse_results.py). No yfinance fallback,
    -- no cross-source comparison, no surprise %.
    revenue_actual_cons REAL,               -- plain INR, consolidated filing
    revenue_actual_std  REAL,               -- plain INR, standalone filing
    pat_actual_cons     REAL,               -- ProfitLossForPeriod (or banking equivalent), consolidated
    pat_actual_std      REAL,               -- same, standalone
    eps_actual_cons     REAL,               -- diluted EPS, consolidated filing
    eps_actual_std      REAL,               -- diluted EPS, standalone filing
    xbrl_url_cons     TEXT,                 -- audit trail: the exact filing parsed
    xbrl_url_std      TEXT,
    actual_source     TEXT,                 -- "nse_xbrl" once any actual column is filled
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
