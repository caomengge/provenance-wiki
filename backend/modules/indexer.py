"""
indexer.py – Incremental retrieval indexing engine.

Single source of truth for keeping retrieval_chunks, retrieval_chunks_fts
and retrieval_embeddings in sync with the documents/groups tables — without
ever re-embedding unchanged content.

Identity of a stored embedding = (content hash, provider, model, index
schema version). Before any embedding API call the indexer:

  1. builds the exact texts that would be embedded (representations.py);
  2. computes a stable sha256 content hash per text;
  3. skips every chunk whose stored embedding matches on all four keys with
     status='ok';
  4. embeds only new/changed chunks (and pending/failed retries), batched.

Consequences:
  • importing N new documents        → only those N units embedded;
  • correcting one transcription     → only that unit's changed chunks;
  • editing entities/tags/metadata   → only that unit's generated repr.;
  • "reindex all" on unchanged data  → all rows inspected, zero API calls;
  • switching model                  → explicit reindex adds vectors for the
                                       new model; old vectors are kept and
                                       never mixed at query time.

Failure handling: representation rows + FTS are committed before any
embedding call, so keyword search over new material works even when the
embedding provider is down. Failed embeddings are stored as
status='pending'/'failed' with the error message, are visible via
index_status(), and are retried by the next index pass. The legacy hashed
bag-of-words vector is never substituted for a semantic embedding.

Ingestion integration: modules/ingestor.py, api/groups.py and the document/
entity/tag/transaction mutation routes call index_unit()/index_units()
after committing content changes, so new documents become searchable
automatically. scripts/reindex.py wraps reindex_all() for manual
maintenance and model migrations.
"""

import json
import logging

from modules.embeddings import get_embedding_backend, EmbeddingError
from modules.representations import (
    CHUNKING_VERSION,
    build_representations,
    get_retrieval_units,
)

logger = logging.getLogger(__name__)


def _active_backend_config(provider=None, model_name=None):
    """Resolve the active (provider, model) pair from args → config → defaults."""
    from config import EMBEDDING_PROVIDER, EMBEDDING_MODEL
    from modules.embeddings import DEFAULT_PROVIDER, DEFAULT_MODELS
    import os
    provider = provider or EMBEDDING_PROVIDER or os.getenv("EMBEDDING_PROVIDER") or DEFAULT_PROVIDER
    model_name = model_name or EMBEDDING_MODEL or os.getenv("EMBEDDING_MODEL") or DEFAULT_MODELS.get(provider)
    return provider, model_name


# ── Chunk synchronisation (no embedding API involved) ────────────────────────

