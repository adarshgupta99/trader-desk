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
├── app.py              # Streamlit UI — the thing you actually run
├── data_loader.py      # Fetches market data; handles SQLite caching
├── data/
│   └── symbols.csv     # Committed instrument table (3,360 rows) — search's source of truth
├── scripts/
│   └── refresh_symbol_names.py  # Maintenance: pull full NSE company names into symbols.csv
├── market_cache.db     # Auto-generated SQLite database (gitignored)
├── requirements.txt    # Python dependencies
├── news_loader.py      # Week 3 RSS/GDELT work; currently unused — nothing imports it
├── test_key.py         # One-off script to verify Gemini API key works
├── .env                # Your secrets (gitignored, never committed)
├── .venv/              # Python 3.13 virtual environment (gitignored)
├── CLAUDE.md           # Project spec and weekly plan
└── ARCHITECTURE.md     # This file
```

---

## How Data Flows Right Now

```
User types a ticker in the browser
        │
        ▼
    app.py
        │  calls get_price_history(ticker, period, interval)
        ▼
  data_loader.py
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
    app.py  ◄──────────────────────────────────────────────────┘
        │  df["Close"] → st.line_chart()
        ▼
  Browser shows the chart
```

---

## Module Details

### `app.py`
The Streamlit entry point. Seven tabs. Each tab owns its own history selector —
there is no longer a shared one in the header.

**Tabs:**

| Tab | Contents |
|---|---|
| Watchlist | Nine views via a chip row: 5 read-only index views + 4 editable custom lists (≤30 each); 1D/1W toggle; × to remove |
| Chart | Plot 1–2 instruments on dual y-axis; own history selector; persists instruments across tab switches |
| Indices | S&P 500, Nasdaq, Nifty 50, KOSPI, FTSE 100, DAX, Euro Stoxx 50, Nikkei 225 |
| India | Bank Nifty, Nifty IT, Nifty Auto, Nifty Energy, Nifty Mid Select, Nifty Defence |
| FX | USD/INR, EUR/USD, GBP/USD, EUR/INR |
| Commodities | WTI Crude Oil, Gold, Silver |
| Volatility | VIX (US) |

Rates tab is parked until `FRED_API_KEY` is added.

**Design tokens** (top of file): `UP`, `DOWN`, `BG`, `SURFACE`, `BORDER`, `GRID`, `T1`, `T2`, `T3`

**Key functions:**
- `price_chart(label, ticker, period)` — plotly area chart, y-axis scaled to spread, green/red by direction
- `asset_card(label, ticker, period, decimal)` — card with CSS-restyled border, label, price, chart
- `_constituents_popover(label, members)` — `st.popover` with approx. index weights
- `render_grid(assets, period, cols, decimal)` — 3-column grid of `asset_card()` calls
- `_watchlist_grid(watchlist, show_1w, view)` — single HTML flex block; remove via `?remove=` query param
- `instrument_options()` — builds `(labels, label → ticker)`; label is the name alone, with
  the ticker appended only when two instruments share a name (Volkswagen, Jain Irrigation)
- `pick_instrument(key, placeholder, ...)` — the searchable dropdown used by Watchlist and Chart

**How instrument search works.** `pick_instrument` is an `st.selectbox` with
`filter_mode="contains"` and `accept_new_options=True`. Streamlit sends only the
*formatted* option strings to the browser and filters against those, so whatever the
dropdown displays is exactly what is searchable — `format_func` cannot hide a
searchable ticker behind a shorter label. That is why the instrument **names** have to
carry the searchable words (see `scripts/refresh_symbol_names.py`). Anything outside the
table stays reachable because `accept_new_options` offers the typed text as a raw ticker,
which `add_to_list` then validates against real price data.

All data goes through `get_price_history()` from `data_loader.py`.

---

### `data_loader.py`
Market data fetcher + watchlist storage, backed by SQLite.

**Public functions:**
```python
get_price_history(ticker, period="1mo", interval="1d", max_age_hours=6)
→ DataFrame with columns: Open, High, Low, Close, Volume

get_custom_lists()                     → the four editable lists
rename_custom_list(list_id, name)      → None
get_list_items(list_id)                → list of {"ticker": ..., "label": ...} dicts
add_to_list(list_id, ticker, label)    → (ok: bool, message: str); validates the ticker has data
remove_from_list(list_id, ticker)      → None
count_list_with_data(list_id)          → int, for the 30-instrument cap

refresh_symbols()                      → reloads the symbols table from data/symbols.csv
get_all_symbols()                      → every row, curated instruments ranked first
search_instruments(query, limit=10)    → server-side name/ticker search
export_symbol_seed(path)               → writes the live symbols table back out to CSV
symbol_count()                         → int
```

**Index views** (`WATCHLIST_PRESETS` dict): Nifty 50, Nifty Midcap, Nifty Auto, Nifty IT,
Bank Nifty. These are read-only views, not presets that get loaded — switching between them
replaces nothing, so there is no confirmation step.

**SQLite tables:**
- `price_cache` — OHLCV rows keyed by (ticker, period, interval, date)
- `cache_meta` — last fetch timestamp per (ticker, period, interval)
- `symbols` — (ticker, name, kind); rebuilt from `data/symbols.csv` on every startup
- `symbols_meta` — bookkeeping for the symbols table
- `custom_lists` / `custom_list_items` — the four editable watchlists

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

`scripts/refresh_symbol_names.py` also uses `requests`, but it is a maintenance script that
never runs in the app, so `requests` stays out of `requirements.txt` (it arrives transitively
with `yfinance` anyway).

**Still to add** as we build: `google-genai`, `feedparser`, `fredapi`

**Data sources:**
- `yfinance` — global indices, FX, commodities, volatility (no key needed)
- `jugaad-data` — Indian sectoral indices pulled directly from NSE (no key needed; results cached to avoid NSE rate limits)

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

**Deployed:** `62ccb3f` is the newest commit on the remote. Everything after it is local only.

---

## What's Coming

- **Rates tab:** Parked until `FRED_API_KEY` is added to `.env`; will use FRED API for interest rate data
- **Index weights:** the `CONSTITUENTS` dict in `app.py` is hand-typed and its weights are
  invented placeholders. Tickertape's `__NEXT_DATA__` payload gives real figures —
  `weight = price × freeFloat / Σ`, where `freeFloat` is a share count in crores. The formula
  was validated to the decimal against all five Indian indices but is **not yet built in**.
- **News tab:** `news_loader.py` is in the repo but unwired; it was held back from deploy.
- **Week 2 remaining:** Return heatmap (1d/1w/1m), cross-asset correlation matrix (cut-first)
