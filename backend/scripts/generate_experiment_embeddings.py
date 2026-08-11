"""
generate_experiment_embeddings.py – Batch-generate neural embeddings for the
experiment, for both representations of every retrieval unit.

Never touches documents.embedding_json (the legacy hashed vectors used by the
production app). Writes to document_embeddings, keyed by
(unit, record_type, representation_type, model_name), so regeneration with a
different model ADDS rows rather than overwriting anything.

Usage (from backend/):
    python scripts/generate_experiment_embeddings.py \
        [--representations original modelled] \
        [--provider sentence_transformers] [--model BAAI/bge-m3] \
        [--batch-size 16] [--force] [--db /path/to.db]

--force re-embeds units whose stored source_text_sha256 already matches;
otherwise unchanged texts are skipped.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--representations", nargs="+", default=["original", "modelled"],
                    choices=["original", "modelled"])
    ap.add_argument("--provider", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--db", help="Optional path to a different SQLite database")
    args = ap.parse_args()

    if args.db:
        import config
        config.DB_PATH = Path(args.db)

    from modules.db import get_db
    from modules.embeddings import get_embedding_backend, EmbeddingError
    from modules.experiment_schema import (
        ensure_experiment_schema, get_retrieval_units, build_modelled_text, sha256_text,
    )

    try:
        backend = get_embedding_backend(args.provider, args.model)
    except EmbeddingError as e:
        print(f"ERROR: embedding backend unavailable: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Backend: {backend.provider} / {backend.model_name}")

    with get_db() as conn:
        ensure_experiment_schema(conn)
        units = get_retrieval_units(conn)
        print(f"{len(units)} retrieval units "
              f"({sum(1 for u in units if u['record_type']=='document')} standalone documents, "
              f"{sum(1 for u in units if u['record_type']=='group')} groups)")

        for rep in args.representations:
            todo, skipped_empty, skipped_same = [], [], 0
            for u in units:
                if rep == "original":
                    text = u["original_text"]
                else:
                    text = build_modelled_text(conn, u["unit_id"], u["record_type"])
                if not text.strip():
                    skipped_empty.append((u["record_type"], u["unit_id"]))
                    continue
                sha = sha256_text(text)
                if not args.force:
                    row = conn.execute(
                        """SELECT source_text_sha256 FROM document_embeddings
                           WHERE document_id=? AND record_type=? AND
                                 representation_type=? AND model_name=?""",
                        (u["unit_id"], u["record_type"], rep, backend.model_name),
                    ).fetchone()
                    if row and row["source_text_sha256"] == sha:
                        skipped_same += 1
                        continue
                todo.append((u, text, sha))

            print(f"\n[{rep}] embedding {len(todo)} units "
                  f"(skipped {skipped_same} unchanged, {len(skipped_empty)} with empty text)")
            if skipped_empty:
                for rt, uid in skipped_empty:
                    print(f"  WARNING: no {rep} text for {rt} #{uid} — excluded from this representation")

            for i in range(0, len(todo), args.batch_size):
                batch = todo[i:i + args.batch_size]
                try:
                    vecs = backend.embed([t for _, t, _ in batch])
                except EmbeddingError as e:
                    print(f"ERROR: embedding failed at batch {i//args.batch_size}: {e}",
                          file=sys.stderr)
                    sys.exit(1)
                for (u, _, sha), vec in zip(batch, vecs):
                    conn.execute(
                        """INSERT INTO document_embeddings
                           (document_id, record_type, representation_type, provider,
                            model_name, dim, embedding_json, source_text_sha256)
                           VALUES (?,?,?,?,?,?,?,?)
                           ON CONFLICT(document_id, record_type, representation_type, model_name)
                           DO UPDATE SET embedding_json=excluded.embedding_json,
                                         dim=excluded.dim,
                                         provider=excluded.provider,
                                         source_text_sha256=excluded.source_text_sha256,
                                         created_at=datetime('now')""",
                        (u["unit_id"], u["record_type"], rep, backend.provider,
                         backend.model_name, len(vec), json.dumps(vec), sha),
                    )
                conn.commit()
                print(f"  {min(i + args.batch_size, len(todo))}/{len(todo)}", flush=True)

        # Summary
        rows = conn.execute(
            """SELECT representation_type, model_name, COUNT(*) c
               FROM document_embeddings GROUP BY representation_type, model_name"""
        ).fetchall()
        print("\nStored embeddings:")
        for r in rows:
            print(f"  {r['representation_type']:9s} {r['model_name']}: {r['c']}")


if __name__ == "__main__":
    main()
