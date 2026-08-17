"""
generate_experiment_embeddings.py – DEPRECATED (chunked_retrieval_v1).

This script generated whole-unit vectors into the legacy
`document_embeddings` table. As of experiment configuration
`chunked_retrieval_v1`, the A/B/C/D experiment runs on the PRODUCTION
chunk-level retrieval index (retrieval_chunks / retrieval_embeddings),
which is built and incrementally maintained by:

    python scripts/reindex.py

The legacy `document_embeddings` rows are preserved untouched for the
reproducibility of past experiment runs, but nothing writes to or reads
from that table any more.
"""

import sys


def main():
    print(__doc__.strip(), file=sys.stderr)
    print("\nNothing to do. Use: python scripts/reindex.py", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
