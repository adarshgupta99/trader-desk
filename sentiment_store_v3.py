"""Writes a v3 extraction into events.db (schema: db/schema_sentiment_v3.sql).

One module so the eight tables are always written together, with the same
timestamps and the same provenance, in one transaction. A half-written
extraction - a sheet row with no frame block, slots with no source row - is
worse than none, because nothing downstream can tell it apart from a genuine
NOT_GIVEN.

IDEMPOTENCY (spec 7.4): the key is
(symbol, quarter_end, source_pdf_sha256, extractor_version). A re-run over an
unchanged document under an unchanged extractor writes nothing and spends no
tokens; a new extractor_version re-extracts; a document NSE has replaced has a
different hash and is therefore a different row, not an overwrite.

TIMESTAMPS (spec section 5) are passed in, never derived here, and written to
every row of every table. `feature_available_at` for a transcript-derived
feature is the transcript's own publish time, NOT the results announcement -
the whole point of storing all three is that a later analysis can tell which
features existed at the moment of the announcement move and which did not.
"""

import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.db")
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "db", "schema_sentiment_v3.sql")


def get_connection(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    # Migrations for a db created before a column existed - CREATE TABLE IF
    # NOT EXISTS only covers a brand-new file. Each ALTER is independently
    # try/except'd, the same pattern jobs/snapshot_estimates.py uses, so a db
    # that already has some of them does not abort on the first duplicate.
    for ddl in (
        "ALTER TABLE earnings_call_extraction ADD COLUMN cite_failures "
        "INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE earnings_call_extraction ADD COLUMN cite_failure_detail TEXT",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    return conn


def already_extracted(conn, symbol, quarter_end, pdf_sha256, extractor_version):
    row = conn.execute(
        "SELECT 1 FROM earnings_call_extraction "
        "WHERE symbol=? AND quarter_end=? AND source_pdf_sha256=? "
        "AND extractor_version=? AND row_status='OK'",
        (symbol, quarter_end, pdf_sha256, extractor_version),
    ).fetchone()
    return row is not None


def _ts(timestamps):
    """The five timestamp columns every feature row carries, in the order the
    INSERTs below use them."""
    return (
        timestamps.get("announcement_at"),
        timestamps.get("announcement_at_precision"),
        timestamps.get("source_published_at"),
        timestamps.get("feature_available_at"),
        timestamps.get("timestamp_quality", "OK"),
        timestamps["extracted_at"],
    )


def write_source(conn, symbol, quarter_end, prepared, timestamps,
                 fiscal_quarter=None, transcript_available=1, pdf_unreadable=0):
    conn.execute(
        """
        INSERT OR REPLACE INTO earnings_transcript_sources
            (symbol, quarter_end, fiscal_quarter, pdf_url, pdf_sha256,
             cleaned_text_sha256, pdf_archive_path, cleaned_text_path,
             pdf_bytes, http_status, fetched_at, transcript_available,
             pdf_unreadable, announcement_at, announcement_at_precision,
             source_published_at, feature_available_at, timestamp_quality)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (symbol, quarter_end, fiscal_quarter, prepared.get("url"),
         prepared.get("pdf_sha256"), prepared.get("cleaned_text_sha256"),
         prepared.get("pdf_archive_path"), prepared.get("cleaned_text_path"),
         prepared.get("pdf_bytes"), prepared.get("http_status"),
         prepared.get("fetched_at"), transcript_available, pdf_unreadable,
         timestamps.get("announcement_at"),
         timestamps.get("announcement_at_precision"),
         timestamps.get("source_published_at"),
         timestamps.get("feature_available_at"),
         timestamps.get("timestamp_quality", "OK")),
    )


def write_exchanges(conn, symbol, quarter_end, exchanges, provenance, timestamps):
    conn.execute(
        "DELETE FROM earnings_call_exchanges WHERE symbol=? AND quarter_end=? "
        "AND extractor_version=?",
        (symbol, quarter_end, provenance["extractor_version"]),
    )
    conn.executemany(
        """
        INSERT INTO earnings_call_exchanges
            (symbol, quarter_end, exchange_ref, kind, seq, analyst_name,
             analyst_firm, management_speakers, char_start, char_end,
             extractor_version, source_pdf_sha256, cleaned_text_sha256,
             extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [(symbol, quarter_end, e["exchange_ref"], e["kind"], e["seq"],
          e.get("analyst_name"), e.get("analyst_firm"),
          json.dumps(e.get("management_speakers") or []),
          e["char_start"], e["char_end"],
          provenance["extractor_version"], provenance["source_pdf_sha256"],
          provenance["cleaned_text_sha256"], timestamps["extracted_at"])
         for e in exchanges],
    )


def _guidance_cell(parsed):
    """The sheet's GUIDANCE cell: 'FORMAL: <...> | SOFT: <...>' (spec 4.4).

    Formal and soft stay visibly separate inside the one cell, which is the
    whole reason the two are modelled apart in the first place.
    """
    formal = parsed.get("guidance_formal") or {}
    given = [(k.replace("guidance_", ""), v) for k, v in formal.items()
             if v.get("status") in ("GIVEN", "WITHDRAWN_OR_CHANGED")]
    if given:
        parts = []
        for name, field in given:
            value = field.get("value_text") or field.get("status")
            period = f" ({field['applies_to_period']})" if field.get("applies_to_period") else ""
            parts.append(f"{name} {value}{period}")
        formal_text = "; ".join(parts)
    else:
        formal_text = "NOT GIVEN"

    soft = parsed.get("guidance_soft") or []
    if soft:
        topics = "; ".join(f"{s.get('direction')} on {s.get('topic')}" for s in soft[:3])
        soft_text = topics + ("; ..." if len(soft) > 3 else "")
    else:
        soft_text = "none"
    return f"FORMAL: {formal_text} | SOFT: {soft_text}"


def write_extraction(conn, symbol, quarter_end, parsed, provenance, timestamps,
                     sector_info, price_move=None, fiscal_quarter=None,
                     call_date=None, transcript_url=None, cite_failures=None):
    def tone(key):
        field = parsed.get(key) or {}
        return field.get("score"), json.dumps(field.get("cites") or [])

    mgmt, mgmt_c = tone("mgmt_confidence")
    overall, overall_c = tone("overall_tone")
    qa, qa_c = tone("qa_tone")
    analyst, analyst_c = tone("analyst_tone")
    divergence = parsed.get("qa_divergence") or {}
    price_move = price_move or {}

    conn.execute(
        """
        INSERT OR REPLACE INTO earnings_call_extraction
            (symbol, quarter_end, fiscal_quarter, call_date,
             price_move_pct, price_move_from_date, price_move_to_date,
             mgmt_confidence, mgmt_confidence_cites,
             overall_tone, overall_tone_cites,
             qa_tone, qa_tone_cites,
             analyst_tone, analyst_tone_cites,
             guidance_cell, evasive_count, evasive_exchanges,
             qa_divergence, qa_divergence_note, qa_divergence_cites,
             transcript_url, source_pdf_sha256, cleaned_text_sha256,
             row_status, failure_reason, cite_failures, cite_failure_detail,
             sector, sector_card, sector_unmapped,
             extractor_version, announcement_at, announcement_at_precision,
             source_published_at, feature_available_at, timestamp_quality,
             extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (symbol, quarter_end, fiscal_quarter, call_date,
         price_move.get("pct"), price_move.get("from_date"), price_move.get("to_date"),
         mgmt, mgmt_c, overall, overall_c, qa, qa_c, analyst, analyst_c,
         _guidance_cell(parsed),
         len(parsed.get("evasive_exchanges") or []),
         json.dumps(parsed.get("evasive_exchanges") or []),
         divergence.get("grade"), divergence.get("note"),
         json.dumps(divergence.get("cites") or []),
         transcript_url, provenance["source_pdf_sha256"],
         provenance["cleaned_text_sha256"],
         "OK", None,
         len(cite_failures or []),
         "; ".join(cite_failures[:5]) if cite_failures else None,
         sector_info.get("sector"), sector_info.get("sector_card"),
         1 if sector_info.get("sector_card") is None else 0,
         provenance["extractor_version"], *_ts(timestamps)),
    )


def write_frame(conn, symbol, quarter_end, parsed, provenance, timestamps):
    frame = parsed.get("frame") or {}
    rows = []
    for key, field in frame.items():
        rows.append((
            symbol, quarter_end, key, field.get("status", "NOT_GIVEN"),
            field.get("value_text"),
            json.dumps(field.get("value_json")) if field.get("value_json") else None,
            field.get("quote"), field.get("exchange_ref"),
            field.get("char_start"), field.get("char_end"),
            provenance["extractor_version"], provenance["source_pdf_sha256"],
            provenance["cleaned_text_sha256"], *_ts(timestamps),
        ))
    conn.executemany(
        """
        INSERT OR REPLACE INTO earnings_frame
            (symbol, quarter_end, field_key, status, value_text, value_json,
             quote, exchange_ref, char_start, char_end, extractor_version,
             source_pdf_sha256, cleaned_text_sha256, announcement_at,
             announcement_at_precision, source_published_at,
             feature_available_at, timestamp_quality, extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)


def write_sector_slots(conn, symbol, quarter_end, parsed, provenance, timestamps,
                       sector_card):
    slots = parsed.get("sector_slots") or {}
    rows = []
    for key, field in slots.items():
        rows.append((
            symbol, quarter_end, sector_card, key,
            field.get("status", "NOT_GIVEN"), field.get("value_text"),
            field.get("value_num"), field.get("unit"), field.get("period_basis"),
            field.get("quote"), field.get("exchange_ref"),
            field.get("char_start"), field.get("char_end"),
            provenance["extractor_version"], provenance["source_pdf_sha256"],
            provenance["cleaned_text_sha256"], *_ts(timestamps),
        ))
    conn.executemany(
        """
        INSERT OR REPLACE INTO earnings_sector_slots
            (symbol, quarter_end, sector, slot_key, status, value_text,
             value_num, unit, period_basis, quote, exchange_ref, char_start,
             char_end, extractor_version, source_pdf_sha256,
             cleaned_text_sha256, announcement_at, announcement_at_precision,
             source_published_at, feature_available_at, timestamp_quality,
             extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)


def write_guidance(conn, symbol, quarter_end, parsed, provenance, timestamps):
    formal = parsed.get("guidance_formal") or {}
    rows = []
    for key, field in formal.items():
        rows.append((
            symbol, quarter_end, key, field.get("status", "NOT_GIVEN"),
            field.get("value_text"), field.get("value_num_low"),
            field.get("value_num_high"), field.get("unit"),
            field.get("applies_to_period"), field.get("prior_value_text"),
            field.get("quote"), field.get("exchange_ref"),
            field.get("char_start"), field.get("char_end"),
            provenance["extractor_version"], provenance["source_pdf_sha256"],
            provenance["cleaned_text_sha256"], *_ts(timestamps),
        ))
    conn.executemany(
        """
        INSERT OR REPLACE INTO earnings_guidance_formal
            (symbol, quarter_end, field_key, status, value_text, value_num_low,
             value_num_high, unit, applies_to_period, prior_value_text, quote,
             exchange_ref, char_start, char_end, extractor_version,
             source_pdf_sha256, cleaned_text_sha256, announcement_at,
             announcement_at_precision, source_published_at,
             feature_available_at, timestamp_quality, extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)

    conn.execute(
        "DELETE FROM earnings_guidance_soft WHERE symbol=? AND quarter_end=? "
        "AND extractor_version=?",
        (symbol, quarter_end, provenance["extractor_version"]),
    )
    soft_rows = []
    for i, item in enumerate(parsed.get("guidance_soft") or [], start=1):
        soft_rows.append((
            symbol, quarter_end, i, item.get("topic"), item.get("direction"),
            item.get("quote"), item.get("exchange_ref"),
            item.get("char_start"), item.get("char_end"),
            provenance["extractor_version"], provenance["source_pdf_sha256"],
            provenance["cleaned_text_sha256"], *_ts(timestamps),
        ))
    conn.executemany(
        """
        INSERT INTO earnings_guidance_soft
            (symbol, quarter_end, item_no, topic, direction, quote,
             exchange_ref, char_start, char_end, extractor_version,
             source_pdf_sha256, cleaned_text_sha256, announcement_at,
             announcement_at_precision, source_published_at,
             feature_available_at, timestamp_quality, extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, soft_rows)


def write_failed_row(conn, symbol, quarter_end, provenance, timestamps, reason,
                     sector_info=None, row_status="EXTRACTION_FAILED"):
    """A row that failed validation or cite verification is recorded as such,
    never silently skipped and never stored half-valid (spec 2.2)."""
    sector_info = sector_info or {}
    conn.execute(
        """
        INSERT OR REPLACE INTO earnings_call_extraction
            (symbol, quarter_end, source_pdf_sha256, cleaned_text_sha256,
             row_status, failure_reason, sector, sector_card, sector_unmapped,
             extractor_version, announcement_at, announcement_at_precision,
             source_published_at, feature_available_at, timestamp_quality,
             extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (symbol, quarter_end, provenance.get("source_pdf_sha256"),
         provenance.get("cleaned_text_sha256"), row_status, reason,
         sector_info.get("sector"), sector_info.get("sector_card"),
         1 if sector_info.get("sector_card") is None else 0,
         provenance["extractor_version"], *_ts(timestamps)),
    )


def write_all(conn, symbol, quarter_end, parsed, exchanges, prepared,
              provenance, timestamps, sector_info, price_move=None,
              fiscal_quarter=None, call_date=None, cite_failures=None):
    """Everything for one (symbol, quarter), in one transaction."""
    with conn:
        write_source(conn, symbol, quarter_end, prepared, timestamps,
                     fiscal_quarter=fiscal_quarter)
        write_exchanges(conn, symbol, quarter_end, exchanges, provenance, timestamps)
        write_extraction(conn, symbol, quarter_end, parsed, provenance, timestamps,
                         sector_info, price_move=price_move,
                         fiscal_quarter=fiscal_quarter, call_date=call_date,
                         transcript_url=prepared.get("url"),
                         cite_failures=cite_failures)
        write_frame(conn, symbol, quarter_end, parsed, provenance, timestamps)
        write_guidance(conn, symbol, quarter_end, parsed, provenance, timestamps)
        if sector_info.get("sector_card"):
            write_sector_slots(conn, symbol, quarter_end, parsed, provenance,
                               timestamps, sector_info["sector_card"])
