"""
db.py – Database layer for Provenance Archive Wiki.

Creates and manages the SQLite database with:
  • Core tables: documents, entities, document_entities, transactions,
    document_links, tags, document_tags
  • FTS5 virtual table (documents_fts) with auto-sync triggers
  • Helper functions for connection management

All financial amounts are stored as REAL (SQLite's native DECIMAL).
Chinese and other non-ASCII text is preserved exactly.
"""

import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path

# Import lazily to avoid circular imports
def _get_db_path():
    from config import DB_PATH
    return DB_PATH


# ── Schema SQL ────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── Documents ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    filename         TEXT    NOT NULL UNIQUE,
    sha256           TEXT    NOT NULL UNIQUE,
    title            TEXT,
    date_depicted    TEXT,
    date_range_start TEXT,
    date_range_end   TEXT,
    location         TEXT,
    medium           TEXT,
    dimensions       TEXT,
    description      TEXT,
    language         TEXT,
    raw_claude_response TEXT,
    transcription    TEXT,
    annotation       TEXT,
    is_key_evidence  INTEGER NOT NULL DEFAULT 0,
    is_trashed       INTEGER NOT NULL DEFAULT 0,
    embedding_json   TEXT,           -- JSON array of floats (semantic vector)
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Entities ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    normalized_name TEXT    NOT NULL,
    type            TEXT    NOT NULL CHECK(type IN ('person','object','institution','place','unknown')),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(normalized_name, type)
);

-- ── Entity aliases (alternate names / "also known as") ──────────────────────
CREATE TABLE IF NOT EXISTS entity_aliases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id       INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    name            TEXT    NOT NULL,
    normalized_name TEXT    NOT NULL,
    source          TEXT    NOT NULL DEFAULT 'merge',   -- 'merge' | 'manual'
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(entity_id, normalized_name)
);

-- ── Document ↔ Entity pivot ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS document_entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    entity_id   INTEGER NOT NULL REFERENCES entities(id)  ON DELETE CASCADE,
    role        TEXT,
    context     TEXT,
    UNIQUE(document_id, entity_id, role)
);

-- ── Transactions ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seller        TEXT,
    buyer         TEXT,
    date          TEXT,
    price         REAL,
    currency      TEXT,
    auction_house TEXT,
    lot_number    TEXT,
    location      TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Document ↔ Document links ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS document_links (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id        INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    target_id        INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    relationship_type TEXT,
    notes            TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_id, target_id, relationship_type)
);

-- ── Tags ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    color      TEXT    NOT NULL DEFAULT '#c9a84c',
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Document ↔ Tag pivot ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS document_tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id)      ON DELETE CASCADE,
    UNIQUE(document_id, tag_id)
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_documents_sha256          ON documents(sha256);
CREATE INDEX IF NOT EXISTS idx_documents_date_depicted   ON documents(date_depicted);
CREATE INDEX IF NOT EXISTS idx_documents_key_evidence    ON documents(is_key_evidence);
CREATE INDEX IF NOT EXISTS idx_entities_normalized       ON entities(normalized_name, type);
CREATE INDEX IF NOT EXISTS idx_entity_aliases_entity     ON entity_aliases(entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_aliases_norm       ON entity_aliases(normalized_name);
CREATE INDEX IF NOT EXISTS idx_doc_entities_doc          ON document_entities(document_id);
CREATE INDEX IF NOT EXISTS idx_doc_entities_entity       ON document_entities(entity_id);
CREATE INDEX IF NOT EXISTS idx_transactions_doc          ON transactions(document_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date         ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_doc_links_source          ON document_links(source_id);
CREATE INDEX IF NOT EXISTS idx_doc_links_target          ON document_links(target_id);
CREATE INDEX IF NOT EXISTS idx_doc_tags_doc              ON document_tags(document_id);
CREATE INDEX IF NOT EXISTS idx_doc_tags_tag              ON document_tags(tag_id);

-- ── FTS5 virtual table ────────────────────────────────────────────────────────
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title,
    description,
    annotation,
    raw_claude_response,
    content='documents',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 0'
);

-- Auto-sync triggers: keep FTS index up to date when documents change
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, title, description, annotation, raw_claude_response)
    VALUES (new.id, new.title, new.description, new.annotation, new.raw_claude_response);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, description, annotation, raw_claude_response)
    VALUES ('delete', old.id, old.title, old.description, old.annotation, old.raw_claude_response);
