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
│   └── events.py
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
│
├── data/
│   └── symbols.csv     # Committed instrument table (3,360 rows) — search's source of truth
├── scripts/
│   ├── refresh_symbol_names.py  # Maintenance: pull full NSE company names into symbols.csv
│   ├── fetch_events.py          # Writes events.db from NSE's two event feeds
│   └── discover_nse.py          # One-off: dump nselib's real output shape
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

`scripts/refresh_symbol_names.py` also uses `requests`, but it is a maintenance script that
never runs in the app, so `requests` stays out of `requirements.txt` (it arrives transitively
with `yfinance` anyway).

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