def sync_unit_chunks(conn, unit_id: int, record_type: str) -> dict:
    """
    Rebuild the representations for one unit and diff them against the
    stored retrieval_chunks. Inserts/updates/deletes only what changed;
    keeps the FTS index in sync. Returns
    {"changed_chunk_ids": [...], "unchanged": n, "deleted": n}.
    """
    reps = build_representations(conn, unit_id, record_type)

    existing = {
        (r["representation_type"], r["chunk_index"]): r
        for r in conn.execute(
            """SELECT id, representation_type, chunk_index, content_sha256,
                      chunk_count, title, source_archive, date_display
               FROM retrieval_chunks
               WHERE unit_id=? AND record_type=?""",
            (unit_id, record_type),
        ).fetchall()
    }

    changed_ids, unchanged = [], 0
    seen_keys = set()

    for rep in reps:
        key = (rep["representation_type"], rep["chunk_index"])
        seen_keys.add(key)
        old = existing.get(key)
        if old is None:
            cur = conn.execute(
                """INSERT INTO retrieval_chunks
                   (unit_id, record_type, representation_type, chunk_index,
                    chunk_count, text, content_sha256, chunking_version,
                    title, source_archive, date_display)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (unit_id, record_type, rep["representation_type"],
                 rep["chunk_index"], rep["chunk_count"], rep["text"],
                 rep["content_sha256"], CHUNKING_VERSION,
                 rep["title"], rep["source_archive"], rep["date_display"]),
            )
            chunk_id = cur.lastrowid
            conn.execute(
                "INSERT INTO retrieval_chunks_fts(text, chunk_id) VALUES (?,?)",
                (rep["text"], chunk_id),
            )
            changed_ids.append(chunk_id)
        elif old["content_sha256"] != rep["content_sha256"]:
            conn.execute(
                """UPDATE retrieval_chunks SET
                     text=?, content_sha256=?, chunk_count=?, chunking_version=?,
                     title=?, source_archive=?, date_display=?,
                     updated_at=datetime('now')
                   WHERE id=?""",
                (rep["text"], rep["content_sha256"], rep["chunk_count"],
                 CHUNKING_VERSION, rep["title"], rep["source_archive"],
                 rep["date_display"], old["id"]),
            )
            conn.execute("DELETE FROM retrieval_chunks_fts WHERE chunk_id=?", (old["id"],))
            conn.execute(
                "INSERT INTO retrieval_chunks_fts(text, chunk_id) VALUES (?,?)",
                (rep["text"], old["id"]),
            )
            changed_ids.append(old["id"])
        else:
            unchanged += 1
            # Content identical; refresh denormalised metadata if it drifted
            # (title/archive/date changes don't require re-embedding).
            if (old["title"], old["source_archive"], old["date_display"],
                    old["chunk_count"]) != (rep["title"], rep["source_archive"],
                                            rep["date_display"], rep["chunk_count"]):
                conn.execute(
                    """UPDATE retrieval_chunks
                       SET title=?, source_archive=?, date_display=?, chunk_count=?
                       WHERE id=?""",
                    (rep["title"], rep["source_archive"], rep["date_display"],
                     rep["chunk_count"], old["id"]),
                )

    # Remove stale chunks (transcription shrank, representation vanished).
    deleted = 0
    for key, old in existing.items():
        if key not in seen_keys:
            conn.execute("DELETE FROM retrieval_chunks_fts WHERE chunk_id=?", (old["id"],))
            conn.execute("DELETE FROM retrieval_chunks WHERE id=?", (old["id"],))
            deleted += 1

    return {"changed_chunk_ids": changed_ids, "unchanged": unchanged, "deleted": deleted}


def remove_unit(conn, unit_id: int, record_type: str) -> int:
    """
    Drop every retrieval record for a unit (deleted, trashed, or absorbed
    into a group). Embeddings cascade. Returns number of chunks removed.
    """
    rows = conn.execute(
        "SELECT id FROM retrieval_chunks WHERE unit_id=? AND record_type=?",
        (unit_id, record_type),
    ).fetchall()
    for r in rows:
        conn.execute("DELETE FROM retrieval_chunks_fts WHERE chunk_id=?", (r["id"],))
        conn.execute("DELETE FROM retrieval_chunks WHERE id=?", (r["id"],))
    return len(rows)


# ── Embedding (incremental) ──────────────────────────────────────────────────

def _chunks_needing_embedding(conn, provider: str, model_name: str,
                              schema_version: int, chunk_ids=None) -> list[dict]:
    """
    Chunks with no 'ok' embedding matching (content hash, provider, model,
    schema version). Optionally restricted to specific chunk ids.
    """
    sql = """
        SELECT c.id AS chunk_id, c.text, c.content_sha256
        FROM retrieval_chunks c
        LEFT JOIN retrieval_embeddings e
               ON e.chunk_id = c.id
              AND e.provider = ? AND e.model_name = ?
              AND e.index_schema_version = ?
        WHERE (e.id IS NULL
               OR e.status != 'ok'
               OR e.content_sha256 != c.content_sha256)
    """
    params = [provider, model_name, schema_version]
    if chunk_ids is not None:
        if not chunk_ids:
            return []
        sql += f" AND c.id IN ({','.join('?' * len(chunk_ids))})"
        params.extend(chunk_ids)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _mark_embeddings(conn, todo, provider, model_name, schema_version,
                     status: str, error: str | None = None):
    """Upsert placeholder rows for chunks whose embedding isn't stored yet."""
    for item in todo:
        conn.execute(
            """INSERT INTO retrieval_embeddings
               (chunk_id, provider, model_name, index_schema_version,
                content_sha256, status, error)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(chunk_id, provider, model_name, index_schema_version)
               DO UPDATE SET content_sha256=excluded.content_sha256,
                             status=excluded.status,
                             error=excluded.error,
                             embedding_json=NULL,
                             dim=NULL,
                             updated_at=datetime('now')""",
            (item["chunk_id"], provider, model_name, schema_version,
             item["content_sha256"], status, error),
        )


def embed_pending(conn, chunk_ids=None, provider=None, model_name=None,
                  batch_size=None) -> dict:
    """
    Embed every chunk (optionally restricted to chunk_ids) that lacks a
    valid stored embedding for the active provider/model/schema version.
    Unchanged chunks are skipped WITHOUT any API call.

    Returns {"embedded": n, "skipped": n, "failed": n, "provider": p,
             "model": m, "error": str|None}.
    Never raises on provider failure — failures are recorded on the
    embedding rows and reported, so ingestion is never destroyed.
    """
    from config import INDEX_SCHEMA_VERSION, EMBED_BATCH_SIZE
    batch_size = batch_size or EMBED_BATCH_SIZE
    provider, model_name = _active_backend_config(provider, model_name)

    total_chunks = conn.execute("SELECT COUNT(*) c FROM retrieval_chunks").fetchone()["c"]
    todo = _chunks_needing_embedding(conn, provider, model_name,
                                     INDEX_SCHEMA_VERSION, chunk_ids)
    skipped = (len(chunk_ids) if chunk_ids is not None else total_chunks) - len(todo)

    result = {"embedded": 0, "skipped": max(skipped, 0), "failed": 0,
              "provider": provider, "model": model_name, "error": None}
    if not todo:
        return result

    try:
        backend = get_embedding_backend(provider, model_name)
    except EmbeddingError as e:
        logger.warning("Embedding backend unavailable: %s", e)
        _mark_embeddings(conn, todo, provider, model_name,
                         INDEX_SCHEMA_VERSION, "pending", str(e))
        result["failed"] = len(todo)
        result["error"] = str(e)
        return result

    for i in range(0, len(todo), batch_size):
        batch = todo[i:i + batch_size]
        try:
            vectors = backend.embed([b["text"] for b in batch], input_type="document")
        except EmbeddingError as e:
            logger.warning("Embedding batch failed: %s", e)
            _mark_embeddings(conn, todo[i:], provider, model_name,
                             INDEX_SCHEMA_VERSION, "failed", str(e))
            result["failed"] += len(todo) - i
            result["error"] = str(e)
            return result

        for item, vec in zip(batch, vectors):
            conn.execute(
                """INSERT INTO retrieval_embeddings
                   (chunk_id, provider, model_name, index_schema_version,
                    content_sha256, dim, embedding_json, status, error)
                   VALUES (?,?,?,?,?,?,?, 'ok', NULL)
                   ON CONFLICT(chunk_id, provider, model_name, index_schema_version)
                   DO UPDATE SET content_sha256=excluded.content_sha256,
                                 dim=excluded.dim,
                                 embedding_json=excluded.embedding_json,
                                 status='ok', error=NULL,
                                 updated_at=datetime('now')""",
                (item["chunk_id"], provider, model_name, INDEX_SCHEMA_VERSION,
                 item["content_sha256"], len(vec), json.dumps(vec)),
            )
            result["embedded"] += 1

    return result


# ── Public entry points ──────────────────────────────────────────────────────

def index_unit(conn, unit_id: int, record_type: str, embed: bool = True) -> dict:
    """
    Incrementally (re)index one retrieval unit: sync chunks, then embed only
    new/changed/pending chunks. Safe to call after any content mutation —
    when nothing changed, no embedding API call is made.
    """
    sync = sync_unit_chunks(conn, unit_id, record_type)
    out = {"unit_id": unit_id, "record_type": record_type, **sync}
    if embed:
        chunk_rows = conn.execute(
            "SELECT id FROM retrieval_chunks WHERE unit_id=? AND record_type=?",
            (unit_id, record_type),
        ).fetchall()
        out["embedding"] = embed_pending(conn, [r["id"] for r in chunk_rows])
    return out


def index_units(conn, units: list[tuple[int, str]], embed: bool = True) -> dict:
    """index_unit over a list of (unit_id, record_type), one embed pass."""
    all_ids = []
    for unit_id, record_type in units:
        sync_unit_chunks(conn, unit_id, record_type)
        rows = conn.execute(
            "SELECT id FROM retrieval_chunks WHERE unit_id=? AND record_type=?",
            (unit_id, record_type),
        ).fetchall()
        all_ids.extend(r["id"] for r in rows)
    out = {"units": len(units)}
    if embed:
        out["embedding"] = embed_pending(conn, all_ids)
    return out


def reindex_all(conn, embed: bool = True, progress=None) -> dict:
    """
    Full incremental reindex / one-time backfill.

    Inspects every retrieval unit, rebuilds representations, and embeds only
    what is new/changed/pending for the active provider/model/schema
    version. On an unchanged corpus this sends NOTHING to the embedding API.
    Also drops retrieval records for units that no longer exist (trashed,
    deleted, absorbed into groups).

    `progress`: optional callable(str) for CLI feedback.
    """
    units = get_retrieval_units(conn)
    valid = {(u["unit_id"], u["record_type"]) for u in units}

    # Remove records for units that are no longer retrievable.
    stale = conn.execute(
        "SELECT DISTINCT unit_id, record_type FROM retrieval_chunks"
    ).fetchall()
    removed_units = 0
    for s in stale:
        if (s["unit_id"], s["record_type"]) not in valid:
            remove_unit(conn, s["unit_id"], s["record_type"])
            removed_units += 1

    changed = unchanged = deleted = 0
    no_transcription = []
    for i, u in enumerate(units):
        sync = sync_unit_chunks(conn, u["unit_id"], u["record_type"])
        changed += len(sync["changed_chunk_ids"])
        unchanged += sync["unchanged"]
        deleted += sync["deleted"]
        if not u["transcription"]:
            no_transcription.append((u["record_type"], u["unit_id"]))
        if progress and (i + 1) % 100 == 0:
            progress(f"  chunks synced for {i + 1}/{len(units)} units")

    out = {
        "units": len(units),
        "chunks_changed": changed,
        "chunks_unchanged": unchanged,
        "chunks_deleted": deleted,
        "stale_units_removed": removed_units,
        "units_without_transcription": no_transcription,
    }
    if embed:
        if progress:
            progress("  embedding new/changed chunks…")
        out["embedding"] = embed_pending(conn)
    return out


# ── Safe hooks for request handlers / ingestion ──────────────────────────────

def reindex_unit_safe(unit_id: int, record_type: str) -> dict | None:
    """
    Incrementally reindex one unit on its own connection, swallowing every
    error (indexing must never break a content mutation that already
    committed). Failed embeddings are recorded as pending/failed rows and
    picked up by the next pass, so nothing is lost by swallowing here.
    """
    from modules.db import get_db
    try:
        with get_db() as conn:
            return index_unit(conn, unit_id, record_type)
    except Exception:
        logger.exception("Retrieval indexing failed for %s #%s (content is "
                         "safe; run scripts/reindex.py to retry)",
                         record_type, unit_id)
        return None


def reindex_document_safe(doc_id: int) -> dict | None:
    """
    Reindex the retrieval unit that a document belongs to: the document
    itself when standalone, its group when it is a page of one (page edits
    change the group's concatenated transcription).
    """
    from modules.db import get_db
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT group_id, is_trashed FROM documents WHERE id=?",
                (doc_id,),
            ).fetchone()
    except Exception:
        logger.exception("Could not resolve document %s for reindexing", doc_id)
        return None
    if row is None:
        return remove_unit_safe(doc_id, "document")
    if row["group_id"] is not None:
        return reindex_unit_safe(row["group_id"], "group")
    if row["is_trashed"]:
        return remove_unit_safe(doc_id, "document")
    return reindex_unit_safe(doc_id, "document")


