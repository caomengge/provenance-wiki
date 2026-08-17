"""
retrieval.py – Hybrid retrieval over the production retrieval index.

True hybrid retrieval (not keyword-first-with-fallback): three ranked
candidate lists are always computed and fused —

  keyword                BM25 (FTS5) over PRIMARY-SOURCE TRANSCRIPTION
                         chunks only (the conventional-archive baseline;
                         AI-generated representations never leak into the
                         keyword condition — pass keyword_representation
                         explicitly to widen it for experiments);
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

import json
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
                       representation_type: str | None = "transcription") -> list[dict]:
    """
    BM25 over retrieval_chunks_fts. Rank 1 = best.

    Defaults to PRIMARY-SOURCE TRANSCRIPTION chunks only — AI-generated
    representations must never produce a keyword hit in production. Pass
    representation_type=None (both) or "generated" explicitly for
    experimental comparisons.
    """
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
                    include_keyword: bool = True,
                    keyword_representation: str | None = "transcription") -> dict:
    """
    Full hybrid retrieval with reciprocal rank fusion.

    The production default fuses exactly three conditions:
      keyword over transcription, semantic over transcription, semantic
      over generated representations. keyword_representation defaults to
    "transcription" so AI-generated text can never produce a keyword hit;
    pass keyword_representation=None (both) or "generated" explicitly for
    experimental comparisons only.

    Returns:
      {
        results: [ {unit_id, record_type, representation_type, chunk_id,
                    excerpt, title, source_archive, date_display,
                    keyword_rank, semantic_transcription_rank,
                    semantic_generated_rank, keyword_score,
                    semantic_transcription_score, semantic_generated_score,
                    fused_score, fused_rank,
                    discovery_matches: [...]   # why the unit was retrieved
                   } ],
        semantic_available: bool,
        semantic_error: str|None,
        fusion: {method, rrf_k, lists, keyword_representation},
        provider/model when semantic ran,
        _query_vec: internal (query embedding for reuse, e.g. evidence
                    hydration); None when semantic is unavailable
      }
    """
    from config import RRF_K, HYBRID_LIST_SIZE

    lists: dict[str, list[dict]] = {}
    if include_keyword:
        lists[KEYWORD] = keyword_candidates(conn, query, HYBRID_LIST_SIZE,
                                            source_archive,
                                            representation_type=keyword_representation)

    semantic_available, semantic_error = True, None
    provider_used = model_used = None
    query_vec = None
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
        query_vec = None
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
                "discovery_matches": [],
            })
            if key not in seen_units_in_list:
                # Best (first-ranked) chunk of this unit in this list
                seen_units_in_list.add(key)
                u[f"{list_name}_rank"] = h["rank"]
                u[f"{list_name}_score"] = h["score"]
                u["fused_score"] += 1.0 / (RRF_K + h["rank"])
            u["discovery_matches"].append({
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
        best = _best_excerpt(u["discovery_matches"])
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
            "keyword_representation": keyword_representation if include_keyword else None,
        },
        "_query_vec": query_vec,
    }


# ── Evidence hydration (for RAG) ──────────────────────────────────────────────

def hydrate_evidence(conn, query: str, units: list[dict], top_n: int = 2,
                     provider: str | None = None, model_name: str | None = None,
                     query_vec: list[float] | None = None) -> list[dict]:
    """
    Post-fusion evidence hydration for Q&A.

    A unit can be selected because its AI-generated representation is
    semantically relevant even when none of its transcription chunks
    appeared in the candidate lists. Discovery is not evidence: for every
    selected unit that HAS transcription, this stage retrieves the top_n
    most query-relevant transcription chunks FROM WITHIN THAT UNIT and
    attaches them as `evidence_chunks` — separate from `discovery_matches`
    (which records why the unit was retrieved).

    Ranking within the unit uses semantic similarity when the embedding
    provider and stored vectors are available, and deterministic keyword
    term-overlap as the fallback. Ties break on chunk order, so a unit with
    no query overlap still yields its opening passage as evidence rather
    than nothing.

    Mutates and returns `units`, adding to each:
      has_transcription: bool
      evidence_chunks: [{chunk_id, chunk_index, chunk_count, text, score,
                         method: 'semantic'|'keyword'}]
    Units without transcription get evidence_chunks=[] — the RAG layer must
    then treat the unit as a finding aid only, never as primary evidence.
    """
    from config import INDEX_SCHEMA_VERSION

    prov, model = _active_backend_config(provider, model_name)
    if query_vec is None:
        try:
            backend = get_embedding_backend(prov, model)
            query_vec = backend.embed_one(query, input_type="query")
        except EmbeddingError as e:
            logger.info("Evidence hydration falling back to keyword ranking (%s)", e)
            query_vec = None

    q_terms = {t for t in query.lower().replace('"', ' ').replace("'", " ").split() if t}

    def _keyword_score(text: str) -> float:
        low = (text or "").lower()
        return float(sum(1 for t in q_terms if t in low))

    for u in units:
        rows = conn.execute(
            """SELECT c.id AS chunk_id, c.chunk_index, c.chunk_count, c.text,
                      e.embedding_json
               FROM retrieval_chunks c
               LEFT JOIN retrieval_embeddings e
                      ON e.chunk_id = c.id AND e.status = 'ok'
                     AND e.provider = ? AND e.model_name = ?
                     AND e.index_schema_version = ?
               WHERE c.unit_id = ? AND c.record_type = ?
                 AND c.representation_type = 'transcription'
               ORDER BY c.chunk_index""",
            (prov, model, INDEX_SCHEMA_VERSION, u["unit_id"], u["record_type"]),
        ).fetchall()

        u["has_transcription"] = bool(rows)
        scored = []
        for r in rows:
            method = "keyword"
            score = _keyword_score(r["text"])
            if query_vec is not None and r["embedding_json"]:
                try:
                    score = vector_store.cosine_similarity(
                        query_vec, json.loads(r["embedding_json"]))
                    method = "semantic"
                except (TypeError, ValueError):
                    pass
            scored.append({
                "chunk_id": r["chunk_id"],
                "chunk_index": r["chunk_index"],
                "chunk_count": r["chunk_count"],
                "text": r["text"],
                "score": round(float(score), 6),
                "method": method,
            })
        # Best score first; ties break on document order (chunk_index).
        scored.sort(key=lambda c: (-c["score"], c["chunk_index"]))
        u["evidence_chunks"] = scored[:top_n]

    return units


def _best_excerpt(discovery_matches: list[dict]) -> dict | None:
    """
    Choose the excerpt shown for a fused unit: primary-source transcription
    chunks take precedence over generated representations (primary evidence
    first); within a representation, the best-ranked chunk wins.
    """
    if not discovery_matches:
        return None
    transcription = [c for c in discovery_matches
                     if c["representation_type"] == "transcription"]
    pool = transcription or discovery_matches
    return min(pool, key=lambda c: c["rank"])
