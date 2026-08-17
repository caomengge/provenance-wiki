"""
experiment_search.py – Experimental retrieval conditions A/B/C for the DH
experiment, running on the PRODUCTION chunk-level retrieval infrastructure
(experiment configuration: chunked_retrieval_v1).

MODE A  keyword_original    BM25 (SQLite FTS5) over PRIMARY-SOURCE
                            transcription chunks ONLY. No semantic search,
                            no generated representations.
MODE B  semantic_original   Voyage semantic similarity (production model,
                            voyage-4 by default) over transcription chunks
                            ONLY. No keyword retrieval, no generated
                            representations.
MODE C  semantic_modelled   Voyage semantic similarity over AI-GENERATED
                            representation chunks ONLY (description,
                            entities, transactions, tags, dates, source
                            metadata — never the transcription, never
                            researcher annotations). No keyword retrieval;
                            transcription similarity is NOT used for
                            corpus-level discovery.

Ranking: chunks are ranked by the condition's own scorer (BM25 magnitude or
cosine similarity), then aggregated to retrieval units — a unit's rank is
the rank of its best-scoring chunk. Grouped pages never surface
individually (the production index has no chunks for them).

Reuse: the candidate generators are the production functions
retrieval.keyword_candidates / retrieval.semantic_candidates — this module
adds NO separate retrieval architecture. The legacy experiment path
(document_embeddings whole-unit vectors + experiment_transcription_fts) is
no longer used; its data is preserved untouched.

Methodological guarantees, unchanged:
  - the identical original query goes to every condition; no query
    rewriting or expansion anywhere;
  - no silent fallback: semantic modes raise ExperimentSearchError on any
    failure (missing chunks/embeddings, provider failure) instead of
    switching method;
  - source_archive and record_type are preserved on every result;
  - researcher annotations never enter any representation;
  - every run logs embedding provider/model, index schema version and
    experiment configuration version, so results can be tied to the exact
    retrieval architecture;
  - retrieval results are logged before any RAG generation;
  - no LLM judges retrieval relevance.
"""

import json
import logging
import uuid

from modules.embeddings import EmbeddingError
from modules.retrieval import keyword_candidates, semantic_candidates
from modules.indexer import _active_backend_config
from modules.experiment_schema import ensure_experiment_schema

logger = logging.getLogger(__name__)

EXPERIMENT_CONFIG_VERSION = "chunked_retrieval_v1"

RETRIEVAL_MODES = ("keyword_original", "semantic_original", "semantic_modelled")

MODE_REPRESENTATION = {
    "keyword_original": "transcription",
    "semantic_original": "transcription",
    "semantic_modelled": "generated",
}

# Accept the legacy vocabulary so old call sites/notes keep working.
_REPRESENTATION_ALIASES = {
    "original": "transcription", "transcription": "transcription",
    "modelled": "generated", "generated": "generated",
}

EXCERPT_CHARS = 400          # stored discovery excerpt length
_CHUNK_HEADROOM = 10         # chunk candidates fetched per requested unit


class ExperimentSearchError(RuntimeError):
    """Raised when a retrieval condition cannot run. Never triggers fallback."""


# ── Chunk → unit aggregation ─────────────────────────────────────────────────

def _aggregate_units(chunk_hits: list[dict], top_k: int) -> list[dict]:
    """
    Collapse a best-first chunk ranking into a retrieval-unit ranking:
    a unit's rank is the rank of its best chunk. Each result records the
    discovery chunk (id, representation type, excerpt, score) that put the
    unit at that rank.
    """
    seen, results = set(), []
    for h in chunk_hits:
        key = (h["record_type"], h["unit_id"])
        if key in seen:
            continue
        seen.add(key)
        text = h["text"] or ""
        results.append({
            "rank": len(results) + 1,
            "doc_id": h["unit_id"],
            "record_type": h["record_type"],
            "score": round(float(h["score"]), 6),
            "title": h["title"],
            "source_archive": h["source_archive"],
            "chunk_id": h["chunk_id"],
            "representation_type": h["representation_type"],
            "chunk_index": h["chunk_index"],
            "excerpt": text[:EXCERPT_CHARS] + ("…" if len(text) > EXCERPT_CHARS else ""),
        })
        if len(results) >= top_k:
            break
    return results


def _require_chunks(conn, representation_type: str) -> int:
    n = conn.execute(
        "SELECT COUNT(*) c FROM retrieval_chunks WHERE representation_type=?",
        (representation_type,),
    ).fetchone()["c"]
    if n == 0:
        raise ExperimentSearchError(
            f"No {representation_type!r} chunks in the retrieval index. "
            "Run `python scripts/reindex.py` first."
        )
    return n


# ── MODE A: keyword over transcription chunks ────────────────────────────────

def keyword_original(conn, query: str, top_k: int = 10) -> dict:
    """
    BM25 lexical retrieval over primary-source transcription chunks — the
    conventional digital-archive baseline. Uses the production
    keyword_candidates() (ranked OR of the query terms, deterministic, no
    stemming, no expansion) restricted to representation_type=
    'transcription'; generated representations cannot produce a hit.
    """
    ensure_experiment_schema(conn)
    n = _require_chunks(conn, "transcription")

    hits = keyword_candidates(conn, query,
                              limit=max(top_k * _CHUNK_HEADROOM, 100),
                              representation_type="transcription")
    results = _aggregate_units(hits, top_k)

    return {
        "mode": "keyword_original",
        "query": query,
        "representation_type": "transcription",
        "embedding_provider": None,
        "embedding_model": None,
        "index_schema_version": _index_schema_version(),
        "config_version": EXPERIMENT_CONFIG_VERSION,
        "results": results,
        "meta": {"indexed_chunks": n,
                 "scoring": "bm25 (abs value; lower raw bm25 = better); "
                            "unit rank = best chunk rank"},
    }


