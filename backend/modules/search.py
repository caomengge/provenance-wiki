"""
search.py – Full-text and semantic search for provenance documents.

Two modes:
  keyword  – SQLite FTS5 with BM25 ranking and snippet highlighting.
             Handles English and CJK text natively via unicode61 tokeniser.
  semantic – Cosine similarity over pre-computed embedding vectors stored
             as JSON in documents.embedding_json.  Falls back gracefully
             if no embeddings are stored yet.

Results are always paginated and returned as a list of document dicts
with an additional `score` and optional `snippet` field.
"""

import json
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


# ── Public entry point ────────────────────────────────────────────────────────

def search_documents(
    query:    str,
    mode:     str = "keyword",
    page:     int = 1,
    per_page: int = 50,
    tag_ids:  list[int] | None = None,
    entity_id: int | None = None,
    source_archive: str | None = None,
) -> dict:
    """
    Search documents using the requested mode.

    Args:
        query:     The user's search string.
        mode:      'keyword' (FTS5) or 'semantic' (vector cosine).
        page:      1-based page number.
        per_page:  Results per page (capped at MAX_PAGE_SIZE).
        tag_ids:   Optional list of tag IDs to restrict results to.
        entity_id: Optional entity ID to restrict results to.

    Returns:
        {results: [...], total: int, page: int, per_page: int, mode: str}
    """
    from config import MAX_PAGE_SIZE, FTS_SNIPPET_TOKENS
    from modules.db import get_db, rows_to_list

    per_page = min(per_page, MAX_PAGE_SIZE)
    offset   = (page - 1) * per_page

    if mode == "semantic":
        return _semantic_search(query, page, per_page, offset, tag_ids, entity_id, source_archive)
    else:
        return _keyword_search(query, page, per_page, offset, tag_ids, entity_id,
                               FTS_SNIPPET_TOKENS, source_archive)


# ── Keyword search ────────────────────────────────────────────────────────────

def _keyword_search(query, page, per_page, offset, tag_ids, entity_id, snippet_tokens, source_archive=None):
    """FTS5 BM25 search with snippet extraction."""
    from modules.db import get_db

    if not query.strip():
        # No query text — if filters are present, browse by filters only; otherwise return empty
        if tag_ids or entity_id or source_archive:
            return _filter_only_search(page, per_page, offset, tag_ids, entity_id, source_archive)
        return _empty_result(page, per_page, "keyword")

    # Escape FTS5 special chars and wrap in quotes for exact-phrase fallback
    fts_query = _escape_fts(query)

    with get_db() as conn:
        # Build base query with optional filters
        joins, wheres, params = _build_filters(tag_ids, entity_id, source_archive)
        where_str = (' AND ' + ' AND '.join(wheres)) if wheres else ''

        # Documents (standalone only)
        doc_where = where_str + ' AND d.group_id IS NULL'
        count_docs = conn.execute(
            f"SELECT COUNT(*) as cnt FROM documents_fts fts JOIN documents d ON d.id = fts.rowid {joins} WHERE documents_fts MATCH ?{doc_where}",
            [fts_query] + params,
        ).fetchone()["cnt"]

        doc_rows = conn.execute(
            f"""SELECT d.*, bm25(documents_fts) AS score,
                snippet(documents_fts, 1, '<mark>', '</mark>', '…', ?) AS snippet,
                'document' as record_type
                FROM documents_fts fts JOIN documents d ON d.id = fts.rowid {joins}
                WHERE documents_fts MATCH ?{doc_where}""",
            [snippet_tokens, fts_query] + params,
        ).fetchall()

        # Groups
        count_groups = conn.execute(
            "SELECT COUNT(*) as cnt FROM groups_fts gfts JOIN document_groups g ON g.id = gfts.rowid WHERE groups_fts MATCH ? AND g.is_trashed=0",
            [fts_query],
        ).fetchone()["cnt"]

        group_rows = conn.execute(
            """SELECT g.id, g.title, g.date_depicted, g.location, g.medium,
                      g.is_key_evidence, g.source_archive, g.created_at, g.updated_at,
                      g.description, g.annotation,
                      bm25(groups_fts) AS score,
                      snippet(groups_fts, 1, '<mark>', '</mark>', '…', ?) AS snippet,
                      'group' as record_type
               FROM groups_fts gfts JOIN document_groups g ON g.id = gfts.rowid
               WHERE groups_fts MATCH ? AND g.is_trashed=0""",
            [snippet_tokens, fts_query],
        ).fetchall()

        # Merge FTS results keyed by (record_type, id) for deduplication
        seen = {}
        for row in list(doc_rows) + list(group_rows):
            d = dict(row)
            d["score"]   = abs(d.get("score") or 0)
            d["snippet"] = d.get("snippet") or ""
            d.pop("embedding_json", None)
            d.pop("raw_claude_response", None)
            seen[(d["record_type"], d["id"])] = d

        # Also search entity names and include linked docs/groups not already found
        entity_matches = conn.execute(
            "SELECT id FROM entities WHERE name LIKE ?",
            [f"%{query}%"],
        ).fetchall()
        matched_entity_ids = [r["id"] for r in entity_matches]

        if matched_entity_ids:
            eid_ph = ",".join("?" * len(matched_entity_ids))

            ent_doc_rows = conn.execute(
                f"""SELECT d.*, 0.5 AS score, '' AS snippet, 'document' as record_type
                    FROM documents d
                    JOIN document_entities de ON de.document_id = d.id
                    WHERE de.entity_id IN ({eid_ph})
                      AND d.is_trashed = 0 AND d.group_id IS NULL""",
                matched_entity_ids,
            ).fetchall()

            ent_group_rows = conn.execute(
                f"""SELECT g.id, g.title, g.date_depicted, g.location, g.medium,
                           g.is_key_evidence, g.source_archive, g.created_at, g.updated_at,
                           g.description, g.annotation,
                           0.5 AS score, '' AS snippet, 'group' as record_type
                    FROM document_groups g
                    JOIN group_entities ge ON ge.group_id = g.id
                    WHERE ge.entity_id IN ({eid_ph})
                      AND g.is_trashed = 0""",
                matched_entity_ids,
            ).fetchall()

            for row in list(ent_doc_rows) + list(ent_group_rows):
                d = dict(row)
                d.pop("embedding_json", None)
                d.pop("raw_claude_response", None)
                key = (d["record_type"], d["id"])
                if key not in seen:
                    seen[key] = d

        total = len(seen)
        results = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
        results = results[offset: offset + per_page]

    return {
        "results":  results,
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "mode":     "keyword",
        "query":    query,
    }


