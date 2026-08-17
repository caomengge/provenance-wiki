"""
config.py – Central configuration for Provenance Archive Wiki.

All configurable values live here so nothing is hardcoded in modules.
Load environment variables from .env at application startup.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (two levels up from backend/)
ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / ".env")

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR       = ROOT_DIR / "data"
PHOTOS_DIR     = ROOT_DIR / "photos"
THUMBNAILS_DIR = DATA_DIR / "thumbnails"
DIST_DIR       = ROOT_DIR / "dist"
DB_PATH        = DATA_DIR / "provenance.db"

# ── Thumbnails ────────────────────────────────────────────────────────────────
THUMBNAIL_MAX_DIM = 480           # longest edge in pixels for cached thumbnails
THUMBNAIL_QUALITY = 82            # JPEG quality for thumbnails
IMAGE_CACHE_SECONDS = 3600        # browser cache lifetime for image responses

# ── API Keys ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
VOYAGE_API_KEY    = os.getenv("VOYAGE_API_KEY", "")

# ── Claude Models ─────────────────────────────────────────────────────────────
VISION_MODEL     = "claude-sonnet-4-6"
REASONING_MODEL  = "claude-sonnet-4-6"

# ── Ingestion ─────────────────────────────────────────────────────────────────
INGEST_BATCH_SIZE    = 10         # photos processed per Claude API call batch
INGEST_WORKERS       = 4          # concurrent workers for parallel ingestion
INGEST_MAX_TOKENS    = 4096       # max output tokens for extraction prompt
SUPPORTED_EXTS       = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
MULTIPAGE_MAX_PAGES  = 50         # max pages per document group

# Auto-drop threshold for LLM-extracted transactions. Counts how many of
# {seller, buyer, date, price, auction_house} are filled.
#
# Default 0 (keep everything): the frontend now shows a quality score
# badge on each transaction so users can review and delete weak rows
# themselves. Set to 1+ if you want the backend to drop them silently.
TRANSACTION_MIN_SCORE = 0

# ── API ───────────────────────────────────────────────────────────────────────
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE     = 200

# ── Search ────────────────────────────────────────────────────────────────────
FTS_SNIPPET_TOKENS = 64           # tokens to include in keyword snippets
SEMANTIC_TOP_K     = 50           # candidates for semantic reranking

# ── Retrieval index (semantic + hybrid) ───────────────────────────────────────
# Embedding provider/model. Defaults: Voyage AI / voyage-4 (see modules/embeddings.py).
# Override via env: EMBEDDING_PROVIDER, EMBEDDING_MODEL. VOYAGE_API_KEY required
# for the voyage provider.
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER") or None   # None → module default
EMBEDDING_MODEL    = os.getenv("EMBEDDING_MODEL") or None      # None → provider default

# Version stamp for the embedding/index schema. Bump when the way texts are
# constructed/embedded changes in a way that invalidates stored vectors
# (chunking algorithm change, representation format change, ...). Vectors are
# only reused when content hash + provider + model + this version all match.
INDEX_SCHEMA_VERSION = 1

# Transcription chunking (chunking_version 'v1' — documented in
# modules/representations.py). Deterministic paragraph packing:
CHUNK_MAX_CHARS     = 1500        # max characters per transcription chunk
CHUNK_OVERLAP_CHARS = 200         # tail of previous chunk prefixed to the next
GENERATED_MAX_CHARS = 6000        # generated representations larger than this
                                  # are split line-wise (large groups can have
                                  # hundreds of entity/transaction lines)

# Hybrid retrieval / reciprocal rank fusion
RRF_K              = 60           # standard RRF constant: score = Σ 1/(RRF_K + rank)
HYBRID_LIST_SIZE   = 50           # candidates taken from each retrieval list before fusion
EMBED_BATCH_SIZE   = 64           # texts per embedding API call during indexing

# ── Q&A ───────────────────────────────────────────────────────────────────────
QA_CONTEXT_DOCS    = 15           # documents retrieved for RAG context
QA_MAX_TOKENS      = 2048

# ── Flask ─────────────────────────────────────────────────────────────────────
FLASK_HOST  = "0.0.0.0"
FLASK_PORT  = int(os.getenv("FLASK_PORT", "5100"))  # 5100 avoids macOS AirPlay on 5000
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# ── MCP ───────────────────────────────────────────────────────────────────────
MCP_PORT = 5001

# Ensure data directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
