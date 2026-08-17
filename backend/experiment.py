"""
experiment.py – CLI runner for the retrieval-methods experiment.

Runs EXACTLY the same query against each selected retrieval condition,
logs every run to the database, and exports structured results.

Usage (from backend/):

    python experiment.py --queries ../experiments/queries.csv \
        --modes keyword_original semantic_original semantic_modelled \
        --top-k 10 --output ../experiments/results/

    # Include MODE D (retrieval logged first, then LLM synthesis):
    python experiment.py --queries ../experiments/queries.csv \
        --modes keyword_original semantic_original semantic_modelled rag \
        --rag-retrieval-mode semantic_modelled --top-k 10 \
        --output ../experiments/results/

queries.csv columns: query_id, query, query_type, notes
(query_type is free text, e.g. factual / relational / interpretive)

Outputs in <output>/<timestamp>/:
    results.json   – full structured record of every run
    results.csv    – one row per (query × mode × rank)
    review.csv     – review-ready sheet with transcription excerpts and
                     BLANK human coding columns (human_relevance,
                     human_interpretive_value, notes)
    rag_answers.md – MODE D answers with citations (if rag mode selected)

The query is never rewritten or expanded. Semantic failures abort that
condition with an explicit error entry — no fallback.
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ALL_MODES = ["keyword_original", "semantic_original", "semantic_modelled", "rag"]


def read_queries(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"No queries found in {path}")
    for i, r in enumerate(rows, 1):
        if not (r.get("query") or "").strip():
            sys.exit(f"Row {i} in {path} has an empty 'query' field")
        r.setdefault("query_id", str(i))
        r.setdefault("query_type", "")
        r.setdefault("notes", "")
    return rows


def main():
    ap = argparse.ArgumentParser(description="Provenance Wiki retrieval experiment runner")
    ap.add_argument("--queries", required=True, help="CSV with query_id,query,query_type,notes")
    ap.add_argument("--modes", nargs="+", default=["keyword_original", "semantic_original",
                                                   "semantic_modelled"],
                    choices=ALL_MODES)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--output", default="../experiments/results")
    ap.add_argument("--rag-retrieval-mode", default="semantic_modelled",
                    choices=["keyword_original", "semantic_original", "semantic_modelled"],
                    help="Retrieval condition used inside MODE D")
    ap.add_argument("--no-generate", action="store_true",
                    help="For rag mode: log retrieval only, skip LLM generation")
    ap.add_argument("--embedding-provider", default=None)
    ap.add_argument("--embedding-model", default=None)
    ap.add_argument("--excerpt-chars", type=int, default=400)
    ap.add_argument("--db", help="Optional path to a different SQLite database")
    args = ap.parse_args()

    if args.db:
        import config
        config.DB_PATH = Path(args.db)

    from modules.db import get_db
    from modules.experiment_search import (
        run_retrieval, log_run, get_transcription_excerpt, ExperimentSearchError,
        EXPERIMENT_CONFIG_VERSION,
    )
    from modules.experiment_rag import run_rag, ExperimentRagError

    queries = read_queries(Path(args.queries))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    outdir = Path(args.output) / stamp
    outdir.mkdir(parents=True, exist_ok=True)

    all_runs = []
    rag_answers = []

    with get_db() as conn:
        for q in queries:
            for mode in args.modes:
                header = f"[{q['query_id']}] {mode}"
                if mode == "rag":
                    try:
                        r = run_rag(conn, q["query"],
                                    retrieval_mode=args.rag_retrieval_mode,
                                    top_k=args.top_k,
                                    provider=args.embedding_provider,
                                    model_name=args.embedding_model,
                                    generate=not args.no_generate,
                                    query_id=q["query_id"],
                                    query_type=q["query_type"],
                                    query_notes=q["notes"])
                        entry = {
                            "query_id": q["query_id"], "query": q["query"],
                            "query_type": q["query_type"], "notes": q["notes"],
                            "mode": f"rag[{args.rag_retrieval_mode}]",
                            "status": "ok",
                            "retrieval": r["retrieval"],
                            "evidence": r["evidence"],
                            "config_version": r["config_version"],
                            "answer": r["answer"],
                            "citations": r["citations"],
                            "llm_model": r["llm_model"],
                            "prompt_template_version": r["prompt_template_version"],
                        }
                        if r["answer"]:
                            rag_answers.append(entry)
                        print(f"{header}: {len(r['retrieval']['results'])} results"
                              + (", answer generated" if r["answer"] else " (retrieval only)"))
                    except (ExperimentSearchError, ExperimentRagError) as e:
                        entry = {"query_id": q["query_id"], "query": q["query"],
                                 "query_type": q["query_type"], "notes": q["notes"],
                                 "mode": f"rag[{args.rag_retrieval_mode}]",
                                 "status": "failed", "error": str(e)}
                        print(f"{header}: FAILED — {e}", file=sys.stderr)
                else:
                    try:
                        outcome = run_retrieval(conn, q["query"], mode, args.top_k,
                                                args.embedding_provider, args.embedding_model)
                        log_run(conn, q["query"], mode, args.top_k, outcome,
                                query_id=q["query_id"], query_type=q["query_type"],
                                query_notes=q["notes"])
                        entry = {"query_id": q["query_id"], "query": q["query"],
                                 "query_type": q["query_type"], "notes": q["notes"],
                                 "mode": mode, "status": "ok",
                                 "embedding_provider": outcome["embedding_provider"],
                                 "embedding_model": outcome["embedding_model"],
                                 "representation_type": outcome["representation_type"],
                                 "index_schema_version": outcome["index_schema_version"],
                                 "config_version": outcome["config_version"],
                                 "results": outcome["results"], "meta": outcome["meta"]}
                        print(f"{header}: {len(outcome['results'])} results")
                    except ExperimentSearchError as e:
                        log_run(conn, q["query"], mode, args.top_k, None,
                                query_id=q["query_id"], query_type=q["query_type"],
                                query_notes=q["notes"], error=str(e))
                        entry = {"query_id": q["query_id"], "query": q["query"],
                                 "query_type": q["query_type"], "notes": q["notes"],
                                 "mode": mode, "status": "failed", "error": str(e)}
                        print(f"{header}: FAILED — {e}", file=sys.stderr)
                all_runs.append(entry)
            conn.commit()

        # ── Exports ──────────────────────────────────────────────────────────
        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "config_version": EXPERIMENT_CONFIG_VERSION,
            "queries_file": str(args.queries),
            "modes": args.modes,
            "top_k": args.top_k,
            "rag_retrieval_mode": args.rag_retrieval_mode if "rag" in args.modes else None,
            "runs": all_runs,
        }
        (outdir / "results.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        flat_fields = ["query_id", "query", "query_type", "retrieval_mode", "status",
                       "config_version", "embedding_provider", "embedding_model",
                       "index_schema_version", "representation_type", "rank", "doc_id",
                       "record_type", "score", "title", "source_archive",
                       "discovery_chunk_id", "discovery_representation_type",
                       "discovery_excerpt", "error"]
        with open(outdir / "results.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=flat_fields)
            w.writeheader()
            for run in all_runs:
                retr = run.get("retrieval") or {}
                base = {"query_id": run["query_id"], "query": run["query"],
                        "query_type": run["query_type"], "retrieval_mode": run["mode"],
                        "status": run["status"],
                        "config_version": run.get("config_version")
                            or retr.get("config_version") or EXPERIMENT_CONFIG_VERSION,
                        "embedding_provider": run.get("embedding_provider")
                            or retr.get("embedding_provider"),
                        "embedding_model": run.get("embedding_model")
                            or retr.get("embedding_model"),
                        "index_schema_version": run.get("index_schema_version")
                            or retr.get("index_schema_version"),
                        "representation_type": run.get("representation_type")
                            or retr.get("representation_type"),
                        "error": run.get("error", "")}
                results = run.get("results") or retr.get("results") or []
                if not results:
                    w.writerow(base)
                for r in results:
                    w.writerow({**base, "rank": r["rank"], "doc_id": r["doc_id"],
                                "record_type": r["record_type"], "score": r["score"],
                                "title": r["title"], "source_archive": r["source_archive"],
                                "discovery_chunk_id": r.get("chunk_id"),
                                "discovery_representation_type": r.get("representation_type"),
                                "discovery_excerpt": r.get("excerpt", "")})

        review_fields = ["query_id", "query", "query_type", "retrieval_mode", "rank",
                         "doc_id", "record_type", "source_archive", "title", "score",
                         "transcription_excerpt",
                         "human_relevance", "human_interpretive_value", "notes"]
        with open(outdir / "review.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=review_fields)
            w.writeheader()
            for run in all_runs:
                if run["status"] != "ok":
                    continue
                results = run.get("results") or (run.get("retrieval") or {}).get("results") or []
                for r in results:
                    w.writerow({
                        "query_id": run["query_id"], "query": run["query"],
                        "query_type": run["query_type"], "retrieval_mode": run["mode"],
                        "rank": r["rank"], "doc_id": r["doc_id"],
                        "record_type": r["record_type"],
                        "source_archive": r["source_archive"], "title": r["title"],
                        "score": r["score"],
                        "transcription_excerpt": get_transcription_excerpt(
                            conn, r["doc_id"], r["record_type"], args.excerpt_chars),
                        # Blank on purpose — human coding happens off-system:
                        "human_relevance": "", "human_interpretive_value": "", "notes": "",
                    })

        if rag_answers:
            lines = ["# MODE D (RAG) answers\n"]
            for a in rag_answers:
                lines.append(f"\n## Query {a['query_id']} ({a['query_type']}): {a['query']}\n")
                lines.append(f"*Retrieval: {a['mode']} (frozen ranking) · "
                             f"config: {a.get('config_version', '')} · "
                             f"LLM: {a['llm_model']} · "
                             f"prompt: {a['prompt_template_version']}*\n")
                lines.append(a["answer"])
                cited = ", ".join(f"{c['record_type']} #{c['doc_id']}" for c in a["citations"])
                lines.append(f"\n**Cited (validated against retrieved set):** {cited or 'none'}\n")
                ev_lines = []
                for e in a.get("evidence", []):
                    if e["evidence_chunks"]:
                        chunks = ", ".join(
                            f"chunk {c['chunk_index']} ({c['method']})"
                            for c in e["evidence_chunks"])
                        ev_lines.append(f"- {e['record_type']} #{e['doc_id']}: {chunks}")
                    else:
                        ev_lines.append(f"- {e['record_type']} #{e['doc_id']}: "
                                        "no transcription (finding aid only)")
                if ev_lines:
                    lines.append("\n**PRIMARY-SOURCE EVIDENCE supplied per document:**\n")
                    lines.extend(ev_lines)
                lines.append("\n---\n")
            (outdir / "rag_answers.md").write_text("\n".join(lines), encoding="utf-8")

    ok = sum(1 for r in all_runs if r["status"] == "ok")
    print(f"\n{ok}/{len(all_runs)} runs succeeded. Output: {outdir}")


if __name__ == "__main__":
    main()
