"""
experiment_search.py – Experimental retrieval modes for the DH experiment.

MODE A  keyword_original   BM25 (FTS5) over PRIMARY-SOURCE transcriptions.
MODE B  semantic_original   Neural embeddings of the transcription, cosine ranked.
MODE C  semantic_modelled   Neural embeddings of a deterministic AI-generated
                            representation (title/description/entities/
                            transactions/tags — NO annotations, NO transcription).

Methodological guarantees (see Research context):
  - No silent fallback: semantic modes raise ExperimentSearchError on any
    failure (missing embeddings, backend failure) instead of switching method.
  - source_archive and record_type are preserved on every result.
  - Units without transcription are excluded from original-text modes and
    reported in result meta, never silently dropped.
  - Researcher annotations are never used in retrieval.
  - Every run can be logged to experiment_runs/experiment_results for
    structured, reproducible analysis.

This module is independent of modules/search.py: the production app is
unchanged.
"""

import json
import logging
import uuid

from modules.embeddings import get_embedding_backend, cosine_similarity, EmbeddingError
from modules.experiment_schema import (
    ensure_experiment_schema,
    rebuild_transcription_fts,
)

logger = logging.getLogger(__name__)

RETRIEVAL_MODES = ("keyword_original", "semantic_original", "semantic_modelled")

MODE_REPRESENTATION = {
    "keyword_original": "original",
    "semantic_original": "original",
    "semantic_modelled": "modelled",
}


class ExperimentSearchError(RuntimeError):
    """Raised when a retrieval condition cannot run. Never triggers fallback."""


def _escape_fts(query: str) -> str:
    """
    Build an FTS5 query for the keyword baseline: ranked OR of the query
    terms (classic BM25 ranked retrieval — documents matching more terms
    rank higher). Deterministic; no stemming, no expansion.

    Note this differs from production search.py, which does strict phrase
    matching; phrase matching returns zero results for most multi-word
    research questions and would misrepresent a conventional archive baseline.
    """
    clean = query.replace('"', " ").replace("'", " ").strip()
    terms = [t for t in clean.split() if t]
    if not terms:
        return '""'
    if len(terms) == 1:
        return f'"{terms[0]}"*'
    return " OR ".join(f'"{t}"' for t in terms)


# ── MODE A: keyword over transcription ───────────────────────────────────────

def keyword_original(conn, query: str, top_k: int = 10) -> dict:
    """
    BM25 lexical retrieval over the primary-source transcription index.
    Represents the conventional digital-archive baseline.
    """
    ensure_experiment_schema(conn)

    n = conn.execute("SELECT COUNT(*) c FROM experiment_transcription_fts").fetchone()["c"]
    if n == 0:
        raise ExperimentSearchError(
            "Transcription FTS index is empty. Run "
            "`python scripts/migrate_experiment.py` (or rebuild_transcription_fts) first."
        )

    rows = conn.execute(
        """SELECT unit_id, record_type, bm25(experiment_transcription_fts) AS score
           FROM experiment_transcription_fts
           WHERE experiment_transcription_fts MATCH ?
           ORDER BY score LIMIT ?""",
        (_escape_fts(query), top_k),
    ).fetchall()

    results = []
    for rank, r in enumerate(rows, start=1):
        meta = _unit_metadata(conn, int(r["unit_id"]), r["record_type"])
        results.append({
            "rank": rank,
            "doc_id": int(r["unit_id"]),
            "record_type": r["record_type"],
            # bm25() returns negative-is-better; store positive magnitude
            "score": abs(r["score"]),
            "title": meta["title"],
            "source_archive": meta["source_archive"],
        })

    return {
        "mode": "keyword_original",
        "representation_type": "original",
        "embedding_provider": None,
        "embedding_model": None,
        "results": results,
        "meta": {"indexed_units": n, "scoring": "bm25 (abs value; lower raw bm25 = better)"},
    }


# ── MODES B & C: semantic retrieval ──────────────────────────────────────────

