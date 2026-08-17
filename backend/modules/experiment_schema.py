"""
experiment_schema.py – Run-log schema for the retrieval-methods experiment.

Everything here is ADDITIVE: no existing table, column, or row is modified.

As of experiment configuration `chunked_retrieval_v1`, the experiment runs
on the PRODUCTION chunk-level retrieval infrastructure
(retrieval_chunks / retrieval_chunks_fts / retrieval_embeddings, built by
scripts/reindex.py). The earlier experiment-only retrieval objects are no
longer used but are intentionally NOT dropped, so historical experiment
data stays reproducible:

  document_embeddings          – legacy whole-unit vectors (unused now)
  experiment_transcription_fts – legacy transcription FTS (unused now)

Active objects (this module):
  experiment_runs       – one row per (query × retrieval condition), with
                          embedding provider/model, index schema version,
                          and experiment configuration version
  experiment_results    – ranked results per run, incl. the discovery
                          chunk id, representation type, and excerpt
  experiment_rag_runs   – MODE D generation details, incl. the hydrated
                          PRIMARY-SOURCE EVIDENCE passages (evidence_json)

Retrieval units: a "unit" is either a standalone document (group_id IS
NULL) or a multi-page document group. Grouped pages are never retrieved
individually. This is enforced upstream by the production indexer.
"""

import hashlib
import logging
import sqlite3

logger = logging.getLogger(__name__)

EXPERIMENT_SCHEMA_SQL = """
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

# Additive columns introduced with chunked_retrieval_v1. Applied with the
# same ignore-duplicate-column pattern as modules/db.py so older databases
# (and their historical rows) migrate in place, non-destructively.
_CHUNKED_V1_MIGRATIONS = [
    "ALTER TABLE experiment_runs    ADD COLUMN index_schema_version INTEGER",
    "ALTER TABLE experiment_runs    ADD COLUMN config_version TEXT",
    "ALTER TABLE experiment_results ADD COLUMN chunk_id INTEGER",
    "ALTER TABLE experiment_results ADD COLUMN representation_type TEXT",
    "ALTER TABLE experiment_results ADD COLUMN excerpt TEXT",
    "ALTER TABLE experiment_rag_runs ADD COLUMN evidence_json TEXT",
]


def ensure_experiment_schema(conn) -> None:
    """Create/upgrade the experiment run-log tables. Purely additive."""
    conn.executescript(EXPERIMENT_SCHEMA_SQL)
    for sql in _CHUNKED_V1_MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
