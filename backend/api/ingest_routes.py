"""
ingest_routes.py – Flask Blueprint for photo ingestion and progress streaming.

Routes:
  POST /api/ingest          – start ingestion pipeline
  GET  /api/ingest/status   – is ingestion running?
  GET  /api/ingest/progress – SSE stream of progress events
"""

from flask import Blueprint, jsonify, request, Response, stream_with_context

from modules.db import get_db, rows_to_list

bp = Blueprint("ingest", __name__)


@bp.route("/api/ingest", methods=["POST"])
def start_ingest():
    """Start the ingestion pipeline in a background thread."""
    from modules.ingestor import start_ingest as _start
    from config import PHOTOS_DIR, ANTHROPIC_API_KEY, INGEST_BATCH_SIZE

    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY is not set in .env"}), 500

    body = request.get_json(silent=True) or {}
    source_archive = (body.get("source_archive") or "").strip()
    if not source_archive:
        return jsonify({"error": "source_archive is required"}), 400

    result = _start(PHOTOS_DIR, ANTHROPIC_API_KEY, INGEST_BATCH_SIZE,
                    source_archive=source_archive)
    status_code = 202 if result["status"] == "started" else 200
    return jsonify(result), status_code


@bp.route("/api/ingest/status", methods=["GET"])
def ingest_status():
    """Return whether the ingestion pipeline is currently running."""
    from modules.ingestor import get_ingest_status
    return jsonify(get_ingest_status())


@bp.route("/api/ingest/progress", methods=["GET"])
def ingest_progress():
    """
    Server-Sent Events stream.
    The client should connect and listen for JSON progress events.
    Connection closes when a {type: 'done'} event is received.
    """
    from modules.ingestor import progress_stream

    def generate():
        yield "retry: 3000\n\n"   # tell client to retry after 3s on disconnect
        for event in progress_stream():
            yield event

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",    # disable Nginx buffering
        }
    )


@bp.route("/api/ingest/runs", methods=["GET"])
def list_runs():
    """Return recent ingestion runs, newest first."""
    limit = min(int(request.args.get("limit", 50)), 200)
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, started_at, finished_at, source_archive,
                      total, processed, skipped, errors, status
               FROM ingest_runs
               ORDER BY id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return jsonify({"runs": rows_to_list(rows)})


@bp.route("/api/ingest/runs/<int:run_id>", methods=["GET"])
def get_run(run_id):
    """Return summary for a single run plus per-status counts."""
    with get_db() as conn:
        run = conn.execute(
            """SELECT id, started_at, finished_at, source_archive,
                      total, processed, skipped, errors, status
               FROM ingest_runs WHERE id=?""",
            (run_id,),
        ).fetchone()
        if not run:
            return jsonify({"error": "Run not found"}), 404
        counts = conn.execute(
            "SELECT status, COUNT(*) AS n FROM ingest_run_files WHERE run_id=? GROUP BY status",
            (run_id,),
        ).fetchall()
    return jsonify({
        "run":    dict(run),
        "counts": {r["status"]: r["n"] for r in counts},
    })


@bp.route("/api/ingest/runs/<int:run_id>/files", methods=["GET"])
def get_run_files(run_id):
    """Return files recorded for a run, optionally filtered by status."""
    status = request.args.get("status")  # 'ok' | 'err' | 'skipped' | 'requeued'
    limit  = min(int(request.args.get("limit", 500)), 2000)
    offset = int(request.args.get("offset", 0))

    sql = """SELECT f.filename, f.sha256, f.status, f.error_message, f.document_id,
                    d.title AS document_title
             FROM ingest_run_files f
             LEFT JOIN documents d ON d.id = f.document_id
             WHERE f.run_id = ?"""
    params = [run_id]
    if status:
        sql += " AND f.status = ?"
        params.append(status)
    sql += " ORDER BY f.filename LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return jsonify({"files": rows_to_list(rows)})


@bp.route("/api/ingest/runs/<int:run_id>/groups", methods=["GET"])
def get_run_groups(run_id):
    """Return groups whose member documents were created in this run."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT g.id, g.title, g.created_at,
                      COUNT(DISTINCT d.id) AS pages_in_group,
                      COUNT(DISTINCT f.sha256) AS pages_from_run
               FROM ingest_run_files f
               JOIN documents d ON d.id = f.document_id
               JOIN document_groups g ON g.id = d.group_id
               WHERE f.run_id = ? AND f.status = 'ok'
               GROUP BY g.id
               ORDER BY g.created_at DESC""",
            (run_id,),
        ).fetchall()
    return jsonify({"groups": rows_to_list(rows)})


# ── Audit log ────────────────────────────────────────────────────────────────

@bp.route("/api/audit", methods=["GET"])
def get_audit():
    """Return audit events filtered by entity_type+entity_id or run_id."""
    entity_type = request.args.get("entity_type")
    entity_id   = request.args.get("entity_id", type=int)
    run_id      = request.args.get("run_id", type=int)
    limit       = min(int(request.args.get("limit", 200)), 1000)

    wheres, params = [], []
    if entity_type:
        wheres.append("entity_type = ?")
        params.append(entity_type)
    if entity_id is not None:
        wheres.append("entity_id = ?")
        params.append(entity_id)
    if run_id is not None:
        wheres.append("run_id = ?")
        params.append(run_id)
    if not wheres:
        return jsonify({"error": "Specify entity_type+entity_id or run_id"}), 400

    sql = f"""SELECT id, ts, actor, entity_type, entity_id, action, field,
                     old_value, new_value, run_id
              FROM audit_events
              WHERE {' AND '.join(wheres)}
              ORDER BY id DESC
              LIMIT ?"""
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return jsonify({"events": rows_to_list(rows)})
