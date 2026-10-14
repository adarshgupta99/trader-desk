# Trader Desk — Architecture & Code Flow

> **Keep this file current.** Update it whenever files are added, removed, or significantly changed. Remove outdated sections — don't leave stale info.

---

## What This App Is

A personal trading desk web app built in Streamlit. You type a ticker, it shows you market data. Over 9 weeks it will grow to include news, AI tagging, alerts, and backtesting.

**Live at:** Streamlit Community Cloud (deployment in progress as of Week 1)
**Local URL:** `http://localhost:8501` (run `streamlit run app.py`)

---

## File Map

```
trader-desk/
├── app.py              # Entry point: page setup, theme, nav, dispatch. ~110 lines
│
├── tabs/               # One module per section, each exposing render()
│   ├── watchlist.py
│   ├── chart.py
│   ├── asset_grids.py  #   Indices, India, FX, Commodities, Volatility (one renderer)
│   ├── events.py
│   └── correlation.py  #   F&O pairwise correlation (Most/Least correlated tables)
│
├── theme.py            # Both palettes, and the stylesheets built from them
├── formatting.py       # Number / change formatting rules (signs, decimals, INR)
├── components.py       # The price card + chart shared by five tabs
├── periods.py          # The history ranges, and date-range price lookups
├── instruments.py      # Curated asset lists, and the instrument picker
├── constituents.py     # Approximate index weights for the card popovers
│
├── storage.py          # Where market_cache.db lives, and its schema
├── prices.py           # Price history and its cache (yfinance + jugaad-data)
├── watchlists.py       # Custom lists, and the read-only index views
├── symbols.py          # The searchable instrument table
├── events_db.py        # Read-only access to events.db (events + earnings_estimates tables)
├── fno.py              # The 213-stock F&O universe and sector lookup (fno_sectors.json)
│
├── analytics/
│   ├── tiers.py         # pick_tier(lookback_days) -> "15m" | "1d", and expected-obs sizing
│   ├── correlation.py   # Bars -> log returns -> pairwise Pearson; the stale-bar policy
│   ├── sector_matrix.py # 15x15 mean-correlation rollup + drill-down data (spec sections 5/5A/5B)
│   ├── stock_matrix.py  # Cluster order + preview selection for the stock-level matrix (spec section 6)
│   ├── rolling.py       # Rolling correlation vs NIFTY/another stock/sector basket, top-5 partners (spec section 7)
│   └── expected_move.py # Pure calc engine for the expected-move feature: release-mode classification, reference-bar/window selection, print/cohort move math (spec: expected_move_spec-3c050a43.txt)
├── data_layer/
│   └── intraday_store.py  # SQLite bar store outside the repo (~/trader_desk_data/intraday.db)
│
├── data/
│   └── symbols.csv     # Committed instrument table (3,360 rows) — search's source of truth
├── scripts/
│   ├── refresh_symbol_names.py  # Maintenance: pull full NSE company names into symbols.csv
│   ├── fetch_events.py          # Writes events.db from NSE's two event feeds
│   ├── discover_nse.py          # One-off: dump nselib's real output shape
│   ├── discover_intraday.py     # Step 0 for the correlation feature: measures Yahoo's 15m feed
│   ├── discover_estimates.py    # Step 0 for the estimates feature: yfinance estimate shapes
│   └── discover_expected_move.py # Step 0 for the expected-move feature: NSE archive shape, filing-clustering patterns
├── jobs/
│   ├── fetch_intraday.py        # Daily cron: downloads 15m + daily F&O bars into the store
│   ├── snapshot_estimates.py    # Daily T-1 snapshot of analyst consensus (earnings_estimates)
│   ├── fill_actuals.py          # Fills actual EPS/revenue + surprise % once results are out
│   ├── refresh_expected_moves.py # Discovers results filings via the NSE archive, fetches price bars, computes and stores expected-move prints/summaries
│   ├── refresh_earnings_history.py # Backfills 6 reported quarters of revenue/PAT per symbol for the Calendar subtab's HISTORY 6Q sparkline
│   └── refresh_earnings_sentiment.py # Week 5-6 task: per-watchlist-symbol transcript fetch + LM word counts + Gemini tone/guidance extraction
├── db/
│   ├── schema_estimates.sql     # earnings_estimates table + the immutability trigger
│   ├── schema_expected_move.sql # earnings_move_prices/prints/summaries tables + the identity-freeze trigger
│   ├── schema_earnings_history.sql # earnings_history_quarters table - one row per (symbol, quarter_end), gaps stored not dropped
│   └── schema_sentiment.sql     # earnings_call_sentiment table - Track A (LM) + Track B (Gemini) per (ticker, quarter_end)
├── docs/
│   ├── intraday_discovery.txt   # Output of discover_intraday.py — the numbers the correlation feature is sized against
│   ├── estimates_discovery.txt  # Output of discover_estimates.py, plus the nselib-actuals discovery note
│   └── expected_move_discovery.txt # NSE archive filing-clustering rules the refresh job's identify_filings() encodes
├── data/bars_daily_500d.db  # Committed daily-bars subset (~10MB) so the DEPLOYED app's Correlation tab has data; read-only fallback when no local store exists
├── data/lm_dictionary.json  # Loughran-McDonald positive/negative/uncertainty/litigious wordlists (Notre Dame SRAF, March 2026 edition), extracted down from the ~86,500-word master CSV
├── nse_results.py       # NSE integrated-filing XBRL -> quarterly actuals (EPS/revenue/PAT); handles YTD-context, bank-taxonomy and standalone-vs-consolidated traps; also quarter_window/previous_quarter_end for rolling N-quarter windows
├── nse_transcripts.py    # Finds and extracts NSE-filed earnings-call transcript PDFs from the corporate-announcements archive (same archive as refresh_expected_moves.py)
├── earnings_sentiment.py # Track B: Gemini structured-output extraction (tone/guidance/risks/hedging), management and Q&A scored separately
├── analytics/sparkline.py # Pure scaling/QoQ/colour logic for the HISTORY 6Q column - min-max blocks, gap handling, no HTML
├── analytics/lm_sentiment.py # Track A: pure Loughran-McDonald word counting, no network, fully reproducible
├── trading_calendar.py  # NSE trading-day wrapper (is_trading_day, next_trading_day, session_bounds_ist) for the expected-move window logic
├── tests/
│   ├── test_correlation.py      # Acceptance checks A-K for the correlation feature
│   ├── test_estimates.py        # Acceptance checks A-G for the analyst estimates feature (H is manual)
│   ├── test_nse_results.py      # Fixture tests for nse_results.py: YTD-context trap, bank taxonomy, filing selection, quarter_window
│   ├── test_sparkline.py        # 16 fixture tests for analytics/sparkline.py: scaling, gaps, QoQ direction, negative values
│   ├── test_refresh_earnings_history.py # Regression test: a ValueError (deterministic parse failure) must fail fast, not burn the retry schedule
│   ├── test_lm_sentiment.py     # Fixture tests for analytics/lm_sentiment.py: tokenization, normalization, zero-division, category overlap
│   ├── test_nse_transcripts.py  # Fixture tests for nse_transcripts.select_transcript: picks the real transcript over the recording/schedule rows in the same category
│   ├── test_refresh_earnings_sentiment.py # Regression test: a quarter already stored in earnings_call_sentiment is never reprocessed
│   ├── test_expected_move.py    # 26 fixture tests for analytics/expected_move.py's pure calc engine
│   └── test_refresh_expected_moves.py # Regression tests for 2 real bugs caught during the live backfill (abbreviated month parsing, same-day-sibling vs. genuine revision)
├── fno_sectors.json    # 213 F&O stocks with sector labels (hand-supplied, 5 Oct 2026)
├── .github/workflows/
│   └── test_nse_access.yml      # Manual-only: does NSE answer GitHub's runner IPs?
├── .streamlit/
│   └── config.toml     # Streamlit chrome colours: [theme] light + [theme.dark] Charcoal Warm
├── events.db           # Committed SQLite file: upcoming F&O events (read-only in the app)
├── market_cache.db     # Auto-generated SQLite database (gitignored)
├── requirements.txt    # Python dependencies
├── news_loader.py      # Week 3 RSS/GDELT work; currently unused — nothing imports it
├── test_key.py         # One-off script to verify Gemini API key works
├── .env                # Your secrets (gitignored, never committed)
├── .venv/              # Python 3.13 virtual environment (gitignored)
├── CLAUDE.md           # Project spec and weekly plan
└── ARCHITECTURE.md     # This file
```

**Where to put new code.** A new section is a new `tabs/<name>.py` with a
`render()`, imported in `app.py` and added to `NAV`. Anything two sections both
need goes in a module beside `app.py`, not into `app.py` itself — `app.py` is
meant to stay short enough to read in one screen.

---

## How Data Flows Right Now

```
User types a ticker in the browser
        │
        ▼
    app.py  →  tabs/<section>.py
        │  calls get_price_history(ticker, period, interval)
        ▼
    prices.py
        │
        ├─ checks SQLite (market_cache.db)
        │       │
        │       ├─ Cache hit + less than 6 hours old?
        │       │       └─ return DataFrame from DB  ──────────┐
        │       │                                              │
        │       └─ Cache miss or stale?                        │
        │               │                                      │
        │               ▼                                      │
        │         yfinance (Yahoo Finance API)                 │
        │               │                                      │
        │               └─ save to SQLite, return DataFrame ───┤
        │                                                      │
        ▼                                                      │
  tabs/<section>.py  ◄─────────────────────────────────────────┘
        │  df["Close"] → a plotly figure (components.py, or the tab's own)
        ▼
  Browser shows the chart
```

---

## Module Details

### `app.py`
The Streamlit entry point, and nothing else: page config, theme, the section
navigation, and an `if/elif` that calls one `render()`. Around 110 lines.

It was 1,550 lines until 5 Oct 2026, holding the design tokens, the
stylesheets, number formatting, the asset definitions, the chart helpers and
all eight section bodies. Those are now the modules in the file map above.
Nothing about the app's behaviour changed in that move.

---

### `tabs/`
One module per section. Eight sections, four modules — Indices, India, FX,
Commodities and Volatility were five near-identical blocks differing only in
asset list, column count, decimals and chart size, so they share
`asset_grids.render(name)` driven by a `GRIDS` table.

Each section owns its own history selector — there is no shared one in the
header.

**Navigation is a `st.segmented_control`, not `st.tabs`.** `st.tabs` runs every tab's
body on every rerun, so all 53 charts were rebuilt on every click no matter which tab
was showing — proven by Indices (8 charts) costing 667ms and Volatility (1 chart)
costing 651ms. An `if/elif` chain on the selected section builds only what is visible:
Indices 667→422ms, Volatility 651→324ms, Commodities 734→352ms. The trade-off is that
switching sections now costs a server round-trip (~382ms) where `st.tabs` switched
instantly in the browser. Roughly 300ms of every figure above is Streamlit's websocket
round-trip floor, so that is the practical lower bound for any click.

`required=True` on the control matters: without it, clicking the already-active
section deselects it and the app silently falls back to the first section.

**Sections:**

| Section | Contents |
|---|---|
| Watchlist | Nine views via two chip rows: 5 read-only index views + 4 editable custom lists (≤30 each); 1D/1W toggle; × to remove. Index views also show the index's own price, signed change and last-close date beside the toggle |
| Chart | Plot 1–2 instruments on dual y-axis; preset history **or** explicit start/end dates; Measure tool for click-to-click % change; persists instruments across section switches |
| Indices | S&P 500, Nasdaq, Nifty 50, KOSPI, FTSE 100, DAX, Euro Stoxx 50, Nikkei 225 |
| India | Bank Nifty, Nifty IT, Nifty Auto, Nifty Energy, Nifty Mid Select, Nifty Defence |
| FX | USD/INR, EUR/USD, GBP/USD, EUR/INR |
| Commodities | WTI Crude Oil, Gold, Silver |
| Volatility | VIX (US) |
| Events | Upcoming F&O board meetings and declared corporate actions, in two subtabs |
| Correlation | Four chip-selected sections (`st.pills`, one shown at a time): Sector overview (15x15 mean correlation with click-through drill-down), Stock matrix (cluster-ordered heatmap of the full eligible universe, previewed down to 60), History (rolling correlation vs NIFTY 50 / another stock / a sector basket, plus frozen top-5 partner panels), Pick one stock (Most/Least correlated tables, any 1-500 day lookback) |

Rates tab is parked until `FRED_API_KEY` is added.

**Key functions, and where they now live:**

| Function | Module |
|---|---|
| `price_chart(label, ticker, period)` — plotly line chart, y-axis scaled to spread, green/red by direction | `components.py` |
| `asset_card(label, ticker, period, decimal)` — card with CSS-restyled border, label, price, chart | `components.py` |
| `_constituents_popover(label, members)` — `st.popover` with approx. index weights | `components.py` |
| `render_grid(assets, period, cols, decimal)` — grid of `asset_card()` calls | `components.py` |
| `_watchlist_grid(watchlist, show_1w, list_id)` — native `st.container(horizontal=True, wrap=True)` of bordered tiles; remove is an `st.button`. `list_id=None` for read-only index views, which draws no remove control | `tabs/watchlist.py` |
| `_pick_index()` / `_pick_custom()` — `on_change` callbacks for the two chip rows; one selection between them, so picking in one clears the other | `tabs/watchlist.py` |
| `range_history(ticker, start, end)` — price history between two dates | `periods.py` |
| `display_name(ticker)` — friendly name for display, via the local `symbols` table; falls back to the raw ticker | `instruments.py` |
| `instrument_options()` — builds `(labels, label → ticker)`; label is the name alone, with the ticker appended only when two instruments share a name (Volkswagen, Jain Irrigation) | `instruments.py` |
| `pick_instrument(key, placeholder, ...)` — the searchable dropdown used by Watchlist and Chart | `instruments.py` |

**The Chart tab's controls are two rows.** Row 1: Instrument 1, Instrument 2, Start, End —
four columns. Row 2: the History presets and the Measure toggle, side by side.

**The Chart tab's date range.** One range with two ways to set it: the 1M–3Y presets write
the Start and End dates, and editing either date clears the preset highlight, so only one
control ever claims the chart (the spec forbids two controls implying authority over the
same chart). The summary line beside the price shows the preset label when one is active
and the literal `10 Jun 2026 – 05 Oct 2026` otherwise.

`range_history()` exists because `get_price_history()` only accepts preset periods and
SQLite caches on them — a cache key per date pair would be useless. It picks the smallest
preset that still reaches back to `start` (`PERIOD_COVERS`) and slices the frame to the
range, so arbitrary dates reuse the same cached fetches the preset buttons already warmed.

Start dates are capped at 3 years back (`MAX_HISTORY_DAYS`). That is the furthest the
loader will go: `_fetch_from_nse()` maps any period it doesn't recognise to 35 days, so a
longer request would quietly return *less* data for the Indian indices rather than erroring.
Lifting the cap means extending `_PERIOD_TO_DAYS` in `prices.py` first.

**How instrument search works.** `pick_instrument` is an `st.selectbox` with
`filter_mode="contains"` and `accept_new_options=True`. Streamlit sends only the
*formatted* option strings to the browser and filters against those, so whatever the
dropdown displays is exactly what is searchable — `format_func` cannot hide a
searchable ticker behind a shorter label. That is why the instrument **names** have to
carry the searchable words (see `scripts/refresh_symbol_names.py`). Anything outside the
table stays reachable because `accept_new_options` offers the typed text as a raw ticker,
which `add_to_list` then validates against real price data.

**The Measure tool** (Chart tab). Click two points on the chart to see the change between
them. Plotly's `on_select` selection lands in `st.session_state[MEASURE_KEY]` as a single
replace-not-accumulate value, so the two picked points are tracked by hand in
`st.session_state.measure_a` / `measure_b`, each new click compared against the last one
processed (via its x/point_index "signature") so an unrelated rerun doesn't get mistaken
for a fresh click.

Streamlit silently **drops the whole click** if a single click event reports points for
more than one trace — which `hovermode="x"` causes whenever a second series is also
hoverable. The fix: only one trace stays hit-testable (`hoverinfo="skip"` on the rest), and
any other series' data rides along in that one trace's `customdata`/`hovertemplate` instead
of getting its own tooltip. With a second instrument plotted, its close price is aligned to
the first instrument's dates (`series2_at_t1 = df2["Close"].reindex(df1.index,
method="nearest")`) so a single click-target trace can carry both instruments' values.

Once point A is picked, hovering shows both instruments' % change from that point
continuously, computed client-side via pre-baked `customdata` — no server round-trip per
mouse move. The final readout (after point B) shows % and absolute change for both
instruments, bar count, and elapsed time. Display text uses `display_name(ticker)` (looks
up the local `symbols` table) everywhere a ticker would otherwise show — `^NSEBANK` reads
as "Bank Nifty". The underlying ticker variables are kept separately for currency/decimal
formatting, which depends on ticker suffixes (`.NS`, `=X`, etc.).

**Nothing in the Watchlist navigates.** The view chips, the remove control and the error
banner were all built on `<a href>` links and `st.query_params`, which meant every click
was a full page load — the page blanked, scroll position reset and the websocket
reconnected. They are native widgets now (`st.pills`, `st.button`) with the active view in
`st.session_state.wl_view` and errors in `st.session_state.wl_err`. Verified in a browser:
0 main-frame navigations across chip, add and remove, and a JS global set at load survives
every interaction. A chip click went from 761ms to a 406ms median. The only remaining
query param in the app is the clear-cache link.

All price data goes through `get_price_history()` from `prices.py`.

---

### `theme.py`
Both palettes, and the stylesheets built from them.

**Design tokens:** `UP`, `DOWN`, `BG`, `SURFACE`, `BORDER`, `GRID`, `T1`, `T2`, `T3`,
`ACCENT`, `SELECTED`, `RULE`, `TOOLTIP_BG` — attributes on a `Palette`, reached as
`P.T1`, `P.UP` and so on.

**Two themes, one set of tokens.** `_LIGHT` (Swiss Editorial) and `_DARK` (Charcoal Warm)
are dicts with identical keys. `inject_css(P)` emits the chosen one as `:root` CSS
variables (`--canvas`, `--card-bg`, `--loss`, `--accent`, …) and then a plain non-f
stylesheet that references only `var(--token)` — **every raw hex in the app is now inside
`theme.py`'s two dicts**, and anything written elsewhere will not follow the theme.
`.streamlit/config.toml` carries the matching `[theme]` and `[theme.dark]` sections for
Streamlit's own chrome.

**The palette is resolved per rerun, not at import time.** `theme.palette()` reads
`st.context.theme` on every call. This used to be a module-level `THEME = …` and worked
only because it lived in the top-level script, which Streamlit reruns; in an imported
module it would freeze on whichever theme the first visitor had, because Python caches a
module for the life of the process. The two `Palette` instances are immutable, so they
are built once and reused — only the choice between them is per-run.

`ACCENT` and `SELECTED` are deliberately separate from `DOWN`. In light all three happen to
be the same terracotta, so a blind hex swap would have turned the active-nav underline and
the selected chip into "loss" colours in dark. See the dark-mode section of CLAUDE.md.

**`sector_colors()` is a scoped exception to the one-accent rule**, agreed with Adarsh 6 Oct
2026: 15 muted, desaturated hues (`_SECTOR_HUES_LIGHT`/`_SECTOR_HUES_DARK`), one per
`fno.DISPLAY_GROUPS` entry, used *only* inside the Correlation tab's sector matrix and
drill-down (legend swatches, column grouping) — never for navigation, controls, or anywhere
else in the app. Correlation *values* still render on the existing `UP`/`DOWN` scale; this
palette is sector identity only, so the new-colour footprint stays confined to one feature.

---

### `storage.py`
Where `market_cache.db` lives, and its schema. One database holds three unrelated things
because they are all per-install state with the same lifetime. Each table's own module
owns the queries against it; this module only owns the file and its shape.

Every caller opens a short-lived `get_connection()` and closes it, rather than holding one
across a Streamlit script run.

**SQLite tables:**
- `price_cache` — OHLCV rows keyed by (ticker, period, interval, date)
- `cache_meta` — last fetch timestamp per (ticker, period, interval)
- `symbols` — (ticker, name, kind); rebuilt from `data/symbols.csv` on every startup
- `symbols_meta` — bookkeeping for the symbols table
- `custom_lists` / `custom_list_items` — the four editable watchlists

---

### `prices.py`
Price history, and the SQLite cache in front of it. Two providers behind one function:
yfinance for everything, and jugaad-data for the NSE indices yfinance does not carry.

```python
get_price_history(ticker, period="1mo", interval="1d", max_age_hours=6)
→ DataFrame with columns: Open, High, Low, Close, Volume