# ── Semantic search ───────────────────────────────────────────────────────────

def _semantic_search(query, page, per_page, offset, tag_ids, entity_id, source_archive=None):
    """Cosine-similarity search over stored embedding vectors."""
    from modules.db import get_db
    from modules.extractor import generate_text_embedding
    from config import ANTHROPIC_API_KEY, SEMANTIC_TOP_K

    # No query text — browse by filters only (same as keyword mode)
    if not query.strip():
        if tag_ids or entity_id or source_archive:
            return _filter_only_search(page, per_page, offset, tag_ids, entity_id, source_archive)
        return _empty_result(page, per_page, "semantic")

    # Generate query embedding
    q_vec = generate_text_embedding(query, ANTHROPIC_API_KEY)
    if q_vec is None:
        logger.warning("Could not generate query embedding; falling back to keyword")
        return _keyword_search(query, page, per_page, offset, tag_ids, entity_id, 64)

    joins, wheres, params = _build_filters(tag_ids, entity_id, source_archive)

    with get_db() as conn:
        filter_sql = f"""
            SELECT d.*
            FROM documents d
            {joins}
            WHERE d.embedding_json IS NOT NULL
            {' AND ' + ' AND '.join(wheres) if wheres else ''}
        """
        rows = conn.execute(filter_sql, params).fetchall()

    if not rows:
        logger.info("No documents have embeddings yet; falling back to keyword search")
        return _keyword_search(query, page, per_page, offset, tag_ids, entity_id, 64)

    # Score all rows
    scored = []
    for row in rows:
        d = dict(row)
        emb_json = d.pop("embedding_json", None)
        d.pop("raw_claude_response", None)
        try:
            doc_vec = json.loads(emb_json)
            score   = _cosine_sim(q_vec, doc_vec)
        except Exception:
            score = 0.0
        d["score"] = score
        scored.append(d)

    scored.sort(key=lambda x: x["score"], reverse=True)
    total   = len(scored)
    results = scored[offset: offset + per_page]

    return {
        "results":  results,
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "mode":     "semantic",
        "query":    query,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Return cosine similarity between two equal-length vectors."""
    if len(a) != len(b):
        return 0.0
    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a)) or 1.0
    nb   = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _escape_fts(query: str) -> str:
    """
    Escape a user query for FTS5.
    Wraps in double-quotes for phrase matching; falls back to prefix match.
    """
    # Remove characters that break FTS5 syntax
    clean = query.replace('"', ' ').replace("'", " ").strip()
    if not clean:
        return '""'
    # If single word, use prefix match
    if " " not in clean:
        return f'"{clean}"*'
    return f'"{clean}"'


def _build_filters(tag_ids, entity_id, source_archive=None):
    """Return (join_sql, where_clauses, params) for optional tag/entity/archive filters."""
    joins  = ""
    wheres = ["d.is_trashed = 0"]
    params = []

    if tag_ids:
        placeholders = ",".join("?" * len(tag_ids))
        joins += f"""
            JOIN document_tags dt ON dt.document_id = d.id
            JOIN tags t ON t.id = dt.tag_id AND t.id IN ({placeholders})
        """
        params.extend(tag_ids)

    if entity_id:
        joins += """
            JOIN document_entities de ON de.document_id = d.id
        """
        wheres.append("de.entity_id = ?")
        params.append(entity_id)

    if source_archive:
        if source_archive == "__none__":
            wheres.append("(d.source_archive IS NULL OR d.source_archive = '')")
        else:
            wheres.append("d.source_archive = ?")
            params.append(source_archive)

    return joins, wheres, params


def _filter_only_search(page, per_page, offset, tag_ids, entity_id, source_archive):
    """Return documents and groups matching filters with no FTS query required."""
    from modules.db import get_db

    # Documents: restrict to standalone (not inside a group) to avoid duplication
    doc_joins, doc_wheres, doc_params = _build_filters(tag_ids, entity_id, source_archive)
    doc_wheres = doc_wheres + ["d.group_id IS NULL"]
    doc_where_clause = " AND ".join(doc_wheres)

    # Groups: build parallel filters against document_groups
    g_joins  = ""
    g_wheres = ["g.is_trashed = 0"]
    g_params = []
    if tag_ids:
        placeholders = ",".join("?" * len(tag_ids))
        g_joins += f"""
            JOIN group_tags gt ON gt.group_id = g.id
            JOIN tags t ON t.id = gt.tag_id AND t.id IN ({placeholders})
        """
        g_params.extend(tag_ids)
    if entity_id:
        g_joins += """
            JOIN group_entities ge ON ge.group_id = g.id
        """
        g_wheres.append("ge.entity_id = ?")
        g_params.append(entity_id)
    if source_archive:
        if source_archive == "__none__":
            g_wheres.append("(g.source_archive IS NULL OR g.source_archive = '')")
        else:
            g_wheres.append("g.source_archive = ?")
            g_params.append(source_archive)
    g_where_clause = " AND ".join(g_wheres)

    with get_db() as conn:
        doc_total = conn.execute(
            f"SELECT COUNT(*) as cnt FROM documents d {doc_joins} WHERE {doc_where_clause}",
            doc_params,
        ).fetchone()["cnt"]

        doc_rows = conn.execute(
            f"SELECT d.*, 'document' as record_type FROM documents d {doc_joins} WHERE {doc_where_clause}"
            " ORDER BY d.date_depicted DESC, d.id DESC",
            doc_params,
        ).fetchall()

        group_total = conn.execute(
            f"SELECT COUNT(DISTINCT g.id) as cnt FROM document_groups g {g_joins} WHERE {g_where_clause}",
            g_params,
        ).fetchone()["cnt"]

        group_rows = conn.execute(
            f"""SELECT DISTINCT g.id, g.title, g.date_depicted, g.location, g.medium,
                       g.is_key_evidence, g.source_archive, g.created_at, g.updated_at,
                       g.description, g.annotation,
                       'group' as record_type
                FROM document_groups g {g_joins} WHERE {g_where_clause}
                ORDER BY g.date_depicted DESC, g.id DESC""",
            g_params,
        ).fetchall()

    merged = []
    for row in list(doc_rows) + list(group_rows):
        d = dict(row)
        d["score"]   = 0
        d["snippet"] = ""
        d.pop("embedding_json", None)
        d.pop("raw_claude_response", None)
        merged.append(d)

    total   = doc_total + group_total
    results = merged[offset: offset + per_page]

    return {"results": results, "total": total, "page": page, "per_page": per_page, "mode": "keyword", "query": ""}


def _empty_result(page, per_page, mode):
    return {"results": [], "total": 0, "page": page, "per_page": per_page, "mode": mode, "query": ""}
