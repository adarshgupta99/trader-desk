import hashlib
import sqlite3
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser

DB_PATH = "market_cache.db"

RSS_FEEDS = {
    "CNBC":          "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "BBC Business":  "http://feeds.bbci.co.uk/news/business/rss.xml",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "Mint":          "https://www.livemint.com/rss/markets",
    "Moneycontrol":  "https://www.moneycontrol.com/rss/marketreports.xml",
    "FT":            "https://www.ft.com/rss/home",
    "Seeking Alpha": "https://seekingalpha.com/feed.xml",
}

REFRESH_INTERVAL_MINUTES = 15


def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS news (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            source      TEXT NOT NULL,
            url         TEXT,
            published_at TEXT,
            fetched_at  TEXT NOT NULL
        )
        """
    )
    return conn


def _article_id(url, title):
    key = (url or title or "").strip()
    return hashlib.sha1(key.encode()).hexdigest()


def _parse_published(entry):
    for field in ("published", "updated"):
        raw = entry.get(field)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
            except Exception:
                pass
    return datetime.now(timezone.utc).isoformat()


def fetch_and_store():
    """Fetch all RSS feeds and store new articles. Returns count of new articles added."""
    conn = _get_connection()
    added = 0
    now = datetime.now(timezone.utc).isoformat()
    try:
        for source, url in RSS_FEEDS.items():
            try:
                feed = feedparser.parse(url)
            except Exception:
                continue
            for entry in feed.entries:
                link  = entry.get("link", "")
                title = entry.get("title", "").strip()
                if not title:
                    continue
                art_id = _article_id(link, title)
                exists = conn.execute(
                    "SELECT 1 FROM news WHERE id = ?", (art_id,)
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT INTO news (id, title, source, url, published_at, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (art_id, title, source, link, _parse_published(entry), now),
                )
                added += 1
        conn.commit()
    finally:
        conn.close()
    return added


def get_news(limit=100):
    """Return latest news articles, newest first."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT title, source, url, published_at
            FROM news
            ORDER BY published_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {"title": r[0], "source": r[1], "url": r[2], "published_at": r[3]}
            for r in rows
        ]
    finally:
        conn.close()


def count_news():
    conn = _get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    finally:
        conn.close()
