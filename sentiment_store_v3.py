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
        "ALTER TABLE earnings_call_extraction ADD COLUMN stage2_model TEXT",
        "ALTER TABLE earnings_sector_slots ADD COLUMN speaker_role TEXT",
        "ALTER TABLE earnings_call_extraction ADD COLUMN stage2_model_fallback "
        "INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE earnings_call_extraction ADD COLUMN pushback_total INTEGER",
        "ALTER TABLE earnings_call_extraction ADD COLUMN pushback_dodged INTEGER",
        "ALTER TABLE earnings_call_extraction ADD COLUMN pushback_example_quote TEXT",
        "ALTER TABLE earnings_call_extraction ADD COLUMN "
        "pushback_example_exchange_ref TEXT",
        "ALTER TABLE earnings_call_extraction ADD COLUMN pushback_summary TEXT",
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
    # A gap row (no transcript filed, or a PDF that could not be read) has no
    # document to hash, so the UNIQUE(symbol, quarter_end, pdf_sha256) key
    # cannot dedupe it - SQLite treats every NULL as distinct. Clear any prior
    # gap row for this quarter first so a re-run updates it instead of
    # stacking duplicates.
    if not prepared.get("pdf_sha256"):
        conn.execute(
            "DELETE FROM earnings_transcript_sources WHERE symbol=? AND "
            "quarter_end=? AND pdf_sha256 IS NULL", (symbol, quarter_end))
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
                     call_date=None, transcript_url=None, cite_failures=None,
                     stage2_model=None, stage2_model_fallback=0):
    pushback_example = parsed.get("pushback_example") or {}
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
             extractor_version, stage2_model, stage2_model_fallback,
             pushback_total, pushback_dodged, pushback_example_quote,
             pushback_example_exchange_ref, pushback_summary,
             announcement_at, announcement_at_precision,
             source_published_at, feature_available_at, timestamp_quality,
             extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
         provenance["extractor_version"], stage2_model,
         1 if stage2_model_fallback else 0,
         parsed.get("pushback_total"), parsed.get("pushback_dodged"),
         pushback_example.get("quote"), pushback_example.get("exchange_ref"),
         parsed.get("pushback_summary"),
         *_ts(timestamps)),
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

    # Risks (v3.1.0): list-shaped, stored as ONE frame row carrying the whole
    # list in value_json - same shape this column was always meant to hold
    # (see earnings_frame's own schema comment). No single quote represents
    # a list of risks, so quote/exchange_ref/char_start/char_end stay NULL
    # on this row; each item's own quote lives inside value_json instead.
    risks = parsed.get("risks") or []
    rows.append((
        symbol, quarter_end, "risks_disclosed",
        "GIVEN" if risks else "NOT_GIVEN",
        f"{len(risks)} risk{'s' if len(risks) != 1 else ''} disclosed" if risks else None,
        json.dumps(risks) if risks else None,
        None, None, None, None,
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
            field.get("speaker_role"),
            field.get("quote"), field.get("exchange_ref"),
            field.get("char_start"), field.get("char_end"),
            provenance["extractor_version"], provenance["source_pdf_sha256"],
            provenance["cleaned_text_sha256"], *_ts(timestamps),
        ))
    conn.executemany(
        """
        INSERT OR REPLACE INTO earnings_sector_slots
            (symbol, quarter_end, sector, slot_key, status, value_text,
             value_num, unit, period_basis, speaker_role, quote, exchange_ref,
             char_start, char_end, extractor_version, source_pdf_sha256,
             cleaned_text_sha256, announcement_at, announcement_at_precision,
             source_published_at, feature_available_at, timestamp_quality,
             extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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


def write_commitments(conn, symbol, quarter_end, parsed, provenance, timestamps):
    """List-shaped like write_guidance's soft-outlook half: delete this
    extractor_version's rows for the quarter, then insert fresh (not
    INSERT OR REPLACE, since item_no numbering can legitimately shrink
    between runs when an item fails cite verification)."""
    conn.execute(
        "DELETE FROM earnings_commitments WHERE symbol=? AND quarter_end=? "
        "AND extractor_version=?",
        (symbol, quarter_end, provenance["extractor_version"]),
    )
    rows = []
    for i, item in enumerate(parsed.get("commitments") or [], start=1):
        rows.append((
            symbol, quarter_end, i, item.get("metric"), item.get("value"),
            item.get("support"), item.get("status"), item.get("quote"),
            item.get("exchange_ref"), item.get("char_start"), item.get("char_end"),
            provenance["extractor_version"], provenance["source_pdf_sha256"],
            provenance["cleaned_text_sha256"], *_ts(timestamps),
        ))
    conn.executemany(
        """
        INSERT INTO earnings_commitments
            (symbol, quarter_end, item_no, metric, value, support, status,
             quote, exchange_ref, char_start, char_end, extractor_version,
             source_pdf_sha256, cleaned_text_sha256, announcement_at,
             announcement_at_precision, source_published_at,
             feature_available_at, timestamp_quality, extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)


