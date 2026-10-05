"""
Fetches F&O corporate actions and earnings/board-meeting dates from NSE
(via nselib) and stores them in events.db, separate from market_cache.db.

Run manually: python scripts/fetch_events.py
Scheduled via .github/workflows/fetch_events.yml (not yet created).

Column names below come from the real nselib 2.5.1 output, captured by
scripts/discover_nse.py on 2026-10-05 - see scratch/*.csv. They differ from
the original spec's guesses in a few places (noted inline); if nselib's
output ever changes shape again, re-run discover_nse.py and update the
candidate lists in pick() rather than guessing.
"""

import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from nselib import capital_market

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "events.db")
WINDOW_DAYS = 60
INSTRUMENTS_REFRESH_DAYS = 7
RETRY_DELAYS = (5, 15, 45)
INTER_CALL_SLEEP = 2

# Checked in order; first match wins. Edit this list if NSE's purpose text
# changes, or add new categories as they show up.
SUBTYPE_KEYWORDS = [
    ("dividend", "dividend"),
    ("split", "split"),
    ("sub-division", "split"),
    ("subdivision", "split"),
    ("bonus", "bonus"),
    ("rights", "rights"),
    ("buyback", "buyback"),
    ("buy back", "buyback"),
    ("fund raising", "fundraise"),
    ("fundraising", "fundraise"),
    ("qip", "fundraise"),
    ("preferential", "fundraise"),
    ("agm", "agm"),
    ("annual general", "agm"),
    ("result", "results"),  # checked last on purpose so "Financial Results/
                             # Dividend" still matches "dividend" first
]


def normalize_colname(col):
    return col.strip().lower().replace(" ", "").replace("_", "")


def pick(df, candidates):
    """Return the first column in df matching a candidate name, or None."""
    normalized = {normalize_colname(c): c for c in df.columns}
    for cand in candidates:
        real = normalized.get(normalize_colname(cand))
        if real is not None:
            return real
    return None