END;

CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, description, annotation, raw_claude_response)
    VALUES ('delete', old.id, old.title, old.description, old.annotation, old.raw_claude_response);
    INSERT INTO documents_fts(rowid, title, description, annotation, raw_claude_response)
    VALUES (new.id, new.title, new.description, new.annotation, new.raw_claude_response);
END;

-- Auto-update updated_at on documents
CREATE TRIGGER IF NOT EXISTS documents_update_ts AFTER UPDATE ON documents
WHEN old.updated_at = new.updated_at BEGIN
    UPDATE documents SET updated_at = datetime('now') WHERE id = new.id;
END;

-- ── Document Groups (multi-page documents) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS document_groups (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT,
    date_depicted    TEXT,
    date_range_start TEXT,
    date_range_end   TEXT,
    location         TEXT,
    medium           TEXT,
    dimensions       TEXT,
    description      TEXT,
    language         TEXT,
    transcription    TEXT,
    raw_claude_response TEXT,
    annotation       TEXT,
    is_key_evidence  INTEGER NOT NULL DEFAULT 0,
    is_trashed       INTEGER NOT NULL DEFAULT 0,
    embedding_json   TEXT,
    source_archive   TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS group_entities (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id  INTEGER NOT NULL REFERENCES document_groups(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id)        ON DELETE CASCADE,
    role      TEXT,
    context   TEXT,
    UNIQUE(group_id, entity_id, role)
);

CREATE TABLE IF NOT EXISTS group_transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id      INTEGER NOT NULL REFERENCES document_groups(id) ON DELETE CASCADE,
    seller        TEXT,
    buyer         TEXT,
    date          TEXT,
    price         REAL,
    currency      TEXT,
    auction_house TEXT,
    lot_number    TEXT,
    location      TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS group_tags (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES document_groups(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)            ON DELETE CASCADE,
    UNIQUE(group_id, tag_id)
);

-- ── Entity relationships (LLM-inferred verb edges between people) ────────────
CREATE TABLE IF NOT EXISTS entity_relationships (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    verb              TEXT    NOT NULL,
    confidence        REAL    NOT NULL DEFAULT 0,
    evidence_doc_ids  TEXT    NOT NULL DEFAULT '[]',  -- JSON array of document ids
    generated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_entity_id, target_entity_id, verb)
);

CREATE INDEX IF NOT EXISTS idx_ent_rel_source ON entity_relationships(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_ent_rel_target ON entity_relationships(target_entity_id);

-- ── Relationship refresh runs (status of batch backfill jobs) ────────────────
CREATE TABLE IF NOT EXISTS relationship_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at  TEXT,
    total        INTEGER NOT NULL DEFAULT 0,
    processed    INTEGER NOT NULL DEFAULT 0,
    created      INTEGER NOT NULL DEFAULT 0,
    errors       INTEGER NOT NULL DEFAULT 0,
    status       TEXT    NOT NULL DEFAULT 'running',  -- 'running' | 'done' | 'crashed'
    error_message TEXT
);

-- ── Ingest runs (log of each ingestion batch) ────────────────────────────────
CREATE TABLE IF NOT EXISTS ingest_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at    TEXT,
    source_archive TEXT,
    total          INTEGER NOT NULL DEFAULT 0,
    processed      INTEGER NOT NULL DEFAULT 0,
    skipped        INTEGER NOT NULL DEFAULT 0,
    errors         INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'running'  -- 'running' | 'done' | 'crashed'
);

CREATE TABLE IF NOT EXISTS ingest_run_files (
    run_id        INTEGER NOT NULL REFERENCES ingest_runs(id) ON DELETE CASCADE,
    filename      TEXT    NOT NULL,
    sha256        TEXT    NOT NULL,
    status        TEXT    NOT NULL,   -- 'ok' | 'err' | 'skipped' | 'requeued'
    error_message TEXT,
    document_id   INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    PRIMARY KEY (run_id, sha256)
);

CREATE INDEX IF NOT EXISTS idx_ingest_runs_started   ON ingest_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_ingest_run_files_run  ON ingest_run_files(run_id, status);

