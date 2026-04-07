"""
ingest_routes.py – Flask Blueprint for photo ingestion and progress streaming.

Routes:
  POST /api/ingest          – start ingestion pipeline
  GET  /api/ingest/status   – is ingestion running?
  GET  /api/ingest/progress – SSE stream of progress events
"""

from flask import Blueprint, jsonify, request, Response, stream_with_context

bp = Blueprint("ingest", __name__)


@bp.route("/api/ingest", methods=["POST"])
def start_ingest():
    """Start the ingestion pipeline in a background thread."""
    from modules.ingestor import start_ingest as _start
    from config import PHOTOS_DIR, ANTHROPIC_API_KEY, INGEST_BATCH_SIZE

    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY is not set in .env"}), 500

    body = request.get_json(silent=True) or {}
    source_archive = (body.get("source_archive") or "").strip() or None

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
