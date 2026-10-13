-- Schema for the Calendar subtab's "History 6Q" sparkline column (13 Oct
-- 2026, Adarsh's spec). One row per (symbol, quarter_end): the last 6
-- REPORTED quarters' consolidated revenue and PAT, from NSE's own filing
-- XBRL (nse_results.py), reused from the same pipeline as the Past
-- earnings tab's actuals.
--
-- A row with both value columns NULL is a REAL, EXPECTED quarter that NSE
-- simply has no filing for (an archive gap) - kept, not dropped, same
-- "never silently drop a gap" convention as earnings_estimates and
-- earnings_move_prints. The UI renders it as a blank space in the
-- sparkline, never 0/nan/interpolated.
--
-- Revenue is whatever nse_results.parse_facts resolved, bank derivation
-- included (InterestEarned - InterestExpended + OtherIncome when
-- RevenueFromOperations is absent) - same function, same concept-name
-- audit trail as the Past earnings tab, so there is no separate
-- "suppress for banks" path here; a bank's REV track renders normally.
--
-- No immutability trigger: actuals, same mutable-on-rerun policy as
-- earnings_move_prints' calculation columns, not the frozen-estimate
-- policy of earnings_estimates. A rerun may correct a row (e.g. a
-- previously-missing quarter gets filed later).

CREATE TABLE IF NOT EXISTS earnings_history_quarters (
    id               INTEGER PRIMARY KEY,
    symbol           TEXT NOT NULL,        -- NSE symbol, no .NS suffix
    quarter_end      TEXT NOT NULL,        -- YYYY-MM-DD, the quarter's own end date
    revenue_cons     REAL,                 -- plain INR; NULL = no filing found for this quarter
    pat_cons         REAL,                 -- ProfitLossForPeriod (total consolidated incl. minorities)
    revenue_concept  TEXT,                 -- audit: concept name, or the derivation label for a bank
    pat_concept      TEXT,
    xbrl_url         TEXT,                 -- the filing parsed, for audit; NULL if nothing was found
    fetched_at       TEXT NOT NULL,        -- full ISO timestamp, IST
    UNIQUE (symbol, quarter_end)
);

CREATE INDEX IF NOT EXISTS idx_earnings_history_quarters_symbol
    ON earnings_history_quarters (symbol, quarter_end);