-- ── Audit events (history of edits to documents, groups, entities, etc.) ──────
CREATE TABLE IF NOT EXISTS audit_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL DEFAULT (datetime('now')),
    actor        TEXT    NOT NULL DEFAULT 'user',  -- 'user' | 'ingest' | 'system'
    entity_type  TEXT    NOT NULL,                 -- 'document' | 'group' | 'entity' | ...
    entity_id    INTEGER NOT NULL,
    action       TEXT    NOT NULL,                 -- 'create' | 'update' | 'delete' | 'link' | 'unlink' | 'add_entity' | 'remove_entity' | 'trash' | 'restore'
    field        TEXT,                             -- for 'update' rows: the changed column
    old_value    TEXT,
    new_value    TEXT,
    run_id       INTEGER REFERENCES ingest_runs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_type, entity_id, ts);
CREATE INDEX IF NOT EXISTS idx_audit_ts     ON audit_events(ts);
CREATE INDEX IF NOT EXISTS idx_audit_run    ON audit_events(run_id);

CREATE INDEX IF NOT EXISTS idx_groups_date       ON document_groups(date_depicted);
CREATE INDEX IF NOT EXISTS idx_groups_key        ON document_groups(is_key_evidence);
CREATE INDEX IF NOT EXISTS idx_group_entities_g  ON group_entities(group_id);
CREATE INDEX IF NOT EXISTS idx_group_txn_g       ON group_transactions(group_id);
CREATE INDEX IF NOT EXISTS idx_group_tags_g      ON group_tags(group_id);

-- FTS for groups
CREATE VIRTUAL TABLE IF NOT EXISTS groups_fts USING fts5(
    title,
    description,
    annotation,
    raw_claude_response,
    content='document_groups',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 0'
);

CREATE TRIGGER IF NOT EXISTS groups_ai AFTER INSERT ON document_groups BEGIN
    INSERT INTO groups_fts(rowid, title, description, annotation, raw_claude_response)
    VALUES (new.id, new.title, new.description, new.annotation, new.raw_claude_response);
END;

CREATE TRIGGER IF NOT EXISTS groups_ad AFTER DELETE ON document_groups BEGIN
    INSERT INTO groups_fts(groups_fts, rowid, title, description, annotation, raw_claude_response)
    VALUES ('delete', old.id, old.title, old.description, old.annotation, old.raw_claude_response);
END;

CREATE TRIGGER IF NOT EXISTS groups_au AFTER UPDATE ON document_groups BEGIN
    INSERT INTO groups_fts(groups_fts, rowid, title, description, annotation, raw_claude_response)
    VALUES ('delete', old.id, old.title, old.description, old.annotation, old.raw_claude_response);
    INSERT INTO groups_fts(rowid, title, description, annotation, raw_claude_response)
    VALUES (new.id, new.title, new.description, new.annotation, new.raw_claude_response);
END;

CREATE TRIGGER IF NOT EXISTS groups_update_ts AFTER UPDATE ON document_groups
WHEN old.updated_at = new.updated_at BEGIN
    UPDATE document_groups SET updated_at = datetime('now') WHERE id = new.id;