# ── MODES B & C: semantic retrieval ──────────────────────────────────────────

def semantic_search(conn, query: str, representation_type: str,
                    top_k: int = 10, provider: str | None = None,
                    model_name: str | None = None) -> dict:
    """
    Cosine-similarity retrieval over stored chunk embeddings of exactly one
    representation ('transcription' → MODE B, 'generated' → MODE C; the
    legacy names 'original'/'modelled' are accepted as aliases).

    Uses the production semantic_candidates() with the production embedding
    model (Voyage voyage-4 by default). Raises ExperimentSearchError if the
    provider fails or no embeddings exist — NO keyword fallback, by design.
    """
    rep = _REPRESENTATION_ALIASES.get(representation_type)
    if rep is None:
        raise ValueError(f"Bad representation_type: {representation_type}")

    ensure_experiment_schema(conn)
    n = _require_chunks(conn, rep)
    prov, model = _active_backend_config(provider, model_name)

    try:
        hits = semantic_candidates(conn, query, rep,
                                   limit=max(top_k * _CHUNK_HEADROOM, 100),
                                   provider=prov, model_name=model)
    except EmbeddingError as e:
        raise ExperimentSearchError(
            f"Query embedding failed ({e}). Semantic retrieval cannot run; "
            "no fallback is performed by design."
        ) from e
    except RuntimeError as e:
        raise ExperimentSearchError(
            f"{e} No fallback is performed by design."
        ) from e

    results = _aggregate_units(hits, top_k)

    mode = "semantic_original" if rep == "transcription" else "semantic_modelled"
    return {
        "mode": mode,
        "query": query,
        "representation_type": rep,
        "embedding_provider": prov,
        "embedding_model": model,
        "index_schema_version": _index_schema_version(),
        "config_version": EXPERIMENT_CONFIG_VERSION,
        "results": results,
        "meta": {"indexed_chunks": n,
                 "scoring": "cosine similarity; unit rank = best chunk rank"},
    }


# ── Dispatcher ────────────────────────────────────────────────────────────────

def run_retrieval(conn, query: str, mode: str, top_k: int = 10,
                  provider: str | None = None, model_name: str | None = None) -> dict:
    """Run exactly one named retrieval condition. No query rewriting."""
    if mode == "keyword_original":
        return keyword_original(conn, query, top_k)
    if mode == "semantic_original":
        return semantic_search(conn, query, "transcription", top_k, provider, model_name)
    if mode == "semantic_modelled":
        return semantic_search(conn, query, "generated", top_k, provider, model_name)
    raise ExperimentSearchError(f"Unknown retrieval mode: {mode!r}")


# ── Run logging ───────────────────────────────────────────────────────────────

def log_run(conn, query: str, mode: str, top_k: int, outcome: dict | None,
            query_id: str | None = None, query_type: str | None = None,
            query_notes: str | None = None, error: str | None = None) -> int:
    """
    Persist one (query × condition) run to experiment_runs/experiment_results,
    including per-result discovery chunk metadata and the provider/model/
    index-schema/config version identifying the exact retrieval setup.
    Returns the run row id.
    """
    ensure_experiment_schema(conn)
    run_uuid = str(uuid.uuid4())
    cur = conn.execute(
        """INSERT INTO experiment_runs
           (run_uuid, query_id, query, query_type, query_notes, retrieval_mode,
            embedding_provider, embedding_model, representation_type, top_k,
            status, error, meta_json, index_schema_version, config_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_uuid, query_id, query, query_type, query_notes, mode,
            (outcome or {}).get("embedding_provider"),
            (outcome or {}).get("embedding_model"),
            (outcome or {}).get("representation_type", MODE_REPRESENTATION.get(mode)),
            top_k,
            "ok" if outcome is not None else "failed",
            error,
            json.dumps((outcome or {}).get("meta", {})),
            (outcome or {}).get("index_schema_version", _index_schema_version()),
            (outcome or {}).get("config_version", EXPERIMENT_CONFIG_VERSION),
        ),
    )
    run_id = cur.lastrowid
    if outcome:
        for r in outcome["results"]:
            conn.execute(
                """INSERT INTO experiment_results
                   (run_id, rank, doc_id, record_type, score, title,
                    source_archive, chunk_id, representation_type, excerpt)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (run_id, r["rank"], r["doc_id"], r["record_type"],
                 r["score"], r["title"], r["source_archive"],
                 r.get("chunk_id"), r.get("representation_type"),
                 r.get("excerpt")),
            )
    return run_id


# ── Helpers ───────────────────────────────────────────────────────────────────

def _index_schema_version() -> int:
    from config import INDEX_SCHEMA_VERSION
    return INDEX_SCHEMA_VERSION


def get_transcription_excerpt(conn, unit_id: int, record_type: str,
                              max_chars: int = 400) -> str:
    """First max_chars of the unit's primary-source transcription (for review CSVs)."""
    from modules.representations import get_unit_transcription
    text = " ".join(get_unit_transcription(conn, unit_id, record_type).split())
    return text[:max_chars] + ("…" if len(text) > max_chars else "")
