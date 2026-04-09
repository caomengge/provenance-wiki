"""
ingestor.py – Batch photo ingestion pipeline.

Scans the photos/ directory for supported image files, skips already-
processed files (by SHA-256 hash), and processes new files in batches.
Progress is broadcast via a thread-safe queue consumed by the SSE endpoint.

Design goals:
  • Fully resumable: safe to interrupt and restart at any time
  • Idempotent: running twice produces the same result
  • Scalable: handles 2000+ files without refactoring
"""

import hashlib
import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

# Global progress queue consumed by the /api/ingest/progress SSE endpoint
_progress_queue: queue.Queue = queue.Queue()
_ingest_lock   = threading.Lock()
_is_ingesting  = False


# ── Public interface ──────────────────────────────────────────────────────────

def start_ingest(photos_dir: Path, api_key: str, batch_size: int = 10,
                 source_archive: str = None) -> dict:
    """
    Launch the ingestion pipeline in a background thread.
    Returns immediately with {status, message}.
    Only one ingestion run is allowed at a time.
    source_archive is stamped on every new document created in this run.
    """
    global _is_ingesting
    with _ingest_lock:
        if _is_ingesting:
            return {"status": "busy", "message": "Ingestion already running"}
        _is_ingesting = True

    thread = threading.Thread(
        target=_ingest_worker,
        args=(photos_dir, api_key, batch_size, source_archive),
        daemon=True,
        name="ingest-worker",
    )
    thread.start()
    return {"status": "started", "message": "Ingestion pipeline started"}


def progress_stream() -> Generator[str, None, None]:
    """
    SSE generator: yields progress events from the ingest queue.
    Each event is a JSON-serialised dict.
    """
    while True:
        try:
            event = _progress_queue.get(timeout=30)
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") == "done":
                break
        except queue.Empty:
            # Heartbeat to keep connection alive
            yield "data: {\"type\":\"heartbeat\"}\n\n"


def get_ingest_status() -> dict:
    """Return whether ingestion is currently running."""
    return {"running": _is_ingesting}


# ── Worker ────────────────────────────────────────────────────────────────────

def _ingest_worker(photos_dir: Path, api_key: str, batch_size: int, source_archive: str = None):
    """Background thread: scan, filter, and process photos."""
    global _is_ingesting
    from config import SUPPORTED_EXTS
    from modules.db import get_db, document_exists_by_sha256, upsert_entity, get_or_create_tag
    from modules.extractor import extract_from_image, generate_text_embedding

    try:
        _emit({"type": "start", "message": "Scanning photos directory…"})

        # Collect all supported files
        all_files = [
            p for p in sorted(photos_dir.iterdir())
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
        ]
        total = len(all_files)
        _emit({"type": "scan", "total": total, "message": f"Found {total} image files"})

        # Filter out already-processed files.
        # Re-queue any document whose description starts with "Extraction failed"
        # (happens when a bad API key was used on a previous ingest run).
        pending = []
        for p in all_files:
            sha = _sha256(p)
            if not document_exists_by_sha256(sha):
                pending.append((p, sha, None))
            else:
                # Check if it was a failed extraction — if so, re-queue for UPDATE
                with get_db() as conn:
                    row = conn.execute(
                        "SELECT id, description FROM documents WHERE sha256=?", (sha,)
                    ).fetchone()
                    if row and row["description"] and row["description"].startswith("Extraction failed"):
                        pending.append((p, sha, row["id"]))
                        logger.info("Queued for retry (previous extraction failed): %s", p.name)

        skipped = total - len(pending)
        _emit({
            "type":    "filter",
            "pending": len(pending),
            "skipped": skipped,
            "message": f"{len(pending)} new files to process, {skipped} already in database",
        })

        if not pending:
            _emit({"type": "done", "processed": 0, "skipped": skipped, "errors": 0})
            return

        # Process in batches
        processed = 0
        errors    = 0

        for batch_start in range(0, len(pending), batch_size):
            batch = pending[batch_start: batch_start + batch_size]

            for idx, (photo_path, sha, existing_id) in enumerate(batch):
                global_idx = batch_start + idx
                _emit({
                    "type":     "processing",
                    "file":     photo_path.name,
                    "index":    global_idx + 1,
                    "total":    len(pending),
                    "percent":  round((global_idx / len(pending)) * 100),
                })

                try:
                    _process_single(photo_path, sha, api_key, get_db,
                                    upsert_entity, get_or_create_tag,
                                    extract_from_image, generate_text_embedding,
                                    source_archive=source_archive,
                                    existing_id=existing_id)
                    processed += 1
                    _emit({
                        "type":      "done_file",
                        "file":      photo_path.name,
                        "index":     global_idx + 1,
                        "processed": processed,
                    })
                except Exception as exc:
                    errors += 1
                    logger.exception("Failed to process %s", photo_path.name)
                    _emit({
                        "type":    "error",
                        "file":    photo_path.name,
                        "message": str(exc),
                    })

                # Small delay to avoid hammering the API
                time.sleep(0.5)

        _emit({
            "type":      "done",
            "processed": processed,
            "skipped":   skipped,
            "errors":    errors,
            "message":   f"Ingestion complete: {processed} processed, {skipped} skipped, {errors} errors",
        })

    except Exception as exc:
        logger.exception("Ingestion worker crashed")
        _emit({"type": "error", "message": f"Ingestion crashed: {exc}"})
        _emit({"type": "done",  "processed": 0, "skipped": 0, "errors": 1})
    finally:
        with _ingest_lock:
            _is_ingesting = False