def semantic_search(conn, query: str, representation_type: str, top_k: int = 10,
                    provider: str | None = None, model_name: str | None = None) -> dict:
    """
    Cosine-similarity retrieval over stored neural embeddings of the given
    representation ('original' → MODE B, 'modelled' → MODE C).

    Raises ExperimentSearchError if the backend fails or no embeddings exist
    for this (model, representation). NO keyword fallback, by design.
    """
    if representation_type not in ("original", "modelled"):
        raise ValueError(f"Bad representation_type: {representation_type}")

    ensure_experiment_schema(conn)

    try:
        backend = get_embedding_backend(provider, model_name)
        q_vec = backend.embed_one(query)
    except EmbeddingError as e:
        raise ExperimentSearchError(
            f"Query embedding failed ({e}). Semantic retrieval cannot run; "
            "no fallback is performed by design."
        ) from e

    rows = conn.execute(
        """SELECT document_id, record_type, embedding_json
           FROM document_embeddings
           WHERE representation_type = ? AND model_name = ?""",
        (representation_type, backend.model_name),
    ).fetchall()

    if not rows:
        raise ExperimentSearchError(
            f"No stored embeddings for representation={representation_type!r}, "
            f"model={backend.model_name!r}. Run "
            "`python scripts/generate_experiment_embeddings.py` first. "
            "No fallback is performed by design."
        )

    scored = []
    for r in rows:
        vec = json.loads(r["embedding_json"])
        scored.append((cosine_similarity(q_vec, vec), int(r["document_id"]), r["record_type"]))
    scored.sort(key=lambda t: (-t[0], t[2], t[1]))  # deterministic tie-break

    results = []
    for rank, (score, unit_id, record_type) in enumerate(scored[:top_k], start=1):
        meta = _unit_metadata(conn, unit_id, record_type)
        results.append({
            "rank": rank,
            "doc_id": unit_id,
            "record_type": record_type,
            "score": round(score, 6),
            "title": meta["title"],
            "source_archive": meta["source_archive"],
        })

    mode = "semantic_original" if representation_type == "original" else "semantic_modelled"
    return {
        "mode": mode,
        "representation_type": representation_type,
        "embedding_provider": backend.provider,
        "embedding_model": backend.model_name,
        "results": results,
        "meta": {"embedded_units": len(rows), "scoring": "cosine similarity"},
    }


# ── Dispatcher ────────────────────────────────────────────────────────────────

def run_retrieval(conn, query: str, mode: str, top_k: int = 10,
                  provider: str | None = None, model_name: str | None = None) -> dict:
    """Run exactly one named retrieval condition. No query rewriting."""
    if mode == "keyword_original":
        return keyword_original(conn, query, top_k)
    if mode == "semantic_original":
        return semantic_search(conn, query, "original", top_k, provider, model_name)
    if mode == "semantic_modelled":
        return semantic_search(conn, query, "modelled", top_k, provider, model_name)
    raise ExperimentSearchError(f"Unknown retrieval mode: {mode!r}")


# ── Run logging ───────────────────────────────────────────────────────────────

def log_run(conn, query: str, mode: str, top_k: int, outcome: dict | None,
            query_id: str | None = None, query_type: str | None = None,
            query_notes: str | None = None, error: str | None = None) -> int:
    """
    Persist one (query × condition) run to experiment_runs/experiment_results.
    `outcome` is the dict returned by run_retrieval, or None on failure.
    Returns the run row id.
    """
    ensure_experiment_schema(conn)
    run_uuid = str(uuid.uuid4())
    cur = conn.execute(
        """INSERT INTO experiment_runs
           (run_uuid, query_id, query, query_type, query_notes, retrieval_mode,
            embedding_provider, embedding_model, representation_type, top_k,
            status, error, meta_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_uuid, query_id, query, query_type, query_notes, mode,
            (outcome or {}).get("embedding_provider"),
            (outcome or {}).get("embedding_model"),
            (outcome or {}).get("representation_type", MODE_REPRESENTATION.get(mode)),
            top_k,
            "ok" if outcome is not None else "failed",
            error,
            json.dumps((outcome or {}).get("meta", {})),
        ),
    )
    run_id = cur.lastrowid
    if outcome:
        for r in outcome["results"]:
            conn.execute(
                """INSERT INTO experiment_results
                   (run_id, rank, doc_id, record_type, score, title, source_archive)
                   VALUES (?,?,?,?,?,?,?)""",
                (run_id, r["rank"], r["doc_id"], r["record_type"],
                 r["score"], r["title"], r["source_archive"]),
            )
    return run_id


# ── Helpers ───────────────────────────────────────────────────────────────────

def _unit_metadata(conn, unit_id: int, record_type: str) -> dict:
    table = "documents" if record_type == "document" else "document_groups"
    row = conn.execute(
        f"SELECT title, source_archive FROM {table} WHERE id=?", (unit_id,)
    ).fetchone()
    if row is None:
        return {"title": None, "source_archive": None}
    return {"title": row["title"], "source_archive": row["source_archive"]}


def get_transcription_excerpt(conn, unit_id: int, record_type: str,
                              max_chars: int = 400) -> str:
    """First max_chars of the unit's primary-source transcription (for review CSVs)."""
    from modules.experiment_schema import get_retrieval_units  # noqa: cyclic-safe
    if record_type == "document":
        row = conn.execute("SELECT transcription FROM documents WHERE id=?",
                           (unit_id,)).fetchone()
        text = (row["transcription"] or "") if row else ""
    else:
        row = conn.execute("SELECT transcription FROM document_groups WHERE id=?",
                           (unit_id,)).fetchone()
        text = (row["transcription"] or "") if row else ""
        if not text:
            pages = conn.execute(
                """SELECT transcription FROM documents WHERE group_id=?
                   ORDER BY COALESCE(page_number, id)""",
                (unit_id,),
            ).fetchall()
            text = "\n".join((p["transcription"] or "") for p in pages)
    text = " ".join(text.split())
    return text[:max_chars] + ("…" if len(text) > max_chars else "")
