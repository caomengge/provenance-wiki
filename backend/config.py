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
DATA_DIR   = ROOT_DIR / "data"
PHOTOS_DIR = ROOT_DIR / "photos"
DIST_DIR   = ROOT_DIR / "dist"
DB_PATH    = DATA_DIR / "provenance.db"

# ── API Keys ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Claude Models ─────────────────────────────────────────────────────────────
VISION_MODEL     = "claude-sonnet-4-6"
REASONING_MODEL  = "claude-sonnet-4-6"

# ── Ingestion ─────────────────────────────────────────────────────────────────
INGEST_BATCH_SIZE    = 10         # photos processed per Claude API call batch
INGEST_MAX_TOKENS    = 4096       # max output tokens for extraction prompt
SUPPORTED_EXTS       = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
MULTIPAGE_MAX_PAGES  = 50         # max pages per document group

# ── API ───────────────────────────────────────────────────────────────────────
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE     = 200

# ── Search ────────────────────────────────────────────────────────────────────
FTS_SNIPPET_TOKENS = 64           # tokens to include in keyword snippets
SEMANTIC_TOP_K     = 50           # candidates for semantic reranking

# ── Q&A ───────────────────────────────────────────────────────────────────────
QA_CONTEXT_DOCS    = 15           # documents retrieved for RAG context
QA_MAX_TOKENS      = 2048

# ── Flask ─────────────────────────────────────────────────────────────────────
FLASK_HOST  = "0.0.0.0"
FLASK_PORT  = int(os.getenv("FLASK_PORT", "5100"))  # 5100 avoids macOS AirPlay on 5000
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# ── MCP ───────────────────────────────────────────────────────────────────────
MCP_PORT = 5001

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)
