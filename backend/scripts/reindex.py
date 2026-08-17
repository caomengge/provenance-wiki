"""
reindex.py – Backfill / incremental reindex of the production retrieval index.

Run from backend/:

    python scripts/reindex.py                # incremental reindex (backfill on first run)
    python scripts/reindex.py --status       # report index health, do nothing
    python scripts/reindex.py --no-embed     # rebuild chunks/FTS only (no API calls)
    python scripts/reindex.py --provider voyage --model voyage-4
                                             # explicit model (migration path)
    python scripts/reindex.py --db /path/to.db

Behaviour:
  • First run after deploying the retrieval refactor = the one-time backfill:
    builds chunk representations and embeddings for every existing document
    and group from database content only — NO image re-extraction, NO Claude
    Vision calls.
  • Every later run is incremental: all records are inspected, but only
    new/changed/pending chunks are sent to the embedding API. Running twice
    in a row sends nothing the second time.
  • Failed/pending embeddings from earlier runs (e.g. provider outage) are
    retried automatically.
  • Switching --provider/--model (or EMBEDDING_PROVIDER/EMBEDDING_MODEL in
    .env) generates vectors for the new model ALONGSIDE the old ones; the
    application always queries a single exact (provider, model, schema
    version) so vectors from different models are never mixed.

The command never deletes or modifies documents, groups, entities,
transactions, tags, annotations, or any other content data.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true",
                    help="Report index health and exit (no changes)")
    ap.add_argument("--no-embed", action="store_true",
                    help="Sync chunks/FTS only; skip embedding API calls")
    ap.add_argument("--provider", default=None,
                    help="Embedding provider (default: config/env, voyage)")
    ap.add_argument("--model", default=None,
                    help="Embedding model (default: provider default, voyage-4)")
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--db", help="Optional path to a different SQLite database")
    args = ap.parse_args()

    if args.db:
        import config
        config.DB_PATH = Path(args.db)

    from modules.db import init_db, get_db
    from modules.indexer import reindex_all, index_status, embed_pending

    init_db()  # additive migrations (creates retrieval tables when absent)

    with get_db() as conn:
        if args.status:
            print(json.dumps(index_status(conn, args.provider, args.model),
                             indent=2, default=str))
            return

        status = index_status(conn, args.provider, args.model)
        print(f"Active embedding backend: {status['active_provider']} / "
              f"{status['active_model']} (index schema v{status['index_schema_version']})")

        result = reindex_all(conn, embed=False, progress=print)
        print(f"\nChunk sync over {result['units']} retrieval units:")
        print(f"  changed/new chunks : {result['chunks_changed']}")
        print(f"  unchanged chunks   : {result['chunks_unchanged']}")
        print(f"  deleted chunks     : {result['chunks_deleted']}")
        print(f"  stale units removed: {result['stale_units_removed']}")
        if result["units_without_transcription"]:
            print(f"  units without transcription "
                  f"({len(result['units_without_transcription'])}):")
            for rt, uid in result["units_without_transcription"]:
                print(f"    - {rt} #{uid} (generated representation only)")
        conn.commit()

        if args.no_embed:
            print("\n--no-embed: skipping embedding stage.")
            return

        print("\nEmbedding new/changed/pending chunks "
              "(unchanged content is skipped without API calls)…")
        emb = embed_pending(conn, provider=args.provider,
                            model_name=args.model, batch_size=args.batch_size)
        conn.commit()
        print(f"  embedded: {emb['embedded']}   skipped (unchanged): {emb['skipped']}   "
              f"failed: {emb['failed']}")
        if emb["error"]:
            print(f"\nWARNING: embedding provider error: {emb['error']}", file=sys.stderr)
            print("Ingested data and keyword search are unaffected. "
                  "Fix the provider (e.g. VOYAGE_API_KEY in .env) and re-run "
                  "this command to retry the pending chunks.", file=sys.stderr)
            sys.exit(2)

        final = index_status(conn, args.provider, args.model)
        print(f"\nIndex ready: semantic_ready={final['semantic_ready']}  "
              f"chunks={final['chunks']}  by_status={final['embeddings_by_status']}")


if __name__ == "__main__":
    main()
