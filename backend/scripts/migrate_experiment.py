"""
migrate_experiment.py – Create/upgrade the experiment run-log tables.
Purely additive; safe to re-run.

As of experiment configuration `chunked_retrieval_v1`, retrieval runs on
the production chunk index (retrieval_chunks / retrieval_chunks_fts /
retrieval_embeddings). This script no longer builds the legacy
experiment_transcription_fts index — build the production index instead:

    python scripts/reindex.py

Usage (from backend/):
    python scripts/migrate_experiment.py [--db /path/to/provenance.db]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="Optional path to a different SQLite database")
    args = ap.parse_args()

    if args.db:
        import config
        config.DB_PATH = Path(args.db)

    from modules.db import get_db
    from modules.experiment_schema import ensure_experiment_schema

    with get_db() as conn:
        ensure_experiment_schema(conn)
        n_t = conn.execute("SELECT COUNT(*) c FROM retrieval_chunks "
                           "WHERE representation_type='transcription'").fetchone()["c"]
        n_g = conn.execute("SELECT COUNT(*) c FROM retrieval_chunks "
                           "WHERE representation_type='generated'").fetchone()["c"]

    print("Experiment run-log schema ready (chunked_retrieval_v1).")
    print(f"Production retrieval index: {n_t} transcription chunks, "
          f"{n_g} generated chunks.")
    if n_t == 0 and n_g == 0:
        print("WARNING: retrieval index is empty — run "
              "`python scripts/reindex.py` before running the experiment.")


if __name__ == "__main__":
    main()
