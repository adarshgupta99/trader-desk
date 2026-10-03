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
├── market_cache.db     # Auto-generated SQLite database (gitignored)
├── requirements.txt    # Python dependencies
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
The Streamlit entry point. Seven tabs with a shared period selector at the top.

**Tabs:**

| Tab | Contents |
|---|---|
| Watchlist | Up to 30 instruments; 1D/1W toggle; preset buttons (Nifty 50, Midcap, Auto, IT, Bank); × to remove |
| Chart | Plot 1–2 tickers on dual y-axis; period selector; persists tickers across tab switches |
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
- `_watchlist_grid(watchlist, show_1w)` — single HTML flex block; remove via `?remove=` query param

All data goes through `get_price_history()` from `data_loader.py`.

---

### `data_loader.py`
Market data fetcher + watchlist storage, backed by SQLite.

**Public functions:**
```python
get_price_history(ticker, period="1mo", interval="1d", max_age_hours=6)
→ DataFrame with columns: Open, High, Low, Close, Volume

get_watchlist()                        → list of {"ticker": ..., "label": ...} dicts
add_to_watchlist(ticker)               → (ok: bool, message: str)
remove_from_watchlist(ticker)          → None
seed_watchlist_if_empty()              → seeds Nifty 50 on first ever launch (flag-guarded)
reset_watchlist_to_preset(preset_name) → clears and reloads a named preset
```

**Watchlist presets** (`WATCHLIST_PRESETS` dict): Nifty 50 (30), Nifty Midcap (20), Nifty Auto (15), Nifty IT (10), Bank Nifty (12)

**SQLite tables:**
- `price_cache` — OHLCV rows keyed by (ticker, period, interval, date)
- `cache_meta` — last fetch timestamp per (ticker, period, interval)
- `watchlist` — (ticker PK, label, added_at)
- `app_flags` — key/value flags; `watchlist_seeded` prevents re-seeding after first launch

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

**Still to add** as we build: `google-genai`, `feedparser`, `requests`, `fredapi`

**Data sources:**
- `yfinance` — global indices, FX, commodities, volatility (no key needed)
- `jugaad-data` — Indian sectoral indices pulled directly from NSE (no key needed; results cached to avoid NSE rate limits)

---

## Git History

| Commit | Date | Author | What changed |
|---|---|---|---|
| `d45e9c1` | 2 Oct 2026 | adarshgupta99 | Initial commit — `.gitignore`, `README.md` |
| `f4b75d1` | 3 Oct 2026 | adarshgupta99 | Week 1: dashboard with five tabs, SQLite cache, dark theme, constituent popovers |
| (pending) | 3 Oct 2026 | adarshgupta99 | Week 2: Watchlist (presets, 1D/1W toggle, HTML flex grid), Chart tab (dual y-axis, persistent state), design system |

---

## What's Coming

- **Rates tab:** Parked until `FRED_API_KEY` is added to `.env`; will use FRED API for interest rate data
- **Week 2 remaining:** Return heatmap (1d/1w/1m), cross-asset correlation matrix (cut-first), Streamlit Cloud deploy
- **Ticker discovery:** Deferred to future week; plan is Yahoo Finance autocomplete API for search-as-you-type
