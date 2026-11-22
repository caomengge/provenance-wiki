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
    """Default per-user archive folder in packaged mode (kept for compatibility)."""
    override = os.getenv("PW_DATA_DIR")
    if override:
        return Path(override).expanduser()
    from modules.archive_location import default_archive_dir
    return default_archive_dir()


# ── Paths ──────────────────────────────────────────────────────────────────────
# Packaged mode: ONE archive folder holds the database, photos, thumbnails,
# backups and logs, wherever the researcher chose (modules/archive_location.py,
# spec: specs/archive-location.md). A small fixed config folder records where.
#
# ARCHIVE_STATE: "ok" | "setup" (first launch, nothing created yet) |
#                "missing" (e.g. external drive unplugged) | "moving"
ARCHIVE_STATE    = "ok"
ARCHIVE_EXPECTED = None        # where a missing archive was last seen
ARCHIVE_LISTENERS = []         # callables run after the archive folder changes

if PACKAGED:
    # Bundled, read-only resources (PyInstaller unpacks/points here).
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", ROOT_DIR))
    DIST_DIR     = RESOURCE_DIR / "dist"
    from modules import archive_location as _archive_location
    CONFIG_DIR   = _archive_location.config_dir()
    _resolved    = _archive_location.resolve()
    ARCHIVE_STATE    = _resolved["state"]
    ARCHIVE_EXPECTED = _resolved["expected"]
    # Without an open archive, nothing may be written anywhere but CONFIG_DIR.
    DATA_DIR     = _resolved["path"] or (CONFIG_DIR / "no-archive")
    # No .env in packaged mode: keys come from the OS credential store.
else:
    load_dotenv(ROOT_DIR / ".env")
    RESOURCE_DIR = ROOT_DIR
    DIST_DIR     = ROOT_DIR / "dist"
    DATA_DIR     = ROOT_DIR / "data"
    CONFIG_DIR   = DATA_DIR


def _archive_paths(data_dir: Path) -> dict:
    data_dir = Path(data_dir)
    return {
        "DATA_DIR":       data_dir,
        "PHOTOS_DIR":     data_dir / "photos" if PACKAGED else ROOT_DIR / "photos",
        "THUMBNAILS_DIR": data_dir / "thumbnails",
        "BACKUP_DIR":     data_dir / "backups",
        "DB_PATH":        data_dir / "provenance.db",
        "LOG_DIR":        data_dir / "logs" if (ARCHIVE_STATE == "ok" or not PACKAGED)
                          else CONFIG_DIR / "logs",
    }


globals().update(_archive_paths(DATA_DIR))


def _ensure_archive_dirs() -> None:
    for d in (DATA_DIR, THUMBNAILS_DIR, LOG_DIR) + ((PHOTOS_DIR,) if PACKAGED else ()):
        Path(d).mkdir(parents=True, exist_ok=True)


def apply_archive(data_dir) -> None:
    """Point the running app at an archive folder (first-run setup, locate,
    or after a move). Every module reads these paths at call time."""
    global ARCHIVE_STATE, ARCHIVE_EXPECTED
    ARCHIVE_STATE = "ok"
    ARCHIVE_EXPECTED = None
    globals().update(_archive_paths(data_dir))
    _ensure_archive_dirs()
    for listener in list(ARCHIVE_LISTENERS):
        try:
            listener()
        except Exception:
            pass


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

# Ensure data directories exist (only when an archive is open: in setup or
# missing state nothing is created outside the config folder)
if ARCHIVE_STATE == "ok":
    _ensure_archive_dirs()
else:
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
