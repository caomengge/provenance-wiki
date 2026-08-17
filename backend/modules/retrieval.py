"""
retrieval.py – Hybrid retrieval over the production retrieval index.

True hybrid retrieval (not keyword-first-with-fallback): three ranked
candidate lists are always computed and fused —

  keyword                BM25 (FTS5) over retrieval chunks (both
                         representation types; transcriptions included);
  semantic_transcription cosine over embeddings of primary-source
                         transcription chunks;
  semantic_generated     cosine over embeddings of AI-generated
                         representations.

Rank fusion: Reciprocal Rank Fusion (Cormack, Clarke & Buettcher 2009) —
    fused_score(unit) = Σ over lists  1 / (RRF_K + best_rank_in_list)
with the standard RRF_K = 60 (config.py). RRF is rank-based, so BM25
scores and cosine similarities need no calibration onto a common scale;
there are no tuned weights and no opaque heuristics. Per-list ranks and
raw scores are preserved on every result for transparency and for the
planned retrieval-method comparisons (each list is computed by its own
public function and can be run alone).

Failure handling: if the embedding provider is unavailable or the semantic
index is empty, the fused result degrades to the keyword list and the
response carries semantic_available=False plus the reason — never a silent
substitution of the legacy hashed bag-of-words vectors.

Results are per retrieval unit (standalone document or group), keyed by
(record_type, unit_id), with the best-matching chunk kept as the excerpt
and full source metadata (title, source_archive, date) preserved.
"""

import logging

from modules.embeddings import get_embedding_backend, EmbeddingError
from modules.indexer import _active_backend_config
from modules import vector_store

logger = logging.getLogger(__name__)

KEYWORD = "keyword"
SEM_TRANSCRIPTION = "semantic_transcription"
SEM_GENERATED = "semantic_generated"


def _fts_query(query: str) -> str:
    """
    Ranked OR of the query terms (classic BM25 ranked retrieval: chunks
    matching more terms rank higher). Deterministic; no stemming, no
    expansion. Single terms get prefix matching.
    """
    clean = query.replace('"', " ").replace("'", " ").strip()
    terms = [t for t in clean.split() if t]
    if not terms:
        return '""'
    if len(terms) == 1:
        return f'"{terms[0]}"*'
    return " OR ".join(f'"{t}"' for t in terms)


# ── Individual candidate lists ────────────────────────────────────────────────

def keyword_candidates(conn, query: str, limit: int = 50,
                       source_archive: str | None = None,
                       representation_type: str | None = None) -> list[dict]:
    """BM25 over retrieval_chunks_fts. Rank 1 = best."""
    sql = """
        SELECT c.id AS chunk_id, c.unit_id, c.record_type,
               c.representation_type, c.chunk_index, c.text,
               c.title, c.source_archive, c.date_display,
               bm25(retrieval_chunks_fts) AS bm25_score
        FROM retrieval_chunks_fts f
        JOIN retrieval_chunks c ON c.id = f.chunk_id
        WHERE retrieval_chunks_fts MATCH ?
    """
    params = [_fts_query(query)]
    if representation_type:
        sql += " AND c.representation_type = ?"
        params.append(representation_type)
    if source_archive:
        if source_archive == "__none__":
            sql += " AND (c.source_archive IS NULL OR c.source_archive = '')"
        else:
            sql += " AND c.source_archive = ?"
            params.append(source_archive)
    sql += " ORDER BY bm25_score LIMIT ?"   # bm25(): lower = better
    params.append(limit)

    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception as e:  # malformed FTS query
        logger.warning("Keyword retrieval failed for %r: %s", query, e)
        return []

    out = []
    for rank, r in enumerate(rows, start=1):
        d = dict(r)
        d["rank"] = rank
        d["score"] = abs(d.pop("bm25_score") or 0.0)
        out.append(d)
    return out


def semantic_candidates(conn, query: str, representation_type: str,
                        limit: int = 50, source_archive: str | None = None,
                        provider: str | None = None,
                        model_name: str | None = None,
                        query_vec: list[float] | None = None) -> list[dict]:
    """
    Cosine retrieval over stored embeddings of one representation type.
    Raises EmbeddingError if the provider cannot embed the query, and
    RuntimeError if no vectors are stored — callers decide how to degrade.
    """
    from config import INDEX_SCHEMA_VERSION
    provider, model_name = _active_backend_config(provider, model_name)

    if query_vec is None:
        backend = get_embedding_backend(provider, model_name)
        query_vec = backend.embed_one(query, input_type="query")

    if not vector_store.count_vectors(conn, provider=provider,
                                      model_name=model_name,
                                      schema_version=INDEX_SCHEMA_VERSION):
        raise RuntimeError(
            f"No stored embeddings for {provider}/{model_name} "
            f"(schema v{INDEX_SCHEMA_VERSION}). Run scripts/reindex.py."
        )

    hits = vector_store.search_vectors(
        conn, query_vec, provider=provider, model_name=model_name,
        schema_version=INDEX_SCHEMA_VERSION,
        representation_type=representation_type,
        top_k=limit, source_archive=source_archive,
    )
    for rank, h in enumerate(hits, start=1):
        h["rank"] = rank
        h["score"] = h.pop("similarity")
    return hits


