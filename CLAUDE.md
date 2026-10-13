# Trader Desk

## Vision and scope

Adarsh Gupta's personal trading desk web app: market charts, news with AI tags
and summaries, news-vs-price analysis, alerts, and backtesting-lite.
A solo-user, learning-focused, portfolio-quality build over 9 weeks:
5 October - 6 December 2026. This repo covers Trader Desk only.

Show likely drivers of price moves, not proof of causation. Free intraday data
is thin; make that limit clear in the app. Report honest sample sizes.

## Stack and locked decisions

- Python 3.13 in a local `.venv`; Streamlit UI and Streamlit Community Cloud.
- `pandas` and `plotly` for data handling and charts; SQLite for cached data.
- `yfinance` for market data, personal use only. Keep the app private when
  using it. Any public demo must use synthetic or appropriately licensed data.
- FRED API for macro/rates data.
- Google Gemini through `google-genai` for news tags and summaries.
  Default model: `gemini-3.5-flash-lite`.
- RSS through `feedparser`, plus GDELT; `requests` for HTTP calls.
- Telegram bot for alerts; `python-dotenv` for configuration.
- These choices supersede the PDF's older Python/LLM setup suggestions.

## Repo conventions

- All secrets belong in `.env`: `GEMINI_API_KEY`, the FRED API key, and the
  Telegram bot token. Never hardcode keys or commit `.env`.
- Keep `.env` and `.venv` in `.gitignore`. Never print secrets in logs or UI.
- Load local environment values with `python-dotenv`; keep deployed secrets
  out of source control too.
- Put LLM provider/model settings in a config file or `.env`, not app logic.
  Keep provider-specific calls separate so the provider can be swapped.
- Keep `requirements.txt` current whenever dependencies change.
- Do not change repo visibility or expose real market data in a public demo
  without checking with Adarsh.

## Analyst estimates tracker (side build, 8 October 2026)

A side feature outside the Week 1-9 plan, built against
`analyst_estimates_spec-c3606434.txt`: for upcoming F&O earnings, the
Events tab Calendar view shows the analyst consensus (EPS, revenue,
analyst count), then the actual and surprise % once the result is out.

- **T-1 rule:** estimates are snapshotted once, the day before the earnings
  date, and frozen forever after - never re-fetched or updated. Analysts
  revise estimates after the print, so a late snapshot would compare
  actuals against a contaminated number. Only the actual-value columns
  (see below) may ever be filled in later, enforced by a SQLite trigger
  on `earnings_estimates` (see `db/schema_estimates.sql`).
- **Estimates** come from yfinance only, and are frozen (see the T-1 rule
  above): `eps_estimate`, `revenue_estimate`.
- **Actuals come from NSE's own filing XBRL only** (12-13 October 2026) -
  `nse_results.py` reads `/api/integrated-filing-results`, picks the
  filing for the right quarter and basis, and parses the figures out of
  the filing's own XBRL. This is a different endpoint from
  `nselib.financial_results_for_equity`'s `/api/corporates-financial-results`,
  which surfaces a narrow NSE filing category and returned zero mainstream
  F&O names across a full year (see `docs/estimates_discovery.txt`);
  re-verified live on 12 October, still zero rows for a window containing
  TCS's real result. It is not used. Do not confuse the two endpoints.
- Three things `nse_results.py` exists to get right, each a real trap
  confirmed against live filings - do not "simplify" past them:
  **(1)** one document holds the quarter *and* the year-to-date figure
  under the same concept name, separated only by `contextRef` (TCS
  Sep-2026: ₹731.88bn vs ₹1,454.63bn), so a context is accepted only when
  its own declared dates prove a ~3-month span ending on the target
  quarter end; **(2)** banks use a different taxonomy, subtly - HDFCBANK's
  profit concept is `ProfitLossForThePeriod`, one word off the standard
  `ProfitLossForPeriod`, and a single-concept parser returns NULL for
  every bank and NBFC while appearing to work, and a bank's revenue is
  **derived** (`InterestEarned - InterestExpended + OtherIncome`), not
  read off the `Income` line, which is a different measure entirely -
  verified exact to the paisa against HDFCBANK's yfinance-reported Total
  Revenue; **(3)** every quarter is filed twice, standalone and
  consolidated, as two separate XBRL documents with different numbers in
  an unpredictable direction (DMART Sep-2026 standalone PAT *exceeds*
  consolidated while its revenue is lower).
- **No surprise % is computed anywhere on the Past earnings tab, by
  design (13 October 2026).** `scripts/discover_yahoo_basis.py` found no
  safe way to match the single yfinance-sourced estimate to one NSE
  basis: most symbols' EPS basis is stable across quarters, but
  RELIANCE's flips between quarters, M&M matches neither NSE basis at
  all, and - the finding that killed the whole approach - RBLBANK and
  SBIN each have EPS matching one basis and revenue matching the
  *other*, so a single stored "basis per symbol" cannot serve both
  measures. Adarsh's call: stop comparing sources. The estimate
  (`eps_estimate`/`revenue_estimate`) and NSE's own actuals are shown
  side by side with no derived comparison between them. **Store BOTH
  bases for every actual measure** - `revenue_actual_cons/std`,
  `pat_actual_cons/std`, `eps_actual_cons/std` (diluted only, never
  basic - confirmed against yfinance's reported EPS in 6/6 discriminating
  cases) - plus `xbrl_url_cons/std` for audit. No yfinance fallback for
  actuals: a quarter NSE hasn't filed yet just stays empty.
