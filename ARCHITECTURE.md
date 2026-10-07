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
├── events_db.py        # Read-only access to events.db
├── fno.py              # The 213-stock F&O universe and sector lookup (fno_sectors.json)
│
├── analytics/
│   ├── tiers.py         # pick_tier(lookback_days) -> "15m" | "1d", and expected-obs sizing
│   ├── correlation.py   # Bars -> log returns -> pairwise Pearson; the stale-bar policy
│   ├── sector_matrix.py # 15x15 mean-correlation rollup + drill-down data (spec sections 5/5A/5B)
│   ├── stock_matrix.py  # Cluster order + preview selection for the stock-level matrix (spec section 6)
│   └── rolling.py       # Rolling correlation vs NIFTY/another stock/sector basket, top-5 partners (spec section 7)
├── data_layer/
│   └── intraday_store.py  # SQLite bar store outside the repo (~/trader_desk_data/intraday.db)
├── jobs/
│   └── fetch_intraday.py  # Daily cron: downloads 15m + daily F&O bars into the store
│
├── data/
│   └── symbols.csv     # Committed instrument table (3,360 rows) — search's source of truth
├── scripts/
│   ├── refresh_symbol_names.py  # Maintenance: pull full NSE company names into symbols.csv
│   ├── fetch_events.py          # Writes events.db from NSE's two event feeds
│   ├── discover_nse.py          # One-off: dump nselib's real output shape
│   └── discover_intraday.py     # Step 0 for the correlation feature: measures Yahoo's 15m feed
├── docs/
│   └── intraday_discovery.txt   # Output of the above — the numbers the feature is sized against
├── tests/
│   └── test_correlation.py      # Acceptance checks A-K for the correlation feature
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

**Deployed:** `a97c532` is the newest commit on `origin/main` — merged from
`refactor/split-into-modules` and pushed. `556a28b`, `0e5a3fb`, `bf2e49f` and `fea18b1`
are local on `main`, not yet pushed.

**In progress:** `feature/hourly-latest-close` branches off `fea18b1` and carries the shift
to hourly-resampled daily candles (see `prices.py` above). Not merged, not pushed.

`feature/fno-correlation` branches off the hourly branch and carries the F&O correlation
matrix, now fully built: `fno.py`, `analytics/tiers.py`, `analytics/correlation.py`,
`analytics/sector_matrix.py`, `analytics/stock_matrix.py`, `analytics/rolling.py`,
`data_layer/intraday_store.py`, `jobs/fetch_intraday.py`, `tests/test_correlation.py`
(15/15 passing) and `tabs/correlation.py` wired into `app.py`'s `NAV`. The local store
has been populated end to end (213 symbols, both tiers, zero failures). The sector
matrix + drill-down (spec sections 5/5A/5B) was added 6 Oct 2026 on top of the per-stock
tab, with a scoped categorical colour-palette exception in `theme.py` (`sector_colors()`)
agreed with Adarsh for that one feature. The Stock matrix (section 6, cluster-ordered
heatmap) and History (section 7, rolling correlation) sections were added 6 Oct 2026 on
top of that, together, with the tab restructured into four `st.pills`-selected sections
so each is shown one at a time. On 7 Oct 2026: sector-strip hover text, a data-fit
correlation-value colour scale plus a second scoped colour exception
(`theme.CORR_POS`/`CORR_NEG`/`CORR_MID`), row isolation via a multiselect, and two click
interactions directly on the Stock matrix heatmap (click a marker on the dedicated
click-to-isolate strip to isolate that row, click a cell to jump to History pre-loaded
with that pair) were all added, with the isolate-click strip rebuilt as a `go.Scatter`
(heatmap traces don't support Plotly's point-selection) and the sector-colour strip
restored to the right edge — see the Correlation tab writeup above for each. Not merged,
not pushed.

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
- **Earnings call sentiment tracker:** the Week 5-6 spec in `CLAUDE.md`. It depends on the
  Events calendar already built here for results dates.
- **F&O correlation matrix:** built on `feature/fno-correlation` (spec:
  `correlation_matrix_spec-43ee700c.txt`), including the sector matrix and drill-down
  (spec sections 5/5A/5B), the Stock matrix (section 6) and History (section 7)
  sections; see the full writeup above. Not yet merged to `main`. Still open: a daily
  cron/launchd entry for `jobs/fetch_intraday.py` is designed (spec section 5) but not
  installed on Adarsh's Mac — until it is, the store only has whatever the manual run on
  6 Oct 2026 fetched, and ages out of the 15m tier's 60-day window over time without a
  human running the job by hand. The rollup-CSV fallback in the original spec was
  dropped outright, not deferred: the app is localhost-only and the repo private, so
  there is no deploy without the SQLite store sitting right beside it. Also open: no UI
  screenshot/browser pass on any of the four Correlation sections beyond `AppTest` — worth
  a real-browser look (sector colours against the beige theme, the History chip row).
  Confirmed by Adarsh in a real browser (7 Oct 2026): the Stock matrix click-to-isolate
  strip registers clicks correctly, and the label/marker overlap that first produced is
  fixed (see the Correlation tab writeup above). Still unconfirmed in a real browser: a
  main-grid cell click jumping to History. No Playwright/Selenium/node/npx tooling is
  available in this environment to check that directly, so it needs Adarsh's own eyes.
  `analytics/stock_matrix.py`/`analytics/rolling.py` also have no committed pytest
  functions yet, only ad-hoc smoke scripts and AppTest runs.
