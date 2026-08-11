"""
experiment_schema.py – Additive schema for the retrieval-methods experiment.

Everything here is ADDITIVE: no existing table, column, or row is modified.
The production app continues to use documents.embedding_json untouched.

New objects:
  document_embeddings          – versioned neural embeddings (per model,
                                 per representation type, per record type)
  experiment_transcription_fts – FTS5 index over PRIMARY-SOURCE transcriptions
                                 (the existing documents_fts indexes only
                                 AI-generated metadata + researcher annotation)
  experiment_runs              – one row per (query × retrieval condition)
  experiment_results           – ranked results for each run
  experiment_rag_runs          – generation details for MODE D runs

Retrieval units: a "unit" is either a standalone document (group_id IS NULL)
or a multi-page document group. Grouped pages are never retrieved
individually; their transcriptions are concatenated in page order.
"""

import hashlib
import logging

logger = logging.getLogger(__name__)

EXPERIMENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS document_embeddings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id         INTEGER NOT NULL,
    record_type         TEXT    NOT NULL CHECK(record_type IN ('document','group')),
    representation_type TEXT    NOT NULL CHECK(representation_type IN ('original','modelled')),
    provider            TEXT    NOT NULL,
    model_name          TEXT    NOT NULL,
    dim                 INTEGER NOT NULL,
    embedding_json      TEXT    NOT NULL,
    source_text_sha256  TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(document_id, record_type, representation_type, model_name)
);

CREATE INDEX IF NOT EXISTS idx_doc_emb_lookup
    ON document_embeddings(model_name, representation_type, record_type);

CREATE TABLE IF NOT EXISTS experiment_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uuid            TEXT    NOT NULL UNIQUE,
    query_id            TEXT,
    query               TEXT    NOT NULL,
    query_type          TEXT,
    query_notes         TEXT,
    retrieval_mode      TEXT    NOT NULL,
    embedding_provider  TEXT,
    embedding_model     TEXT,
    representation_type TEXT,
    top_k               INTEGER NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'ok',   -- 'ok' | 'failed'
    error               TEXT,
    meta_json           TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS experiment_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES experiment_runs(id) ON DELETE CASCADE,
    rank         INTEGER NOT NULL,
    doc_id       INTEGER NOT NULL,
    record_type  TEXT    NOT NULL,
    score        REAL,
    title        TEXT,
    source_archive TEXT
);

CREATE INDEX IF NOT EXISTS idx_exp_results_run ON experiment_results(run_id);

CREATE TABLE IF NOT EXISTS experiment_rag_runs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  INTEGER NOT NULL REFERENCES experiment_runs(id) ON DELETE CASCADE,
    context_ids_json        TEXT    NOT NULL,  -- [{"doc_id":..,"record_type":..}] actually sent to LLM
    prompt_template_version TEXT    NOT NULL,
    full_prompt             TEXT    NOT NULL,
    system_prompt           TEXT,
    llm_model               TEXT    NOT NULL,
    generation_params_json  TEXT,
    answer                  TEXT,
    citations_json          TEXT,              -- [{"doc_id":..,"record_type":..}] cited in answer
    created_at              TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