- Scheduled jobs: `jobs/snapshot_estimates.py` (T-1 snapshot) then
  `jobs/fill_actuals.py` (NSE actuals, both bases, retries daily for up
  to 7 days) - both read-write `events.db`, same as
  `scripts/fetch_events.py`. The app only reads it
  (`events_db.get_earnings_estimates`), never writes.
- A row with no estimate is kept and shown as "no estimate", never dropped.

## Expected move tracker (side build, 9 October 2026)

A side feature outside the Week 1-9 plan, built against
`expected_move_spec-3c050a43.txt`: for upcoming F&O results, the Events tab
Calendar view shows an "Expected move" and "Worst case" percentage, derived
from how far the stock actually moved around its last 8 usable results
prints. This is a cohort of historical reactions, not a prediction, and the
UI says so.

A separate **"Expected moves" subtab** (9 October 2026) lists all 213 F&O
stocks from `fno_sectors.json`, ranked by this same figure, regardless of
whether its next result is scheduled yet - the Calendar view only ever
shows symbols with an upcoming board meeting, so a stock between results
was invisible there even with a full history. Backed by
`events_db.get_all_expected_moves()`, which builds its row set from the
full F&O universe and left-joins each symbol's aggregate from
`earnings_move_prints` (not a new summary-table row -
`earnings_move_summaries.event_date` is tied to one upcoming event by
design). Adarsh chose to backfill the full 213-symbol universe rather than
reuse the ~75 symbols already covered; 212 of 213 now have at least one
stored print. The one exception, `MCX`, has zero filings in NSE's
corporate-announcements archive under that symbol across both a 3-year and
a 120-day window (confirmed against a known-good symbol returning hundreds
over the same range) - a genuine data gap, not a bug - so it still appears
in the table as a normal row showing "Unavailable", never silently dropped.

- **Method:** `analytics/expected_move.py` (pure, offline, fixture-tested)
  classifies each historical filing's release mode (intraday / post-close /
  pre-open / non-session) from its NSE filing timestamp against the real
  session calendar (`trading_calendar.py`), picks the matching reference bar
  and same-session/next-session windows, and computes each print's move %.
  Intraday prints use the larger of the filing-session and next-session
  moves; post-close prints use the next session versus that day's close.
  The headline numbers are the mean and max across the latest 8 usable
  prints before the upcoming event.
- Source is yfinance only: hourly bars (730-day window) for the reaction
  windows, daily bars (3-year window) for older reference closes. Filings
  are discovered from NSE's corporate-announcements archive
  (`https://www.nseindia.com/api/corporate-announcements`), fetched via
  `nselib.libutil.nse_urlfetch` for the cookie bootstrap.
- **Filing clustering:** NSE sometimes files the same result as 2-3 separate
  announcements minutes apart (e.g. Outcome-of-Board-Meeting, then
  Dividend). The earliest same-period filing is the print of record; every
  other filing on the *same calendar day* is a cluster sibling (kept as
  audit trail, not a conflict). Only a match on a genuinely different
  calendar day counts as a real revision (`source_conflict`). See
  `docs/expected_move_discovery.txt` and
  `tests/test_refresh_expected_moves.py` for the exact cases this covers.