END;
"""


# ── Connection management ─────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Open a SQLite connection with row_factory for dict-like access."""
    db_path = _get_db_path()
    # Wait up to 5 seconds for the writer lock instead of erroring immediately —
    # required when ingestion workers and request handlers write concurrently.
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def get_db():
    """Context manager: yields a connection and commits/rolls back automatically."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Initialization ────────────────────────────────────────────────────────────

def init_db():
    """Create all tables, indexes, FTS table, and triggers if they don't exist."""
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Rebuild the entities table if its CHECK constraint predates the 'place'
    # entity type. Runs first, on its own connection, because it must disable
    # foreign keys — a PRAGMA that is silently ignored inside an open
    # transaction. No-op on a fresh database (no entities table yet).
    _migrate_entity_type_check(db_path)

    with get_db() as conn:
        conn.executescript(SCHEMA_SQL)
        # Migrations: add columns that may not exist in older databases
        _migrate(conn, "ALTER TABLE documents ADD COLUMN is_trashed INTEGER NOT NULL DEFAULT 0")
        _migrate(conn, "ALTER TABLE documents ADD COLUMN source_archive TEXT")
        _migrate(conn, "ALTER TABLE documents ADD COLUMN transcription TEXT")
        _migrate(conn, "ALTER TABLE documents ADD COLUMN group_id INTEGER REFERENCES document_groups(id) ON DELETE SET NULL")
        _migrate(conn, "ALTER TABLE documents ADD COLUMN page_number INTEGER")
        _migrate(conn, "CREATE INDEX IF NOT EXISTS idx_documents_group ON documents(group_id)")
        _migrate(conn, "ALTER TABLE documents ADD COLUMN medium_category TEXT")
        _migrate(conn, "ALTER TABLE document_groups ADD COLUMN medium_category TEXT")
        _backfill_medium_category(conn)

        # Drop legacy per-file "skipped" rows — we no longer record these
        # because the count is kept on ingest_runs.skipped and the per-file
        # rows bloat the table without adding value. Safe to re-run.
        _migrate(conn, "DELETE FROM ingest_run_files WHERE status='skipped'")

        # Sweep any pre-existing orphan entities and tags left behind by
        # deletions that occurred before the cleanup logic was added.
        conn.execute("""
            DELETE FROM entities
            WHERE id NOT IN (SELECT entity_id FROM document_entities)
              AND id NOT IN (SELECT entity_id FROM group_entities)
        """)
        conn.execute("""
            DELETE FROM tags
            WHERE id NOT IN (SELECT tag_id FROM document_tags)
              AND id NOT IN (SELECT tag_id FROM group_tags)
        """)
    return True


def _migrate(conn: sqlite3.Connection, sql: str):
    """Run a migration statement, silently ignoring 'duplicate column' errors."""
    try:
        conn.execute(sql)
    except sqlite3.OperationalError:
        pass  # Column already exists