def reindex_entity_units_safe(entity_id: int) -> dict | None:
    """
    Reindex every retrieval unit whose generated representation mentions
    this entity (documents and groups). Incremental: only the generated
    representation of each affected unit actually changes, so only those
    single chunks are re-embedded.
    """
    from modules.db import get_db
    try:
        with get_db() as conn:
            units = [(r["document_id"], "document") for r in conn.execute(
                "SELECT DISTINCT document_id FROM document_entities WHERE entity_id=?",
                (entity_id,)).fetchall()]
            units += [(r["group_id"], "group") for r in conn.execute(
                "SELECT DISTINCT group_id FROM group_entities WHERE entity_id=?",
                (entity_id,)).fetchall()]
            # Pages of groups are indexed via their group.
            resolved = []
            for uid, rt in units:
                if rt == "document":
                    row = conn.execute(
                        "SELECT group_id, is_trashed FROM documents WHERE id=?",
                        (uid,)).fetchone()
                    if row is None or row["is_trashed"]:
                        continue
                    if row["group_id"] is not None:
                        resolved.append((row["group_id"], "group"))
                        continue
                resolved.append((uid, rt))
            return index_units(conn, sorted(set(resolved)))
    except Exception:
        logger.exception("Entity-driven reindex failed for entity %s "
                         "(content is safe; run scripts/reindex.py to retry)",
                         entity_id)
        return None