clear_price_cache()                    → empties price_cache and cache_meta
```

**Daily candles are built from hourly bars, not from yfinance's daily feed.** Callers still
get one row per trading day — that contract hasn't changed — but for any date inside
yfinance's 1h retention window, `_fetch_daily_via_hourly()` resamples hourly bars into that
day's candle: open = first bar, high/low = session extremes, close = **last bar**, volume =
summed. The last hourly bar is the real close the moment it prints, where yfinance's daily
feed for Indian tickers can sit null or omit the date entirely for hours after a session
ends — the bug behind a Watchlist tile showing "as of 01 Oct" on the 6th. Verified against
jugaad-data's official closes for 5 Oct: `^NSEI` 22,555.75, `^NSEBANK` 54,714.10,
`RELIANCE.NS` 1,186.40 — exact to the last decimal on all three.

Sessions are bucketed on the hourly index's own **exchange-local** date, so an NSE day and a
New York day each collapse as one candle without a UTC conversion splitting or merging them.
The close is taken positionally (last bar), not by hardcoded clock time, so it holds across
exchanges — NSE's 15:15 bucket, New York's 15:30 — and a session still in progress yields
its latest price rather than nothing.

**The daily feed is permanently still needed for older dates.** Yahoo hard-refuses any 1h
request reaching more than 730 days back (`_HOURLY_MAX_DAYS = 720`, staying under rather than
on the limit), for every ticker, Indian or not. That window is **rolling, anchored to today** —
it slides forward daily, so it never accumulates into a fixed multi-year archive. For periods
longer than it (`3Y` is 1095 days in `CHART_PRESETS`), the daily feed covers the older stretch
and hourly wins on every date both cover. Measured on `^NSEI` `3y`: 739 rows spanning the full
1096 days, no null closes. A hourly fetch that fails or returns nothing falls back to the daily
feed alone rather than taking the result down with it.

`interval` is the cache's row identity, not a request parameter to yfinance — the yfinance path
always asks for hourly and aggregates. All call sites pass the default `"1d"`.

**The cache window tightens to 10 minutes while NSE is open.** `_in_nse_session()` is true
Mon–Fri between 09:15 and 15:45 IST (the tail runs past the 15:30 bell so the closing hourly
bar is picked up once it prints), and inside it `max_age_hours` drops to 10 minutes — via
`min()`, so the session can only ever make the cache fresher than the caller asked, never
staler. Outside the session it is the usual 6 hours. There is no trading-holiday calendar: on
a holiday the window is still "open" and the hourly fetch just finds no new bars, costing one
wasted yfinance call and changing nothing — preferred over depending on a holiday list that
goes stale silently.

**Only instruments with a live intraday source get that window** — `_has_intraday_source()`.
That is the three yfinance Indian indices (`^NSEI`, `^NSEBANK`, `^CNXIT`), every `.NS` equity,
and the two INR pairs (the rupee's onshore market runs on the same clock). Two exclusions,
both deliberate:
- **Non-Indian instruments** (US/European/Asian indices, VIX, metals, crude, non-INR pairs)
  stay on 6 hours — their markets are shut or simply unrelated to this clock, so a 10-minute
  refresh re-fetches an unchanged number.
- **The `NSE:`-prefixed indices** (Nifty Auto, Energy, Mid Select, Defence) are Indian but
  excluded anyway: yfinance does not carry them at all, so jugaad-data's EOD feed is their
  *only* source and it does not publish until after the bell. A 10-minute window on them
  would be the same all-cost-no-data scraping the mid-session jugaad skip exists to avoid.

`_INDIAN_INDEX_TICKERS` is kept separate from `_JUGAAD_FALLBACK_INDEX` even though both
currently hold the same three tickers — one means "trades on the Indian session", the other
"has a second provider to merge from", and they would drift apart as soon as either changes
for its own reasons.

**jugaad-data is deliberately skipped mid-session.** Its feeds are EOD-derived and publish
after the bell, so during the session it holds nothing for today that the hourly bars don't
already have — and it is the scrape-based source with real NSE flagging risk, so calling it
every 10 minutes would be pure cost for zero data. It runs exactly as before once the session
ends, which is when it is actually the thing that supplies an official close. Historical rows
it previously filled are not at risk from this: the upsert only touches dates present in the
new fetch, so a date the hourly feed has no bars for keeps its cached value.

A `NSE:` prefix on the ticker routes to jugaad-data; everything else goes to yfinance —
except anything with a jugaad-data equivalent, where yfinance stays primary but every fetch
also merges in jugaad-data's close for any date the combined hourly-plus-daily result above
still left null or omitted. This step is unchanged by the hourly shift and still runs last.
That's Nifty 50
(`^NSEI`), Bank Nifty (`^NSEBANK`) and Nifty IT (`^CNXIT`) — the three Indian indices that
exist on **both** providers, via `index_raw` — plus every NSE-listed equity (any `.NS`
yfinance ticker), via jugaad-data's `stock_df`. Neither provider's Indian feed is reliably
ahead of the other near today's session — one has shown a real close before the other has it,
and vice versa, within the same evening — so this takes whichever has the data rather than
trusting one source. See `_jugaad_fallback()`. The other NSE-prefixed indices (Midcap, Auto,
Energy, Defence) have no yfinance equivalent to merge against. FX, commodities, and non-Indian
tickers have no jugaad-data equivalent at all.

`stock_df`'s `DATE` column needs its own fix-up before `_normalize_dates()` can treat it like
any other provider's output: it comes back as IST midnight minus 5:30 (`2026-10-04 18:30:00`
for the 5 Oct session) rather than a clean calendar date — the same east-of-UTC rewind already
fixed for `index_raw` (`b5eaf84`), reaching the app again through a different column shape on a
different jugaad-data function. `_fetch_equity_from_nse()` adds the offset back before anything
downstream sees it.

Every NSE equity now being in scope changes the cost profile: a refetch across a whole
watchlist can mean a jugaad-data call per holding, not just per index. The `max_age_hours=6`
cache in front of `get_price_history()` is the only throttle — there is no separate rate limit
or backoff on the jugaad-data calls themselves.

**A failed refetch falls back to the stale cache.** When the freshness window has expired the
fetch has to happen, and if it comes back empty — a Yahoo 429, a network blip — the cached rows
are served rather than nothing. Without this the app got 0 rows while good data sat untouched
in SQLite, showing a "Couldn't load" tile instead of a price a few minutes old. It matters
disproportionately under the intraday window, which expires every 10 minutes and so reaches
this path roughly 36× more often than the 6-hour one. `fetched_at` is deliberately *not*
updated on a failure, so the next interaction retries instead of treating the failure as a
successful refresh. A ticker with nothing cached still returns an empty (correctly shaped)
frame, which is what the Watchlist's "Couldn't load" tile is built on.

Known and unmitigated: Yahoo's actual rate limits are undocumented and change, and Streamlit
Community Cloud shares an egress IP between apps while Yahoo limits per IP — so a deployed
instance can take 429s caused by unrelated apps, independent of this app's own volume. The
fallback above is what keeps that from being user-visible as a broken tile.

**The cache never lets a worse fetch overwrite a better one.** `_write_to_cache()` used to
`DELETE` every cached row for a ticker before writing a fresh fetch — so a refetch that came
back with a null close (which yfinance's Indian-index feed can do while a session's close is
still landing) silently erased a previously-good cached value. It's now an upsert: `COALESCE`
keeps the old value when the new one is null, and still takes a real update when there is one,
genuine corrections included. This required converting `NaN` to actual SQL `NULL` before the
insert (`_none_if_nan()`) — `sqlite3` binds a `NaN` float as a real value, not `NULL`, so
`COALESCE` would never fire on raw pandas `NaN`.

**Dates are exchange-local trading dates.** yfinance returns daily bars stamped at
exchange-local midnight (`2026-10-02 00:00:00+09:00` for KOSPI). `_normalize_dates()` keeps
that calendar date and re-tags it as UTC midnight — the same convention `_fetch_from_nse()`
already used — rather than converting to true UTC, which rewound the date by a day for every
market east of UTC. Both providers' output is normalized before being cached, compared, or
merged, so storage stays one consistent format and the two providers' dates line up exactly
when `_fill_gaps_from_nse()` merges them.

---

### `watchlists.py`
The four editable custom lists, and the five read-only index views beside them.

```python
get_custom_lists()                     → the four editable lists
rename_custom_list(list_id, name)      → (ok: bool, message: str)
get_list_items(list_id)                → list of {"ticker": ..., "label": ...} dicts
add_to_list(list_id, ticker, label)    → (ok: bool, message: str); validates the ticker has data
remove_from_list(list_id, ticker)      → None
count_list_with_data(list_id)          → int, for the 30-instrument cap
```

**Index views** (`PRESET_NAMES`, `WATCHLIST_PRESETS`, `PRESET_INDEX_TICKER`): Nifty 50,
Nifty Midcap, Nifty Auto, Nifty IT, Bank Nifty. These are read-only views, not presets that
get loaded — switching between them replaces nothing, so there is no confirmation step.
They are plain data in this file, not database rows.

---

### `symbols.py`
The instrument universe. `CURATED_INSTRUMENTS` is the hand-picked set the per-tab grids are
built from; the searchable table is wider and comes from `data/symbols.csv`.

```python
refresh_symbols()                      → reloads the symbols table from data/symbols.csv
get_all_symbols()                      → every row, curated instruments ranked first
```

`symbol_count()`, `export_symbol_seed(path)` and `search_instruments(query, limit)` are
also here but **nothing in the app calls them** — they read as deliberate helpers for a
Python shell, so they were kept when the module was split rather than quietly deleted.
Worth a decision either way.

---

### `periods.py`, `instruments.py`, `constituents.py`, `formatting.py`, `components.py`
The small shared modules. `periods.py` holds `PERIODS`, `CHART_PRESETS`,
`MAX_HISTORY_DAYS` and `range_history()`; `instruments.py` the curated asset lists and the
picker; `constituents.py` the approximate index weights; `formatting.py` the signed /
decimal / INR-grouping rules; `components.py` the price card and chart shared by five tabs.

**1D change added to every small-multiples card (13 Oct 2026).** Adarsh asked to show each
index/asset's latest 1-day return on the Indices/India/FX/Commodities/Volatility grids,
alongside whatever period is selected (1M by default) - "don't add a different button,
just add a print": plain text, no new control. `get_price_history()` already returns one
row per trading day regardless of the requested period, so `price_chart()` computes it for
free from the last two rows already in hand (`last / df["Close"].iloc[-2] - 1`) rather than
a second fetch - `None` when there's no prior row yet (a one-row period or a brand-new
instrument), rendered as nothing rather than a blank "1D" badge. Printed next to the date
under the big value, signed and colour-coded the same as the existing period badge
(`signed_pct`, `P.UP`/`P.DOWN`). `price_chart()`'s return signature grew a 5th value
(`day_pct`); it has exactly one caller (`asset_card`), confirmed by grep before changing
it. Verified via `AppTest` across all five grid tabs, zero exceptions; Indices' S&P 500
card confirmed rendering `-0.04% 1D` in signal terracotta next to `12 Oct 2026`. Suite
74/74 (no existing test touched this code path).

---

### `data/symbols.csv`
The instrument table every search reads from: 3,360 rows of `ticker,name,kind`, committed
to the repo. `refresh_symbols()` wipes and reloads the SQLite `symbols` table from this file
on every startup, so the CSV is the single source of truth and the app makes **no network
calls to build it**. That matters because Streamlit Cloud wipes the filesystem on reboot —
an earlier design fetched symbols at runtime and silently shipped a partial table whenever a
source was unreachable. Cold start went from ~93s to ~0.01s when this replaced it.

Breakdown: NSE equity 2,662 · S&P 500 503 · FTSE 100 100 · DAX 40 · Euro Stoxx 50 33 ·
plus the curated indices, FX, commodities and volatility entries.

To change it, edit the CSV and commit — or run the script below and commit the result.

---

### `scripts/refresh_symbol_names.py`
Maintenance script, not app code. The NSE equity names originally came from the daily
bhavcopy, which truncates them to ~25 characters — `MOTHERSON.NS` arrived as
"Samvrdhna Mthrsn Intl Ltd", which contains neither "motherson" nor its ticker, so the
dropdown could not find it. This pulls the untruncated names from NSE's `EQUITY_L.csv`
and merges them in (1,872 names improved). Run it by hand, then commit the CSV:

```
.venv/bin/python scripts/refresh_symbol_names.py
```

`EQUITY_L.csv` covers only the EQ series, so ETFs and SM-series rows (349 of them) have no
full name available and keep their bhavcopy name.

---

### `market_cache.db`
Auto-generated SQLite file. Never committed. Safe to delete — it will be recreated on next run.

---

### `events.db`, `events_db.py` and `scripts/fetch_events.py`
Upcoming events for the F&O stock list. Unlike `market_cache.db` this file **is**
committed, so a fresh Streamlit Cloud boot has data without calling NSE at request time.

The write side is `scripts/fetch_events.py`, run by hand (and, later, on a schedule); the
app only ever reads, through `events_db.py`, which opens the file `mode=ro` and returns an
empty frame rather than raising if it is missing or empty.

**Two NSE feeds, and they are not the same thing** — this is the distinction the Events tab
exists to keep straight:

| Feed | What a row is | Columns that matter |
|---|---|---|
| `corporate_actions_for_equity` | An action already **declared**: fixed amount, hard ex-date | ex-date, record date, amount in the purpose text |
| `event_calendar_for_equity` | A board meeting **scheduled**, outcome unknown | meeting date, agenda text |

There is no join key between them, so a company can appear in both with no way to link the
meeting to the action that came out of it. The tab shows them as two subtabs — Calendar and
Corporate actions — with different columns, rather than one merged list that would imply a
relationship that is not in the data.

`SUBTYPE_KEYWORDS` in `fetch_events.py` tags each row by scanning the purpose text, in
order, first match wins. `("result", "results")` is last on purpose so "Financial
Results/Dividend" still tags as a dividend.

**Agenda text links to NSE, not to the filing itself.** Only NSE's
`/api/corporate-announcements` endpoint carries attachment URLs, and it cannot be matched
reliably to a row in either feed above: the corporate-actions API has no attachment field
at all, `caBroadcastDate` comes back `NaN`, and a declared dividend often has no
announcement anywhere near its date. So the link goes to the company-scoped NSE page
(`corporate-filings-board-meetings?symbol=X` or `corporate-filings-actions?symbol=X`),
which is honest about what we actually know. Exact PDF matching is **not** built.

`nselib` 2.5.1 has no `corporate_announcements` wrapper — confirmed against
`dir(capital_market)`, not assumed.

**SQLite tables in `events.db`:** `instruments` (the F&O list), `events` (keyed on symbol,
event_type, event_date, purpose), `meta` (`last_success_at`, `last_error`,
`instruments_updated_at`). The tab shows "Last updated" from `meta` and warns if it is more
than three days stale.

---

### `jobs/snapshot_estimates.py`, `jobs/fill_actuals.py`, `db/schema_estimates.sql` and the Events tab estimate columns

A side build outside the Week 1-9 plan (spec: `analyst_estimates_spec-c3606434.txt`),
documented in `CLAUDE.md`. Adds a third table, `earnings_estimates`, to `events.db` —
same file, same read-only-from-the-app pattern as `events`/`meta` above, but with its
own writers.

**T-1 immutable snapshot.** `jobs/snapshot_estimates.py` runs once a day and looks for
`board_meeting` events (results purpose only) dated tomorrow or the day after. For each
one due, it fetches yfinance's analyst consensus (`get_earnings_estimate()` /
`get_revenue_estimate()`, `"0q"` row) and inserts a row — once. A SQLite `BEFORE UPDATE`
trigger (`db/schema_estimates.sql`) blocks any later change to the estimate columns
(`RAISE(ABORT, ...)`, NULL-safe `IS` comparisons), so the only thing that can ever be
written to a row again is the actual/surprise columns. The reason: analysts revise
estimates after the print, so a late or re-taken snapshot would compare the actual
against a contaminated number. If T-1 is missed, the job logs `missed_t1` and moves on —
it never backfills a stale estimate as if it were the T-1 one. A symbol with no analyst
coverage still gets a row (all estimate columns `NULL`), shown in the UI as "no estimate"
rather than dropped.

**Actuals, source flipped from the original spec.** `jobs/fill_actuals.py` runs after it
and fills `eps_actual`/`revenue_actual`/`surprise_pct` for any row whose event has passed
and whose actual is still `NULL`, retrying daily for up to 7 days before logging
`gave_up`. The spec originally called for `nselib.financial_results_for_equity` as the
actuals source; Step 0 discovery (`docs/estimates_discovery.txt`) found that function
queries a date-range feed and parses per-filing XBRL, not a per-symbol lookup, and across
a full trailing year it surfaced only 18 symbols — all distressed/suspended smallcaps,
zero mainstream F&O names. Escalated rather than built around; Adarsh confirmed the fix
8 Oct 2026: yfinance's `get_earnings_dates()['Reported EPS']` becomes the only automated
source (matched to `event_date` within ±1 day), verified against
`quarterly_income_stmt`'s independently-computed Diluted EPS for TCS before trusting it
across the watchlist. `nselib` is dropped from this path entirely, not kept as a dead
fallback. Screener.in stays a manual backup for Adarsh, same as any symbol yfinance never
fills — not a scraped pipeline.

**Superseded 12 Oct 2026 — actuals now come from NSE's own filing XBRL, with yfinance
demoted to fallback (`nse_results.py`).** The 8 Oct conclusion above was right about the
endpoint it tested and wrong as a general claim about NSE. `financial_results_for_equity`
reads `/api/corporates-financial-results`; re-queried live on 12 Oct for 1–12 Oct 2026
with no symbol filter it still returns **zero rows**, in a window containing TCS's real
8 Oct result — so that part of the finding is reconfirmed, not overturned. But a
different endpoint, `/api/integrated-filing-results`, works and exposes each filing's own
`xbrl` URL. Adarsh brought this one; it was verified from scratch here before any code
was written.

Verified live 12 Oct 2026 against the filings themselves: TCS Sep-2026 consolidated
revenue ₹731.88bn / PAT ₹139.34bn / EPS 38.37; DMART Sep-2026 ₹19,644.01cr / ₹742.98cr /
11.40; RELIANCE Jun-2026 revenue ₹3,118.5bn. Coverage spot-check across large caps, a
bank, an NBFC, a midcap and a symbol with `&` in its name: every one returned 12
financial filings spanning 6 quarters. MCX returns 0 rows — consistent with the
already-documented MCX gap, so corroboration rather than a new problem.

`nse_results.py` is a standalone module (pure selection/parsing + thin fetch helpers,
`tests/test_nse_results.py`, 12 fixture cases, no network). Three real traps drove its
shape, each confirmed against live documents:

- **Year-to-date contexts.** One document carries the quarter *and* the YTD figure under
  the *same concept name*, separated only by `contextRef` — TCS Sep-2026 has `OneD`
  (1 Jul–30 Sep, ₹731.88bn) beside `FourD` (1 Apr–30 Sep, ₹1,454.63bn). Reading
  positionally stores a half-year as a quarter: a 2× error that looks entirely plausible.
  `quarter_contexts()` accepts a context only when its own declared start/end prove a
  ~3-month span ending on the target quarter end, and `parse_facts` raises rather than
  falling back to some other period.
- **Banking taxonomy.** HDFCBANK has none of `RevenueFromOperations`,
  `ProfitLossForPeriod`, or the standard EPS concept. It uses `Income`/`InterestEarned`
  and — the nasty one — `ProfitLossForThePeriod`, one word off the standard concept. A
  single-concept parser returns NULL for every bank and NBFC in F&O while appearing to
  work. Hence ordered `CONCEPT_CANDIDATES` per field, with the resolved concept name
  stored alongside each value so a wrong pick is auditable. Bank EPS lives under
  `Basic/DilutedEarningsPerShareAfterExtraordinaryItems` (preferred over `Before…`, being
  the bottom-line figure the analyst estimate is measured against). Bank REVENUE is derived, not read
  off a line: `_derive_bank_revenue()` computes net interest income plus other income
  (`InterestEarned - InterestExpended + OtherIncome`), because that is the measure the
  analyst estimate is set against. Pinned down exactly rather than inferred — HDFCBANK
  Jun-2025 derives to ₹85,346.46cr and yfinance reports Total Revenue of ₹85,346.46cr for
  the same quarter, equal to the paisa, while that filing's own `Income` line is
  ₹133,054.97cr, 56% higher. Using `Income` made bank revenue surprise % nonsense: RBLBANK
  Sep-2026 read **+64.12%** against consensus and reads **−2.44%** once derived. The
  derivation only runs when the filer has no `RevenueFromOperations`, so a non-financial
  is never rerouted through it; if a bank files `InterestEarned` without
  `InterestExpended`, NII can't be computed and it falls back to `Income` with the concept
  recorded as `Income`, so the wrong-measure case stays diagnosable instead of
  masquerading as a derived topline.
- **Basis.** Every quarter is filed twice, seconds apart, with genuinely different
  numbers and no consistent direction: DMART Sep-2026 standalone PAT (₹803.60cr)
  *exceeds* consolidated (₹742.98cr) while its revenue is lower. `ACTUALS_BASIS` is
  **Consolidated** — what headlines and analyst consensus quote, so surprise % stays
  comparable to the frozen estimate. Changing it makes new rows incomparable to existing
  ones; it is not a free toggle. Also, `Integrated Filing- Governance` rows share the
  feed and can carry a *later* broadcast date than the financials for the same quarter
  (DMART Jun-2026: governance 29 Jul vs financials 11 Jul), so `select_filing()` filters
  by type and never takes "the most recent row".

Units are absolute INR (verified across TCS/DMART/RELIANCE; these documents carry no
`LevelOfRoundingUsedInFinancialStatements` at all). If one ever appears declaring a
scale, or a non-INR currency shows up, `find_nse_actuals` logs and skips NSE for that row
rather than mis-scaling by 10⁵.

**yfinance is kept, deliberately.** It is the only cover for a symbol whose concepts
aren't mapped, and a disagreement between the two sources is how a bad mapping surfaces
instead of silently storing a wrong number. Fallback is per-field, so a filer whose EPS
concept is unmapped still gets its revenue from NSE. `fill_row` now takes an explicit
`source` and every row records it (`nse_xbrl:Consolidated` or `yfinance`, or both joined
when a row was completed from each).

**A second bug fixed in the same pass.** `due_rows` selected on `eps_actual IS NULL`
only, so a row that got an EPS but no revenue — the common yfinance outcome; TCS and
DMART were both sitting at EPS-filled/revenue-NULL — was never revisited and could never
be completed. It now selects rows missing *either* actual, bounded to the retry window so
long-abandoned rows aren't re-fetched forever.

First live run (12 Oct 2026) filled all three outstanding rows. The cross-source check
landed well: NSE independently returned EPS 38.37 for TCS and 11.40 for DMART, matching
what yfinance had already stored, while supplying the revenue both were missing — and
filling ANANDRATHI, which yfinance had left completely empty days after the result
(EPS 5.38, revenue ₹343.99cr). Full suite 74/74.

**The Past earnings tab wasn't showing any of it (fixed same day).** The tab read EPS
estimate/actual/surprise live from Yahoo via `get_eps_history()`/`match_eps_row()` and
ignored the stored columns entirely, so the newly-filled NSE actuals were invisible:
Actual EPS sat empty for DMART (Yahoo has the estimate but no actual) and ANANDRATHI
(Yahoo has no row for that quarter at all), while both were stored as 11.40 and 5.38.

It also quietly broke the T-1 rule. `get_eps_history`'s docstring assumed Yahoo keeps a
quarter's pre-print estimate permanently once it reports — it doesn't. DMART's Sep-2026
estimate was **13.93** when frozen at T-1 and reads **13.42** from Yahoo now, after the
print. The tab was displaying the revised number, which is exactly the contamination the
freeze exists to prevent, and measuring surprise % against it.

Now: estimate always from the frozen snapshot (never Yahoo live); actual prefers the
stored value (NSE, with yfinance already tried inside the job) and falls back to Yahoo
only for a result that landed since the job last ran; surprise % is recomputed from the
two values actually on screen so it can never disagree with the columns beside it. A
`_num()` guard was added because these values arrive through pandas, where a NULL in a
mixed-type column becomes `float('nan')` — not None and truthy, so the old
`if v is not None` check would have formatted it as the string "nan" (the same trap
already documented for the Calendar subtab). Footnote corrected; it had been telling the
user EPS came from Yahoo.

**Not yet done:** NSE carries ~6 quarters of history per symbol, and actuals have no
T-1 freeze constraint, so past quarters *could* be backfilled — but only as actual-only
rows, since estimates can never be backfilled. Left for Adarsh to decide rather than
silently writing history into the table.

**Superseded 13 Oct 2026 — surprise % removed entirely; actuals stored both-basis, NSE
only.** Comparing the single yfinance-sourced estimate to one NSE actual required knowing
which basis (standalone/consolidated) and which dilution (basic/diluted) Yahoo's figure
sat on. `scripts/discover_yahoo_basis.py` tested that live across 10 symbols and up to 4
completed quarters each, matching Yahoo's reported EPS/revenue against both NSE bases per
quarter:

- **EPS basis is mostly stable per symbol but not universally.** 9/10 symbols matched the
  same basis in every quarter checked (TCS/HCLTECH/INFY → consolidated;
  RBLBANK/HDFCBANK/BAJFINANCE/SBIN/TATAELXSI → standalone). RELIANCE's matching basis
  *flipped* between quarters (3/4 consolidated, 1/4 standalone). M&M matched **neither**
  basis in any of 4 quarters — Yahoo's reported EPS for it doesn't correspond to either
  NSE figure at all.
- **The fatal finding: EPS basis and revenue basis disagree within the same symbol.**
  RBLBANK's EPS matches standalone every quarter; its revenue matches consolidated every
  quarter. Same pattern for SBIN. One stored "basis" per symbol cannot serve both measures
  — the premise of "detect once per symbol, apply everywhere" doesn't hold for real,
  liquid F&O names, not edge cases.
- Diluted (never basic) was separately confirmed as the EPS convention Yahoo reports,
  checked against 6 symbols where basic and diluted differ enough to discriminate.

Adarsh's call given this: stop comparing sources entirely. "Let's not compare on our own,
just give the analyst estimate and the NSE numbers." No surprise % of any kind on the Past
earnings tab now (the Calendar subtab's separate EPS-surprise cell is untouched — it
compares Yahoo's own estimate to Yahoo's own reported EPS, same source both sides, so it
was never exposed to this problem).

**New storage shape** (`db/schema_estimates.sql`, migrated via `jobs/snapshot_estimates.py
get_connection()`'s ALTER TABLE block): the old single `eps_actual` / `revenue_actual` /
`surprise_pct` / `revenue_surprise_pct` columns are **dropped**, not deprecated in place —
replaced by six actual columns, standalone and consolidated for each of revenue, PAT and
diluted EPS (`revenue_actual_cons/std`, `pat_actual_cons/std`, `eps_actual_cons/std`),
plus `xbrl_url_cons/std` for audit. PAT was already parsed by `nse_results.py`
(`CONCEPT_CANDIDATES["pat"]`) but never persisted before this; it's a straight read now,
no new parsing logic. `jobs/fill_actuals.py` is rewritten around this: `find_nse_actuals()`
fetches both bases unconditionally (`ACTUAL_FIELDS` maps each basis to its three db
columns), `due_rows()` picks up a row missing *any* of the six, and `fill_row()` takes a
partial `{column: value}` dict so one basis filing ahead of the other is a normal partial
state, not an error. The yfinance EPS/revenue-actual fallback functions are removed
outright — NSE is now the only source for actuals, full stop; a quarter NSE hasn't filed
yet just stays empty rather than falling back to a number with no comparison to validate
it against anyway.

Existing rows (TCS, ANANDRATHI, DMART, HCLTECH, RBLBANK) were backfilled under the new
shape by rerunning `fill_actuals.py` after the migration — all five picked up both bases
plus PAT in one run; none needed a fresh NSE fetch beyond what the module already does per
quarter. Spot check: TCS cons/std revenue ₹73,188.00cr / ₹60,441.00cr, PAT ₹13,934.00cr /
₹14,104.00cr, diluted EPS 38.37 / 38.98 — the std/cons gap itself is the reason this whole
redesign happened.

**Past earnings tab** (`tabs/events.py`) rewritten to 10 columns: results date, symbol,
est. revenue, est. EPS, then the six NSE actual columns, revenue/PAT/diluted-EPS ×
cons/std, wrapped in a horizontal-scroll container since it no longer fits a phone width.
No surprise % column. Footnote states plainly that no comparison is computed and why.

Verified via `AppTest`: zero exceptions, all values render with correct thousands
grouping, "no estimate" shown for ANANDRATHI (no analyst EPS coverage), no "Surprise %"
header anywhere, the lowercase "surprise %" appearing only once — inside the explanatory
footnote sentence, not as data. Suite 74/74 (78 before this change: 5 cases removed with
`test_d_surprise_math`, now-dead code testing the removed `compute_surprise`; 1 added,
`test_e2_fill_row_with_empty_dict_is_a_no_op`, confirming a quarter with nothing yet filed
writes no provenance rather than a false "checked, found nothing" stamp).

**Expected move / Worst case columns removed from the Calendar subtab (13 Oct 2026).**
Adarsh asked to drop them from Calendar specifically - the dedicated **Expected moves**
subtab (a separate, independent view ranking all 213 F&O symbols) keeps them untouched,
since it's a different code path with its own `_move_cell` and its own data source
(`get_all_expected_moves()`, not `get_expected_move_summary`/`get_expected_move_history`).
Removed from Calendar: the `_expected_move_cells()` helper, its two `<td>`s per row, the
two `<th>` headers, and the entire trailing block that only existed to annotate those two
columns - the "Historical earnings reactions..." footnote, the "How expected move is
calculated" expander, and a per-symbol "expected-move history" expander for every row in
the table. Left half of that trailing block in place (just the two columns gone) would
have been an orphaned explanation for data no longer on screen.

Also removed as dead code once nothing called them from this file: the
`get_expected_move_summary`/`get_expected_move_history` imports, the `json` import (used
only inside the removed history-expander loop), and the `REASON_CODE_LABELS` dict (used
only to translate that loop's `reason_codes` into plain language). Both `events_db.py`
functions themselves are left in place - general-purpose read accessors, not view code,
and genuinely reusable if a future feature wants per-event move data again; only the
Calendar tab's use of them is gone.

Verified via `AppTest`: Calendar subtab renders with no exception, no "Expected move" or
"Worst case" text anywhere, no stray "no data yet"/"How expected move is calculated". The
Expected moves subtab separately confirmed unaffected - all four of its own columns still
render. Suite 74/74 (no tests touched this UI path).

**Actual EPS / Surprise % also removed from the Calendar subtab (13 Oct 2026, same
session).** Calendar is now Est. Rev / Est. EPS / Analysts only - three columns, not five.
NSE's own actuals, both bases, live on the Past earnings tab; this was the last place in
the app still doing a cross-source comparison (Yahoo-estimate vs Yahoo-actual, both from
`get_eps_history`), so dropping it finishes the "no comparisons" direction from earlier in
the day rather than leaving one inconsistent exception.

`_estimate_cells()` still computes `eps_is_preview` from whether Yahoo has an actual for
the quarter yet - that's retained because it drives the Est. EPS cell's own "~" preview
styling (a result that hasn't reported should still look provisional), even though the
actual value itself is no longer rendered. `_surprise_text_color()` is deleted outright:
with both its callers gone (Calendar's EPS surprise, Past earnings' old revenue/EPS
surprise), it was dead code, not a kept utility.

**A footnote bug fixed in passing.** The Calendar table's note claimed "Yahoo... keeps
\[the EPS estimate\] unrevised once a result is out" - the exact claim already disproved
the same day while fixing the Past earnings tab (DMART's Yahoo-sourced estimate read 13.93
at T-1 and 13.42 after the print). This Calendar footnote had been asserting the same
false thing independently; corrected to say Yahoo's figure *can* revise, and points to
Past earnings for NSE's filed numbers, which can't.

Verified via `AppTest`: no "Actual EPS" or "Surprise %" text anywhere on Calendar, "Est.
EPS" and "Analysts" still present, corrected footnote text renders. Suite 74/74 (no tests
touched this UI path).

**Analysts column removed too, replaced by a hover tooltip per cell - and a real
mismatched-count bug caught in the process (13 Oct 2026, same session).** Asked whether
the Calendar's "Analysts" column was for Est. Rev or Est. EPS, the honest answer was
neither, correctly: `_estimate_cells` read `n_val = est.n_analysts_eps` and displayed it
next to `rev_val = est.revenue_estimate` - the EPS analyst count, shown beside the revenue
figure. Real on live data, not theoretical: TCS showed "20 analysts" next to a revenue
consensus actually built from 30 (`n_analysts_rev`); HCLTECH 8 vs 6; NESTLEIND 8 vs 23.

Adarsh's direction: drop the standalone column, put each estimate's own analyst count on
that cell as a `title=` hover tooltip instead, so the count can never again be attached to
the wrong figure. Est. Rev's tooltip reads `n_analysts_rev`, Est. EPS's reads
`n_analysts_eps`, each cell gets `cursor:help` so the affordance is visible without
hovering. Both counts now split by the same has_frozen/live-preview logic the revenue
value itself already used.

**A second bug, hiding behind the first:** `events_db.get_earnings_estimates()`'s SELECT
never included `n_analysts_rev` at all - the column existed in the schema and was written
by `jobs/snapshot_estimates.py`, but no reader of this function could have surfaced it,
tooltip or otherwise, until it was added to the column list. Caught immediately by
`AppTest` (`AttributeError: 'Pandas' object has no attribute 'n_analysts_rev'`) the moment
the Calendar code tried to read it - the kind of bug a live UI test catches in one run that
unit tests over `fetch_estimate()`'s dict shape never would, since that dict always had the
field; only the read path leading to the UI was missing it.

New regression test (`test_c2_eps_and_revenue_analyst_counts_stay_distinct`) snapshots TCS
with `n_eps=20, n_rev=30` and asserts both come back distinct and correctly attached.
Verified via `AppTest`: no "Analysts" header anywhere, TCS's Est. Rev cell carries
`title='30 analysts'`, its Est. EPS cell `title='20 analysts'` - the exact split that
disproves the old bug. Suite 75/75.

---

### HISTORY 6Q sparkline column (13 Oct 2026, Adarsh's spec)

A new column on the Calendar subtab's results rows only (dividend/other/fundraise board
meetings get an empty cell - there's no earnings history to show there): two stacked
unicode-block sparklines, REV above PAT, each the last 6 REPORTED calendar quarters of
consolidated revenue/PAT plus the latest value in ₹cr. Scope is deliberately narrow - only
symbols with an upcoming results board meeting get backfilled, the exact set the column can
ever render for, not the full 213-symbol F&O universe (that was the Expected-move tracker's
own, different call, for a dedicated ranking subtab with no scheduled-event requirement).

**Three design decisions Adarsh made explicitly, confirmed before building:**
1. **Storage, not a live fetch per page load.** Each historical quarter is a separate NSE
   XBRL document (established earlier the same day for the actuals work) - 6 symbols-with-
   upcoming-events × 6 quarters would be dozens of NSE requests on every Calendar tab visit.
   Built the same way as every other NSE-sourced feature here: a backing table
   (`earnings_history_quarters`) plus a backfill job (`jobs/refresh_earnings_history.py`),
   the UI only ever reads.
2. **Banks use the already-verified derived revenue, not suppression.** The original spec
   called for blanking the REV track on banks ("the revenue concept does not map"). Adarsh's
   direction: reuse `nse_results.parse_facts`'s existing bank derivation
   (`InterestEarned - InterestExpended + OtherIncome`, paisa-exact against yfinance's
   reported Total Revenue, built earlier the same day) instead - so no special-casing exists
   in this column's code at all; `get_quarter_actuals` already returns a usable revenue
   figure for a bank, full stop.
3. **The negative-quarter marker is per-quarter, not a single flag before the row.** A small
   grey "-" renders immediately before the block for the specific quarter that went negative
   - styled separately from the block's own QoQ-direction colour, since a loss quarter is
   still coloured by whether it's bigger or smaller than the prior one, not by the fact that
   it's a loss at all.

**`analytics/sparkline.py`** is the pure logic (no HTML, no Streamlit, no network, fixture-
tested in isolation): `build_blocks(window)` takes `[(quarter_end, value_or_None), ...]`
oldest-first and returns one `Block` per quarter with its scaled character, QoQ direction,
and whether that quarter's own value was negative.
- **Scaling is plain min-max over whatever values are present.** The spec calls this "signed
  min-max" for handling negative numbers, but that's just what min-max already does - the
  most negative value becomes the floor, no separate branch needed. A flat window (every
  present value equal) maps every block to the middle level rather than arbitrarily full or
  empty, since there's no real shape to represent.
- **A gap breaks the QoQ chain.** The quarter immediately after a missing one has no real
  prior to compare against and renders with direction `"none"` (neutral), rather than
  silently reaching past the gap to compare against the quarter before it.
- **Missing values never affect the scale.** min/max are computed only over present values,
  so two gaps either side of a flat trio still read as flat, not stretched by phantom
  extremes.

**The job (`jobs/refresh_earnings_history.py`)** computes, per due symbol, the 6-quarter
window ending the quarter BEFORE the one its upcoming board meeting will report (e.g. a
13 Oct 2026 meeting reporting Sep-2026 gets a Mar-2025-through-Jun-2026 window), fetches one
filing index and up to 6 XBRL documents (consolidated only - half the fetch cost of the
dual-basis actuals job), and **stores a row for every quarter in the window even when NSE
has no filing for it** - `revenue_cons`/`pat_cons` both `NULL`, a real, logged, expected
gap, never dropped and never interpolated. `previous_quarter_end`/`quarter_window`, added to
`nse_results.py` as general quarter-stepping utilities (not job-specific), build the window;
`quarter_end_for_event_date` stays in `jobs/fill_actuals.py` since it answers a different
question (which quarter does THIS event report on, not a window ending at a known quarter).

**A real bug found and fixed during the first live backfill, in both NSE-actuals jobs at
once.** `call_with_retry` retried any non-rate-limit exception blindly, including
`parse_facts`'s `ValueError` for "this filing has no 3-month context" - a deterministic
property of the document, not a transient fetch problem, so retrying with backoff could
never produce a different result. Caught live on **ICICIPRULI**, a life insurer: its XBRL
files under a different taxonomy variant (`INTEGRATED_FILING_LI_...`) whose declared
contexts span ~356 and ~362 days - neither is a quarter at all, by that insurer's own filing
convention. Before the fix this burned the full 4-attempt, ~75-second backoff schedule for
every insurer quarter checked; after, it fails in under a second and is correctly stored as
a gap. The identical flaw existed in `jobs/fill_actuals.py` (same `call_with_retry` shape,
same `nse_results.get_quarter_actuals` call) and was fixed there too, even though it hadn't
fired yet - it would have, the next time an insurer's own current quarter came due. New
regression test (`test_refresh_earnings_history.py`) asserts a `ValueError` produces zero
retry-sleep calls while a generic error still backs off normally.

**A unit bug caught by eye before shipping, not by a test.** The per-block hover tooltip
initially printed the raw absolute-INR value - `"QE Sep 2025: 19,602,000,000,000 cr"` - while
the line's own latest-value print correctly showed `19,602` (crore). `build_blocks` and
`qoq_pct` work in raw INR throughout (percentage change is scale-invariant, so this is
harmless for the maths), but the UI must divide by `1e7` before handing a value to
`tooltip_text`, which prints its argument as-is. Fixed in `tabs/events.py`'s `_line()`;
nothing in `analytics/sparkline.py` needed to change.

**One live data point investigated and found to be real, not a bug.** CANBK's derived
revenue dropped from ~₹19,602cr to ~₹10,381cr between Sep-2025 and Dec-2025 - a near-halving
that looked suspicious enough to check before trusting. Verified against the raw XBRL: the
Dec-2025 context is a clean `2025-10-01`–`2025-12-31` span (a genuine quarter, not a parsing
error), and the swing traces entirely to `OtherIncome` dropping from ~₹9,918cr to ~₹2,152cr
quarter over quarter while `InterestEarned`/`InterestExpended` stayed smooth and consistent
with neighbouring quarters - plausible treasury/trading-gain volatility for a bank, not an
extraction fault. Left as-is; this is exactly the kind of real shape the sparkline exists to
surface.

**Also verified:** TATAELXSI and COLPAL are standalone-only filers with **zero** consolidated
quarters in the window - every slot a gap, rendering "no history" rather than a sparkline of
nothing. Confirmed via `AppTest`: the HISTORY 6Q header renders, CANBK's corrected tooltip
reads `19,602 cr`, TATAELXSI's cell reads "no history", no exception. Full backfill run
across every symbol with an upcoming results board meeting (88 symbols); see the job's log
for the final per-symbol tally. Suite 95/95.

**Visual-only rendering pass (13 Oct 2026, same day): unicode block characters replaced with
HTML bar spans.** No data, scaling, or logic change - `analytics.sparkline.Block` gained a
`height_px` field (continuous 4-26px, same lo/hi scale as the existing 8-level `char` field,
just not quantized) alongside `char`, which stays exactly as it was for the 20 existing
fixture tests. Each quarter is now `display:inline-block; width:7px; margin-right:2px;
height:{height_px}px; background:{colour}`, six of them wrapped in `display:inline-flex;
align-items:flex-end; height:30px` so every bar shares one baseline regardless of height.
REV/PAT labels dropped their 10px override (inherits the cell's 13px, matching Est. Rev) and
gained `font-variant:small-caps`. A missing quarter is a zero-height, invisible span with
the same width/margin, not a space character - keeps its slot for alignment inside the flex
row rather than relying on a literal space glyph.

**Colour source resolved before touching anything, not guessed.** The brief's three hex
values (`#A6D3A0`/`#D6946E`/`#989080`) were close to but not identical to the existing dark-
theme tokens (`P.UP` #9AC69C, `P.DOWN` #E8A078, `P.T3` #AAA197) - introducing new one-off
hex values here would have broken the design language's "define the palette once, nothing
outside it" rule. Adarsh's call: reuse the existing tokens. They already resolve correctly
per-theme via `palette()`, so the bars match light and dark automatically with no new colour
definitions anywhere.

**The negative-quarter marker's placement was corrected mid-edit, not left as drifted.** A
first draft accidentally moved the "-" marker from "immediately before the specific negative
bar" (Adarsh's explicit 13 Oct direction, confirmed earlier the same day) to "once before the
latest value, flagging that the window contains a loss somewhere" - a real behaviour change
this pass was never supposed to make ("rendering only, no logic changes"). Caught before
shipping and reverted to the original per-quarter placement, just reskinned for a bar instead
of a text character. Also corrected a stale assumption in the brief itself: "bank REV
suppression" doesn't exist to preserve - Adarsh's 13 Oct direction was the opposite (reuse
the derived bank revenue), so "keep it exactly as it is" was read as keeping that, not
reintroducing suppression.

