#!/usr/bin/env python3
"""
clean_weak_transactions.py – Drop low-quality transactions from the DB.

Counts how many of {seller, buyer, date, price, auction_house} are filled
per transaction. Anything below TRANSACTION_MIN_SCORE (from config.py, or
--min-score on the CLI) is deleted.

By default this is a DRY RUN — it shows what would be deleted but changes
nothing. Pass --apply to actually delete. Deletions are recorded in
audit_events so they can be inspected later.

Usage (from the project root):
    .venv/bin/python -m backend.scripts.clean_weak_transactions          # dry-run
    .venv/bin/python -m backend.scripts.clean_weak_transactions --apply  # delete
    .venv/bin/python -m backend.scripts.clean_weak_transactions --min-score 3 --apply

TAKE A BACKUP FIRST:
    cp data/provenance.db data/provenance.db.bak
"""

import argparse
import json
import sys
from pathlib import Path

# Make `import config`, `from modules...` work when run via -m or directly.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # backend/

from config import TRANSACTION_MIN_SCORE  # noqa: E402
from modules.db import get_db             # noqa: E402
from modules.extractor import transaction_score, _TRANSACTION_ANCHORS  # noqa: E402


def _row_to_txn(row):
    return {k: row[k] for k in _TRANSACTION_ANCHORS}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-score", type=int, default=TRANSACTION_MIN_SCORE,
                        help=f"Anchor-field threshold (default: {TRANSACTION_MIN_SCORE} from config)")
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete. Without this flag, just shows what would be deleted.")
    args = parser.parse_args()

    if args.min_score <= 0:
        print(f"min-score must be > 0 (got {args.min_score}); nothing to do.")
        return

    with get_db() as conn:
        doc_rows = conn.execute(
            "SELECT id, document_id, seller, buyer, date, price, auction_house, notes "
            "FROM transactions"
        ).fetchall()
        grp_rows = conn.execute(
            "SELECT id, group_id, seller, buyer, date, price, auction_house, notes "
            "FROM group_transactions"
        ).fetchall()

        doc_weak = [r for r in doc_rows if transaction_score(_row_to_txn(r)) < args.min_score]
        grp_weak = [r for r in grp_rows if transaction_score(_row_to_txn(r)) < args.min_score]

        print(f"Threshold: keep transactions with >= {args.min_score} of {_TRANSACTION_ANCHORS}")
        print(f"  documents:        {len(doc_weak)} of {len(doc_rows)} would be deleted")
        print(f"  group documents:  {len(grp_weak)} of {len(grp_rows)} would be deleted")

        if not args.apply:
            # Show a few samples so the user can sanity-check before applying.
            for label, rows in (("document tx", doc_weak[:5]), ("group tx", grp_weak[:5])):
                if not rows:
                    continue
                print(f"\nSample weak {label}:")
                for r in rows:
                    print(f"  id={r['id']} {_row_to_txn(r)}")
            print("\nDry-run only. Re-run with --apply to delete.")
            return

        # Apply deletions, recording audit events.
        for r in doc_weak:
            payload = {"table": "transactions", **_row_to_txn(r),
                       "notes": (r["notes"] or "")[:200]}
            conn.execute(
                """INSERT INTO audit_events
                   (actor, entity_type, entity_id, action, old_value)
                   VALUES ('system', 'document', ?, 'delete_weak_transaction', ?)""",
                (r["document_id"], json.dumps(payload)),
            )
            conn.execute("DELETE FROM transactions WHERE id = ?", (r["id"],))

        for r in grp_weak:
            payload = {"table": "group_transactions", **_row_to_txn(r),
                       "notes": (r["notes"] or "")[:200]}
            conn.execute(
                """INSERT INTO audit_events
                   (actor, entity_type, entity_id, action, old_value)
                   VALUES ('system', 'group', ?, 'delete_weak_transaction', ?)""",
                (r["group_id"], json.dumps(payload)),
            )
            conn.execute("DELETE FROM group_transactions WHERE id = ?", (r["id"],))

    print(f"\nDeleted {len(doc_weak)} document transactions and {len(grp_weak)} group transactions.")
    print("Audit entries recorded with action='delete_weak_transaction'.")


if __name__ == "__main__":
    main()
