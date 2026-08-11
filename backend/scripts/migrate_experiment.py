"""
migrate_experiment.py – Create experiment tables and build the transcription
FTS index. Purely additive; safe to re-run.

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
    from modules.experiment_schema import ensure_experiment_schema, rebuild_transcription_fts

    with get_db() as conn:
        ensure_experiment_schema(conn)
        stats = rebuild_transcription_fts(conn)

    print(f"Experiment schema ready.")
    print(f"Transcription FTS: {stats['indexed']} units indexed.")
    if stats["missing_transcription"]:
        print(f"WARNING: {len(stats['missing_transcription'])} units have no "
              f"transcription and were NOT indexed:")
        for rt, uid in stats["missing_transcription"]:
            print(f"  - {rt} #{uid}")


if __name__ == "__main__":
    main()