def write_promise_checks(conn, symbol, quarter_end, prior_quarter_end, checks,
                         provenance, timestamps):
    """Each row tests one prior-quarter commitment against this quarter's
    call. List-shaped, same delete-then-insert pattern as write_commitments -
    an unverifiable check is dropped by promise_check.verify() before this
    is ever called, so item_no numbering reflects only what survived."""
    conn.execute(
        "DELETE FROM earnings_promise_checks WHERE symbol=? AND quarter_end=? "
        "AND extractor_version=?",
        (symbol, quarter_end, provenance["extractor_version"]),
    )
    rows = []
    for i, item in enumerate(checks, start=1):
        rows.append((
            symbol, quarter_end, prior_quarter_end, i, item.get("metric"),
            item.get("promised_value"), item.get("promised_support"),
            item.get("delivered_value"), item.get("quote"),
            item.get("exchange_ref"), item.get("char_start"), item.get("char_end"),
            item.get("verdict"), provenance["extractor_version"],
            provenance["source_pdf_sha256"], provenance["cleaned_text_sha256"],
            *_ts(timestamps),
        ))
    conn.executemany(
        """
        INSERT INTO earnings_promise_checks
            (symbol, quarter_end, prior_quarter_end, item_no, metric,
             promised_value, promised_support, delivered_value, quote,
             exchange_ref, char_start, char_end, verdict, extractor_version,
             source_pdf_sha256, cleaned_text_sha256, announcement_at,
             announcement_at_precision, source_published_at,
             feature_available_at, timestamp_quality, extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)


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


# ── Direct reads, for the job's own use ──────────────────────────────────────
# events_db.py's accessors go through @st.cache_data, which can return a
# stale result for a write this same process just made - a real risk mid-
# backfill, where the job writes quarter N then immediately needs to read
# quarter N-1 (or vice versa) for promised-vs-delivered. These read straight
# off the connection the job is already using, same transaction visibility
# as any other write in this module, latest extractor_version only (an
# older version's row for the same field must never leak into a facts input
# that's supposed to describe what THIS extraction found).

def read_guidance_formal(conn, symbol, quarter_end):
    rows = conn.execute(
        """
        SELECT field_key, status, value_text, value_num_low, value_num_high,
               unit, applies_to_period, prior_value_text
        FROM earnings_guidance_formal
        WHERE symbol=? AND quarter_end=?
          AND extractor_version = (
              SELECT MAX(extractor_version) FROM earnings_guidance_formal g2
              WHERE g2.symbol=earnings_guidance_formal.symbol
                AND g2.quarter_end=earnings_guidance_formal.quarter_end
                AND g2.field_key=earnings_guidance_formal.field_key
          )
        """,
        (symbol, quarter_end),
    ).fetchall()
    cols = ["field_key", "status", "value_text", "value_num_low", "value_num_high",
            "unit", "applies_to_period", "prior_value_text"]
    return {r[0]: dict(zip(cols, r)) for r in rows}


def read_sector_slots(conn, symbol, quarter_end):
    rows = conn.execute(
        """
        SELECT slot_key, status, value_text, value_num, unit, period_basis
        FROM earnings_sector_slots
        WHERE symbol=? AND quarter_end=?
          AND extractor_version = (
              SELECT MAX(extractor_version) FROM earnings_sector_slots s2
              WHERE s2.symbol=earnings_sector_slots.symbol
                AND s2.quarter_end=earnings_sector_slots.quarter_end
                AND s2.slot_key=earnings_sector_slots.slot_key
          )
        """,
        (symbol, quarter_end),
    ).fetchall()
    cols = ["slot_key", "status", "value_text", "value_num", "unit", "period_basis"]
    return {r[0]: dict(zip(cols, r)) for r in rows}


def read_risks(conn, symbol, quarter_end):
    row = conn.execute(
        "SELECT value_json FROM earnings_frame WHERE symbol=? AND quarter_end=? "
        "AND field_key='risks_disclosed' "
        "AND extractor_version = (SELECT MAX(extractor_version) FROM earnings_frame f2 "
        "WHERE f2.symbol=earnings_frame.symbol AND f2.quarter_end=earnings_frame.quarter_end "
        "AND f2.field_key='risks_disclosed')",
        (symbol, quarter_end),
    ).fetchone()
    return json.loads(row[0]) if row and row[0] else []


def read_commitments(conn, symbol, quarter_end):
    """This quarter's commitments, latest extractor_version only - the input
    promise_check.py needs for the NEXT quarter's testing run."""
    rows = conn.execute(
        """
        SELECT metric, value, support, status, quote, exchange_ref
        FROM earnings_commitments
        WHERE symbol=? AND quarter_end=?
          AND extractor_version = (
              SELECT MAX(extractor_version) FROM earnings_commitments c2
              WHERE c2.symbol=earnings_commitments.symbol
                AND c2.quarter_end=earnings_commitments.quarter_end
          )
        ORDER BY item_no
        """,
        (symbol, quarter_end),
    ).fetchall()
    cols = ["metric", "value", "support", "status", "quote", "exchange_ref"]
    return [dict(zip(cols, r)) for r in rows]


def read_due_quarter_label(conn, symbol, quarter_end):
    row = conn.execute(
        "SELECT fiscal_quarter FROM earnings_call_extraction "
        "WHERE symbol=? AND quarter_end=? ORDER BY extractor_version DESC LIMIT 1",
        (symbol, quarter_end),
    ).fetchone()
    return row[0] if row else quarter_end


def write_verdict(conn, symbol, quarter_end, verdict, input_facts,
                  extractor_version, generated_at):
    """The Guidance tab's top-card synthesis (spec 4a). `verdict` is
    {headline, supporting_line, chips: [{label, role}]}. input_facts is
    stored verbatim alongside it - the exact structured facts the model was
    given, so a reader can audit that the headline only restates what was
    already extracted rather than inventing something new."""
    conn.execute(
        """
        INSERT OR REPLACE INTO earnings_verdict
            (symbol, quarter_end, headline, supporting_line, chips,
             input_facts_json, extractor_version, generated_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (symbol, quarter_end, verdict["headline"], verdict.get("supporting_line"),
         json.dumps(verdict.get("chips") or []), json.dumps(input_facts),
         extractor_version, generated_at),
    )


def write_all(conn, symbol, quarter_end, parsed, exchanges, prepared,
              provenance, timestamps, sector_info, price_move=None,
              fiscal_quarter=None, call_date=None, cite_failures=None,
              stage2_model=None, stage2_model_fallback=0):
    """Everything for one (symbol, quarter), in one transaction."""
    with conn:
        write_source(conn, symbol, quarter_end, prepared, timestamps,
                     fiscal_quarter=fiscal_quarter)
        write_exchanges(conn, symbol, quarter_end, exchanges, provenance, timestamps)
        write_extraction(conn, symbol, quarter_end, parsed, provenance, timestamps,
                         sector_info, price_move=price_move,
                         fiscal_quarter=fiscal_quarter, call_date=call_date,
                         transcript_url=prepared.get("url"),
                         cite_failures=cite_failures,
                         stage2_model=stage2_model,
                         stage2_model_fallback=stage2_model_fallback)
        write_frame(conn, symbol, quarter_end, parsed, provenance, timestamps)
        write_guidance(conn, symbol, quarter_end, parsed, provenance, timestamps)
        write_commitments(conn, symbol, quarter_end, parsed, provenance, timestamps)
        if sector_info.get("sector_card"):
            write_sector_slots(conn, symbol, quarter_end, parsed, provenance,
                               timestamps, sector_info["sector_card"])