# Contentless-style FTS over primary-source transcriptions.
# Rebuilt from scratch by rebuild_transcription_fts(); safe to re-run.
TRANSCRIPTION_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS experiment_transcription_fts USING fts5(
    transcription,
    unit_id UNINDEXED,
    record_type UNINDEXED,
    tokenize='unicode61 remove_diacritics 0'
);
"""


def ensure_experiment_schema(conn) -> None:
    """Create all experiment tables/indexes if absent. Purely additive."""
    conn.executescript(EXPERIMENT_SCHEMA_SQL)
    conn.executescript(TRANSCRIPTION_FTS_SQL)


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ── Retrieval units ───────────────────────────────────────────────────────────

def get_retrieval_units(conn) -> list[dict]:
    """
    Return every retrieval unit with metadata and its ORIGINAL text
    (primary-source transcription).

    Units = standalone documents (group_id IS NULL, not trashed)
          + document groups (not trashed).

    Group original text = group.transcription if present, otherwise the
    page transcriptions concatenated in page order.

    Each dict: {unit_id, record_type, title, source_archive, original_text}
    original_text may be "" when no transcription exists — callers must
    handle (and report) that case explicitly, never silently.
    """
    units = []

    docs = conn.execute(
        """SELECT id, title, source_archive, transcription
           FROM documents
           WHERE group_id IS NULL AND is_trashed = 0
           ORDER BY id"""
    ).fetchall()
    for d in docs:
        units.append({
            "unit_id": d["id"],
            "record_type": "document",
            "title": d["title"],
            "source_archive": d["source_archive"],
            "original_text": (d["transcription"] or "").strip(),
        })

    groups = conn.execute(
        """SELECT id, title, source_archive, transcription
           FROM document_groups
           WHERE is_trashed = 0
           ORDER BY id"""
    ).fetchall()
    for g in groups:
        text = (g["transcription"] or "").strip()
        if not text:
            pages = conn.execute(
                """SELECT transcription FROM documents
                   WHERE group_id = ? AND is_trashed = 0
                   ORDER BY COALESCE(page_number, id)""",
                (g["id"],),
            ).fetchall()
            text = "\n\n".join(
                (p["transcription"] or "").strip()
                for p in pages if (p["transcription"] or "").strip()
            )
        units.append({
            "unit_id": g["id"],
            "record_type": "group",
            "title": g["title"],
            "source_archive": g["source_archive"],
            "original_text": text,
        })

    return units


def rebuild_transcription_fts(conn) -> dict:
    """
    (Re)build the transcription FTS index from current data.

    Returns {"indexed": n, "missing_transcription": [(record_type, unit_id), ...]}
    Units without any transcription are NOT indexed and are reported.
    """
    ensure_experiment_schema(conn)
    conn.execute("DELETE FROM experiment_transcription_fts")

    indexed, missing = 0, []
    for u in get_retrieval_units(conn):
        if u["original_text"]:
            conn.execute(
                "INSERT INTO experiment_transcription_fts(transcription, unit_id, record_type) VALUES (?,?,?)",
                (u["original_text"], u["unit_id"], u["record_type"]),
            )
            indexed += 1
        else:
            missing.append((u["record_type"], u["unit_id"]))

    logger.info("Transcription FTS rebuilt: %d units indexed, %d missing transcription",
                indexed, len(missing))
    return {"indexed": indexed, "missing_transcription": missing}


# ── Modelled representation ───────────────────────────────────────────────────

def build_modelled_text(conn, unit_id: int, record_type: str) -> str:
    """
    Deterministic AI-generated REPRESENTATION of a unit (MODE C).

    Includes: title, description, entities (name, role, context),
    transactions, tags — all machine-extracted at ingest time.

    Deliberately EXCLUDES:
      - the primary-source transcription (kept separate by design)
      - researcher annotations (would leak human interpretation into retrieval)

    Deterministic: fixed field order; entities/tags/transactions ordered by
    stable sort keys, so the representation is reproducible from the DB.
    """
    if record_type == "document":
        row = conn.execute("SELECT * FROM documents WHERE id=?", (unit_id,)).fetchone()
        ent_sql = """SELECT e.name, e.type, de.role, de.context
                     FROM document_entities de JOIN entities e ON e.id = de.entity_id
                     WHERE de.document_id=? ORDER BY e.name, de.role"""
        txn_sql = """SELECT seller, buyer, date, price, currency, auction_house,
                            lot_number, location, notes
                     FROM transactions WHERE document_id=? ORDER BY date, id"""
        tag_sql = """SELECT t.name FROM document_tags dt JOIN tags t ON t.id = dt.tag_id
                     WHERE dt.document_id=? ORDER BY t.name"""
    elif record_type == "group":
        row = conn.execute("SELECT * FROM document_groups WHERE id=?", (unit_id,)).fetchone()
        ent_sql = """SELECT e.name, e.type, ge.role, ge.context
                     FROM group_entities ge JOIN entities e ON e.id = ge.entity_id
                     WHERE ge.group_id=? ORDER BY e.name, ge.role"""
        txn_sql = """SELECT seller, buyer, date, price, currency, auction_house,
                            lot_number, location, notes
                     FROM group_transactions WHERE group_id=? ORDER BY date, id"""
        tag_sql = """SELECT t.name FROM group_tags gt JOIN tags t ON t.id = gt.tag_id
                     WHERE gt.group_id=? ORDER BY t.name"""
    else:
        raise ValueError(f"Unknown record_type: {record_type}")

    if row is None:
        raise ValueError(f"No {record_type} with id {unit_id}")

    parts = []
    if row["title"]:
        parts.append(f"Title: {row['title']}")
    if row["date_depicted"]:
        parts.append(f"Date: {row['date_depicted']}")
    if row["location"]:
        parts.append(f"Location: {row['location']}")
    if row["description"]:
        parts.append(f"Description: {row['description']}")

    ents = conn.execute(ent_sql, (unit_id,)).fetchall()
    for e in ents:
        line = f"Entity: {e['name']} ({e['type']})"
        if e["role"]:
            line += f", role: {e['role']}"
        if e["context"]:
            line += f", context: {e['context']}"
        parts.append(line)

    txns = conn.execute(txn_sql, (unit_id,)).fetchall()
    for t in txns:
        bits = ["Transaction:"]
        for field in ("seller", "buyer", "date", "price", "currency",
                      "auction_house", "lot_number", "location", "notes"):
            if t[field]:
                bits.append(f"{field}={t[field]}")
        parts.append(" ".join(bits))

    tags = [r["name"] for r in conn.execute(tag_sql, (unit_id,)).fetchall()]
    if tags:
        parts.append("Tags: " + ", ".join(tags))

    return "\n".join(parts)
