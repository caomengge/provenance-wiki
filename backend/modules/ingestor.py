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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    """Background coordinator: scan, filter, then process photos in a thread pool."""
    global _is_ingesting
    from config import SUPPORTED_EXTS, INGEST_WORKERS
    from modules.db import get_db, document_exists_by_sha256, upsert_entity, get_or_create_tag
    from modules.extractor import extract_from_image, generate_text_embedding
    from modules.thumbnails import ensure_thumbnail

    run_id = _create_run(source_archive)
    try:
        _emit({"type": "start", "run_id": run_id, "message": "Scanning photos directory…"})

        # Collect all supported files
        all_files = [
            p for p in sorted(photos_dir.iterdir())
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
        ]
        total = len(all_files)
        _update_run(run_id, total=total)
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

        # Record requeued files in the run log.
        # We intentionally don't store per-file "skipped" rows — the count is
        # kept on ingest_runs.skipped, and writing N rows per run for an N-file
        # archive bloats the table for very little value.
        _record_files(run_id, [
            (p.name, sha, "requeued", None, existing_id)
            for (p, sha, existing_id) in pending if existing_id is not None
        ])

        skipped = total - len(pending)
        _update_run(run_id, skipped=skipped)
        _emit({
            "type":    "filter",
            "pending": len(pending),
            "skipped": skipped,
            "message": f"{len(pending)} new files to process, {skipped} already in database",
        })

        if not pending:
            _finalize_run(run_id, processed=0, skipped=skipped, errors=0, status="done")
            _emit({"type": "done", "processed": 0, "skipped": skipped, "errors": 0})
            return

        processed = 0
        errors    = 0
        completed = 0
        total_pending = len(pending)
        counter_lock  = threading.Lock()

        def _run_one(item):
            photo_path, sha, existing_id = item
            _emit({
                "type": "processing",
                "file": photo_path.name,
            })
            try:
                doc_id = _process_single(photo_path, sha, api_key, get_db,
                                upsert_entity, get_or_create_tag,
                                extract_from_image, generate_text_embedding,
                                source_archive=source_archive,
                                existing_id=existing_id)
                ensure_thumbnail(photo_path, sha)
                _record_files(run_id, [(photo_path.name, sha, "ok", None, doc_id)])
                return ("ok", photo_path.name, None)
            except Exception as exc:
                logger.exception("Failed to process %s", photo_path.name)
                _record_files(run_id, [(photo_path.name, sha, "err", str(exc), existing_id)])
                return ("err", photo_path.name, str(exc))

        worker_count = max(1, INGEST_WORKERS)
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="ingest") as pool:
            futures = [pool.submit(_run_one, item) for item in pending]
            for fut in as_completed(futures):
                status, name, err = fut.result()
                with counter_lock:
                    completed += 1
                    if status == "ok":
                        processed += 1
                        _emit({
                            "type":      "done_file",
                            "file":      name,
                            "processed": processed,
                            "completed": completed,
                            "total":     total_pending,
                            "percent":   round((completed / total_pending) * 100),
                        })
                    else:
                        errors += 1
                        _emit({
                            "type":    "error",
                            "file":    name,
                            "message": err,
                        })

        _finalize_run(run_id, processed=processed, skipped=skipped,
                      errors=errors, status="done")
        _emit({
            "type":      "done",
            "processed": processed,
            "skipped":   skipped,
            "errors":    errors,
            "message":   f"Ingestion complete: {processed} processed, {skipped} skipped, {errors} errors",
        })

    except Exception as exc:
        logger.exception("Ingestion worker crashed")
        _finalize_run(run_id, status="crashed")
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

    # Trust the LLM's category if it lands on a known canonical value, but
    # always fall back to the substring mapper so junk/typos still resolve.
    from modules.medium_taxonomy import categorize, CATEGORIES
    raw_medium     = data.get("medium")
    llm_category   = (data.get("medium_category") or "").strip().lower()
    medium_category = llm_category if llm_category in CATEGORIES else categorize(raw_medium)

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
                    medium_category     = ?,
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
                    _normalize_date(data.get("date_depicted")),
                    data.get("date_range_start"),
                    data.get("date_range_end"),
                    data.get("location"),
                    raw_medium,
                    medium_category,
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
                    location, medium, medium_category, dimensions, description, language,
                    raw_claude_response, transcription, is_key_evidence, embedding_json, source_archive)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    photo_path.name,
                    sha,
                    data.get("title"),
                    _normalize_date(data.get("date_depicted")),
                    data.get("date_range_start"),
                    data.get("date_range_end"),
                    data.get("location"),
                    raw_medium,
                    medium_category,
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
                return None
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

        # Insert transactions (filtered for quality — see config.TRANSACTION_MIN_SCORE)
        from modules.extractor import filter_transactions
        for txn in filter_transactions(data.get("transactions") or []):
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

    return doc_id


# ── Run-log helpers ───────────────────────────────────────────────────────────

def _create_run(source_archive: str | None) -> int:
    from modules.db import get_db
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO ingest_runs (source_archive, status) VALUES (?, 'running')",
            (source_archive,),
        )
        return cur.lastrowid


def _update_run(run_id: int, **fields) -> None:
    if not fields:
        return
    from modules.db import get_db
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [run_id]
    with get_db() as conn:
        conn.execute(f"UPDATE ingest_runs SET {cols} WHERE id=?", vals)


def _finalize_run(run_id: int, *, processed: int = None, skipped: int = None,
                  errors: int = None, status: str = "done") -> None:
    from modules.db import get_db
    sets = ["finished_at = datetime('now')", "status = ?"]
    vals = [status]
    if processed is not None:
        sets.append("processed = ?"); vals.append(processed)
    if skipped is not None:
        sets.append("skipped = ?"); vals.append(skipped)
    if errors is not None:
        sets.append("errors = ?"); vals.append(errors)
    vals.append(run_id)
    with get_db() as conn:
        conn.execute(f"UPDATE ingest_runs SET {', '.join(sets)} WHERE id=?", vals)


def _record_files(run_id: int, rows: list) -> None:
    """rows: iterable of (filename, sha256, status, error_message, document_id)."""
    if not rows:
        return
    from modules.db import get_db
    with get_db() as conn:
        conn.executemany(
            """INSERT INTO ingest_run_files
               (run_id, filename, sha256, status, error_message, document_id)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(run_id, sha256) DO UPDATE SET
                   filename       = excluded.filename,
                   status         = excluded.status,
                   error_message  = excluded.error_message,
                   document_id    = excluded.document_id""",
            [(run_id, *r) for r in rows],
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_date(value) -> str | None:
    """Convert 'date unknown' (LLM sentinel) to None for DB storage."""
    if isinstance(value, str) and value.strip().lower() == "date unknown":
        return None
    return value


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