def _migrate_entity_type_check(db_path):
    """Rebuild the entities table so its type CHECK constraint allows 'place'.

    SQLite cannot ALTER a CHECK constraint, so the table must be recreated.
    No-op if the constraint already permits 'place' or the table doesn't
    exist yet (fresh database).

    Uses a dedicated connection: `PRAGMA foreign_keys=OFF` is silently
    ignored inside an open transaction, and it MUST take effect here —
    otherwise `DROP TABLE entities` performs an implicit cascade DELETE
    that wipes document_entities / group_entities / entity_aliases.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='entities'"
        ).fetchone()
        if not row or "'place'" in row[0]:
            return

        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript("""
            BEGIN;
            CREATE TABLE entities_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT    NOT NULL,
                normalized_name TEXT    NOT NULL,
                type            TEXT    NOT NULL CHECK(type IN ('person','object','institution','place','unknown')),
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(normalized_name, type)
            );
            INSERT INTO entities_new (id, name, normalized_name, type, created_at)
                SELECT id, name, normalized_name, type, created_at FROM entities;
            DROP TABLE entities;
            ALTER TABLE entities_new RENAME TO entities;
            CREATE INDEX IF NOT EXISTS idx_entities_normalized ON entities(normalized_name, type);
            COMMIT;
        """)
    finally:
        conn.close()


def _backfill_medium_category(conn: sqlite3.Connection):
    """Populate documents.medium_category and document_groups.medium_category
    for any rows where it is NULL, using the canonical taxonomy mapping."""
    from modules.medium_taxonomy import categorize
    for table in ("documents", "document_groups"):
        rows = conn.execute(
            f"SELECT id, medium FROM {table} WHERE medium_category IS NULL"
        ).fetchall()
        for r in rows:
            conn.execute(
                f"UPDATE {table} SET medium_category = ? WHERE id = ?",
                (categorize(r["medium"]), r["id"]),
            )


# ── Helper utilities ──────────────────────────────────────────────────────────

def row_to_dict(row) -> dict:
    """Convert sqlite3.Row to a plain dict."""
    return dict(row) if row else {}


def rows_to_list(rows) -> list:
    """Convert a list of sqlite3.Row objects to a list of dicts."""
    return [dict(r) for r in rows]


def normalize_entity_name(name: str) -> str:
    """Normalize an entity name for deduplication: lowercase, strip extra spaces."""
    if not name:
        return ""
    return " ".join(name.lower().split())


def upsert_entity(conn: sqlite3.Connection, name: str, entity_type: str) -> int:
    """
    Insert or return existing entity id.
    Deduplicates by normalized_name + type.
    """
    if not name or not entity_type:
        return None
    norm = normalize_entity_name(name)
    entity_type = entity_type.lower()
    if entity_type not in ("person", "object", "institution", "place"):
        entity_type = "unknown"

    cur = conn.execute(
        "SELECT id FROM entities WHERE normalized_name=? AND type=?",
        (norm, entity_type)
    )
    row = cur.fetchone()
    if row:
        return row["id"]

    # No entity with that name — check whether the name is a known alias of an
    # existing entity (recorded by a prior merge or added manually). This makes
    # ingestion resolve alternate names ("Larry Sickman" → "Laurence Sickman").
    cur = conn.execute(
        """SELECT a.entity_id FROM entity_aliases a
           JOIN entities e ON e.id = a.entity_id
           WHERE a.normalized_name=? AND e.type=?""",
        (norm, entity_type)
    )
    row = cur.fetchone()
    if row:
        return row["entity_id"]

    cur = conn.execute(
        "INSERT INTO entities (name, normalized_name, type) VALUES (?,?,?)",
        (name.strip(), norm, entity_type)
    )
    return cur.lastrowid


def add_entity_alias(conn: sqlite3.Connection, entity_id: int, name: str,
                     source: str = "merge") -> bool:
    """Record an alternate name for an entity.

    Returns True if a new alias row was created, False if it was skipped
    (blank name, or the name already matches the entity itself or an
    existing alias). Safe to call repeatedly.
    """
    if not name or not name.strip():
        return False
    norm = normalize_entity_name(name)

    ent = conn.execute(
        "SELECT normalized_name FROM entities WHERE id=?", (entity_id,)
    ).fetchone()
    if not ent or ent["normalized_name"] == norm:
        return False

    cur = conn.execute(
        """INSERT OR IGNORE INTO entity_aliases
           (entity_id, name, normalized_name, source) VALUES (?,?,?,?)""",
        (entity_id, name.strip(), norm, source)
    )
    return cur.rowcount > 0


def get_or_create_tag(conn: sqlite3.Connection, name: str, color: str = "#c9a84c") -> int:
    """Return existing tag id or create new tag."""
    name = name.strip()
    if not name:
        return None
    cur = conn.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO tags (name, color) VALUES (?,?)", (name, color)
    )
    return cur.lastrowid


# ── Audit log helper ──────────────────────────────────────────────────────────

# Long-text fields get truncated in old/new_value to keep audit_events compact.
_AUDIT_LONG_FIELDS = {"transcription", "description", "annotation",
                      "raw_claude_response", "embedding_json"}
_AUDIT_TRUNCATE = 1000


def _audit_serialize(field: str | None, value) -> str | None:
    """Render a field value for storage in audit_events."""
    if value is None:
        return None
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except Exception:
            value = str(value)
    if field in _AUDIT_LONG_FIELDS and len(value) > _AUDIT_TRUNCATE:
        return value[:_AUDIT_TRUNCATE] + f"… [truncated, original length {len(value)}]"
    return value


def record_audit(conn: sqlite3.Connection, *, entity_type: str, entity_id: int,
                 action: str, field: str = None, old=None, new=None,
                 actor: str = "user", run_id: int = None) -> None:
    """Append an audit_events row. Skips no-op updates where old == new."""
    if action == "update" and old == new:
        return
    conn.execute(
        """INSERT INTO audit_events
           (actor, entity_type, entity_id, action, field, old_value, new_value, run_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (actor, entity_type, entity_id, action, field,
         _audit_serialize(field, old), _audit_serialize(field, new), run_id),
    )


def record_audit_diff(conn: sqlite3.Connection, *, entity_type: str, entity_id: int,
                      old_row: dict, new_values: dict, actor: str = "user",
                      run_id: int = None) -> list:
    """Convenience: emit one update row per field that actually changed.
    Returns the list of changed field names."""
    changed = []
    for field, new in new_values.items():
        old = old_row.get(field) if old_row else None
        if old == new:
            continue
        record_audit(conn, entity_type=entity_type, entity_id=entity_id,
                     action="update", field=field, old=old, new=new,
                     actor=actor, run_id=run_id)
        changed.append(field)
    return changed


def document_exists_by_sha256(sha256: str) -> bool:
    """Return True if a document with this hash is already in the database."""
    with get_db() as conn:
        cur = conn.execute("SELECT 1 FROM documents WHERE sha256=?", (sha256,))
        return cur.fetchone() is not None