Verified via `AppTest` against BHEL's real negative PAT quarter (Jun-2025, -456cr): the "-"
sits immediately before that one bar, the bar is still coloured by its own QoQ direction
(terracotta, a down move), the oldest bar (Mar-2025) renders in the neutral token, heights
span the full 4.0-26.0px range, and gap bars render as `height:0` with a "no filing found"
title. Suite 99/99 (4 new fixture tests for `height_px`: extremes, flat-window midpoint, gap
returns `None`, and height/char never disagree on direction - quantization can tie two
different heights at the same character level, so exact rank equality isn't the right
assertion there).

**Fixed same day: the tooltip didn't fire for most bars.** Adarsh reported getting no number
on hover. Root cause: `title` lived on the visible coloured bar itself, bottom-aligned inside
a 30px row - a near-minimum-height bar (4px, the common case, since most bars aren't near the
window's max) left ~26px of empty, title-less space above it. Hovering anywhere but the exact
few coloured pixels found nothing. Fixed by making each quarter a fixed `height:30px` column
that carries the `title` and is itself the full hover target (`display:inline-flex;
flex-direction:column; justify-content:flex-end`), with the coloured bar as a shorter inner
`<span>` bottom-aligned inside it. The whole 30px column is now hoverable, not just the
visible sliver. Verified via `AppTest`: BHEL's Mar-2025 bar's title span now reports
`height:30px` with the 15.3px coloured bar nested inside it. Suite 99/99.

**Still didn't work - because native `title=` tooltips never fired at all, on anything, in
this app's rendering environment.** Adarsh reported hovering a bar still showed nothing after
the hit-target fix; asked whether the pre-existing `title=` tooltip on Est. Rev/Est. EPS's
"N analysts" (built the same day, before the sparkline work) also failed, and it did - a
systemic failure, not a bars-specific one. The hit-target fix above was a real bug and stays
fixed, but it was never the actual blocker Adarsh was hitting.

No browser access here to root-cause exactly why the native attribute wasn't rendering a
popup (sanitization, a CSS override, or something else in Streamlit's `unsafe_allow_html`
pipeline) - theme.py's own global CSS injection via the identical mechanism (`st.markdown(
_MAIN_CSS, unsafe_allow_html=True)`) demonstrably works for everything else in this app, so
raw HTML/CSS isn't being stripped wholesale; something specific to the browser's native
`title` popup specifically isn't showing.

Fixed by not depending on the browser's native tooltip at all: a CSS-only hover tooltip added
to `theme.py`'s `_MAIN_CSS` (injected once per run via the existing `inject_css()`, so it
applies globally, not per-feature) - `[data-tip]:hover::after { content: attr(data-tip); ...
}`, positioned via `position:relative`/`absolute`, styled from the existing `--card-bg`/
`--primary-text`/`--card-border` custom properties so it matches the app's theme rather than
introducing new colours. Every spot that used `title=` now uses `data-tip=` instead: the
sparkline bars, the Est. Rev/Est. EPS analyst-count cells, and the two column-header hints.
Nothing about WHAT is shown changed, only HOW it's rendered - fully CSS, no JS, no dependency
on browser-specific native-tooltip behaviour.

Verified via `AppTest` that the CSS rule is present in the injected stylesheet and every
former `title=` site now carries `data-tip=` with identical text. Suite 99/99 (no test
exercises actual hover rendering - that still needs Adarsh's eyes in a real browser, same gap
as the rest of this UI).

**Real bug found and fixed same day: the column's visibility gate used the wrong field.**
Adarsh asked why Asian Paints' HISTORY 6Q cell was empty despite 6 real quarters already
sitting backfilled in `earnings_history_quarters`. The gate checked `subtype == "results"`,
but `scripts/fetch_events.py`'s `SUBTYPE_KEYWORDS` list deliberately matches `"dividend"`
before `"result"` (its own comment documents this, for agenda-label purposes) - so a combined
"Financial Results/Dividend" board meeting, routine for Indian companies that declare both in
one sitting, gets `subtype="dividend"`. `subtype` was never the right field to ask "does this
meeting cover results" - it answers "what should this row's agenda be labelled," a different
question with intentionally different priorities.

The correct check already existed, twice, just not here: `jobs/snapshot_estimates.py`'s
`due_events()` and `jobs/refresh_earnings_history.py`'s `due_symbols()` both decide what to
back fill via `LOWER(purpose) LIKE '%result%'` substring matching on the raw purpose text,
which is exactly why Asian Paints already had history data - the backfill job was never
confused by this, only the UI's separate gate was. Fixed `_history_sparkline_cell` to take
`purpose` instead of `subtype` and use the identical `"result" in purpose.lower()` check, so
the thing deciding "show the column" finally agrees with the thing that decided "fetch the
data" in the first place.

Found via query, not guesswork, exactly how many rows this was silently hiding: 15 symbols
with an upcoming board meeting as of 13 Oct 2026 - including INFY, HINDUNILVR, TECHM,
DABUR, GODREJCP - all classified `dividend` or `fundraise` despite their purpose text plainly
containing "Results." All fixed by the one change, verified via `AppTest`: Asian Paints now
renders real bars (`QE Mar 2025: 8,359 cr` as its oldest quarter). Suite 99/99.

**Second real bug, same column, found minutes later: the backfill job's own scope window
didn't match the Calendar table's.** TCS, DMART, HCLTECH, RBLBANK and others - all reported
8-12 Oct 2026, still showing in Calendar because `get_upcoming_events(..., lookback_days=14)`
deliberately keeps a just-reported row visible for a while - all showed "no history" despite
being exactly the rows a reader most wants it for. `due_symbols()` only selected
`event_date >= today`, a forward-only window that never matched the UI's own backward-looking
one; nothing had ever fetched these symbols' history at all.

Fixed by adding `CALENDAR_LOOKBACK_DAYS = 14` to `jobs/refresh_earnings_history.py` and
widening `due_symbols()`'s query to `event_date >= today - 14 days`, matching
`tabs/events.py`'s literal exactly. No shared constant between the two modules on purpose -
the job intentionally carries no Streamlit/`events_db` dependency - so a comment in each file
points at the other, the same "keep two literals in sync by hand, flag it loudly" pattern
already used for the daily-jobs cron schedule. New regression test
(`test_due_symbols_includes_a_recently_reported_event`) plants a 5-days-ago event, a
20-days-ago one, and an upcoming one, and asserts only the stale one is excluded. Full
backfill rerun to pick up every symbol newly in scope. Suite 100/100.

**Three more additions, same day: sector filter chips, a 7th "just reported" bar, a narrower
Agenda column.**

- **Sector filter**, Calendar subtab only - a single-select `st.pills` row over
  `fno.DISPLAY_GROUPS`, filtering `type_df` to `fno.symbols_in_display_group(sector)`.
  Explicitly NOT the Correlation tab's sector-pair drill-down (a different, correlation-
  specific heatmap feature reusing the same 15 labels) - Adarsh's ask was the simpler
  "click a sector, see only its stocks," which is what this is. Verified via `AppTest`:
  selecting "IT" keeps TCS, drops RBLBANK.

- **A 7th bar for the quarter a row's own event just reported.** The 6-quarter window
  deliberately stops BEFORE that quarter (so a row's own Est./Actual columns describe a
  quarter the sparkline doesn't repeat) - but that meant TCS and DMART, both already
  reported by the time this column shipped, never showed their most recent, most relevant
  print at all. No new fetch needed: the just-reported quarter's `revenue_actual_cons`/
  `pat_actual_cons` already sit in `earnings_estimates` from the Past earnings actuals
  pipeline, read via the same `estimates_by_key` lookup `_estimate_cells` already uses.
  `quarter_end_for_event_date` (from `jobs/fill_actuals.py`, imported rather than
  duplicated) names which quarter that is. When available, it's appended as a 7th point to
  the SAME min-max scale as the other 6 (so heights stay comparable, no special-cased
  maths) and rendered with an 8px left margin plus a `1px solid` outline - a visual break
  and a border, not a new accent colour, so it reads as "just landed" without introducing
  anything outside the existing palette. A row that hasn't reported yet contributes nothing
  (`revenue_actual_cons`/`pat_actual_cons` are still NULL), so it stays at exactly 6, no
  special-casing required. A row with ZERO historical quarters but a fresh first-ever
  report (new listing) still shows that one bar rather than "no history," since the gate
  was widened to `not history and not has_new_quarter`. Verified via `AppTest`: TCS now
  shows 7 REV bars, the 7th tagged `QE Sep 2026: 73,188 cr (+1.3% QoQ) - just reported` with
  the border styling present; ICICIGI (not yet reported) still shows exactly 6, no marker.

- **Agenda column** capped to `max-width:160px` with `text-overflow:ellipsis`, full text on
  `data-tip` hover (not `title=` - this table's native tooltips were already confirmed not
  to render, see the two entries above).

No new unit tests for the 7-bar logic specifically - it's UI-embedded HTML construction in a
nested closure, same as the rest of this cell's rendering, and the underlying scaling/QoQ
maths it reuses is already covered by `analytics/sparkline.py`'s 20 fixture tests. Verified
via `AppTest` instead, same as the rest of this column's rendering checks. Suite 100/100 (no
existing test touched).

**Latest-bar rendering redone, same day, immediately after Adarsh saw it: style-only fix,
no data/scaling change.** The first version's distinguishing marks - a border on the 30px
hit-target wrapper, plus extra left margin - made the latest bar look like a tall box
regardless of its real computed height, dwarfing the 6 history bars beside it. The fix moves
the border onto the actual height-scaled bar element itself (the one already sized by the
same `height_px`/min-max scale every other bar uses - nothing in `analytics/sparkline.py`
changed), drops the extra margin entirely so all 7 bars sit identically spaced, and makes the
distinction purely stylistic: history bars keep a solid fill, the latest bar gets
`background:transparent` with a `2px solid` border in its own QoQ colour (same `P.UP`/
`P.DOWN`/`P.T3` rule every bar already uses - no new colours), with `box-sizing:border-box`
so the border is absorbed into the bar's own 7px×`height_px` box rather than growing past it.
Tooltip wording changed to put `(latest)` inline after the quarter label
(`QE Sep 2026 (latest): 73,188 cr (+1.3% QoQ)`) rather than a trailing suffix.

Verified via `AppTest` against TCS/DMART/HCLTECH: 7 bars per track, every one exactly 7px
wide with identical spacing, heights spanning the full 4.0-26.0px scale with the ceiling
(26.0px) landing on whichever bar is actually the window's maximum (the latest bar for TCS
and DMART), the latest bar's inner span showing `background:transparent;border:2px solid`
in its own QoQ colour, and the corrected tooltip wording. No pixel-rendered screenshot was
produced - no browser access here - so this is verified by inspecting the exact computed
CSS values via the test harness; Adarsh's own eyes still confirm the actual rendering.
Suite 100/100 (no existing test touched - same reasoning as the version above).

**Read side.** `events_db.get_earnings_estimates(symbols=None)` reads `earnings_estimates`
the same way `get_upcoming_events()` reads `events` — `mode=ro`, empty frame if the table
or file doesn't exist. One wrinkle: `pandas.read_sql_query` turns a SQL `NULL` numeric
column into `NaN`, not `None`, which silently breaks an `is None` check in the UI, so the
function normalizes with `df.astype(object).where(pd.notna(df), None)` before returning.

**UI.** `tabs/events.py`'s Calendar subtab gained five columns (Est. EPS, Est. Rev (Rs
cr), Analysts, Actual EPS, Surprise %), colour-coded the same `P.UP`/`P.DOWN` way as
everywhere else in the app, with a footnote explaining the estimates are frozen T-1 and
never revised. Building this surfaced a second bug: Calendar was built on
`get_upcoming_events()`'s `event_date >= today` filter, so a result's row — and its
freshly-filled actual/surprise — vanished from the UI the instant the event date passed,
before the actual could ever be shown. Fixed with an opt-in `lookback_days` parameter
(default 0, every existing caller unaffected); only the Calendar fetch passes
`lookback_days=14`, Corporate Actions is untouched.

**Tests.** `tests/test_estimates.py` covers acceptance checks A-G against a scratch
SQLite db with yfinance/nselib monkeypatched out — immutability, T-1 timing, a no-estimate
row surviving the round trip, the surprise-% formula, the actuals fill touching only
actual columns, idempotent re-runs, and a mid-run rate-limit stopping cleanly without a
partial write. Check H (one real big-cap row with an estimate, one mid-cap showing "no
estimate", visible together in a real browser) is manual, not automated.

**Live, unfrozen preview for not-yet-snapshotted events (8 Oct 2026).** Adarsh pushed
back on T-1-only: events further out than T-1 (e.g. HCLTECH, RBLBANK, both several days
out) show "no estimate" until the day before, by design — he asked for a non-frozen
preview in the meantime rather than nothing. `events_db.get_live_estimate_preview(symbol)`
(`st.cache_data(ttl=21600)`) calls the same `fetch_estimate` the T-1 job uses, but never
writes to `earnings_estimates` and is superseded the instant the real frozen row exists.
`tabs/events.py`'s `_estimate_cells` falls back to it only when there's no frozen snapshot
and `event_date > today`, rendering with a `~` prefix and secondary colour, and a footnote
explains the marker — never participates in `surprise_pct` math, since it's never stored.

**Live-preview perf cap, same day — then replaced (8 Oct 2026).** With no frozen
snapshots yet for most upcoming events, the Calendar view called
`get_live_estimate_preview` — a live, synchronous yfinance fetch — once per row, serially,
with no limit. Adarsh saw the page stuck on Streamlit's "Running
get_live_estimate_preview(...)" indicator for an extended period; with ~13 not-yet-
snapshotted rows visible, that's up to 13 blocking network calls before anything rendered.
First fix was a flat `MAX_LIVE_PREVIEWS_PER_RENDER = 8` cap, bitten by the
furthest-out rows first — but a day-0 follow-up fix (widening the live-preview condition
from `event_date > today` to `>= today`, to also cover TCS/VEDL-style same-day gaps)
consumed two more of that fixed budget and silently regressed rows that had been working
(ANGELONE, ICICIGI), caught by Adarsh from a screenshot rather than by any test — a flat
cap trades away correctness for speed in an unpredictable way.

Replaced with concurrent prefetch: `tabs/events.py`'s `_prefetch_concurrent()` runs
`get_live_estimate_preview` over every symbol a page's rows actually need (deduplicated,
`ThreadPoolExecutor(max_workers=5)`) once, before any row is built, into a `preview_map`
dict that `_estimate_cells` just looks up — no cap, no dropped rows, wall time bounded by
roughly one call's latency instead of the serial sum. Backed by a new on-disk cache,
`live_data_cache.py` (`live_data_cache.db`, app-owned — unlike `events.db`, which the app
only ever reads): `st.cache_data` alone is lost on every Streamlit process restart (a code
change, a Cloud redeploy), so this adds a second layer that survives it, at Adarsh's own
suggestion mid-build. Deliberately left untracked (not committed) — unlike `events.db`,
which already is tracked and is where the data that actually matters (frozen estimates)
persists across redeploys, this cache only holds disposable previews that are superseded
the moment a real frozen row lands, so there's little value in version-controlling it.

Same change dropped the Est. EPS column from the Calendar table, at Adarsh's request —
yfinance has no real net-income/profit consensus API, so an EPS-derived "approx. profit"
figure was considered and dropped as not worth the complexity; Revenue-only is simpler and
was the original ask ("I dont need eps, just estimated revenue and profit" → settled on
just revenue for now). Actual EPS and Surprise % are untouched — those come from the real
filed result once frozen, not from an estimate preview.

pyflakes clean on `tabs/events.py`/`events_db.py`/`live_data_cache.py`; full `pytest` suite
(26 cases) unaffected. Not yet reconfirmed by Adarsh in a real browser.

**Revenue surprise % and a Past earnings subtab (8 Oct 2026).** `surprise_pct` was EPS-only
(`jobs/fill_actuals.py`'s `compute_surprise`, `(actual-estimate)/|estimate|*100`) — revenue
had an estimate and an actual stored side by side but nothing diffed them. Added
`revenue_surprise_pct` (same formula, reused `compute_surprise` under its original generic
name) as a new column on `earnings_estimates`: `jobs/snapshot_estimates.py`'s
`get_connection()` adds it to the `CREATE TABLE` for a fresh db and `ALTER TABLE` (wrapped
in a `try/except sqlite3.OperationalError`) for an existing one, since `events.db` is
already a committed, populated file and `CREATE TABLE IF NOT EXISTS` alone wouldn't reach
it. `fill_row` and `due_rows` in `jobs/fill_actuals.py` updated to carry it through;
`events_db.get_earnings_estimates` added it to its column list.

Surfaced on a new Calendar-adjacent subtab, "Past earnings" — Adarsh asked for a place to
see expected-vs-actual for results that have already come in, separate from the
Calendar's upcoming-agenda job. Reads `estimates_by_key`'s underlying `estimates_df`
directly (no `events` table join needed — `earnings_estimates` alone has both the frozen
estimate and the filled actual), filtered to rows where either actual is non-null, sorted
`event_date` descending. Shows Est./Actual Rev, Rev surprise %, Est./Actual EPS, EPS
surprise % — unlike the Calendar table, EPS is kept here since a real reported actual is
meaningful in a way a live, unfrozen EPS *estimate* preview wasn't judged to be. The
surprise-color/sign logic (green/terracotta, always-signed) was pulled out of
`_estimate_cells` into a shared `_surprise_text_color()` helper so both tabs render it
identically rather than duplicating the branching.

**Clarified, same day:** committing `events.db` to git (see above) does not by itself make
fetching faster — the app always reads it from local disk at runtime regardless of git
tracking, and that read was never the slow part. The slow part is live yfinance network
calls for rows with no frozen snapshot yet, which the concurrent-prefetch + disk-cache
change already addresses. Git tracking only means a fresh deploy starts from whatever
frozen estimates were last committed, instead of an empty table.

**Not yet done:** the jobs have only run against scratch/synthetic data, not the real
`events.db`/live yfinance. No cron/launchd entry exists yet for the daily
snapshot-then-fill-actuals pair (both scripts' docstrings describe ~6pm IST, after
`fetch_events.py`), same as the still-missing cron for `jobs/fetch_intraday.py` below.

---

### Earnings call sentiment tracker (Week 5-6 task, built 14 Oct 2026)

The CLAUDE.md-specced feature: for each watchlist symbol's completed results quarters, find
the NSE-filed transcript, run two independent tracks over it, and store one row per
(ticker, quarter) in a new additive table - `earnings_call_sentiment`
(`db/schema_sentiment.sql`). Built and verified live against real transcripts before any
job code was written, same discipline as every other NSE-sourced feature this session.

**Discovery first (`nse_transcripts.py`'s module docstring has the full notes), checked
against 4 real companies - RELIANCE, TCS, HDFCBANK, HINDUNILVR:**
- Transcripts sit in the same NSE corporate-announcements archive `refresh_expected_moves.py`
  already knows how to query (`desc == "Analysts/Institutional Investor Meet/Con. Call
  Updates"`), alongside the audio-recording filing and the advance meeting-schedule notice
  for the same call - same category, different documents. The word "transcript"
  (case-insensitive) in the filing's own `attchmntText` reliably picks out the real one across
  all 4 companies, despite each wording the sentence differently (RELIANCE: "Transcript of
  the discussion on the Unaudited Financial Results..."; TCS: "...has informed the Exchange
  about Transcript"; HINDUNILVR: yet a third phrasing).
- All 4 PDFs are real extractable text via `pypdf`, not scanned images - 17-26 pages, 8,400
  to 17,000 words each.
- **Speaker-label formatting is not standardised across companies** - RELIANCE uses block
  headers (`"Questioner (Name, Firm)"` / `"Company Speaker (Name)"`), TCS/HDFCBANK/HINDUNILVR
  use inline `"Name: text"` labels with a `"Moderator:"` role. This is the concrete reason
  the spec's management-vs-Q&A split is Gemini's job, not a Python regex: no single pattern
  handles all four conventions seen already, and the next new company would just break it
  again. `nse_transcripts.py` only finds and extracts the raw transcript text; it never
  attempts to split it.

**Track A (`analytics/lm_sentiment.py`), pure, no network, fully reproducible.**
`data/lm_dictionary.json` holds the Loughran-McDonald positive/negative/uncertainty/litigious
wordlists (2,355 / 354 / 297 / 905 words), extracted from the published ~86,500-word Master
Dictionary CSV (Notre Dame SRAF, March 2026 edition, "free for use in academic research" -
this is a solo learning-focused build, see CLAUDE.md's Vision section). Counting is plain
tokenize-uppercase-lookup; `per_1000_words` normalizes so a short Q&A exchange and a long
prepared-remarks monologue are comparable. Sanity-checked against the real RELIANCE
transcript before trusting it: positive 13.67/1000, negative 6.92/1000, litigious 1.0/1000 -
plausible for a confident, non-legal earnings call.

**Storage deviates from the spec's literal list in two places, both documented in
`db/schema_sentiment.sql`'s header and both deliberate:** `quarter_end` (an ISO date) instead
of a free-text "quarter" label, so this table joins against `earnings_estimates` and
`earnings_history_quarters` without a label-parsing step; and `lm_litigious` is stored
despite the spec's storage section omitting it, since its own Method section requires
computing all four LM categories and dropping the fourth from storage would silently throw
away a number the spec asked for.

**Track B (`earnings_sentiment.py`), Gemini structured extraction, 2 calls per transcript
(management, then Q&A) - comfortably inside the spec's "max ~20 calls" budget.** Both calls
receive the FULL transcript text, with a role-specific instruction telling Gemini which
portion to attend to and which to ignore, relying on it to resolve speaker roles from context
(job titles, a participants roster most transcripts open with, analyst firm names) exactly
the way a human reader would - this is the direct consequence of the speaker-format discovery
above. `response_schema` + `response_mime_type="application/json"` (google-genai 2.27.0,
verified live) constrains output to the spec's exact shape, `guidance`'s
raised/maintained/lowered/none as a real enum constraint, not a string validated after the
fact. Token usage (`response.usage_metadata`) is logged per call, per the spec's cost guard.

**`jobs/refresh_earnings_sentiment.py` orchestrates it, scoped to the 4 custom watchlists**
(`watchlists.get_custom_lists()`), not the 5 read-only index-preset views - those are 50+
constituents each by design and reference data, not "the user's watchlist" in the sense this
spec means. `--symbols` overrides this for manual testing.

**A real design problem found and fixed before the first live run:** `due_quarters` was
first written against the `events` table (same as every other job this session), but
`events` turned out to be the wrong source here - it's a narrow rolling window (upcoming +
very recent, ~1 month wide in practice, built for the Calendar tab's own purpose), not a
historical archive. Found live: RELIANCE's 17 Jul 2026 results don't appear in `events` at
all, three months later. Fixed by querying NSE's own corporate-announcements archive directly
via `identify_filings()` - already built and proven for the Expected-move tracker's
multi-year backfill - which gives real historical results dates (and the exact quarter each
one covers, read from the filing's own "for the period ended ..." text, more precise than
inferring it from a calendar date).

**First live run, RELIANCE, exceeded the acceptance test rather than just meeting it:** found
and processed **8 real completed quarters** (Sep-2024 through Jun-2026) in one run, not the
minimum 2 the spec's acceptance test asks for. Every row's `transcript_source_url` is the
actual NSE filing parsed. Tone scores are genuinely differentiated across quarters, not a
static model output - Sep-2024 (mgmt_tone 0.5, qa_tone 0.0) reads distinctly flatter than the
1.2-1.5 range every quarter since, and LM word counts vary plausibly by quarter (9,042 to
22,365 total words). `guidance` read "maintained" for all 8 quarters checked - plausible for
a company that, per its own transcripts, explicitly avoids giving formal forward guidance.

Also fixed in passing: `pypdf` and `google-genai` were both in active use in this repo
(`test_key.py` already imported `google.genai`) but neither had ever been added to
`requirements.txt`.

**Not yet built:** the spec's UI piece (a sentiment trend line under the Chart tab's price
chart). This session's work stopped at a verified, working data pipeline - storage schema,
discovery, both tracks, the orchestrating job, 13 new fixture/regression tests, all run
against real transcripts and a real Gemini key, not mocked. Suite 113/113.

---

### `analytics/expected_move.py`, `trading_calendar.py`, `db/schema_expected_move.sql`, `jobs/refresh_expected_moves.py`, and the Events tab expected-move columns

A side build outside the Week 1-9 plan (spec: `expected_move_spec-3c050a43.txt`), built
9 Oct 2026 overnight under Adarsh's standing go-ahead to complete it autonomously while
he slept. For watchlist/F&O companies with an upcoming results date, shows a cohort-based
"expected move" and "worst case" percentage, derived from how much the stock actually
moved around its last several results prints — not a model, not implied options
volatility, just the empirical spread of real post-print reactions.

**Three new tables in `events.db`** (`db/schema_expected_move.sql`, additive — no existing
table touched): `earnings_move_prices` (raw cached hourly/daily bars, keyed by symbol+date),
`earnings_move_prints` (one row per historical filing: reference price, same-session and
next-session move, classification, status, reason codes), `earnings_move_summaries` (one row
per upcoming event: the cohort's expected/worst-case % rolled up from its last 8 usable
prints). A `BEFORE UPDATE` trigger freezes only the filing-*identity* columns on
`earnings_move_prints` (symbol, financial_period, filing timestamp, calculation_version) —
unlike the estimates table's trigger, calculation columns stay mutable so a rerun with a
bug fix or a later-arriving price bar can correct a row in place.

**`analytics/expected_move.py`** is the pure calculation engine — no network, no database,
fixture-tested (`tests/test_expected_move.py`, 26 cases). It classifies a filing's
`release_mode` (`intraday` / `post_close` / `pre_open` / `non_session`) from its timestamp
against the NSE session clock, picks the reference bar and the same-session/next-session
windows that mode implies, and computes a single print's move % and a cohort's
expected/worst-case % from a list of prints. `trading_calendar.py` wraps NSE's real
trading-day calendar (`is_trading_day`, `next_trading_day`, `next_trading_day_on_or_after`,
`session_bounds_ist`) so the window logic never assumes every weekday is a session.

**`jobs/refresh_expected_moves.py`** is the orchestration job, run by hand (not yet on a
schedule): for each symbol, it discovers historical results filings from NSE's corporate-
announcements archive (`identify_filings()`), fetches yfinance hourly (730-day window) and
daily (3-year) bars, runs each filing through `compute_print()`, and upserts the resulting
prints and the upcoming event's cohort summary. Idempotent by design — `upsert_print()`/
`upsert_summary()` check for an existing row by identity key and UPDATE rather than
duplicate-INSERT, confirmed by rerunning the full 5-symbol backfill twice with unchanged
row counts.

```
.venv/bin/python -m jobs.refresh_expected_moves --symbols TCS RELIANCE HDFCBANK INFY WIPRO --backfill
.venv/bin/python -m jobs.refresh_expected_moves   # default: all symbols with an upcoming results board meeting, 120-day backfill
```

**Filing clustering, and the bug this caused.** NSE sometimes files two or three separate
announcements for the same result within minutes of each other (Outcome-of-Board-Meeting,
then Dividend, etc.) — same calendar day, same underlying event. `identify_filings()`
treats the earliest same-period match as the print of record and every other same-*day*
match as a cluster sibling (stored as audit trail in `results_cluster`), never a conflict.
Only a match on a genuinely different calendar day counts as `source_conflict` — a real
revision. The first version of this function got that boundary wrong twice (see Errors
below); both are now regression-tested in `tests/test_refresh_expected_moves.py`.

**Four real bugs found and fixed during the live backfill runs (9 Oct 2026), not
hypothetical:**
1. `compute_print()`'s `status` defaulted to `"pending"` and the final classification
   block never ran for already-completed filings — every historical print showed
   `status='pending'` with no `print_move_pct` even when the next-session bars were
   present and the move was fully computable. Fixed by tracking completion via a separate
   `is_pending` flag, with `status` assigned exactly once at the end.
2. `extract_period_end()` only parsed full month names (`"%B %d %Y"`); TCS's own Q1
   filings read "...for the period ended Jun 30, 2026" with an abbreviated month, so every
   Q1 filing for every symbol was silently dropped. Fixed by trying `"%b %d %Y"` too.
3. `identify_filings()`'s revision check (`rows[1:]`) flagged same-evening multi-row
   announcements and exact-duplicate archive rows as `source_conflict` — exactly the
   pattern `results_cluster` exists to hold instead. Fixed by restricting a genuine
   revision to a match on a different calendar day (`rv[0].date() != filing_dt.date()`).
4. `fetch_archive()` built the NSE URL with raw string interpolation
   (`f"...&symbol={symbol}"`, no encoding). For `M&M`, the literal `&` was parsed as a
   query-string separator, so NSE received `symbol=M` plus a stray empty param instead of
   `symbol=M%26M` — zero filings matched, and the Calendar view showed "no data yet" for
   M&M specifically while 72 other upcoming-results symbols backfilled fine. Found when
   Adarsh asked why the live Calendar still showed gaps after the first full-universe run;
   fixed with `urllib.parse.quote(symbol, safe='')`. M&M went from 0 to 10 distinct filings
   identified on rerun. No other current F&O symbol has a URL-special character, so this
   was invisible until M&M specifically was backfilled.

None of these four were caught by the offline fixture tests on `analytics/expected_move.py`
alone, since those only exercise the pure calculation, not the filing-discovery/clustering
logic — which is why `tests/test_refresh_expected_moves.py` exists as a second, separate
test file targeting the job itself.

**Live backfill results (5 discovery names, 1100-day lookback, 9 Oct 2026):** TCS (10
usable, 1 excluded, 1 pending), RELIANCE (11 usable, 1 excluded), HDFCBANK (10 usable, 1
excluded; summary for 2026-10-17: 8/8 usable, expected move ≈3.97%, worst case ≈7.09%),
INFY (10 usable, 1 excluded; summary for 2026-10-23: 8/8 usable, expected ≈4.93%, worst
≈10.44%), WIPRO (10 usable, 1 excluded; summary for 2026-10-15: 8/8 usable, expected
≈7.00%, worst ≈16.83%). TCS and RELIANCE have no upcoming `board_meeting` event row in
`events.db` yet (their next result hasn't been scheduled), so no summary row exists for
either — expected, not a bug. Zero false `source_conflict` reason codes remain after the
fix above. Hand-verified against the raw formula: TCS's 2024-09-30 print (filed 10-Oct-2024
16:06:39 IST, `post_close`) stored `reference_price=3927.51`, `high_b=4240.0`,
`low_b=4112.5`, `print_move_pct≈7.956` — matches `move_pct(3927.51, 4240.0, 4112.5)`
exactly by hand.

**Second run, full real-world universe (74 symbols, 1100-day lookback, 9 Oct 2026, no
`--symbols` filter — the job's own `default_symbols()` picked every symbol with an upcoming
results-related board meeting).** `74 succeeded, 0 failed`. 73 of 74 produced a summary row
(the 74th, TCS, correctly has none — its board meeting already passed with no new one filed
yet, per the "expected, not a bug" note above; this is a distinct symbol count from the
74-processed/73-summarized split). After bug #4's fix above and a one-symbol rerun, all 73
summary rows have a full 8/8 usable cohort — zero remaining gaps. Sample: ANANDRATHI
(2026-10-09, expected ≈6.22%, worst ≈13.35%), DMART (2026-10-10, ≈4.59%/≈9.35%), HCLTECH
(2026-10-12, ≈7.39%/≈14.48%), WIPRO (2026-10-15, ≈6.99%/≈16.83%), M&M (2026-11-05, after the
fix, ≈4.68%/≈10.62%).

**UI (`tabs/events.py`).** The Calendar subtab got two new columns, "Expected move" and
"Worst case", next to the existing estimate columns, reading from
`events_db.get_expected_move_summary()` — "no data yet" if the job hasn't run for that
symbol/event, "Unavailable" (with a tooltip giving the usable/selected counts) if fewer than
1 usable print exists. An always-visible note states the honest caveat (this is a cohort of
past reactions, not a prediction) plus an expander explaining the method. Per symbol, an
expander shows the full print history — period, filing time (IST), reference price/method,
same-session move, next-session move, print move, status, and any reason codes translated
into plain language via `REASON_CODE_LABELS` (e.g. `"pending_window"` → "next session hasn't
completed yet"), with a link to the NSE filing.

**A separate "Expected moves" subtab (added 9 Oct 2026, Adarsh's own request)** ranks every
F&O stock by its historical earnings-day reaction, independent of whether its next result is
scheduled yet — the Calendar subtab only ever shows upcoming-event rows, so a stock between
results (nothing scheduled) was invisible there even with a full print history. Backed by a
new `events_db.get_all_expected_moves()`: for every symbol with at least one stored print,
takes the latest up to 8 (any status, by filing date) and aggregates the usable ones with the
same mean/max formula (`analytics.expected_move.aggregate_cohort`) the job itself uses — not
a new summary-table row, since `earnings_move_summaries.event_date` is `NOT NULL` and scoped
to a specific upcoming event; this reads straight from `earnings_move_prints` instead, so it
needs no schema change. Columns: Symbol, Expected move, Worst case, Prints used
(usable/selected), Next results (or "not scheduled"). Sorted by expected move descending by
default; respects the existing Scope (All F&O / My watchlist) and Search controls already on
the tab. `events_db.get_expected_move_summary()` and `get_expected_move_history()` (used by
Calendar) and `get_all_expected_moves()` (used by this subtab) all follow the same read-only/
`mode=ro`/safe-empty-return convention the estimates functions already use.

**A fifth real bug, caught building this subtab, not hypothetical:** `pd.DataFrame(get_all_expected_moves())`
puts `None` into a column that also holds floats (`expected_move_pct`, `next_event_date` for
symbols with no upcoming event) — pandas silently upcasts the whole column to `float64` and
turns every `None` into `nan`, which is truthy in Python. Both `if r.next_event_date else
'not scheduled'` and `pct is not None` read straight past that and reached `datetime.strptime(nan, ...)`
and `f"{nan:.2f}%"` respectively. Caught by `AppTest` (the first one raised; the second didn't
crash but would have silently printed "nan%" to Adarsh). Fixed with explicit
`isinstance(..., str)` / `pd.notna()` checks instead of truthiness.

**Full F&O universe backfill (9 Oct 2026).** Before this subtab existed, only the 75 symbols
with upcoming results had ever been run (see the two backfill runs above). Adarsh chose, when
asked, to back-fill the remaining 138 symbols in `fno_sectors.json` (the 213-symbol F&O
universe already used by the correlation tab) so the new subtab's "all F&O stocks" claim is
actually true rather than true for a third of them. Run: `--symbols <138 names> --backfill`,
same job, no code changes needed beyond bug #4's URL-encoding fix above (this universe
includes `GVT&D`, another `&`-symbol that would have silently failed without it).

**Result: 212 of 213 symbols have at least one stored print.** 137 of 138 succeeded outright;
`SBILIFE` hit a transient `ConnectionResetError` from NSE and succeeded cleanly on a solo
retry. The one true gap is `MCX`: its archive fetch returns zero rows — not an error, not a
timeout, just an empty result — across both the full 3-year window and a 120-day sanity check,
while a known-good symbol (`TCS`) returns hundreds over the same range. That means NSE's
corporate-announcements archive genuinely has no filings under `symbol=MCX` for this endpoint,
not a bug in `identify_filings()` or the date range. No further probing was done once that was
established, to stay inside the polite-fetching rule (no repeated hits chasing a symbol that
already answered twice). `get_all_expected_moves()` in `events_db.py` now builds its result
from the full `fno_sectors.json` universe rather than only symbols present in
`earnings_move_prints` — so MCX still appears in the "Expected moves" table, as a normal row
with "Unavailable" in both the Expected move and Worst case columns and `0/0` in Prints used,
the same "kept, never dropped" treatment the estimates tracker gives a missing estimate. It is
not silently missing from the list of 213.

**Verification status — read this before trusting the UI claims above.** `python -c
"import tabs.events"` imports cleanly. The full test suite passes: 58/58 (`test_expected_move.py`
26 cases + `test_refresh_expected_moves.py` 6 cases + the rest of the pre-existing suite).
Streamlit's `AppTest` harness drove the app to the Events tab, clicked the "Expected moves"
pill, toggled Scope to "My watchlist", and typed into Search (including searching "MCX"
specifically, to confirm it renders as an "Unavailable" row rather than vanishing) — zero
exceptions on any of those paths (after the pandas-NaN fix above). **That is the limit of
what this environment can verify.** There is no browser/screenshot tool available here, so none of the Calendar
columns, the "Expected moves" table, the note, the expander, or the per-symbol history table
have been **visually** confirmed — only that the code runs without raising. Per the standing
instruction not to claim UI success without being able to actually test it: this UI needs
Adarsh's own eyes in a real browser before it's considered done, same caveat as several
Correlation-tab fixes above.

**Not yet done:** no cron/launchd entry for the daily refresh (same gap as
`jobs/fetch_intraday.py` and the estimates jobs); `yfinance`'s `auto_adjust=True`
dividend-adjustment behavior (as opposed to split-adjustment, which earlier discovery work
did confirm) is not independently verified, so `PRICE_BASIS = "yfinance_auto_adjust"` records
the assumption rather than a proven fact.
Not committed or pushed — standing rule, building was authorized overnight but committing
was not.

**A sixth real bug, caught from a live number looking obviously wrong (9 Oct 2026):** the
new "Expected moves" subtab showed `BAJFINANCE` at 163.48% expected / 438.16% worst case —
impossible for a single earnings reaction. Root cause: `yfinance`'s `auto_adjust=True`
retroactively back-adjusts `interval="1d"` history for later stock splits/bonuses, but does
**not** apply that same retroactive adjustment to `interval="1h"` history. `find_price_bar()`
mixes both intervals (daily bars for older reference closes, hourly bars for the reaction
window), so a print straddling a real corporate action computed its move off a reference
price on the wrong price basis — confirmed live for BAJFINANCE (same-day daily-vs-hourly
close ratio ≈5.06x) and SIEMENS (≈1.72x). This is a genuine yfinance provider inconsistency,
not a bug in this repo's own math.

Adarsh's directed fix, not a generic plausibility cap: reuse the NSE corporate-announcements
archive rows already fetched for results identification (no extra NSE call) to find each
symbol's own **"Record Date"** filings — dedicated announcements whose `attchmntText` names
the record date and purpose of a genuine price-basis-altering action (bonus, split,
subdivision, scheme of arrangement, demerger). Ordinary cash-dividend record dates are left
alone; yfinance's total-return adjustment already handles those adequately. New code in
`jobs/refresh_expected_moves.py`: `DISCONTINUITY_KEYWORDS`, `RECORD_DATE_PATTERNS`,
`_parse_record_date()`, and `find_price_discontinuities(archive_rows)`, called once per
symbol inside `refresh_symbol()`. Any print whose window straddles a detected discontinuity
date (`filing_date < discontinuity_date <= today`) and was otherwise `status="usable"` is
recomputed in place via `compute_print_daily_fallback(filing_dt, daily_bars, today)` —
calculation columns stay mutable (see the trigger note above), so this is a correction in
place, not a new row.

**Superseded (9 Oct 2026), Adarsh's directed change:** the first version of this fix
dropped a straddled print entirely (`status="excluded"`, `print_move_pct=None`). Adarsh
asked instead to recover the quarter rather than discard it: `compute_print_daily_fallback`
redoes the whole calculation — reference price and both reaction windows — using only daily
OHLC bars (`fetch_daily_bars`, renamed from `fetch_daily_closes`, now returning full
open/high/low/close per date, not just close). Daily bars are back-adjusted for a later split
on both sides of the discontinuity, so staying on them end-to-end cancels the split out
exactly; the hourly series is abandoned for that print entirely rather than mixed in. Coarser
than the normal calculation — a whole day's high/low instead of an hourly window anchored to
the filing minute — so results are tagged `reference_method="daily_close_fallback"` (shown
directly in the Events-tab per-symbol history table, no UI wiring needed) and reason code
`price_basis_daily_fallback`, so nothing claims hourly-level precision it doesn't have. One
subtlety caught by the fixture tests before any live rerun: `em.select_daily_close_reference`
treats the filing date itself as an eligible "completed" close (it only compares dates, not
time-of-day), which is correct for `post_close`/`pre_open`/`non_session` modes but wrong for
`intraday` — that day's own close is *after* the filing, not before it. Fixed by excluding
the filing date from the eligible-closes dict before calling it, for `intraday` mode only, so
the reference is always the prior trading day's close. **Known limit, left open for
now (9 Oct 2026):** the fallback assumes daily bars stay on one consistent
scale across a discontinuity, which holds for split/bonus-type actions
(yfinance's `auto_adjust` genuinely back-adjusts those) but not for VEDL's
case — a real demerger, which is a permanent price-level drop that yfinance
does not back-adjust in *either* interval (confirmed live: VEDL's own daily
bars show close ≈773 on 2026-04-29 dropping to ≈272 on 2026-04-30, unadjusted).
VEDL's straddled print still shows an implausible 63.65% after the fallback
as a result — Adarsh's call (9 Oct 2026) was to leave this one symbol as-is
for now rather than add a detection/exclusion special-case. Four new fixture
tests in
`tests/test_refresh_expected_moves.py` cover `post_close` (consistent-scale move %),
`intraday` (reference excludes filing day; takes the larger of both days' ranges), `pending`
(reaction day not yet trading), and `excluded` (reaction day genuinely has no bar). Full suite
62/62 after this change. Unit-verified live (prior to this change):
`find_price_discontinuities()` returned exactly `[2025-06-16]` for BAJFINANCE, `[2025-04-07]`
for SIEMENS, and `[2026-05-01]` for VEDL (VEDL's case is a genuine demerger, not a yfinance
artifact — same detection signal, different underlying cause). Full suite still 58/58 after
this change.

**Full-universe rerun with the fix (9 Oct 2026), Adarsh's own choice over just the 3 known-bad
symbols:** `--symbols <all 213 from fno_sectors.json> --backfill`. First pass: 206 succeeded,
7 failed on transient NSE errors (one read-timeout on HAL, six back-to-back 503s on
TRENT/TVSMOTOR/UJJIVANSFB/ULTRACEMCO/UNIONBANK/UNITDSPR right at the tail of the run — an NSE-
side hiccup, not a code fault). A solo retry of those 7 succeeded cleanly (7/7), bringing the
rerun to 213/213. **42 symbols** turned out to have at least one genuine price-basis
discontinuity, not just the 3 originally spotted — including RELIANCE, HDFCBANK, ITC,
KOTAKBANK, DRREDDY, CONCOR, NESTLEIND (two separate events), and WIPRO/TVSMOTOR (only caught
on the retry pass, confirming the detector runs independently of retry timing). Their
previously-stored absurd prints no longer feed the mean/max cohort at the wrong scale; after
the daily-fallback change above, a second rerun scoped to exactly these 42 symbols
recomputed each straddled print via `compute_print_daily_fallback` instead of leaving it
`excluded`. `MCX` remains the sole true data gap (see above), unaffected by either fix. Not
yet re-verified beyond the job's own log output and the unit-level checks above — same
AppTest-only, no-real-browser caveat applies to confirming the "Expected moves" subtab now
renders sane numbers for these 42 symbols.

---

### `scripts/discover_nse.py` and `.github/workflows/test_nse_access.yml`
Both diagnostics, neither part of the app. `discover_nse.py` dumps the real row count,
columns and dtypes of each `nselib` call, because its output shape is documented nowhere
reliable. The workflow runs it on a GitHub runner on manual dispatch only, to answer
whether NSE 403s datacenter IPs — it does not, 213 rows came back.

---

### `scripts/discover_intraday.py`, `docs/intraday_discovery.txt` and `fno_sectors.json`

Step 0 of the F&O correlation matrix (spec: `correlation_matrix_spec-43ee700c.txt`).
A diagnostic, not part of the app — nothing imports it, and it writes no bar data
into the repo. It exists because the feature's whole design depends on what Yahoo's
15-minute feed actually returns, and that was worth measuring rather than assuming.

`fno_sectors.json` is hand-supplied: 213 F&O stocks, each with sector labels, themes
and a `sector_source` provenance field. Every stock has at least one sector. It has no
turnover field, which is why the script ranks liquidity from the bars themselves.

What the run on 6 Oct 2026 established:

- **All 213 tickers resolve as plain `<symbol>.NS`**, zero failures, including the
  four awkward ones (`BAJAJ-AUTO`, `GVT&D`, `M&M`, `NAM-INDIA`). No symbol-mapping
  table is needed — the spec had asked for one.
- **One `yf.download` of all 213 with `threads=True` took 6.0 seconds** and returned
  306,280 bars. Batching in the eventual fetch job is therefore politeness toward
  Yahoo's undocumented limits, not a performance measure.
- **15m reaches exactly 60 days.** 61 days returns an empty frame with an explicit
  "within the last 60 days" error. A hard ceiling, like the 730-day one on hourly
  bars that `prices.py` already works around.
- **50.6 MB for the whole tier**, 306,280 rows at 165 bytes each, measured in the
  real schema after `VACUUM`. The spec's own ~40 MB estimate was about 25% low.
- **Zero-volume bars are feed artifacts, not quiet bars.** 217 of 306,280 (0.0709%).
  They are all the 15:15 bar, they cluster on three dates rather than on particular
  symbols, and the price moved inside every one inspected. So volume is *not* used as
  a staleness test; a bar counts as stale only when its O/H/L/C all equal the previous
  close (231 bars, 0.0754%). The decisive number: the stale rate among the 20 thinnest
  names by traded value is 0.0941% against 0.1075% for the 20 thickest — marginally
  *more* common in the most liquid names, so it does not track illiquidity at all. The
  spec's original volume-based policy rested on the opposite claim.
- **Bars are not aligned across tickers** — min 1,423, median 1,438, max 1,440-odd
  over the same 58 sessions, with most stocks missing a bar in ~10 of them and not the
  same bar. Pairwise-complete correlation is required, not a refinement.

Re-run with `.venv/bin/python scripts/discover_intraday.py`. The size probe writes a
throwaway SQLite file to `scratch/` (gitignored).

---

### `fno.py`, `analytics/`, `data_layer/intraday_store.py`, `jobs/fetch_intraday.py`, `tabs/correlation.py`

The F&O correlation matrix (spec: `correlation_matrix_spec-43ee700c.txt`), built 6 Oct
2026 on `feature/fno-correlation`. Correlates log returns of F&O stocks pairwise, for
any lookback from 1 to 500 days, and shows the names most and least correlated with a
picked stock. The full 213×213 matrix is computed and queryable but never drawn as a
grid — about 22,000 pairs, nobody can read that.

**`fno.py`** reads `fno_sectors.json` once (`functools.lru_cache`) and exposes
`symbols()`, `yahoo_ticker()`, `display_name()`, `sectors_of()`/`primary_sector()`.
Both the fetch job and the tab need the same universe and sector lookup, so neither
parses the JSON itself.

**`DISPLAY_GROUPS` merges the 22 raw sector labels down to 15** (spec section 5B, 6 Oct
2026): `fno_sectors.json`'s raw labels have near-duplicates (`Bank`/`Private Bank`/
`PSU Bank`, `Energy`/`Oil & Gas`, ...) that make a sector-level matrix no more readable
than the 213x213 stock grid it's meant to replace. `_RAW_TO_GROUP` is the one place the
merge happens; raw labels in the underlying data are untouched. `display_groups_of(symbol)`
can return more than one group — 5 of the 213 stocks (`AXISBANK`, `HDFCBANK`, `ICICIBANK`,
`KOTAKBANK`, `SBIN`) span two merged groups, which is what `analytics/sector_matrix.py`'s
same-symbol exclusion exists to handle. `symbols_in_display_group(group)` is the reverse
lookup, built once via `_group_membership()`.

**Two tiers, decided in one place (`analytics/tiers.py`):**

| Lookback | Bars | Why |
|---|---|---|
| 1-60 days | 15-minute | Yahoo's 15m retention is exactly 60 days — measured, a hard ceiling (61 days comes back empty with an explicit error) |
| 61-500 days | daily | the existing size of history Yahoo serves on request |

`pick_tier(lookback_days)` is the only place that decides; nothing else branches on bar
size. **15 minutes is a floor, not a default** — 1-minute bars were considered and
dropped: correlations from very fine bars are biased toward zero by asynchronous
trading (the Epps effect), worst for exactly the illiquid F&O names where a correlation
estimate would matter most, and no stale-bar filter fixes it because thin-but-nonzero
bars pass every filter. The operational case against 1m was separate and is now mostly
moot (see `data_layer/intraday_store.py` below): 1m bars older than 7 days are gone from
Yahoo for good, which would have made the daily fetch job mandatory rather than an
optimisation.

**`data_layer/intraday_store.py`** — SQLite outside the repo, `~/trader_desk_data/
intraday.db` by default or `TRADER_DESK_DATA_DIR` from `.env`. One `bars` table holds
both tiers (`symbol, interval, ts, open, high, low, close, volume`, PK on
`(symbol, interval, ts)`), so `INSERT OR IGNORE` makes refetching an overlapping window
free. `ts` is stored as naive IST text (`YYYY-MM-DDTHH:MM:SS`) rather than ISO8601 with
an offset — NSE bars are only ever IST, the offset would be the same string repeated
306,280 times, and the naive text form sorts and filters lexicographically so a window
read is a plain `WHERE ts >= ?` that SQLite can use its index for. That matters because
of a measurement: loading and shaping the full table costs 1.45s against 0.12s for the
correlation itself, so the store always reads a window, never the whole table.

Both tiers are **disposable by design**. The 15m tier only ever has to answer lookbacks
up to 60 days, and Yahoo serves that whole window on demand — so the file can always be
rebuilt from scratch, a missed fetch day costs nothing, and no history is ever
stranded. That is the real prize from dropping the 1-minute tier, which had the opposite
property. `prune()` keeps the newest N **sessions**, not calendar days — a 60-session
lookback spans 83 calendar days (58 real sessions, 14 Jul-5 Oct 2026), so pruning on a
calendar cutoff deleted bars the longest lookback needed; this was caught and fixed the
first time the full job ran. `corr_cache` exists as a table but is intentionally
unused — see below.

Daily bars live here too, under `interval='1d'`, not in `market_cache.db`'s
`price_cache`. That table is built around one ticker per call with its own freshness
window; getting daily history for all 213 F&O names through it would mean 213
sequential yfinance calls on a cold cache, against the one 6.0-second bulk download this
module already does. `prices.py` keeps owning the dozen curated instruments; nothing
there changed.

**`analytics/correlation.py`** — `compute(lookback_days)` returns a `CorrelationResult`
(the matrix, the n_obs matrix, tier, as-of date, min_periods, flagged extreme returns).
In order: load only the requested window, pivot to wide close prices with the stale-bar
policy applied, log-return within each session (the first bar of a session has no
return — including it would let the overnight gap dominate), pairwise `DataFrame.corr`
with `min_periods` at 50% of the expected observations.

**The stale-bar policy** (`STALE_POLICY`, quoted in the tab's footer) was settled by
measurement, against this spec's own original assumption. A bar is stale only when its
open, high, low **and** close all equal the previous bar's close — volume is never used
as a staleness test. The spec originally proposed treating `Volume == 0` as stale, on
the theory that a quiet bar just repeats the old price. Step 0 disproved it: of 306,280
bars across all 213 stocks, 217 had `Volume == 0` (0.0709%), every one of them the
15:15 bar, clustered on three specific dates rather than on particular symbols, and the
price moved inside every one inspected. Worse for the original theory, the stale rate
(flat-OHLC, the policy actually used) is *higher* among the 20 most liquid names
(0.1075%) than the 20 thinnest (0.0941%) — staleness does not track illiquidity at all
in this universe. Treating `Volume == 0` as stale would have discarded real closing
moves on those three dates. Stale bars become `NaN`, never forward-filled; pairwise
correlation then uses only timestamps where both stocks have a real return.

**Pairwise, not `dropna(how='any')`, and this is load-bearing, not a nicety.** Step 0
measured bar counts of min 1,423 / median 1,438 / max 1,450 across the same 58 sessions
— most stocks miss a bar in roughly 10 sessions, and not the same bar as each other.
Dropping any incomplete timestamp across all 213 kept only 88.2% of rows for no speed
benefit (pandas' pairwise path is Cython, not a Python loop).

**`corr_cache` is unused on purpose.** The worst case this feature has — 60 days, 1,450
rows, all 22,578 pairs — correlates in 0.12s, measured on the real bars with their real
gaps. A SQLite cache table would be persisting a tenth of a second. `compute()` instead
keeps an in-process memo (`(tier, lookback_days, asof_date) -> CorrelationResult`,
capped at 8 entries) so repeated Streamlit reruns of the same view don't redo the 1.45s
of bar loading. No `@st.cache_data` — CLAUDE.md rules it out; it would conflict with the
SQLite caching convention this app already uses.

**`jobs/fetch_intraday.py`** — run by hand or by cron/launchd, never by the app:

```
.venv/bin/python -m jobs.fetch_intraday
```

Batches of 45 symbols with a 2s pause between batches; on an empty batch (yfinance never
raises on a failed fetch, it returns an empty frame — so failure is tested by emptiness)
backs off 20s and retries once, single-threaded. Decides cold-start (`period=60d`/`3y`)
versus top-up (`5d`/`1mo`) from how old the newest stored bar is, so the daily run stays
cheap and a first run or a long gap still backfills everything. Logs the `Volume == 0`
diagnostic every run — not used for anything, kept so a change in Yahoo's feed doesn't
pass unnoticed. First full run: 213 symbols, both tiers, 38.0s, zero failures, 306,280
15m rows + 155,994 daily rows.

**`tabs/correlation.py`** opens with the sector-level overview, *then* the per-stock
picker (retitled "Pick one stock"): stock picker (`st.selectbox` over the 213 names), a
lookback number input (1-500, labelled with which tier and roughly how many
observations it implies), a top-N input, and two tables: Most correlated / Least
correlated (most negative first — the useful end for finding a hedge). Sector is shown
as plain text per the design language in that per-stock section; a row sharing the
picked stock's own sector is marked in the primary text token instead. Footer states the
bar tier, the stale-bar policy, the as-of date and the data source. If the store or the
tier's bars are missing, the tab says so and gives the exact fetch command rather than
failing — there is no rollup-CSV fallback (see "In progress" below for why).

**The sector matrix and drill-down (spec sections 5/5A/5B, 6 Oct 2026)** sit above the
per-stock section, built from the same `CorrelationResult` (`analytics/sector_matrix.py`
does no fetching or pairwise math of its own — it only aggregates). A 15x15 heatmap of
mean pairwise correlation per `fno.DISPLAY_GROUPS` pair, legend swatches in
`sector_colors()`, then `st.pills` to drill into one sector: a summary bar of that
sector's mean r against every other sector, then a rectangle of its stocks against every
other eligible stock (narrowed to a clean S×T rectangle once a second sector is picked
via a second `st.pills` row). A "Clear" button resets both picks.

*Same-symbol exclusion (spec Addition 1)* — a stock in both sectors S and T (5 of the
213 stocks span two display groups) would otherwise contribute a self-correlation of
1.0 to the S,T cell, inflating the mean. `_off_diagonal_cell()` excludes it via
`np.equal.outer` on the row/column symbol lists. Verified against real 120-day daily
data: Banks x Financial Services has `possible == 835` (20x42 minus the 5 known
overlapping banks) and no value of exactly 1.0 in that cell.

*Diagonal cells* use unique unordered pairs of distinct stocks only — a one-stock sector
(Textiles) has no within-sector pair and correctly shows "no data" rather than a forced
1.0.

*Drill-down reads the cached snapshot matrix, never recomputes* — so its cells match the
main matrix exactly by construction, not by re-derivation.

*Two decisions made without a separate round of confirmation, flagged for the record:*
`st.pills` chips are the actual click mechanism, not a fallback — the spec's "Primary:
Plotly selection/click events" was judged too fragile to get right without live browser
verification (axis tick labels aren't clickable Plotly data points; multi-trace subplot
colour strips are easy to get subtly wrong). And the drill-down rectangle's "all other
stocks" column view excludes a multi-sector stock already shown as a row, rather than
letting it also appear as a column under a different sector's grouping (avoiding
duplicate-column complexity) — a deliberate simplification of the spec's "appears in
EACH of their sectors' rows/columns" wording.

*The pills' own session-state keys are never written to directly* — a nonce
(`sector_drill_nonce`) is bumped and `st.rerun()` called instead, same pattern as
`tabs/watchlist.py`'s `add_nonce`. Directly assigning
`st.session_state.sector_drill_a = None` from the Clear button raised
`StreamlitWidgetAlreadyInstantiatedError`, because that pills widget had already been
instantiated earlier in the same script run by the time the button's code executes.
`sector_b`'s key also folds in the current `sector_a`, so switching the top-level sector
resets the second pick instead of carrying over a stale selection — caught via `AppTest`,
not just the unit tests.

**The tab is now four chip-selected sections (`st.pills("corr_section", ...)`), not one
long scroll** — Sector overview, Stock matrix, History, Pick one stock. Only the picked
section's body runs each rerun (same `if/elif` reasoning as the top-level nav in
`app.py`), and the chip row's CSS is the Watchlist view chips' existing `.st-key-wl_idx`/
`.st-key-wl_cst` selector list in `theme.py`, extended to include `.st-key-corr_section`
rather than duplicated.

**Stock matrix (spec section 6, 6 Oct 2026) — cluster first, filter second.**
`analytics/stock_matrix.py`'s `compute_order()` always clusters the *full* eligible
universe (after any sector/stock filter) before anything is hidden; the preview
functions only select a subset of, and re-sort into, that one stable order — never a
fresh re-clustering of whatever happens to be visible. Distance comes from the already-
computed `CorrelationResult.corr`, not raw returns: `d(i,j) = sqrt(max(0, 2*(1-r(i,j))))`,
then `scipy.cluster.hierarchy.linkage(..., method="average")` — average linkage because
correlation-derived distances aren't guaranteed true-Euclidean, which Ward's method
assumes. A pair with no valid correlation at all is repaired by repeatedly dropping the
symbol with the most undefined distances (alphabetical tie-break, so the result is
deterministic) until the remaining submatrix is fully finite; dropped symbols stay
searchable elsewhere and are disclosed under the heatmap, never silently hidden.

The tab shows at most 60 stocks at once (`STOCK_MATRIX_CAP`) via two preview modes, both
re-sorting into the cluster order rather than re-clustering: **Sector-balanced** (round-
robin across `fno.DISPLAY_GROUPS`, alphabetical within each group, so no single sector
crowds out the rest) and **Most liquid** (median `close × volume`, via the new
`intraday_store.read_bars_with_volume()` — volume is read here only for this ranking;
the stale-bar policy still never uses it). A focus stock, if picked, is always kept in
whichever preview is showing. The heatmap itself is a `plotly.subplots.make_subplots`
2x2 grid: the main pairwise correlation heatmap plus two thin "sector strip" heatmaps
(top edge = columns, right edge = rows) sharing its axes, each strip using a hand-built
stepped colorscale (`_discrete_colorscale`) because Plotly has no native way to colour
individual axis tick labels — reusing `theme.sector_colors()`, the same scoped per-
sector palette the Sector overview section already uses. Both strips also carry
hover text (`_group_label`) naming the sector, added 7 Oct 2026 after Adarsh asked what
the strip colours meant — they used to be `hoverinfo="skip"`.

**Correlation-value colour fix (7 Oct 2026).** Adarsh flagged the heatmap cells (not the
sector strips) as too muted to read. Two causes, both fixed: (1) `zmin`/`zmax` used to be
a flat `[-1, 1]` regardless of what the data held, so real-world `r` (usually 0.05-0.5)
always landed in the palest slice of any palette — `_symmetric_zrange()` now fits the
range to the matrix's own largest magnitude each render (rounded up to the next 0.1,
floored/capped to `[0.1, 1.0]`), applied at all three heatmap call sites
(`_sector_heatmap`, `_drill_rectangle`, `_stock_matrix_heatmap`). (2) the muted green/
terracotta value palette was swapped for a richer blue-white-red diverging scale —
`theme.CORR_POS`/`CORR_NEG`/`CORR_MID`, a second scoped exception to the one-accent rule
alongside `sector_colors()`, documented the same way in `theme.py`. This is a **deliberate
inversion** of the app's usual green-gain/red-loss convention — blue is positive, red is
negative here — flagged to and accepted by Adarsh before building, on the reasoning that
"correlation isn't a gain or a loss." Sector identity colouring is untouched by this change.

**Isolating rows, two ways (7 Oct 2026).** `_stock_matrix_heatmap(P, result, cols, rows=None)`
takes `rows` separately from `cols` now — `cols` is always the full displayed universe (so
an isolated row is still read against everything, not just against other isolated stocks).
Two ways to isolate: the **"Isolate rows" multiselect** (`sm_isolate`, typed or picked from
its dropdown — no typing required, it opens on click), and **clicking the heatmap itself**
— see the click-interaction writeup immediately below, which supersedes the earlier "no
click-to-populate wiring" unilateral call.

**History (spec section 7, 6 Oct 2026) — rolling correlation through time.**
`analytics/rolling.py` is always the daily tier (the 2-year default span is far past the
15m tier's 60-day Yahoo ceiling, so there's no tier choice to make the way
`analytics/correlation.py` has to make one). A focus stock is compared against one of
three things, picked by `st.pills`: **NIFTY 50** (`^NSEI` via `periods.range_history`,
confirmed `Close == Adj Close` for this ticker — a plain price index, no dividend
adjustment to reconcile), **another stock**, or a **sector basket** (equal-weight mean
log return of a `fno.DISPLAY_GROUPS` group's constituents, excluding the focus stock
itself, using the 15 *merged* display groups rather than the 22 raw sector labels — the
same merge the Sector overview heatmap already uses). A basket day is `NaN` unless at
least 80% of its mapped constituents (minimum 2) have a return that day; the UI discloses
the constituent count and that membership is held fixed, not reconstructed point-in-time.

The rolling window (sessions, default 100, minimum 3) is a separate control from the
History span (6mo/1yr/2yr, default 2yr) — the window is how the correlation is averaged,
the span is how far back the line is drawn, and fetching the span needs extra calendar-
day warm-up before the visible range starts. `rolling_correlation()` doesn't pre-drop
NaNs from either series; it masks a window point only when fewer than 80% of its sessions
have a valid pair (`min_pairs`), and separately reports the raw valid-pair count so the UI
can warn (not mask) below 50. **Top 5 partners** are the five highest signed partners from
a dedicated daily-tier snapshot (`daily_tier_result`) taken *today* and then plotted
through the whole history — frozen identities, not re-picked at each date — so the panel
is explicitly labelled "Today's top 5, shown through time" to disclose the retrospective
selection.

Found and fixed during AppTest smoke-testing, not reported by a user: yfinance's daily
index for `^NSEI` is UTC-midnight stamped as a *nominal session date*, not a real UTC
instant. The first fix (`tz_convert("Asia/Kolkata")`) stopped a tz-aware/tz-naive
`pd.concat` crash but silently shifted every timestamp to 05:30, which no longer matched
the stored daily bars' pure-midnight-naive index — so History rendered "not enough
overlapping data" against 744 real stored sessions. The correct fix is a bare
`tz_localize(None)` (strip the label, don't shift the clock) in `nifty_log_returns()`.

**Unilateral calls made building Sections 6/7, flagged for the record, not yet
re-confirmed with Adarsh:** the single comparison line in History uses `P.T1` (the
existing neutral chart token) rather than a new blue accent, to keep the no-new-colour
rule; and the optional dendrogram the spec mentions for Section 6 was skipped.

**Clicking the Stock matrix heatmap directly (7 Oct 2026, revised same day).** Plotly axis
tick *labels* are never clickable — only real trace data points are — so there is no way to
make clicking a row's name itself do anything. The subplot now has four traces, laid out
left to right as: a dedicated click-to-isolate strip (col 1, right next to the row-label
axis), the main correlation grid (col 2), and the sector-colour strip, back in its original
position on the right edge (col 3) — plus the top sector-colour strip (row 1, above col 2).

The click-to-isolate strip is a `go.Scatter` of one square marker per row, **not** a
`go.Heatmap`. First attempt made it a heatmap cell (like the sector strip), and clicking it
never produced a selection event — Plotly's point-selection model, which Streamlit's
`on_select` reads, only exists for point-based traces (scatter, bar, …), not heatmaps. That
is the same reason the Chart tab's Measure tool uses a scatter trace rather than drawing on
the price heatmap. The main grid stays a heatmap (it needs a continuous colour-mapped
value), so a click there is still unconfirmed in a real browser; the sector strips are
colour context only and are not click targets.

Both remaining click targets are wired via `st.plotly_chart(..., key=STOCK_MATRIX_PLOT_KEY,
on_select="rerun", selection_mode=("points",))`, reusing the Chart tab's Measure-tool
timing: `_apply_pending_stock_matrix_click()`, called at the very top of `render()` (before
`corr_section`, `hist_*` or `sm_isolate` are instantiated this run), reads
`st.session_state[STOCK_MATRIX_PLOT_KEY]` — Streamlit syncs the click that triggered this
rerun into there before the script runs — dedupes it against `sm_click_last_sig`, and
acts on it:
- **Click-to-isolate strip click** (`curve_number == 1`, one marker per row): isolates
  that row by setting `st.session_state["sm_isolate"] = [symbol]` directly — the same
  effect as picking it from the multiselect, just reachable by clicking next to the name.
- **Main grid cell click** (`curve_number == 2`, the row/column intersection): jumps to
  History pre-loaded with that exact pair — sets `corr_section` to `"History"` and seeds
  `hist_symbol`/`hist_mode="Another stock"`/`hist_other` before those widgets exist yet
  this run.

Both writes land *before* their widgets are instantiated in the same run, so this is a
plain pre-seeded default, not the nonce+rerun dance `sector_drill_nonce` needs elsewhere
in this file — that pattern is only required when a widget with the same key has *already*
rendered earlier in the same run. No index lookup is needed either: for both the scatter
strip and the categorical-axis main heatmap, Plotly's selection point gives `x`/`y` as the
actual tick label strings, not numeric positions, so the clicked symbols are read straight
off the click event. The two y-axes (col 1's strip and col 2's grid) are kept row-aligned
with an explicit matching `categoryorder="array", categoryarray=rows` on each, rather than
`shared_yaxes`, because sharing an axis across subplots can make Plotly report a click from
more than one trace for the same point — and Streamlit silently drops the whole click when
that happens.

Verified via `AppTest` by writing a synthetic selection payload straight into
`st.session_state[STOCK_MATRIX_PLOT_KEY]` and asserting the resulting widget state (no real
browser click, since AppTest can't drive Plotly's JS) — not yet confirmed in an actual
browser. No Playwright/Selenium/node/npx tooling exists in this environment to check it
directly, so that confirmation still depends on Adarsh testing in a real browser after
reloading the app.

**Label/marker overlap bug, found and fixed in a real browser (7 Oct 2026).** Confirmed
working: the click-to-isolate strip does register clicks. But its first version put the
row-name text on col 2's (the main grid's) y-axis, as the original single-heatmap version
had. A subplot's y-axis tick labels render just *outside* its own domain by default, which
put the names in the narrow gap between col 1 and col 2 - directly on top of the col 1
click markers, a jumbled overlap Adarsh caught from a full-browser-window screenshot.
Reproduced locally by rendering the real `_stock_matrix_heatmap()` output to a PNG with
`kaleido` (not a project dependency - installed only for this one diagnostic run, then
removed) at both a phone-width (430px) and a desktop-width (1400px) render, which showed
the exact same collision kaleido-side, confirming it wasn't a Streamlit-only artifact.
Fixed by moving the tick labels onto col 1's own y-axis instead (`ticks=""`,
`showline=False` so there's no second axis line) — labels now render in the figure's left
margin (sized via `margin.l`, bumped from a flat `10` to `58`/`72` depending on row count),
clear of the markers, which sit inside col 1's own domain just to the labels' right. Col 2's
y-axis is now `showticklabels=False`. Re-verified clean at both widths via the same kaleido
render.

**Strip width tuning, then two real bugs found in a real browser (7-8 Oct 2026).** After
several rounds of narrowing both sector-colour strips and the click-to-isolate strip on
request (`column_widths` down to `[0.015, 0.95, 0.035]`, markers down to size 5, then
`row_heights`/`column_widths` for the sector strips down again to `[0.018, 0.982]` /
`[..., 0.018]`), Adarsh reported two real problems, not just more width requests:

1. **Click-to-isolate precision:** clicking directly on a visible marker did nothing;
   clicking slightly to its left did. Column 1 had been narrowed to `0.015` of the figure
   width - at that point the marker's own diameter exceeded the subplot's pixel width, so
   its right side was clipped by the domain boundary. The *visible* sliver of colour sat
   left of the marker's true data-coordinate centre, which is where Plotly actually does
   hit-testing - so the visible colour and the clickable point were no longer the same
   pixel. Fixed by widening col 1 back out to `0.028` (markers to size 9) - enough pixel
   room for the full marker to render unclipped, confirmed with the same kaleido PNG
   render used above (no clipping at either a phone-width or desktop-width render).
2. **Main grid cell click never worked, confirmed in a real browser:** this was the
   suspected-but-unconfirmed risk flagged when Section 6/7 first shipped - a `go.Heatmap`
   simply does not emit Plotly's point-selection event, in any browser, at any column
   width. No amount of width tuning could have fixed it. Fixed properly: the main grid
   heatmap (curve_number 2) now has `hoverinfo="skip"` and carries colour only; a new
   invisible `go.Scatter` trace (curve_number 3), one marker per cell at `marker.opacity=0`
   with a generous `size=16` hit area, is stacked on the exact same `row=2, col=2` subplot
   and carries both the hover tooltip text and the click handling.
   `_apply_pending_stock_matrix_click()`'s cell-click branch now keys off `curve_number ==
   3`, not `2`. Opacity-0 markers stay fully hit-testable in Plotly - this is the standard
   invisible-hover-target technique, not a hack specific to this chart.

Trace order is now five deep: 0 top strip, 1 click-to-isolate strip, 2 main grid colour
(no hover/click), 3 main grid invisible click/hover overlay, 4 sector-colour strip (right
edge). Re-verified: pyflakes clean, the `AppTest` click harness passes for both curve 1
and curve 3, all 15 `pytest` cases pass, and the kaleido PNG render is unclipped at both
widths. Still open: Adarsh has not yet confirmed the main-grid-cell-to-History click in an
actual browser - the overlay is new and only `AppTest`-verified so far.

**Colorbar gap fixed (8 Oct 2026).** Adarsh reported too much empty canvas-colored space
framing the Stock matrix heatmap. A kaleido render at realistic width pinned it down: the
main grid's colorbar was anchored at `x=1.06` with no explicit `thickness` - past the
right-edge sector strip's own domain, so Plotly's automargin expanded the right margin to
fit it, leaving a wide blank gap between the strip and the colorbar. Fixed by pulling the
colorbar in to `x=1.015` with `thickness=12`, and tightening the figure's own right margin
from `r=60` to `r=40` now that it no longer needs to reserve room for the wider automargin.
Re-rendered with kaleido: the gap is gone, colorbar sits tight against the strip. Re-ran the
15-case `pytest` suite and the `AppTest` click harness (curve 1 isolate, curve 3 cell-click)
- both still pass unchanged, since this is a pure layout/visual change with no effect on
trace structure or click logic. Not yet confirmed by Adarsh in a real browser.

**That colorbar fix wasn't the real cause (8 Oct 2026).** Adarsh reported the chart looked
identical and that he could drag the whole heatmap around inside its own margins - direct
proof the plot area was bigger than the actual grid and pannable into the slack. None of the
four subplot axis pairs had `fixedrange` set, so each one autoranged with its usual padding
beyond the real categories, and that padding was draggable. Fixed by setting
`fixedrange=True` on every x and y axis across all four subplots (top strip, isolate strip,
main grid, right strip) - this is a fixed-size correlation matrix, not something that needs
zoom or pan, so locking the axes removes the slack space entirely rather than just
relocating the colorbar closer to the edge. Re-ran the 15-case `pytest` suite and the
`AppTest` click harness again - both still pass, confirming `fixedrange` doesn't interfere
with Plotly's click/hover event path. Not yet confirmed by Adarsh in a real browser.

**The actual gap: an unranged axis shared by a heatmap and a scatter trace (8 Oct
2026).** Adarsh's own screenshot (119x579px, cropped right at the boundary) showed
genuinely flat background pixels after the isolate strip, not just low-contrast cells -
proof the grid's first real column wasn't starting where its domain said it should. Pixel-
diffed against a kaleido render with saturated test data (not near-zero correlations, to
rule out a contrast illusion) and found a consistent ~50px dead zone on every row before
the first cell, regardless of row. Isolated with a series of minimal repros: a bare
`go.Heatmap` alone in a figure has no such gap; the same heatmap inside this exact 2x3
subplot layout still has no gap; adding back the invisible `go.Scatter` click/hover overlay
(curve 3) into the same subplot reproduces the gap exactly. Plotly's combined auto-range
for a heatmap and a scatter trace sharing one axis works out some range wider than the
categories actually need, and kaleido (confirmed against the real screenshot) renders that
extra range as literal blank canvas before the first column. Fixed by pinning both axes of
the main grid (`row=2, col=2`) to an explicit `range` matching the category span exactly
(`[-0.5, len(cols)-0.5]` / `[len(rows)-0.5, -0.5]`), the same approach col 1's isolate strip
axis already used. Re-rendered: gap closed on every row, confirmed with both the earlier
low-correlation synthetic data and the high-correlation stress test. 15-case `pytest` and
the `AppTest` click harness both still pass unchanged. Not yet confirmed by Adarsh in a
real browser.

**Row labels drifting out of alignment with their own grid row (8 Oct 2026).** Adarsh's
screenshot showed row labels and the isolate strip visibly shifted relative to the main
grid's rows - a hover near the ANANDRATHI label returned BAJAJ-AUTO in the tooltip.
Root cause was the same class of bug as the horizontal gap just above: col 1's y-axis still
used `autorange="reversed"` while col 2's had just been pinned to an explicit `range`. Three
separate subplots (col 1's label/strip axis, col 2's grid axis, col 3's sector-strip axis)
each resolve their own y-axis independently even over the identical `categoryarray=rows` -
a scatter trace (col 1) and heatmaps (col 2, col 3) don't autorange identically over the
same categories, so the rows drifted apart instead of landing on the same pixel per row.
Fixed by pinning all three y-axes to the same explicit `range=[len(rows)-0.5, -0.5]`
instead of mixing autorange and explicit range. Re-rendered with a diagonal-highlighted
test matrix (where misalignment is obvious - the diagonal should land exactly on each row's
own label) and confirmed every row's label, isolate-strip marker, and diagonal cell now
line up. 15-case `pytest` and the `AppTest` click harness still pass. Not yet confirmed by
Adarsh in a real browser.

**History chart y-axis zoomed to the data, not fixed at [-1, 1] (8 Oct 2026).** Adarsh
reported the rolling-r line chart (e.g. "INDUSTOWER vs ASIANPAINT — 100-session rolling r")
was hard to read: real rolling correlations usually live in a narrow band (often 0-0.5),
but the y-axis was pinned to the coefficient's full theoretical range of `[-1, 1]`, so the
actual line rode flat and cramped near the bottom quarter of the plot. `[-1, 1]` is the
range `r` *can* take, not the range it typically *does* take for any one pair. Fixed with a
new `_rolling_range(rho)` helper in `tabs/correlation.py`: drops NaNs, takes the series' own
min/max, pads by `max(0.05, (max-min)*0.1)` so the line never touches the plot edge, clamps
back to `[-1, 1]`, and falls back to `[-1, 1]` only when the series has nothing to measure.
`_rolling_chart` passes this instead of a hardcoded range; both call sites (the single
History chart and the compact Top-5-partners grid) pick it up automatically since the range
is computed inside `_rolling_chart`, not at each call site. Verified with a synthetic series
shaped like the reported case (mean ~0.25, amplitude ~0.1): the axis now reads `[0.047,
0.429]` instead of `[-1, 1]`, and a kaleido render confirms the line now fills the plot
instead of riding flat near the bottom. `numpy` was added as an import to `tabs/correlation.py`
for this (previously unused in the file). pyflakes clean, 15-case `pytest` still passes. Not
yet confirmed by Adarsh in a real browser.

**Multi-row isolate via shift-click was silently collapsing to one row (8 Oct
2026).** Adarsh reported that holding ctrl (cmd on Mac) and clicking several row
markers didn't select more than one row. Two separate things were going on. First,
Plotly's own multi-select modifier for a click on a point trace is **shift**, not
ctrl/cmd - that's fixed inside Plotly.js itself, not something this app's Python
code or Streamlit's wrapper can remap, so ctrl/cmd was never going to do anything
regardless of the second bug. Second, there was a real bug: `_apply_pending_stock_
matrix_click()` read `points[-1]` only, discarding every other point in the
payload. On a shift-click, Plotly re-sends *all* currently-selected points (not
just the newly added one) each time, so a three-row shift-click arrived as three
`curve_number == 1` points and the old code kept only the last, silently
collapsing it back to a single isolated row. Fixed by collecting every
`curve_number == 1` point in the payload and setting `sm_isolate` to the full
list; a plain click with no modifier still isolates just one row, because
Plotly itself only ever sends that single point back in that case. Verified with
a new `AppTest` case that writes a synthetic three-point shift-click payload and
asserts `sm_isolate` ends up with all three symbols, then a follow-up plain
single-click payload that correctly replaces it with one. The existing click
harness (single isolate-strip click, main-grid cell click) still passes
unchanged. pyflakes clean, 15-case `pytest` still passes. Not yet confirmed by
Adarsh in a real browser - he should use shift, not ctrl/cmd, when he tries it.

**Still not working after that fix - the real cause was a missing `uirevision`
(8 Oct 2026).** Adarsh reported multi-select still didn't work after the
points[-1] fix above. The remaining cause: `st.plotly_chart(..., on_select=
"rerun", ...)` triggers a full Streamlit rerun after every click, and that
rerun calls `_stock_matrix_heatmap()` again, sending the browser a brand-new
figure spec. Plotly.js decides whether to treat a new figure as "the same
chart, just updated" or "a different chart" using `layout.uirevision` - this
figure had none, so every post-click rerun looked like a different chart to
Plotly.js, which resets its own client-side selection state on that boundary.
Concretely: shift-click row A arrives correctly, triggers a rerun, the new
figure comes back with no `uirevision`, Plotly wipes the selection it was
tracking, and the next shift-click on row B starts from empty instead of
adding to A - so the end result always looked like "only one row, no matter
what." This fully explains the symptom even with the earlier points[-1] fix
correctly deployed; the two bugs were independent and both had to be fixed.
Fixed by setting a constant `uirevision="stock_matrix"` on the figure's
layout, telling Plotly.js this is the same chart across reruns, so it keeps
its selection state between clicks instead of resetting it. This is a
browser-side state-persistence behaviour with no Python-observable effect -
`AppTest` writes directly into `st.session_state` and bypasses the real
Plotly.js client entirely, so it cannot confirm or refute this fix the way it
could the points[-1] bug. pyflakes clean, 15-case `pytest` still passes
(no behavioural change observable outside the browser). This one genuinely
needs Adarsh to confirm with a real shift-click in a real browser - there is
no remaining automated way to verify it in this environment.

**The actual root cause: isolating a row hid every other row's marker (8 Oct
2026).** Adarsh reported it still didn't work after both fixes above. Those
fixes were real but addressed symptoms, not the structural cause: `_stock_
matrix_section` set `rows = isolate if isolate else shown` and passed that
narrowed `rows` straight into `_stock_matrix_heatmap`. The instant one row
was isolated - by click or by the multiselect - the heatmap re-rendered
showing *only* that isolated row. Every other row's click-to-isolate marker
disappeared along with it, so there was no second marker left to click,
shift-held or not. No modifier key, and no amount of `uirevision` tuning,
could have fixed this - the marker for the second stock simply wasn't being
drawn after the first click landed.

Fixed by decoupling "what's isolated" from "what's shown": the grid now
always renders every row in `shown` (`rows = shown`, unconditionally), and
`_stock_matrix_heatmap(..., isolated=set(isolate))` only changes how
isolated rows' markers are *drawn* - bigger (size 12 vs 9) with a 2px accent-
coloured (`P.ACCENT`) outline, everything else left at its normal size with
no outline - rather than filtering any row out of the matrix. Click handling
in `_apply_pending_stock_matrix_click()` changed to match: a plain click (no
modifier needed at all now) toggles that one row's membership in `sm_isolate`
- adds it if absent, removes it if already there - instead of replacing the
whole list. Multiple rows accumulate one plain click at a time because
nothing about the grid changes shape as you go. The "Isolate rows" multi-
select's placeholder/caption text changed from "see only their rows" to
"highlight their rows" to match - narrowing-the-view is no longer what this
control does.

Verified with a new `AppTest` case: three sequential plain single-point
clicks (A, then B, then C) land as `['A']`, then `['A','B']`, then
`['A','B','C']` - confirming accumulation needs no shift/ctrl/cmd at all now
- and a fourth click re-clicking A drops it back to `['B','C']`, confirming
toggle-off. The existing click harness (isolate-strip single click, main-
grid cell click to History) still passes unchanged. A kaleido render with
three isolated rows out of ten shows all ten rows present with the three
isolated ones visibly larger and outlined, confirming the view genuinely
stops narrowing. pyflakes clean, 15-case `pytest` still passes. The earlier
two fixes (points[-1] merge, `uirevision`) are kept - both are real,
independently-correct fixes for problems that existed - but neither was
sufficient on its own because this structural issue sat underneath both of
them. Not yet confirmed by Adarsh in a real browser, but this fix no longer
depends on any particular modifier key or browser-side state-persistence
behaviour, so it should be more robust to verify than the previous two
attempts.

**Narrowing is back, as an explicit opt-in checkbox, not automatic (8 Oct
2026).** Adarsh confirmed multi-select-by-click now worked (a screenshot
showed four highlighted, outlined rows among the full 60), then asked why
the matrix wasn't actually filtering down to just those rows - the
highlight-only fix above solved "select several by clicking" but silently
dropped the original "isolate = see only these rows" behaviour the feature
was named for, since that's exactly what caused the earlier bug (narrowing
immediately hid the markers needed to pick more). These two things are
structurally exclusive on the same chart at the same moment - a row's
marker can't stay clickable to add more AND be absent because it's not in
the narrowed set. Resolved by splitting them into two explicit steps instead
of one implicit one: a new "Show only isolated rows" checkbox (`sm_narrow`,
default off, disabled when nothing is isolated) controls `rows = isolate if
(isolate and narrow_only) else shown` - unchecked, every row stays visible
and clickable exactly as the highlight-only fix left it; checked, the grid
narrows to just the isolated rows, same as the original pre-bug behaviour.
Building the list (by click or by typing in the "Isolate rows" dropdown,
which always lists the full `shown` set regardless of the checkbox) and
narrowing down to it are now two deliberate actions - pick first, then
narrow, rather than narrowing automatically cutting off your own ability to
keep picking. The caption under the chart spells out the workflow so this
isn't a hidden control. Verified: a new `AppTest` case confirms the checkbox
defaults to off and flips on correctly; a kaleido render with the checkbox's
equivalent state (`rows` passed as just the 3 isolated symbols) shows the
grid correctly narrowed to exactly those 3 rows against every column, same
layout and alignment the original narrowed view had. pyflakes clean,
15-case `pytest` still passes. Confirmed by Adarsh in a real browser (8 Oct
2026): "yeah its good now" - committed as `ffd3721`.

**Box-select restored, then its actual blocker found (8 Oct 2026).** Adarsh
separately reported two things no longer worked: (1) the "Focus (always
included)" dropdown only ever took one stock - not a regression, `sm_focus`
has been a single-select `st.selectbox` since the Stock matrix was first
built (commit `1167612`); what he remembered as multi-select is the separate
"Isolate rows" control covered above. (2) Dragging a rectangle over the
heatmap used to zoom/narrow to the stocks inside it and no longer did -
this one was real. `fixedrange=True` on every axis (added in `085ea41` to
kill a subplot-misalignment bug) only disables the zoom/pan drag modes, not
box-select, so re-enabling it didn't need to touch any axis range or risk
reintroducing that drift. Added `"box"` to `st.plotly_chart`'s
`selection_mode` and extended `_apply_pending_stock_matrix_click`: when a
single on_select event carries more than one point for a curve (only
possible from a box drag, confirmed via the payload's `selection.box` key -
a plain click always sends exactly one), the union of every x/y in the
box becomes the new `sm_isolate` list with `sm_narrow` forced on, covering
both the isolate strip and the main grid. Existing single-point behavior
(toggle-isolate, click-a-cell-for-History) is untouched - a new `AppTest`
case drives both box shapes through synthetic payloads and reconfirms the
old single-click path alongside them, all passing.

That shipped but still didn't work in the browser - the real blocker was
`config={"displayModeBar": False}`, which hides the toolbar button that
normally switches Plotly's dragmode into `"select"`. With no toolbar and no
other way to flip it, the chart stayed on its default `"zoom"` dragmode,
which does nothing against `fixedrange=True` axes - dragging silently had
no effect regardless of the selection_mode fix. Fixed by setting
`dragmode="select"` directly in `fig.update_layout`, so the gesture works
with no toolbar needed; a plain click still registers as a single-point
selection under this dragmode, so nothing else changed. Confirmed working
by Adarsh in a real browser. pyflakes clean, 15-case `pytest` passes.

**`dragmode="select"` reverted - it broke all three plain-click behaviors
(8 Oct 2026, same day).** The "confirmed working" claim directly above did
not hold up: Adarsh reported click-to-isolate, click-a-cell-for-History,
and shift+click all stopped working. Root cause is a plotly.js behavior
that the comment justifying `dragmode="select"` had assumed rather than
verified in a real click-and-release test: under `dragmode="select"` (or
`"lasso"`), a zero-movement click on a point trace is treated as a
deselect, not a point selection - the selection tool needs the pointer to
actually move while down. That silently broke all point-trace clicks on
both the isolate strip (curve 1) and the invisible cell overlay (curve 3)
at once, which matches exactly what Adarsh saw. Fixed by removing
`dragmode="select"` from `_stock_matrix_heatmap` and dropping `"box"` from
`st.plotly_chart`'s `selection_mode` (now just `"points"`), restoring the
default (inert, since every axis is `fixedrange=True`) dragmode. The
box-drag-isolate bonus from the two sections above is gone as a result -
it was never itself confirmed working by Adarsh, only assumed compatible
with the three behaviors he had actually confirmed (7 Oct 2026), so
dropping it to get those three back is a clean trade, not a new loss.
Also removed the now-unreachable box-drag branches from
`_apply_pending_stock_matrix_click` and the matching help-text sentence
under the chart. pyflakes clean, 15-case `pytest` passes.
**Still unconfirmed in a real browser** - same testing-gap caveat as
always: no Playwright/Selenium in this environment, so this revert is
reasoned from known plotly.js semantics, not verified by actually clicking
in a browser. Needs Adarsh to confirm click-to-isolate, click-a-cell, and
shift+click all work again before this line is updated to say so.

**The revert above was lost, then re-applied (12 Oct 2026).** Adarsh
reported that clicking a cell in the Stock matrix did nothing - no History
section, and the isolate-strip row markers were dead too, while the Chart
tab's Measure tool (same `on_select="rerun"` + `selection_mode=("points",)`
pattern, but no `dragmode` set at all) still worked. That is the exact
signature of the `dragmode="select"` bug documented above, which should
already have been fixed. `git log --date=short` explains why it came back:
`b6f2263 (8 Oct 2026) Restore Stock matrix box-select (drag-to-isolate)` is
the newest commit touching `tabs/correlation.py`, so the committed state is
the *broken* `dragmode="select"` version. The revert was written the same
day but never committed - it existed only in the uncommitted working copy,
and a `git checkout -- tabs/correlation.py` run earlier in the 9 Oct
session discarded it, silently restoring the broken code. Lesson for this
file specifically: a fix that lives only in the working tree is one
`checkout` away from gone, and the commit log read alone will actively
mislead you about which version is current.

Re-applied the same four-part fix verbatim (no `dragmode` in
`_stock_matrix_heatmap`, `selection_mode=("points",)`,
box branches removed from `_apply_pending_stock_matrix_click`, help-text
box-drag sentence removed). The `dragmode` comment block now also states
that restoring box-select would mean putting back both the `"box"`
selection mode and the handler branches, not just re-showing the modebar -
the earlier wording claimed the handler still accepted box payloads, which
stopped being true once those branches were deleted.

Verified Python-side by injecting synthetic selection payloads into
`st.session_state["sm_heatmap_plot"]` under `AppTest`, using symbols taken
from the live `sm_isolate` multiselect's own options (an out-of-options
symbol is silently dropped by Streamlit, which produced misleading empty
results on a first pass): cell click → `History` / row / `Another stock` /
column; single row-marker click → `['ANANDRATHI']`; two-point shift payload
→ both rows; diagonal self-pair → no navigation. Zero exceptions, full
suite 62/62. **Still unconfirmed in a real browser, for the same reason as
the 8 Oct entry** - needs Adarsh to click a cell, a row marker, and
shift+click before this is called done.

**Box-drag-select brought back alongside plain click, via an explicit
toggle (12 Oct 2026).** Adarsh asked for box-drag-to-isolate back on top
of everything above - click-to-isolate, click-a-cell-to-History, and
shift-click all still working too. That is not simultaneously possible
under one `dragmode`: the 8 Oct finding was that under `dragmode="select"`
a zero-movement click never fires a selection event at all (it registers
as a deselect), so a dragmode that enables box-select structurally
disables every plain click at the same time, and there is no third
dragmode that does both. No way was found around this within plotly.js's
own selection model.

The resolution is an explicit `st.toggle("Box select (drag to isolate
several rows at once)", key="sm_box_mode")`, default off, read by both
`_stock_matrix_section` (which now passes `box_mode` into
`_stock_matrix_heatmap` and switches `selection_mode` to
`("points", "box")` only while it's on) and `_stock_matrix_heatmap` itself
(`dragmode="select"` only while `box_mode` is True, otherwise `"zoom"` -
plotly's own default, inert since every axis is `fixedrange=True`). The
help text under the chart switches with it, so the user is told which
gestures are live. `_apply_pending_stock_matrix_click` restores the
box-payload branches removed earlier today (multi-point + the payload's
own `selection.box` key → union of rows/columns in the rectangle →
`sm_isolate` replaced and `sm_narrow` forced on), now guarded by that same
`payload.get("selection", {}).get("box")` check so a box-mode-off
shift-click (also multi-point, but no `box` key) still falls through to
the existing per-point toggle loop rather than being treated as a
rectangle.

This is a real trade-off, not a workaround that quietly restores
everything: box-drag and plain-click are mutually exclusive within a
single render, by one `st.toggle` flip, not simultaneously live on the
same click. That is the best available shape given plotly.js's actual
selection semantics in the Streamlit 1.64 build this app ships.

Verified Python-side under `AppTest`, extending the synthetic-payload
method above to seed `sm_box_mode` before each run: with the toggle off,
plain click-isolate, shift-click (two rows), and cell-click-to-History all
reproduce the earlier results unchanged. With it on, a 3-point box payload
over the isolate strip (`curve_number 1`, with `selection.box` set)
isolates and narrows to all three rows; a 2-point box payload over the
main grid (`curve_number 3`) isolates the union of its rows and columns;
and a degenerate single-point payload with a `box` key present (e.g. a
drag that didn't actually move) still falls through to the ordinary
single-cell → History path, not the bulk-isolate one, since that branch
requires more than one point. All seven cases: zero exceptions. Full
suite 62/62.

**Confirmed working by Adarsh in a real browser (12 Oct 2026).**

**Deployed-app bar data (12 Oct 2026).** Streamlit Community Cloud ships only what is in
git and never runs `jobs/`, so the Correlation tab was permanently empty there — the real
store lives outside the repo at `~/trader_desk_data/intraday.db` (80MB) by design.
Adarsh chose to commit a trimmed subset rather than ship the whole thing: daily bars only,
last 500 sessions, all 213 symbols, `corr_cache` emptied — `data/bars_daily_500d.db`,
10MB on disk / 3.2MB compressed. The full store was rejected at 80MB (GitHub warns past
50MB, hard-blocks at 100MB) because every refresh would commit a fresh ~18MB blob that git
keeps forever.

`intraday_store.db_path()` now returns the local store wherever it exists and the bundled
copy otherwise, so a machine running the fetch jobs behaves exactly as before. Two guards
keep the tracked file safe: `get_connection()` opens the bundled path strictly read-only
(`mode=ro`), and the new `get_write_connection()` — which `jobs/fetch_intraday.py` now
uses — always targets the local path, so a job run on a machine without a local store
creates one outside the repo instead of writing into tracked data. Schema DDL moved into
`_init_schema()`, shared by both and never run against the read-only copy.

`DEFAULT_LOOKBACK` moved 20 → 90. The tier rule sends 1–60 days to 15m bars, which the
bundled store does not contain, so the old default would have landed every visitor on an
empty matrix. 90 resolves to the daily tier and works in both environments; a local
machine can still type any lookback down to 1.

**Known limits of this, by construction:** on the deployed app any lookback of 60 days or
less still has nothing to read, because there are no 15m bars in the bundle. The snapshot
is also point-in-time — bars run to 2026-10-05 and go stale until re-committed; nothing
refreshes it automatically, unlike `events.db` which the daily workflow updates. Verified
by running the app with `TRADER_DESK_DATA_DIR` pointed at a nonexistent directory: resolves
to the bundled copy, 72,174 daily rows, writes rejected with `OperationalError`, Correlation
renders at the 90-day default with no empty state and no exception. Suite 74/74.

**Acceptance checks (`tests/test_correlation.py`, run with `.venv/bin/python -m pytest
tests/test_correlation.py -v`), all 15 passing — unchanged by Sections 6/7, which have
so far been verified by ad-hoc synthetic/real-data smoke scripts and `AppTest` UI-level
runs rather than committed pytest functions:** self-correlation is 1.0, symmetry,
range, tier boundaries (including that "1m" is never returned by anything), log returns
strip price level but not shared drift, the overnight gap is excluded, stale bars become
`NaN` without forward-filling, the memo really skips recomputation, a missing store
raises a typed error instead of crashing, and the sector sanity check. On the last one:
at 20 days on 15m bars HDFCBANK/ICICIBANK (+0.2297) does not clearly separate from
HDFCBANK/SUNPHARMA (+0.1568) — short-window 15m correlation is genuinely noisy, not
wrong. The daily tier at longer lookbacks resolves it cleanly: 120 days gives 0.3874 vs
0.0310, 250 days gives 0.5052 vs 0.1062. The test asserts on the 120-day daily
comparison, not the 20-day one. Plus four sector-matrix tests: same-symbol exclusion on
a synthetic 3-stock case, a one-stock sector's diagonal correctly returning no data,
every F&O symbol mapping into at least one display group, and the real-data no-self-pairs
check above.

---

### `test_key.py`
A scratch script. Run it once with `python test_key.py` to confirm your `GEMINI_API_KEY` in `.env` works. Not part of the app itself.

---

### `.env`
Holds secrets. Never committed. Required keys:
- `GEMINI_API_KEY` — Google Gemini (used for news tagging in Week 4+)
- `FRED_API_KEY` — FRED macro/rates data (used in Week 1 Rates tab)
- `TELEGRAM_BOT_TOKEN` — Telegram alerts (used in Week 7)

---

## Dependencies (`requirements.txt`)

| Package | Why |
|---|---|
| `streamlit` | Web UI |
| `yfinance` | Market price data (personal use only) |
| `pandas` | Data handling and DataFrames |
| `plotly` | Interactive charts |
| `python-dotenv` | Load `.env` secrets |
| `jugaad-data` | Indian NSE index history (Nifty Auto, Energy, Mid Select, Defence) |
| `nselib` | NSE corporate actions and board-meeting calendar, for the Events tab |
| `scipy` | Hierarchical clustering (average linkage) for the Correlation tab's Stock matrix section |

`scripts/refresh_symbol_names.py` also uses `requests`, but it is a maintenance script that
never runs in the app, so `requests` stays out of `requirements.txt` (it arrives transitively
with `yfinance` anyway).

`pytest` is used by `tests/test_correlation.py` but, like `pyflakes`, stays out of
`requirements.txt` as a dev-only tool — nothing in the deployed app imports it.

**Still to add** as we build: `google-genai`, `feedparser`, `fredapi`

**Data sources:**
- `yfinance` — global indices, FX, commodities, volatility (no key needed)
- `jugaad-data` — Indian sectoral indices pulled directly from NSE (no key needed; results cached to avoid NSE rate limits)
- `nselib` — corporate actions and the board-meeting calendar, fetched by a script into the committed `events.db`, never at request time

---

## Git History

| Commit | Date | Author | What changed |
|---|---|---|---|
| `d45e9c1` | 2 Oct 2026 | Adarsh Gupta | Initial commit — `.gitignore`, `README.md` |
| `f4b75d1` | 3 Oct 2026 | Adarsh Gupta | Week 1: dashboard with five tabs, SQLite cache, dark theme, constituent popovers |
| `b0315c9` | 3 Oct 2026 | Adarsh Gupta | Design: Swiss editorial restyle — light canvas, Inter, signal accent |
| `d6a13c7` | 4 Oct 2026 | Adarsh Gupta | Design: matte palette, compact watchlist tiles, direction-coloured lines |
| `550eb10` | 4 Oct 2026 | Adarsh Gupta | Watchlist: five read-only index views plus four editable custom lists |
| `833e8e4` | 4 Oct 2026 | Adarsh Gupta | Search instruments from a local symbol table instead of typing tickers |
| `823f3bc` | 4 Oct 2026 | Adarsh Gupta | Replace the delisted Tata Motors ticker; hold the news tab back from deploy |
| `b816e49` | 4 Oct 2026 | Adarsh Gupta | Instrument picker is a type-ahead dropdown, and the Chart tab has one too |
| `49bd7bd` | 4 Oct 2026 | Adarsh Gupta | Add global index members to the instrument table |
| `3a46e9b` | 4 Oct 2026 | Adarsh Gupta | Ship the instrument table as a seed file instead of building it on first load |
| `edfbdda` | 4 Oct 2026 | Adarsh Gupta | Always top-up the symbol table from the seed file on startup |
| `62ccb3f` | 4 Oct 2026 | Adarsh Gupta | Replace dynamic symbol fetch with a fixed lookup table |
| `bd50a3c` | 4 Oct 2026 | Adarsh Gupta | Show name only in instrument dropdown, ticker only when needed |
| `d32ebdd` | 4 Oct 2026 | Adarsh Gupta | Move period control into each tab, remove it from the header |
| `5256b31` | 4 Oct 2026 | Adarsh Gupta | Use NSE's full company names so instruments are findable by name |
| `90d6c86` | 5 Oct 2026 | Adarsh Gupta | Stop reloading the page on every watchlist click |
| `6a697eb` | 5 Oct 2026 | Adarsh Gupta | Add start and end date pickers to the Chart tab |
| `1556e1e` | 5 Oct 2026 | Adarsh Gupta | Restore the watchlist tile geometry lost in the native rewrite |
| `cbbda77` | 5 Oct 2026 | Adarsh Gupta | Add a Measure tool to the Chart tab |
| `a03dcda` | 5 Oct 2026 | Adarsh Gupta | Show friendly instrument names and tidy the Chart tab layout |
| `866f5ef` | 5 Oct 2026 | Adarsh Gupta | Document the Measure tool and Chart tab layout in ARCHITECTURE.md |
| `b5eaf84` | 5 Oct 2026 | Adarsh Gupta | Fix index dates rewinding a day for markets east of UTC |
| `237cf27` | 5 Oct 2026 | Adarsh Gupta | Add the Week 5-6 earnings call sentiment tracker spec to CLAUDE.md |
| `b64899a` | 5 Oct 2026 | Adarsh Gupta | Show each index's own return beside the Watchlist's Change window toggle |
| `31b21b2` | 5 Oct 2026 | Adarsh Gupta | Move the header rule closer to the wordmark |
| `815f58d` | 5 Oct 2026 | Adarsh Gupta | Add dark mode (Charcoal Warm) |
| `ab338dd` | 5 Oct 2026 | Adarsh Gupta | Make the dark/light toggle apply without a manual refresh |
| `094ca14` | 5 Oct 2026 | Adarsh Gupta | Document date normalization and index staleness in ARCHITECTURE.md |
| `6e603fc` | 5 Oct 2026 | Adarsh Gupta | Add a one-off workflow to test NSE access from GitHub Actions |
| `371497b` | 5 Oct 2026 | Adarsh Gupta | Add an Events tab for F&O board meetings and corporate actions |
| `388d1df` | 5 Oct 2026 | Adarsh Gupta | Split app.py into one module per tab plus shared modules |
| `d327a03` | 5 Oct 2026 | Adarsh Gupta | Split data_loader.py by concern into storage/prices/watchlists/symbols |
| `a97c532` | 5 Oct 2026 | Adarsh Gupta | Rewrite ARCHITECTURE.md for the module split |
| `556a28b` | 6 Oct 2026 | Adarsh Gupta | Fix the price cache silently losing good closes, and merge in jugaad-data for Nifty/Bank Nifty/Nifty IT |
| `0e5a3fb` | 6 Oct 2026 | Adarsh Gupta | Document the price cache upsert fix and jugaad-data merge in ARCHITECTURE.md |
| `bf2e49f` | 6 Oct 2026 | Adarsh Gupta | Extend the jugaad-data gap-fill to every NSE equity, not just the 3 indices |
| `fea18b1` | 6 Oct 2026 | Adarsh Gupta | Document the per-equity jugaad-data gap-fill in ARCHITECTURE.md |
| `556a28b` | 6 Oct 2026 | Adarsh Gupta | Fix the price cache silently losing good closes, merge in jugaad-data for Nifty/Bank Nifty/Nifty IT |
| `357a85c` | 6 Oct 2026 | Adarsh Gupta | Build daily candles from hourly bars instead of yfinance's daily feed |
| `96759d2` | 6 Oct 2026 | Adarsh Gupta | Refresh the price cache every 10 minutes while NSE is open |
| `1a1a0bd` | 6 Oct 2026 | Adarsh Gupta | Limit the 10-minute refresh to instruments with a live Indian source |
| `72eb408` | 6 Oct 2026 | Adarsh Gupta | Serve the stale cache when a refetch comes back empty |
| `82760ec` | 6 Oct 2026 | Adarsh Gupta | Add the Step 0 discovery run for the F&O correlation matrix |
| `714efbf` | 6 Oct 2026 | Adarsh Gupta | Build the F&O correlation matrix: store, fetch job, compute, tab |
| `1167612` | 8 Oct 2026 | Adarsh Gupta | Add Stock matrix and History sections to the F&O correlation tab |
| `085ea41` | 8 Oct 2026 | Adarsh Gupta | Fix Stock matrix heatmap layout bugs: colorbar gap, draggable slack, cell-start gap, row misalignment |
| `ffd3721` | 8 Oct 2026 | Adarsh Gupta | Zoom History y-axis to the data; fix Stock matrix multi-row isolate |
| `b6f2263` | 8 Oct 2026 | Adarsh Gupta | Restore Stock matrix box-select (drag-to-isolate) |
| `8f8661b` | 8 Oct 2026 | Adarsh Gupta | Merge feature/fno-correlation: F&O correlation tab |

**Deployed:** status not reconfirmed since `a97c532`; verify against `origin/main` before
relying on this.

**Merged to `main`:** `feature/fno-correlation` (commit `8f8661b`, 8 Oct 2026) — the full
F&O correlation matrix: `fno.py`, `analytics/tiers.py`, `analytics/correlation.py`,
`analytics/sector_matrix.py`, `analytics/stock_matrix.py`, `analytics/rolling.py`,
`data_layer/intraday_store.py`, `jobs/fetch_intraday.py`, `tests/test_correlation.py`
(15/15 passing) and `tabs/correlation.py` wired into `app.py`'s `NAV`. Built across
6-8 Oct 2026: the sector matrix + drill-down (spec sections 5/5A/5B) with a scoped
categorical colour-palette exception in `theme.py` (`sector_colors()`); the Stock matrix
(section 6, cluster-ordered heatmap) and History (section 7, rolling correlation)
sections, with the tab restructured into four `st.pills`-selected sections; then
sector-strip hover text, a data-fit correlation-value colour scale with a second scoped
colour exception (`theme.CORR_POS`/`CORR_NEG`/`CORR_MID`), row isolation via a
multiselect, and two click interactions on the Stock matrix heatmap (an isolate-click
strip rebuilt as `go.Scatter`, since heatmap traces don't support Plotly's
point-selection, plus a cell click that jumps to History pre-loaded with that pair) —
see the Correlation tab writeup above.

**In progress, uncommitted:** the analyst estimates tracker (spec:
`analyst_estimates_spec-c3606434.txt`), built 8 Oct 2026 on top of `main` —
`jobs/snapshot_estimates.py`, `jobs/fill_actuals.py`, `db/schema_estimates.sql`,
`tests/test_estimates.py` (11/11 passing), `docs/estimates_discovery.txt`, and edits to
`events_db.py` and `tabs/events.py`; see the module writeup above. Not committed, not
pushed — the jobs have only run against scratch data, not live yfinance.

**In progress, uncommitted:** the expected-move feature (spec:
`expected_move_spec-3c050a43.txt`), built 9 Oct 2026 overnight under Adarsh's standing
go-ahead — `analytics/expected_move.py`, `trading_calendar.py`, `db/schema_expected_move.sql`,
`jobs/refresh_expected_moves.py`, `scripts/discover_expected_move.py`,
`docs/expected_move_discovery.txt`, `tests/test_expected_move.py` (26/26),
`tests/test_refresh_expected_moves.py` (6/6), and edits to `events_db.py` and
`tabs/events.py`; see the module writeup above. Live-run-verified against the real
`events.db` for 5 symbols (TCS, RELIANCE, HDFCBANK, INFY, WIPRO), three real bugs found
and fixed, UI only `AppTest`-verified (no real browser available). Not committed, not
pushed.

---

## What's Coming

- **Rates tab:** Parked until `FRED_API_KEY` is added to `.env`; will use FRED API for interest rate data
- **Index weights:** the `CONSTITUENTS` dict in `constituents.py` is hand-typed and its weights are
  invented placeholders. Tickertape's `__NEXT_DATA__` payload gives real figures —
  `weight = price × freeFloat / Σ`, where `freeFloat` is a share count in crores. The formula
  was validated to the decimal against all five Indian indices but is **not yet built in**.
- **News tab:** `news_loader.py` is in the repo but unwired; it was held back from deploy.
- **Staleness indicator:** the `NSE:`-prefixed indices (Nifty Midcap, Nifty Auto) lag because
  `jugaad_data.nse.index_raw` itself publishes late — a last close more than one trading day
  old is upstream, not a cache bug. Offered but not built: flag that in the UI.
- **Week 2 remaining:** Return heatmap (1d/1w/1m), cross-asset correlation matrix (cut-first)
- **Scheduled events refresh:** `events.db` is refreshed by running `scripts/fetch_events.py`
  by hand. A `.github/workflows/fetch_events.yml` on a weekday-evening cron, committing the
  database back only when it changed, is designed but **not built** — it needs
  `permissions: contents: write` and Adarsh's go-ahead before it runs on a schedule.
- **Earnings call sentiment tracker:** the Week 5-6 spec in `CLAUDE.md`. Depends on the
  Events calendar (built) and, now, the analyst estimates tracker below for the T-1
  pattern it reuses.
- **Analyst estimates tracker — cron and live run:** `jobs/snapshot_estimates.py` and
  `jobs/fill_actuals.py` are built and test-covered (see the module writeup above) but
  have never run against live yfinance/the real `events.db`, and no cron/launchd entry
  exists yet for the daily pair (both docstrings describe ~6pm IST, after
  `fetch_events.py`). Acceptance check H (one real estimate row and one "no estimate"
  row, both visible in the Events tab in a real browser) is Adarsh's own manual check.
- **Expected-move feature — cron and real-browser UI check:** `jobs/refresh_expected_moves.py`
  has now been run by hand against the real `events.db` twice: the original 5-symbol
  discovery backfill, then a full real-world run (74 symbols, every upcoming-results
  F&O name, `--backfill`) that found and fixed bug #4 above (the M&M `&`-encoding bug)
  — see the module writeup above for both runs' results. Still no cron/launchd entry.
  The Calendar-subtab columns, note, expander and per-symbol history table are only
  verified via `AppTest` (zero exceptions) — no browser/screenshot tool exists in this
  environment, so Adarsh still needs to look at it in a real browser before it's
  considered visually confirmed.
- **F&O correlation matrix:** merged to `main` (`8f8661b`, 8 Oct 2026) — see Git History
  above. Still open: a daily cron/launchd entry for `jobs/fetch_intraday.py` is designed
  (spec section 5) but not installed on Adarsh's Mac — until it is, the store only has
  whatever the manual run on 6 Oct 2026 fetched, and ages out of the 15m tier's 60-day
  window over time without a human running the job by hand. The rollup-CSV fallback in
  the original spec was dropped outright, not deferred: the app is localhost-only and the
  repo private, so there is no deploy without the SQLite store sitting right beside it.
  Also open: no UI screenshot/browser pass on any of the four Correlation sections beyond
  `AppTest` — worth a real-browser look (sector colours against the beige theme, the
  History chip row). Confirmed by Adarsh in a real browser (7 Oct 2026): the Stock matrix
  click-to-isolate strip registers clicks correctly, and the label/marker overlap that
  first produced is fixed (see the Correlation tab writeup above). Still unconfirmed in a
  real browser: a main-grid cell click jumping to History. No Playwright/Selenium/node/npx
  tooling is available in this environment to check that directly, so it needs Adarsh's
  own eyes. `analytics/stock_matrix.py`/`analytics/rolling.py` also have no committed
  pytest functions yet, only ad-hoc smoke scripts and AppTest runs.
