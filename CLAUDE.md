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
- **Week 7 (16-21 November):** Telegram abnormal-move alerts with top likely driver; morning brief of overnight moves and tagged news; first parked feature. Demo: an alert arriving on the phone unprompted. Cut first: morning brief, keep alerts.
- **Week 8 (23-28 November):** Event studies: after news type X, what did asset Y do over 1h/1d? Build a hit-rate table and the second parked feature. Demo: hit-rate page with honest sample sizes. Cut first: backtesting, keep the chosen feature.
- **Week 9 (30 November-5 December; rest 6 December):** Buffer, bug fixes, README, screenshots, two-minute demo video, clean deploy. No new features. Portfolio demo must respect data-use limits; keep the repo private unless Adarsh chooses otherwise. Cut first: anything unfinished.

## Parking lot

- India macro panel using RBI DBIE, planned for the weeks 7-8 feature slots.
  Verify available API access and data-use terms before implementing.
- Keep other new ideas parked until Monday planning; do not invent features
  to fill the two reserved slots.

## Working with Adarsh

Adarsh is a capable beginner, not a developer. Explain changes briefly in
plain English, including how to run or check them. Prefer small working
increments over big rewrites. Keep the app runnable after every change.
Help him follow the weekly plan as well as write the code.
