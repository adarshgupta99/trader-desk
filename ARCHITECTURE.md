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
The Streamlit entry point. Four asset-class tabs, each with plotly charts and a period selector.

**Tabs (Rates skipped — no FRED key yet):**

| Tab | Assets |
|---|---|
| Indices | S&P 500, Nasdaq, Nifty 50, KOSPI, FTSE 100, DAX, Euro Stoxx 50, Nikkei 225 |
| India | Bank Nifty, Nifty IT, Nifty Auto, Nifty Energy, Nifty Midcap 150, Nifty Defence |
| FX | USD/INR, EUR/USD, GBP/USD |
| Commodities | WTI Crude Oil, Gold, Silver |
| Volatility | VIX (US) |

**Key functions:**
- `price_chart(label, ticker, period, height)` — builds a plotly line chart; colours the line and % change green/red
- `render_grid(assets, period, cols)` — lays out charts in a multi-column grid
- `period_selector(key)` — renders a horizontal radio (1W / 1M / 3M / 6M / 1Y) and returns the yfinance period string

All data goes through `get_price_history()` from `data_loader.py`.

---

### `data_loader.py`
Market data fetcher with a SQLite cache layer.

**Public function:**
```python
get_price_history(ticker, period="1mo", interval="1d", max_age_hours=6)
→ pandas DataFrame with columns: Open, High, Low, Close, Volume
```

**Internal flow:**
1. `_get_connection()` — opens `market_cache.db`, creates tables if they don't exist
2. `_is_fresh()` — checks `cache_meta` table; returns True if last fetch was < `max_age_hours` ago
3. `_read_from_cache()` — reads rows from `price_cache` table, returns DataFrame
4. `_fetch_from_yfinance()` — calls `yf.Ticker(ticker).history()`
5. `_write_to_cache()` — writes OHLCV rows + updates `cache_meta` timestamp

**SQLite tables:**
- `price_cache` — rows of (ticker, period, interval, date, open, high, low, close, volume)
- `cache_meta` — one row per (ticker, period, interval) tracking when it was last fetched

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

**Still to add** as we build: `google-genai`, `feedparser`, `requests`, `fredapi`

**Data sources:**
- `yfinance` — global indices, FX, commodities, volatility (no key needed)
- `jugaad-data` — Indian sectoral indices pulled directly from NSE (no key needed; results cached to avoid NSE rate limits)

---

## Git History

| Commit | Date | Author | What changed |
|---|---|---|---|
| `d45e9c1` | 2 Oct 2026 | adarshgupta99 | Initial commit — `.gitignore`, `README.md` |
| *(uncommitted)* | 3 Oct 2026 | — | Added `app.py`, `data_loader.py`; wired SQLite cache; updated `.gitignore` for `market_cache.db` |

---

## What's Coming

- **Rates tab:** Parked until `FRED_API_KEY` is added to `.env`; will use FRED API for interest rate data
- **Week 2:** Watchlists, return heatmap, cross-asset correlation matrix, date-range controls, Streamlit Cloud deploy