- Storage: three additive tables in `events.db` —
  `earnings_move_prices` (cached bars), `earnings_move_prints` (one row per
  historical filing), `earnings_move_summaries` (one row per upcoming
  event's cohort rollup). A SQLite trigger freezes only the filing-identity
  columns on `earnings_move_prints`; calculation columns stay mutable so a
  rerun can correct a row. See `db/schema_expected_move.sql`.
- Job: `jobs/refresh_expected_moves.py`, run by hand (no cron/launchd entry
  yet):
  ```
  .venv/bin/python -m jobs.refresh_expected_moves --symbols TCS RELIANCE HDFCBANK INFY WIPRO --backfill
  .venv/bin/python -m jobs.refresh_expected_moves   # default: every symbol with an upcoming results board meeting
  ```
  Idempotent — reruns update existing rows in place rather than
  duplicating them. Both read-write `events.db`, same as
  `scripts/fetch_events.py` and the estimates jobs above. The app only
  reads it (`events_db.get_expected_move_summary`,
  `get_expected_move_history`), never writes.
- Tests: `tests/test_expected_move.py` (26 fixture cases for the pure
  calculator) and `tests/test_refresh_expected_moves.py` (regression tests
  for two real bugs caught during the first live backfill — an abbreviated-
  month date parser gap, and the filing-clustering boundary above). A
  third job bug (an unescaped `&` breaking the NSE archive URL for symbols
  like `M&M`/`GVT&D`) and a fourth, UI-side one (pandas turning `None` into
  a truthy `nan` in a mixed-type column, feeding `nan` into `strptime`/a
  percent format) were both caught the same way and fixed - see
  ARCHITECTURE.md for detail.
- **A sixth real bug (9 October 2026):** `yfinance` back-adjusts daily bars
  for later stock splits/bonuses but does not apply the same adjustment to
  hourly bars, so a print straddling a real corporate action computed an
  impossible move % (BAJFINANCE showed 163%/438%). Fixed per Adarsh's
  direction by reusing the NSE archive's own "Record Date" filings
  (already fetched for results identification) to detect genuine
  bonus/split/demerger dates - `find_price_discontinuities()` in
  `jobs/refresh_expected_moves.py`. A full 213-symbol rerun with the fix
  found 42 affected symbols (not just the 3 first spotted). Per Adarsh's
  follow-up direction, a straddling print is recovered rather than
  dropped: `compute_print_daily_fallback()` redoes the whole calculation
  from daily OHLC bars only (consistently back-adjusted on both sides of
  the split), tagged `reference_method="daily_close_fallback"` and
  reason code `price_basis_daily_fallback`. See ARCHITECTURE.md.
- **Not yet verified:** the Calendar-subtab columns and the "Expected
  moves" subtab have only been checked via Streamlit's `AppTest` harness
  (zero exceptions navigating the Events tab, clicking subtabs, toggling
  Scope, typing into Search) — no real-browser/screenshot check has been
  done, since none is available in the environment this was built in.
  Treat the UI as functionally wired but visually unconfirmed until
  checked by eye.

## Earnings history sparkline (side build, 13 October 2026)

A new column on the Events tab's Calendar subtab, results rows only: **HISTORY 6Q (RS CR)**
- two stacked unicode-block sparklines (REV, PAT), the last 6 reported calendar quarters of
NSE-filed consolidated revenue/PAT, plus the latest value. Shape per row only - block
heights are never comparable across rows or between the REV/PAT tracks.

- Source: NSE integrated-filing XBRL, same pipeline as the Past earnings tab's actuals
  (`nse_results.py`) - consolidated basis, diluted not relevant here (revenue/PAT have no
  dilution axis). A bank's revenue is the already-verified derivation
  (`InterestEarned - InterestExpended + OtherIncome`), reused as-is rather than suppressed.
- Scaling is min-max over whatever values are present in the 6-quarter window; a missing
  quarter (`NULL` in storage) renders as a blank space, never interpolated, never affects
  the scale. Each block is coloured by that quarter's own QoQ direction; the oldest quarter
  in the window and any quarter right after a gap render neutral (no prior to compare
  against). A small grey "-" marks a quarter whose own value went negative, separate from
  its QoQ colour.
- Storage: one additive table, `earnings_history_quarters` (`db/schema_earnings_history.sql`)
  - a row per (symbol, quarter_end), gaps stored explicitly rather than just absent rows.
  Job: `jobs/refresh_earnings_history.py`, scoped to symbols with a results board meeting
  either upcoming or within the last 14 days (`CALENDAR_LOOKBACK_DAYS`, must match
  `tabs/events.py`'s own `lookback_days=14` - the exact window Calendar actually shows) -
  run manually or in the same daily cron slot as the estimates/actuals jobs, after them:
  ```
  .venv/bin/python -m jobs.refresh_earnings_history
  .venv/bin/python -m jobs.refresh_earnings_history --symbols TCS,RELIANCE
  ```
  Both read-write `events.db`, same as every other events-feature job; the app only reads
  it (`events_db.get_earnings_history_quarters`), never writes.
- A real bug fixed the same day, in both this job and `jobs/fill_actuals.py` at once: a
  deterministic parse failure (a filing with no 3-month context - found live on ICICIPRULI,
  a life insurer filing under a different XBRL taxonomy whose contexts span ~356/~362 days)
  was being retried with the full backoff schedule, wasting ~75 seconds per occurrence for
  something retrying could never fix. Fixed to fail fast on that specific error. See
  ARCHITECTURE.md for the full investigation.
- Tests: `tests/test_sparkline.py` (fixture cases for the pure scaling/QoQ engine,
  `analytics/sparkline.py`) and `tests/test_refresh_earnings_history.py` (retry-fast-fail and
  the lookback-window scope regressions).
- Hover tooltips use `data-tip` + a CSS-only `[data-tip]:hover::after` popup
  (`theme.py`'s `_MAIN_CSS`), not the native `title=` attribute - confirmed in a real browser
  that `title=` never rendered anything on this app's `unsafe_allow_html` markup, for any
  element, not just this column.
- Rendering (13 Oct 2026): bars are HTML spans (`height:4-26px`, `width:7px`), not unicode
  block characters - colours reuse the existing `P.UP`/`P.DOWN`/`P.T3` tokens rather than
  new ones, so light/dark both resolve automatically.
- A sector filter chip row (Calendar subtab only, `fno.DISPLAY_GROUPS`) narrows the table to
  one sector at a time - a plain filter, not the Correlation tab's sector-pair drill-down.
- The just-reported quarter (the one a row's own board meeting is about) renders as a 7th
  bar, set apart with extra margin and an outline, once its actual lands in
  `earnings_estimates` - no new fetch, same pipeline as Past earnings. A row that hasn't
  reported yet stays at 6 bars.
- **Not yet verified:** checked via `AppTest` only, same caveat as the rest of the Events
  tab - no real-browser check has been done.

## Starting state (handoff: 3 October 2026)

- Repo: `adarshgupta99/trader-desk`; intended to remain private.
- Starting `app.py`: minimal ticker input with a close-price line chart.
- Streamlit Cloud deployment is in progress; verify its status rather than
  assuming it is live. Inspect the actual files before making changes.

## Build rhythm

Trader Desk budget: 21.5 hours/week, 3.5 hours/day Monday-Friday and 4 hours
Saturday. Sunday is off. Each Saturday ends with a working demo.
Plan the week in the first 10 minutes on Monday. Add new ideas to the parking
lot, not the current week's scope. If behind by Thursday, drop the week's
"cut first" item instead of extending the week. Week 9 adds no new features.

## Week 1: foundation and first dashboard (5-10 October)

1. Check the repo, Python 3.13 environment, dependencies, secret loading, and
   the existing app. Preserve the working ticker chart as the starting point.
2. Build a market-data loader with a SQLite cache.
3. Expand the Streamlit app into five asset-class tabs:
   - Indices.
   - FX.
   - Rates, using FRED macro/rates data.
   - Commodities.
   - Volatility, including VIX.
4. Add charts for each asset class and keep the app runnable at each step.
5. Saturday deliverable: dashboard running locally with five asset-class tabs.
   Cut first if behind: the rates panel; ship the reduced version explicitly.

## Weeks 2-9

- **Week 2 (12-17 October):** Watchlists, 1d/1w/1m return heatmap, cross-asset correlation matrix, date-range controls, Streamlit Cloud deploy. Demo: phone-accessible private app, or synthetic/licensed-data public demo. Cut first: correlation matrix.
- **Week 3 (19-24 October):** RSS feeds (Reuters, FT, CNBC, central banks) plus GDELT; deduplicate, timestamp, store, and display news. Demo: automatically refreshing feed with 500+ stored items. Cut first: GDELT, keep RSS.
- **Week 4 (26-31 October):** Tag news with affected assets, direction, and confidence; hand-label 50 items and measure accuracy. Add batching and a cost cap. Demo: tagged news and measured accuracy. Cut first: direction score, keep asset tags.
- **Week 5 (2-7 November):** Match news timestamps to price moves in surrounding windows; build a "Why did X move" panel with ranked candidate headlines. Demo: click a chart move to see likely drivers. Cut first: multi-asset view, start with one asset.
- **Week 6 (9-14 November):** Store 5-minute bars daily, add z-score abnormal-move detection, tighter windows, confidence labels, and a noise filter. Demo: explanations for intraday moves on three real past days. Cut first: confidence labels.
```
## Week 5-6 task: Earnings call sentiment tracker

GOAL
For watchlist companies with an earnings call, fetch the transcript on/after the results date, quantify management tone, and track it quarter over quarter. The output is a sentiment TREND per company, not a one-off score.

DEPENDENCIES (must exist first - do not start before these)
- Events calendar feed (NSE corporate event calendar) supplying results dates per watchlist ticker
- A storage layer (SQLite table) for per-quarter results
- Gemini API access via .env key (already set up)

DATA SOURCES (in priority order)
1. NSE/BSE corporate announcement feed: companies are legally required to file concall transcripts as PDFs. Fetch the filing, extract text (pdftotext or pypdf).
2. Screener.in Concalls section for the company, as fallback.
3. US tickers: Financial Modeling Prep transcript API (free tier) or Motley Fool transcripts.
If no transcript is found, mark the quarter as "transcript unavailable" - never substitute a news article or results press release and treat it as a transcript.

METHOD (two tracks, both required)
Track A - deterministic baseline: Loughran-McDonald finance dictionary counts (positive, negative, uncertainty, litigious wordlists) over the transcript text. Report counts normalized per 1000 words. No model, fully reproducible.
Track B - LLM extraction: chunk the transcript, split "management commentary" from "analyst Q&A", and send each to Gemini (flash tier) with a prompt returning strict JSON:
  {tone_score: -2..+2, guidance: "raised|maintained|lowered|none", key_numbers: [...], new_risks_vs_prior_quarter: [...], hedging_language_examples: [...]}
Score management and Q&A SEPARATELY - Q&A tone divergence from management tone is itself a signal worth storing.
Do NOT use FinBERT - it does both jobs worse than these two tracks.

STORAGE SCHEMA (per company per quarter)
ticker, quarter, call_date, transcript_source_url, lm_positive, lm_negative, lm_uncertainty, mgmt_tone, qa_tone, guidance, extraction_json, created_at

UI
- On the Chart tab for a ticker: sentiment trend line (mgmg tone and QA tone over quarters) under the price chart, aligned by date.
- Follow the CLAUDE.md design language: black/orange convention, tabular numbers, no new panels beyond this.

CONSTRAINTS
- Polite fetching only: cache everything locally, never re-download a transcript already stored, no parallel hammering of NSE/Screener (rate limits and account flagging risk).
- Read-only feature: it must never touch watchlist state.
- Gemini cost guard: max ~20 calls per transcript; log token usage.

ACCEPTANCE TEST
- Run on one completed quarter for RELIANCE: LM counts reproducible on re-run, Gemini JSON parses cleanly, and the stored row links to the actual NSE/Screener transcript URL.
- Sentiment chart renders for a ticker with >= 2 stored quarters.
```
- **Week 7 (16-21 November):** Telegram abnormal-move alerts with top likely driver; morning brief of overnight moves and tagged news; first parked feature. Demo: an alert arriving on the phone unprompted. Cut first: morning brief, keep alerts.
- **Week 8 (23-28 November):** Event studies: after news type X, what did asset Y do over 1h/1d? Build a hit-rate table and the second parked feature. Demo: hit-rate page with honest sample sizes. Cut first: backtesting, keep the chosen feature.
- **Week 9 (30 November-5 December; rest 6 December):** Buffer, bug fixes, README, screenshots, two-minute demo video, clean deploy. No new features. Portfolio demo must respect data-use limits; keep the repo private unless Adarsh chooses otherwise. Cut first: anything unfinished.

## Parking lot

- India macro panel using RBI DBIE, planned for the weeks 7-8 feature slots.
  Verify available API access and data-use terms before implementing.
- Playwright browser-click test suite, scoped to the Correlation tab's Stock
  matrix (click-to-isolate, click-a-cell-to-History, shift+click) - parked
  8 October 2026 after a `dragmode="select"` regression broke all three and
  the existing `pytest` suite, which is logic-level only, couldn't have
  caught it. New dependency (Playwright + browser binaries) and slower to
  maintain than the rest of this repo's tests, so scope it to just these
  three interactions rather than broad UI coverage.
- Keep other new ideas parked until Monday planning; do not invent features
  to fill the two reserved slots.

## Working with Adarsh

Adarsh is a capable beginner, not a developer. Explain changes briefly in
plain English, including how to run or check them. Prefer small working
increments over big rewrites. Keep the app runnable after every change.
Help him follow the weekly plan as well as write the code.


# Trader Desk: design language

## 1. Swiss Editorial
Use an off-white canvas, near-black text, readable gray metadata, thin neutral rules, and one red-orange signal accent. Let typography, prices, and a visible grid define the hierarchy. Use square corners and no decorative shadows. Apply these rules to existing Streamlit tabs and components only.

**Scope: restyle only.** Preserve the live app's existing content, controls, and features. Do not copy invented mockup elements: no watchlist sparklines, normalized-return toggle, breadth/advancing-declining/top-mover panels, new volatility indicators, or header extras such as "Personal workspace / INR · NSE / AG". Only the listed QA fixes may add corrective feedback or confirmation controls. Restyle existing labels and metadata; do not add UI or fetch new data to satisfy a design rule.

## 2. Global rules

### Palette
The color roles are selected; these exact hex values are **proposed implementation tokens**, not verified mockup samples:

| Token | Proposed value | Use |
| --- | --- | --- |
| Canvas | `#DFDACB` | Page background |
| Surface | `#D7D2C1` | Card and panel backgrounds |
| Primary | `#1F1D19` | Headings and prices |
| Secondary | `#6B675F` | Metadata, labels, neutral chart ticks |
| Tertiary | `#9C988D` | De-emphasised controls (remove ×) |
| Rule | `#BDB7A5` | Thin separators and panel boundaries |
| Signal | `#C25C36` | Negative changes and accent lines |
| Positive | `#35875B` | Positive changes |

**Adarsh's decision (3 October 2026):** the palette is deliberately matte — muted and low-glare rather than bright. Keep new colours desaturated to match; do not reintroduce saturated or neon values.

- Remove teal navigation/inputs and yellow series styling. Use neutral controls; use primary text or a thin signal underline for active navigation.
- **Adarsh's decision (3 October 2026), overrides the original spec:** positive changes are green `#35875B`, not near-black. Green is used for direction only (change values and single-series chart lines), never for navigation, controls or surfaces.
- Do not add extra accent colors, gradients, rounded cards, or decorative shadows. Check text contrast against its actual background.

### Type and layout
- Use one sans-serif family throughout. **Proposed stack:** `Inter, Arial, sans-serif`; do not depend on an unavailable font.
- **Proposed type scale:** wordmark 36-40px; section headings 28-32px; key values 32-40px; names/controls 14-16px; metadata/chart ticks 12-13px.
- Use `font-variant-numeric: tabular-nums lining-nums` for values, changes, weights, and tables; configure matching chart typography where supported.
- Place the full-size wordmark below Streamlit's toolbar, followed by a strong rule and navigation. Never clip the title.
- Style existing tab headings consistently. Keep existing coverage, period, unit, and timing text compact and secondary; do not invent context lines or imply live data without evidence.
- Align brand, navigation, headings, controls, cells, and charts to shared edges. Reuse panel components and consistent spacing; avoid tab-specific styling drift.
- Collapse grids on narrow screens without horizontal overflow. Keep names, units, controls, and chart ticks readable rather than shrinking them to fit.

### Direction and number formatting
- Say "change", not "gain", "loss", "win", or "good/bad". A rising FX quote or VIX value is not inherently favorable.
- Show positive changes in green `#35875B` and negative changes in signal terracotta. Always show a sign on change values, including `+0.00`; suppress negative zero after rounding.
- **Adarsh's decision (3 October 2026), overrides the original spec:** single-series chart lines are also coloured by direction — green when the change over the plotted range is positive, terracotta when negative. Panel fills remain removed, so a panel is never flooded with colour; only the line and the change value carry direction.
- Put the change period beside the change (`1D`, `1W`, or the actual selected period). Keep change windows distinct from chart-history windows.
- Show currency or units with each value or in an unambiguous persistent label. Never infer units from a ticker alone.
- Preserve four decimal places and trailing zeros for FX quotes. **Proposed default:** two decimals for other prices, index points, and percentage changes unless source precision or the instrument requires otherwise.
- **Proposed locale rule:** use Indian digit grouping for INR (`₹1,23,456.78`) and international grouping for other currencies (`$123,456.78`). Preserve INR labels on currency values; existing Indian index unit labels must use index points, not rupees.
- Use one scale formatter per comparable set of charts. Do not mix raw thousands with `k` abbreviations; show full-precision values and exact units on hover. Never fabricate missing values or changes.

## 3. Per-tab rules

### Watchlist

**Adarsh's model (4 October 2026). The earlier "load a preset into one watchlist" design was a misreading — there is no loading, replacing or saving.** The tab shows one of nine views, selected by a chip row:

- **Five index views** — Nifty 50, Nifty Midcap, Nifty Auto, Nifty IT, Bank Nifty. These are fixed and read-only: each already carries its own constituents. They change only when the real index constituents or weightings change. Render no add control, no rename and no remove control on these.
- **Four custom lists** — renameable, up to 30 instruments each, with add and remove available. These are the only editable views.

Because nothing is ever destroyed by switching views, there is no confirmation step.

- Use compact tiles that wrap and hug their content, not equal-width columns. Hierarchy per tile is name with the remove control beside it, then the **signed change as the dominant value**, then the price beneath it. Do not show the ticker on the tile and do not make the price dominant.
- Instruments are added by searching a **local symbol table**, not by typing a raw ticker. The table lives in SQLite and holds every mainboard NSE equity plus the curated instruments, so search is instant and can never be rate-limited mid-keystroke. It is built from NSE's own daily bhavcopy via `jugaad-data` (already a dependency) and refreshed weekly. Yahoo's `/v1/finance/search` endpoint was the original plan but it is unofficial and rate-limits under load — keep it as a possible fallback for the long tail, not the primary path.
- The table covers NSE and the curated set, so when a search returns nothing, offer the typed text as a raw ticker. That keeps US equities and anything else reachable; `add_to_list` validates it has data before accepting it.
- Rank curated instruments above bulk NSE rows in search results, otherwise ETFs whose tickers begin with "NIFTY" or "GOLD" bury the actual index and the gold contract.
- Resolve a friendly name when an instrument is added, falling back to the raw ticker. Read it from the chart endpoint's metadata (`Ticker.history_metadata`), not `Ticker.info` — `.info` uses Yahoo's quoteSummary endpoint, which returns 404 for tickers whose price data loads fine.
- **Superseded (5 October 2026).** The active view now lives in `st.session_state`, not the URL. The URL rule existed because the view chips and the remove control were HTML links, and a link click is a page navigation that session state does not reliably survive. They are native widgets now — `st.pills` and `st.button` — so nothing navigates and session state is the right home. Measured: 0 page navigations across chip, add and remove; a chip click went from 761ms (link) to a 406ms median rerun.
- To clear a text input after a successful submit, change the widget's key (a nonce). Deleting the key alone does not empty the widget.
- Keep entry controls secondary to the heading. Label the change window separately from chart history.
- Keep failed instruments visible and removable; preserve tile geometry while loading.

### Chart
- Keep existing field labels visible and existing empty-state text readable. Do not add examples or new empty-state content.
- Align existing instrument name, ticker, value, unit, and period-change information above the chart; do not introduce fields absent from the live tab.
- Make the local history range authoritative, or explicitly label the scope of any global control. Never let two controls imply authority over the same chart.
- For a single series, use a neutral line or the specified orange accent consistently. For comparisons, use black solid versus gray dashed lines and explicit labels; never use orange as a comparison-series identity. Reserve comparison orange for negative-change badges.
- Label both axes and units on dual-axis charts. Keep neutral tick text at 12-13px.

### Indices and India
- Restyle both tabs with the same three-column small-multiples component. Align existing India heading/context with Indices; do not add new content.
- Use larger values, square ruled boundaries, and matching name/value/chart baselines.
- Use thin direction-coloured lines with no fill. Do not create saturated loss-filled panels.
- Format constituents/weights as a readable two-column ruled popover table. Right-align tabular weights and label them "approximate". Normalize axis scale formatting across panels.

### FX
- Use a 2×2 layout for the four pairs, collapsing on narrow screens.
- Preserve four decimals and trailing zeros. Keep existing pair, quote-convention, and period labels readable; use "quote per 1 base" when revising an existing convention label.
- Treat direction as change in the quoted rate, not good or bad.

### Commodities
- Reuse the three-panel component and use restrained or no fills.
- Verify the data mapping, contract metadata, and units before displaying labels such as `$/barrel` or `$/ounce`. The reviewed screen does not establish them; leave uncertain labels unresolved rather than guessing.

### Volatility
- Restyle the existing single-instrument layout with a dominant VIX value and larger chart. Keep existing unit/period labels readable; VIX units are index points.
- Do not invent filler indicators. Apply the same signed-change convention; higher VIX is not a green win.

### Shared charts
- Match plot heights and padding. Use neutral ticks, faint horizontal gridlines, and minimal vertical gridlines.
- Include years in multi-year x-axis labels and exact units on hover.
- Keep panel geometry stable while loading; use in-panel feedback rather than dimming the whole page.

## 4. Known issues to fix

These are supplied design-sweep/QA findings from 3 October 2026, not a fresh code audit. Fix in this severity order and test each result.

| Severity | Issue | Suggested fix |
| --- | --- | --- |
| High | A hidden failed ticker still counts toward the 30-ticker cap; Add disables without explanation. | Render a "Couldn't load X" tile with a remove button. Explain the cap whenever Add is disabled, including failed entries. |
| High | Remove is an 11px link; it opens a new tab and leaves the original stale. | Fixed, then superseded (5 October 2026). Remove is now a real `st.button`, so it reruns instead of navigating. The old "`st.button` cannot be placed inside `st.markdown`" constraint no longer binds: `st.container(horizontal=True, wrap=True)` gives the flex-wrap layout natively. |
| Medium | Presets silently replace the entire watchlist. | Obsolete — index presets are now read-only views that replace nothing, so there is nothing to confirm. |
| Medium | 3Y takes 20-30 seconds; clicks take 5-15 seconds and dim the whole page. | Batch compatible yfinance downloads and use an in-panel `st.spinner`. Keep SQLite as the cache layer — do not add `@st.cache_data`, it conflicts with existing SQLite caching and loses data on reboot. |
| Medium | "Trader Desk" is clipped under the toolbar on desktop and mobile. | Use appropriate `.block-container` top padding (about `3rem` as a starting point) or `st.title`; check both viewports. |
| Medium | Global `1W-3Y` and Chart `1M-3Y` range controls disagree. | Merge them or clearly scope them; make the Chart tab's authoritative range explicit. |
| Low | Add input retains stale text after a successful add. | Clear its widget/session-state value after success, without clearing failed input prematurely. |
| Low | Bad-ticker banner pushes tiles down about 72px. | Reserve feedback space or use `st.toast`; keep failed-entry details accessible. |
| Low | 3Y repeats "01 Jan" without a year; 1W has four points and a blank gap. | Include years on multi-year ticks; inspect the 1W data/calendar mapping and range padding. Do not invent points for non-trading days. |
| Low | Page width shifts about 10px when the scrollbar appears. | Reserve scrollbar space where supported and verify stable alignment. (Friendly-name resolution is done.) |

**Untested:** verify the Watchlist `1D`/`1W` toggle changes the intended calculation, label, and state. Do not treat its behavior as confirmed.

## 5. Decisions pending

- **Watchlist ownership:** the supplied QA notes say the public app shares watchlist state across visitors. Decide whether this is intentional or whether watchlists should be per-session or per-user. Do not silently change persistence or treat this as a styling fix.
- **Proposed, not verified:** exact hex tokens, font stack, type-scale values, non-FX decimal defaults, and locale grouping above are implementation proposals. Keep them centralized and consistent; confirm or revise them rather than adding ad hoc alternatives.
- **Verification required:** commodity contracts/units and source mappings; actual `1D`/`1W` behavior. Do not claim these checks are complete from this document alone.


## Dark mode: Charcoal Warm

Use Charcoal Warm as the dark variant of Swiss Editorial. Change colors only. Keep layout, typography, spacing, grid, hierarchy, component geometry and behavior identical to the existing design language. These are the chosen palette values, not alternatives.

### Color tokens

| Token | Hex | Apply to |
| --- | --- | --- |
| `canvas` | `#24211E` | App background; labels on filled selected controls |
| `card_bg` | `#302B27` | Existing cards, panels, widget surfaces and tooltip backgrounds |
| `card_border` | `#554A40` | Existing card, panel and control borders; subtle chart gridlines |
| `primary_text` | `#ECE5DA` | Headings, body text, key values and neutral chart series |
| `secondary_text` | `#AAA197` | Supporting labels, metadata, axis labels and comparison series |
| `gain_color` | `#9AC69C` | Positive changes, gains and positive chart marks |
| `loss_color` | `#E8A078` | Negative changes, losses and negative chart marks |
| `accent_selected` | `#D8B58A` | Selected/active states, focus indicators and existing highlights |
| `rule_color` | `#96816A` | Existing editorial rules, separators and chart axis/zero lines |

Keep gains green and losses warm orange — the same direction convention as light mode, which Adarsh's 3 October decision already settled (green gains, terracotta losses). The "black-gain" wording refers to the original pre-override spec; there is no clash with light mode, and **where this dark section and an earlier decision ever do disagree, the earlier decision wins and light mode is left alone.**

Use `accent_selected`, never `loss_color`, for selection or active navigation. On filled selected controls, use `accent_selected` for the background and `canvas` for the label. This applies to dark only: light mode keeps its terracotta nav underline and near-black selected chip.

### Application rules

- Define the palette once. Map every existing hardcoded color to a semantic token, including inline styles, widget states, tables, tooltips and chart configuration. Do not perform blind hex replacement where the same old color has different meanings.
- Do not introduce colors outside this palette. Derive any existing translucent treatment from a token with explicit alpha; do not add standalone tint hex values. Do not fade text below the contrast floor.
- Preserve existing hover, focus, disabled and selected behavior. Change only their color assignments. Do not use gain/loss colors for unrelated controls.

Use these chart color defaults; retain existing chart geometry, series order, line styles and data meaning:

| Chart role | Token derivation |
| --- | --- |
| Plot/paper background | Match the containing surface: `card_bg` or `canvas` |
| Main neutral line | `primary_text` |
| Comparison line | `secondary_text`; retain the existing dashed style |
| Positive/negative marks | `gain_color` / `loss_color` |
| Existing selected mark | `accent_selected` |
| Existing area fill | Corresponding line token at 12% opacity; do not add fills |
| Axis labels, ticks, legend text | `secondary_text` |
| Axis and zero lines | `rule_color` |
| Gridlines | `card_border`, opaque |
| Tooltip | `card_bg` background, `card_border` border, `primary_text` text |

Do not apply gain/loss semantics to a neutral series. Do not let chart-library defaults inject new colors. If more series need distinction, retain their existing dash/marker distinctions rather than adding hues.

### Streamlit implementation

Set the dark theme's native colors in `.streamlit/config.toml`: `primaryColor = "#D8B58A"`, `backgroundColor = "#24211E"`, `secondaryBackgroundColor = "#302B27"`, and `textColor = "#ECE5DA"`. Use the installed Streamlit version's supported dark-theme configuration. Preserve the light theme and all existing font, sizing, radius and border-width settings.

Use native theme options where supported. Use scoped CSS overrides for remaining semantic colors and component states; keep selectors narrow and verify them after Streamlit updates. Configure chart colors explicitly rather than relying on theme inheritance. Check sidebar surfaces and selected-label colors, since native widget defaults may differ from this mapping.

**Implemented (5 October 2026).** Streamlit 1.64 takes both themes at once: `[theme]` holds light, `[theme.dark]` holds Charcoal Warm, and the viewer switches in the toolbar (⋮ → Settings → Appearance) or follows the system setting. Light is untouched.

How the palette is wired, so later work extends it rather than re-hardcoding:

- `app.py` defines `_LIGHT` and `_DARK` dicts with identical role keys and picks one via `st.context.theme.type`. The existing token names (`UP`, `DOWN`, `BG`, `SURFACE`, `BORDER`, `GRID`, `T1`-`T3`) read from that dict, so Plotly and inline HTML follow the theme with no call-site changes.
- The same values are emitted as CSS custom properties on `:root` (`--canvas`, `--card-bg`, `--loss`, `--accent`, …), and the main stylesheet references `var(--token)` only. **Add no raw hex to `app.py`** — add a role to both dicts instead.
- `ACCENT`, `SELECTED` and `RULE` are separate tokens, not aliases of `DOWN`. In light, `accent` is terracotta and `selected` is near-black; in dark, both are `#D8B58A`. This is the "same old color, different meanings" trap the rules above warn about: the nav underline and selected chip must not inherit the loss color in dark.
- Dark has no distinct tertiary: `t3` maps to `secondary_text`, keeping de-emphasised controls in-palette (5.50:1, above the floor).
- Translucent button fills derive from the loss token with explicit alpha (`--loss-soft`, `--loss-hover`), replacing the off-palette `#c74424`/`#d4a89a` tints that had drifted into the light stylesheet.

Contrast re-verified by calculation against both dark surfaces: text roles 5.50:1 to 12.80:1, `canvas` on `accent_selected` 8.31:1 — matching this section's stated figures.

### Contrast and scope

Require at least **4.5:1** contrast for text against its actual background. Verified by sRGB relative-luminance calculation: the text-role colors above are **5.50:1 or higher** on both `canvas` and `card_bg`; the `canvas` label on `accent_selected` is **8.31:1**. Borders and rules are not text colors. Recheck any alpha treatment or different foreground/background pairing.

Restyle only. Add no elements, panels, controls or chart content. Change no layout, typography, spacing, dimensions, hierarchy, functionality or data logic.
