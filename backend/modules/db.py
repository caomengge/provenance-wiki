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
    type            TEXT    NOT NULL CHECK(type IN ('person','object','institution','unknown')),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(normalized_name, type)
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
"""


# ── Connection management ─────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Open a SQLite connection with row_factory for dict-like access."""
    db_path = _get_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
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
    with get_db() as conn:
        conn.executescript(SCHEMA_SQL)
        # Migrations: add columns that may not exist in older databases
        _migrate(conn, "ALTER TABLE documents ADD COLUMN is_trashed INTEGER NOT NULL DEFAULT 0")
        _migrate(conn, "ALTER TABLE documents ADD COLUMN source_archive TEXT")
        _migrate(conn, "ALTER TABLE documents ADD COLUMN transcription TEXT")
    return True


def _migrate(conn: sqlite3.Connection, sql: str):
    """Run a migration statement, silently ignoring 'duplicate column' errors."""
    try:
        conn.execute(sql)
    except sqlite3.OperationalError:
        pass  # Column already exists


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
    if entity_type not in ("person", "object", "institution"):
        entity_type = "unknown"

    cur = conn.execute(
        "SELECT id FROM entities WHERE normalized_name=? AND type=?",
        (norm, entity_type)
    )
    row = cur.fetchone()
    if row:
        return row["id"]

    cur = conn.execute(
        "INSERT INTO entities (name, normalized_name, type) VALUES (?,?,?)",
        (name.strip(), norm, entity_type)
    )
    return cur.lastrowid


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


def document_exists_by_sha256(sha256: str) -> bool:
    """Return True if a document with this hash is already in the database."""
    with get_db() as conn:
        cur = conn.execute("SELECT 1 FROM documents WHERE sha256=?", (sha256,))
        return cur.fetchone() is not None