def parse_date(raw):
    """Parse an NSE date string to ISO YYYY-MM-DD, or None if unparseable."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    raw = str(raw).strip()
    if not raw or raw == "-":
        return None
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def pick_subtype(purpose_text):
    text = (purpose_text or "").lower()
    for keyword, subtype in SUBTYPE_KEYWORDS:
        if keyword in text:
            return subtype
    return "other"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS instruments (
            symbol     TEXT PRIMARY KEY,
            name       TEXT,
            lot_size   INTEGER,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            symbol      TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            subtype     TEXT,
            event_date  TEXT NOT NULL,
            record_date TEXT,
            purpose     TEXT NOT NULL,
            source      TEXT,
            fetched_at  TEXT,
            raw_json    TEXT,
            PRIMARY KEY (symbol, event_type, event_date, purpose)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    return conn


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_meta(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def call_with_retry(label, fn, *args, **kwargs):
    last_error = None
    for attempt, delay in enumerate((0,) + RETRY_DELAYS):
        if delay:
            print(f"  retrying {label} in {delay}s...")
            time.sleep(delay)
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_error = e
            print(f"  {label} attempt {attempt + 1} failed: {type(e).__name__}: {e}")
    raise RuntimeError(f"{label} failed after retries") from last_error


def refresh_instruments(conn):
    row = conn.execute("SELECT COUNT(*) FROM instruments").fetchone()
    count = row[0] if row else 0
    updated_at = get_meta(conn, "instruments_updated_at")
    is_stale = True
    if updated_at:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)
        is_stale = age > timedelta(days=INSTRUMENTS_REFRESH_DAYS)

    if count > 0 and not is_stale:
        print(f"instruments: using cached table ({count} rows, fresh)")
        symbols = {
            r[0] for r in conn.execute("SELECT symbol FROM instruments").fetchall()
        }
        return symbols

    print("instruments: fetching fno_equity_list()")
    df = call_with_retry("fno_equity_list", capital_market.fno_equity_list)
    print(f"  fetched {len(df)} F&O instruments")

    symbol_col = pick(df, ["symbol", "sym"])
    name_col = pick(df, ["underlying", "name"])
    if symbol_col is None:
        print(f"FATAL: no symbol column found. Columns were: {list(df.columns)}")
        sys.exit(1)

    now = datetime.now(timezone.utc).isoformat()
    symbols = set()
    for _, row in df.iterrows():
        symbol = str(row[symbol_col]).strip()
        name = str(row[name_col]).strip() if name_col else None
        symbols.add(symbol)
        conn.execute(
            "INSERT INTO instruments (symbol, name, lot_size, updated_at) "
            "VALUES (?, ?, NULL, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, "
            "updated_at=excluded.updated_at",
            (symbol, name, now),
        )
    set_meta(conn, "instruments_updated_at", now)
    set_meta(conn, "fno_count", str(len(symbols)))
    conn.commit()
    return symbols


def normalize_rows(df, event_type, source_label, valid_symbols):
    """Returns (rows, dropped_non_fno, skipped_bad_date)."""
    if df is None or len(df) == 0:
        return [], 0, 0

    symbol_col = pick(df, ["symbol", "sym"])
    purpose_col = pick(df, ["purpose", "subject", "bm_desc", "description", "bmdesc"])
    date_col = pick(df, ["exdate", "bm_date", "date", "meetingdate", "eventdate"])
    record_col = pick(df, ["recdate", "recorddate", "bcstartdate"])

    missing = [
        name
        for name, col in (("symbol", symbol_col), ("purpose", purpose_col), ("event date", date_col))
        if col is None
    ]
    if missing:
        print(
            f"FATAL: {source_label} is missing required column(s) {missing}. "
            f"Columns found: {list(df.columns)}"
        )
        sys.exit(1)

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    dropped = 0
    skipped = 0

    for _, row in df.iterrows():
        symbol = str(row[symbol_col]).strip()
        if symbol not in valid_symbols:
            dropped += 1
            continue

        event_date = parse_date(row[date_col])
        if event_date is None:
            skipped += 1
            continue

        purpose = str(row[purpose_col]).strip()
        record_date = parse_date(row[record_col]) if record_col else None
        raw_json = json.dumps(row.to_dict(), default=str)

        rows.append(
            (
                symbol,
                event_type,
                pick_subtype(purpose),
                event_date,
                record_date,
                purpose,
                source_label,
                now,
                raw_json,
            )
        )

    return rows, dropped, skipped


def upsert_events(conn, rows):
    conn.executemany(
        """
        INSERT INTO events
            (symbol, event_type, subtype, event_date, record_date, purpose,
             source, fetched_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, event_type, event_date, purpose) DO UPDATE SET
            subtype=excluded.subtype,
            record_date=excluded.record_date,
            source=excluded.source,
            fetched_at=excluded.fetched_at,
            raw_json=excluded.raw_json
        """,
        rows,
    )


def main():
    conn = get_connection()
    try:
        today = date.today()
        to_date = today + timedelta(days=WINDOW_DAYS)
        from_str = today.strftime("%d-%m-%Y")
        to_str = to_date.strftime("%d-%m-%Y")
        print(f"Window: {from_str} to {to_str}")

        valid_symbols = refresh_instruments(conn)
        time.sleep(INTER_CALL_SLEEP)

        print("corporate_actions_for_equity: fetching")
        ca_df = call_with_retry(
            "corporate_actions_for_equity",
            capital_market.corporate_actions_for_equity,
            from_date=from_str,
            to_date=to_str,
            fno_only=True,
        )
        ca_rows, ca_dropped, ca_skipped = normalize_rows(
            ca_df, "corp_action", "nselib.corporate_actions", valid_symbols
        )
        upsert_events(conn, ca_rows)
        conn.commit()
        time.sleep(INTER_CALL_SLEEP)

        print("event_calendar_for_equity: fetching")
        ec_df = call_with_retry(
            "event_calendar_for_equity",
            capital_market.event_calendar_for_equity,
            from_date=from_str,
            to_date=to_str,
            fno_only=True,
        )
        ec_rows, ec_dropped, ec_skipped = normalize_rows(
            ec_df, "board_meeting", "nselib.event_calendar", valid_symbols
        )
        upsert_events(conn, ec_rows)
        conn.commit()

        now = datetime.now(timezone.utc).isoformat()
        set_meta(conn, "last_success_at", now)
        set_meta(conn, "last_error", "")
        conn.commit()

        print("\n--- Summary ---")
        print(f"instruments: {len(valid_symbols)}")
        print(f"corp_action rows upserted: {len(ca_rows)} (dropped non-F&O: {ca_dropped}, skipped bad dates: {ca_skipped})")
        print(f"board_meeting rows upserted: {len(ec_rows)} (dropped non-F&O: {ec_dropped}, skipped bad dates: {ec_skipped})")
        print(f"last_success_at: {now}")

    except SystemExit:
        raise
    except Exception as e:
        error_text = f"{type(e).__name__}: {e}"
        print(f"FAILED: {error_text}")
        set_meta(conn, "last_error", error_text)
        conn.commit()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