def _process_single(photo_path, sha, api_key, get_db, upsert_entity,
                    get_or_create_tag, extract_from_image, generate_text_embedding,
                    source_archive: str = None, existing_id: int = None):
    """Extract data from one photo and write everything to the database."""
    data = extract_from_image(photo_path, api_key)

    # Build embedding from title + description
    embed_text = " ".join(filter(None, [
        data.get("title", ""),
        data.get("description", ""),
        " ".join(data.get("tags", [])),
    ]))
    embedding = generate_text_embedding(embed_text, api_key)

    with get_db() as conn:
        if existing_id:
            # UPDATE existing record in-place (preserves annotation, tags, links,
            # and the user-set is_key_evidence flag across re-extraction).
            conn.execute(
                """UPDATE documents SET
                    title               = ?,
                    date_depicted       = ?,
                    date_range_start    = ?,
                    date_range_end      = ?,
                    location            = ?,
                    medium              = ?,
                    dimensions          = ?,
                    description         = ?,
                    language            = ?,
                    raw_claude_response = ?,
                    transcription       = ?,
                    embedding_json      = ?,
                    source_archive      = COALESCE(source_archive, ?),
                    updated_at          = datetime('now')
                   WHERE id = ?""",
                (
                    data.get("title"),
                    data.get("date_depicted"),
                    data.get("date_range_start"),
                    data.get("date_range_end"),
                    data.get("location"),
                    data.get("medium"),
                    data.get("dimensions"),
                    data.get("description"),
                    data.get("language"),
                    json.dumps(data),
                    data.get("transcription"),
                    json.dumps(embedding) if embedding else None,
                    source_archive or None,
                    existing_id,
                )
            )
            doc_id = existing_id
        else:
            # INSERT new document row
            cur = conn.execute(
                """INSERT OR IGNORE INTO documents
                   (filename, sha256, title, date_depicted, date_range_start, date_range_end,
                    location, medium, dimensions, description, language,
                    raw_claude_response, transcription, is_key_evidence, embedding_json, source_archive)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    photo_path.name,
                    sha,
                    data.get("title"),
                    data.get("date_depicted"),
                    data.get("date_range_start"),
                    data.get("date_range_end"),
                    data.get("location"),
                    data.get("medium"),
                    data.get("dimensions"),
                    data.get("description"),
                    data.get("language"),
                    json.dumps(data),
                    data.get("transcription"),
                    0,  # is_key_evidence — never set automatically; user flags manually
                    json.dumps(embedding) if embedding else None,
                    source_archive or None,
                ),
            )
            if cur.lastrowid == 0:
                # Already exists (race condition guard)
                return
            doc_id = cur.lastrowid

        # Insert entities
        for ent in (data.get("entities") or []):
            if not ent.get("name"):
                continue
            entity_id = upsert_entity(conn, ent["name"], ent.get("type", "unknown"))
            if entity_id:
                conn.execute(
                    """INSERT OR IGNORE INTO document_entities
                       (document_id, entity_id, role, context)
                       VALUES (?,?,?,?)""",
                    (doc_id, entity_id, ent.get("role"), ent.get("context")),
                )

        # Insert transactions
        for txn in (data.get("transactions") or []):
            conn.execute(
                """INSERT INTO transactions
                   (document_id, seller, buyer, date, price, currency,
                    auction_house, lot_number, location, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    doc_id,
                    txn.get("seller"),
                    txn.get("buyer"),
                    txn.get("date"),
                    _to_float(txn.get("price")),
                    txn.get("currency"),
                    txn.get("auction_house"),
                    str(txn.get("lot_number")) if txn.get("lot_number") else None,
                    txn.get("location"),
                    txn.get("notes"),
                ),
            )

        # Insert tags
        for tag_name in (data.get("tags") or []):
            if not tag_name:
                continue
            tag_id = get_or_create_tag(conn, str(tag_name).strip())
            if tag_id:
                conn.execute(
                    "INSERT OR IGNORE INTO document_tags (document_id, tag_id) VALUES (?,?)",
                    (doc_id, tag_id),
                )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_float(value) -> float | None:
    """Safely convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _emit(event: dict):
    """Push an event onto the progress queue (non-blocking)."""
    _progress_queue.put(event)