def remove_unit_safe(unit_id: int, record_type: str) -> dict | None:
    """Drop a unit's retrieval records on its own connection; never raises."""
    from modules.db import get_db
    try:
        with get_db() as conn:
            removed = remove_unit(conn, unit_id, record_type)
        return {"removed_chunks": removed}
    except Exception:
        logger.exception("Could not remove retrieval records for %s #%s",
                         record_type, unit_id)
        return None


def index_status(conn, provider=None, model_name=None) -> dict:
    """
    Report semantic-indexing health for the active provider/model/version:
    chunk counts by representation, embedding counts by status, and how many
    chunks still need embedding (missing, pending, failed, or stale-hash).
    """
    from config import INDEX_SCHEMA_VERSION
    provider, model_name = _active_backend_config(provider, model_name)

    chunks_by_rep = {
        r["representation_type"]: r["c"]
        for r in conn.execute(
            """SELECT representation_type, COUNT(*) c
               FROM retrieval_chunks GROUP BY representation_type"""
        ).fetchall()
    }
    emb_by_status = {
        r["status"]: r["c"]
        for r in conn.execute(
            """SELECT status, COUNT(*) c FROM retrieval_embeddings
               WHERE provider=? AND model_name=? AND index_schema_version=?
               GROUP BY status""",
            (provider, model_name, INDEX_SCHEMA_VERSION),
        ).fetchall()
    }
    needed = len(_chunks_needing_embedding(conn, provider, model_name,
                                           INDEX_SCHEMA_VERSION))
    models = [dict(r) for r in conn.execute(
        """SELECT provider, model_name, index_schema_version,
                  COUNT(*) c, SUM(status='ok') ok
           FROM retrieval_embeddings
           GROUP BY provider, model_name, index_schema_version"""
    ).fetchall()]

    return {
        "active_provider": provider,
        "active_model": model_name,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "chunks": chunks_by_rep,
        "embeddings_by_status": emb_by_status,
        "chunks_needing_embedding": needed,
        "semantic_ready": needed == 0 and sum(chunks_by_rep.values()) > 0,
        "stored_models": models,
    }