# ── Hybrid fusion ─────────────────────────────────────────────────────────────

def hybrid_retrieve(conn, query: str, top_k: int = 15,
                    source_archive: str | None = None,
                    provider: str | None = None,
                    model_name: str | None = None,
                    include_keyword: bool = True) -> dict:
    """
    Full hybrid retrieval with reciprocal rank fusion.

    Returns:
      {
        results: [ {unit_id, record_type, representation_type, chunk_id,
                    excerpt, title, source_archive, date_display,
                    keyword_rank, semantic_transcription_rank,
                    semantic_generated_rank, keyword_score,
                    semantic_transcription_score, semantic_generated_score,
                    fused_score, fused_rank,
                    matched_chunks: [...] } ],
        semantic_available: bool,
        semantic_error: str|None,
        fusion: {method, rrf_k, lists},
        provider/model when semantic ran
      }
    """
    from config import RRF_K, HYBRID_LIST_SIZE

    lists: dict[str, list[dict]] = {}
    if include_keyword:
        lists[KEYWORD] = keyword_candidates(conn, query, HYBRID_LIST_SIZE, source_archive)

    semantic_available, semantic_error = True, None
    provider_used = model_used = None
    try:
        prov, model = _active_backend_config(provider, model_name)
        backend = get_embedding_backend(prov, model)
        query_vec = backend.embed_one(query, input_type="query")
        lists[SEM_TRANSCRIPTION] = semantic_candidates(
            conn, query, "transcription", HYBRID_LIST_SIZE, source_archive,
            prov, model, query_vec)
        lists[SEM_GENERATED] = semantic_candidates(
            conn, query, "generated", HYBRID_LIST_SIZE, source_archive,
            prov, model, query_vec)
        provider_used, model_used = prov, model
    except (EmbeddingError, RuntimeError) as e:
        logger.warning("Semantic retrieval unavailable (%s); "
                       "degrading to keyword-only results", e)
        semantic_available, semantic_error = False, str(e)
        lists[SEM_TRANSCRIPTION] = []
        lists[SEM_GENERATED] = []

    # ── Reciprocal rank fusion at the unit level ─────────────────────────────
    units: dict[tuple, dict] = {}
    for list_name, hits in lists.items():
        seen_units_in_list = set()
        for h in hits:
            key = (h["record_type"], h["unit_id"])
            u = units.setdefault(key, {
                "unit_id": h["unit_id"],
                "record_type": h["record_type"],
                "title": h["title"],
                "source_archive": h["source_archive"],
                "date_display": h["date_display"],
                "keyword_rank": None, "keyword_score": None,
                "semantic_transcription_rank": None,
                "semantic_transcription_score": None,
                "semantic_generated_rank": None,
                "semantic_generated_score": None,
                "fused_score": 0.0,
                "matched_chunks": [],
            })
            if key not in seen_units_in_list:
                # Best (first-ranked) chunk of this unit in this list
                seen_units_in_list.add(key)
                u[f"{list_name}_rank"] = h["rank"]
                u[f"{list_name}_score"] = h["score"]
                u["fused_score"] += 1.0 / (RRF_K + h["rank"])
            u["matched_chunks"].append({
                "chunk_id": h["chunk_id"],
                "representation_type": h["representation_type"],
                "chunk_index": h["chunk_index"],
                "list": list_name,
                "rank": h["rank"],
                "score": h["score"],
                "text": h["text"],
            })

    fused = sorted(units.values(),
                   key=lambda u: (-u["fused_score"], u["record_type"], u["unit_id"]))
    fused = fused[:top_k]

    for rank, u in enumerate(fused, start=1):
        u["fused_rank"] = rank
        best = _best_excerpt(u["matched_chunks"])
        u["chunk_id"] = best["chunk_id"] if best else None
        u["representation_type"] = best["representation_type"] if best else None
        u["excerpt"] = best["text"] if best else None

    return {
        "results": fused,
        "semantic_available": semantic_available,
        "semantic_error": semantic_error,
        "provider": provider_used,
        "model": model_used,
        "fusion": {
            "method": "reciprocal_rank_fusion",
            "rrf_k": RRF_K,
            "lists": {name: len(hits) for name, hits in lists.items()},
        },
    }


def _best_excerpt(matched_chunks: list[dict]) -> dict | None:
    """
    Choose the excerpt shown for a fused unit: primary-source transcription
    chunks take precedence over generated representations (primary evidence
    first); within a representation, the best-ranked chunk wins.
    """
    if not matched_chunks:
        return None
    transcription = [c for c in matched_chunks
                     if c["representation_type"] == "transcription"]
    pool = transcription or matched_chunks
    return min(pool, key=lambda c: c["rank"])
