"""
config.py – Central configuration for Provenance Archive Wiki.

All configurable values live here so nothing is hardcoded in modules.
Load environment variables from .env at application startup.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

from version import __version__ as APP_VERSION, TESTING_BUILD  # noqa: F401

# ── Mode ──────────────────────────────────────────────────────────────────────
# Source mode (a git checkout run with run.sh / python backend/app.py) keeps
# everything inside the repository, exactly as before.
# Packaged mode (the downloadable desktop app) keeps read-only program files
# inside the app bundle and ALL researcher data in a per-user folder, because
# the installed program folder may be read-only and is replaced on upgrade.
PACKAGED = bool(getattr(sys, "frozen", False)) or os.getenv("PW_PACKAGED") == "1"

ROOT_DIR = Path(__file__).parent.parent


def user_data_dir() -> Path:
    """Per-user folder for the archive in packaged mode (PW_DATA_DIR overrides)."""
    override = os.getenv("PW_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ProvenanceWiki"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ProvenanceWiki"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "provenance-wiki"


# ── Paths ──────────────────────────────────────────────────────────────────────
if PACKAGED:
    # Bundled, read-only resources (PyInstaller unpacks/points here).
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", ROOT_DIR))
    DIST_DIR     = RESOURCE_DIR / "dist"
    DATA_DIR     = user_data_dir()
    PHOTOS_DIR   = DATA_DIR / "photos"
    if sys.platform == "darwin" and not os.getenv("PW_DATA_DIR"):
        LOG_DIR = Path.home() / "Library" / "Logs" / "ProvenanceWiki"
    else:
        LOG_DIR = DATA_DIR / "logs"
    # No .env in packaged mode: keys come from the OS credential store.
else:
    load_dotenv(ROOT_DIR / ".env")
    RESOURCE_DIR = ROOT_DIR
    DATA_DIR     = ROOT_DIR / "data"
    PHOTOS_DIR   = ROOT_DIR / "photos"
    DIST_DIR     = ROOT_DIR / "dist"
    LOG_DIR      = DATA_DIR / "logs"

THUMBNAILS_DIR = DATA_DIR / "thumbnails"
BACKUP_DIR     = DATA_DIR / "backups"
DB_PATH        = DATA_DIR / "provenance.db"

# ── Thumbnails ────────────────────────────────────────────────────────────────
THUMBNAIL_MAX_DIM = 480           # longest edge in pixels for cached thumbnails
THUMBNAIL_QUALITY = 82            # JPEG quality for thumbnails
IMAGE_CACHE_SECONDS = 3600        # browser cache lifetime for image responses

# ── API Keys ──────────────────────────────────────────────────────────────────
# Template placeholders (e.g. YOUR_KEY_HERE from .env.example) must never count
# as a configured key. They are removed from the environment as well, because
# modules/embeddings.py reads VOYAGE_API_KEY from os.environ directly.
def _is_placeholder_key(value: str) -> bool:
    v = (value or "").strip()
    return not v or v.upper().startswith("YOUR_") or v.upper() in {"CHANGEME", "REPLACE_ME", "XXX"}


for _name in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY"):
    if _is_placeholder_key(os.environ.get(_name, "")):
        os.environ.pop(_name, None)

# Keys saved through the app's Settings screen live in the OS credential store
# (macOS Keychain / Windows Credential Manager) and take precedence over the
# environment / .env. See modules/secrets_store.py.
from modules.secrets_store import load_into_environ as _load_stored_keys  # noqa: E402
_load_stored_keys()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
VOYAGE_API_KEY    = os.getenv("VOYAGE_API_KEY", "").strip()

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
# Loopback only by default: the server has no login, so it must not be
# reachable from other computers on the network. Override with FLASK_HOST
# only if you understand that anyone who can reach the port can read and
# delete the archive (and also set ALLOWED_HOSTS; see modules/local_security.py).
FLASK_HOST  = (os.getenv("FLASK_HOST") or "127.0.0.1").strip()
FLASK_PORT  = int(os.getenv("FLASK_PORT", "5100"))  # 5100 avoids macOS AirPlay on 5000
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# ── MCP ───────────────────────────────────────────────────────────────────────
MCP_PORT = 5001

# Ensure data directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
if PACKAGED:
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
