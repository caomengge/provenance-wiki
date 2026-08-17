"""
vector_store.py – Vector search behind a minimal, swappable interface.

The current backend keeps embeddings as JSON in SQLite
(retrieval_embeddings) and computes cosine similarity in Python — entirely
adequate for the current corpus (~thousands of chunks) and fully
transparent. Retrieval/RAG code depends only on `search_vectors()`, so a
later FAISS/Qdrant/pgvector backend replaces this one module without
touching ingestion or retrieval logic.

Guarantee: results only ever come from vectors matching the requested
(provider, model, index schema version) exactly — vectors from different
models are never mixed.
"""

import json
import logging

logger = logging.getLogger(__name__)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def search_vectors(conn, query_vec: list[float], *,
                   provider: str, model_name: str, schema_version: int,
                   representation_type: str | None = None,
                   top_k: int = 50,
                   source_archive: str | None = None) -> list[dict]:
    """
    Cosine-rank stored chunk embeddings against a query vector.

    Filters: representation_type ('transcription' | 'generated' | None=both),
    optional source_archive. Only status='ok' vectors of the exact
    (provider, model, schema_version) are considered.

    Returns (best first):
      [{chunk_id, unit_id, record_type, representation_type, chunk_index,
        text, title, source_archive, date_display, similarity}]
    """
    sql = """
        SELECT c.id AS chunk_id, c.unit_id, c.record_type,
               c.representation_type, c.chunk_index, c.text,
               c.title, c.source_archive, c.date_display,
               e.embedding_json
        FROM retrieval_embeddings e
        JOIN retrieval_chunks c ON c.id = e.chunk_id
        WHERE e.status = 'ok'
          AND e.provider = ? AND e.model_name = ? AND e.index_schema_version = ?
    """
    params = [provider, model_name, schema_version]
    if representation_type:
        sql += " AND c.representation_type = ?"
        params.append(representation_type)
    if source_archive:
        if source_archive == "__none__":
            sql += " AND (c.source_archive IS NULL OR c.source_archive = '')"
        else:
            sql += " AND c.source_archive = ?"
            params.append(source_archive)

    scored = []
    for row in conn.execute(sql, params):
        try:
            vec = json.loads(row["embedding_json"])
        except (TypeError, ValueError):
            continue
        sim = cosine_similarity(query_vec, vec)
        scored.append({
            "chunk_id": row["chunk_id"],
            "unit_id": row["unit_id"],
            "record_type": row["record_type"],
            "representation_type": row["representation_type"],
            "chunk_index": row["chunk_index"],
            "text": row["text"],
            "title": row["title"],
            "source_archive": row["source_archive"],
            "date_display": row["date_display"],
            "similarity": sim,
        })

    # Deterministic ordering: similarity desc, then stable ids.
    scored.sort(key=lambda r: (-r["similarity"], r["record_type"],
                               r["unit_id"], r["chunk_id"]))
    return scored[:top_k]


def count_vectors(conn, *, provider: str, model_name: str,
                  schema_version: int) -> int:
    """Number of usable ('ok') vectors for a given model/version."""
    return conn.execute(
        """SELECT COUNT(*) c FROM retrieval_embeddings
           WHERE status='ok' AND provider=? AND model_name=?
             AND index_schema_version=?""",
        (provider, model_name, schema_version),
    ).fetchone()["c"]
