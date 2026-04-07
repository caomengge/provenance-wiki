"""
app.py – Flask application factory for Provenance Archive Wiki.

Registers all API blueprints, configures CORS, serves the React SPA
from /dist, and initialises the database on first run.

Usage:
    python backend/app.py        (development)
    gunicorn backend.app:app     (production-like)
"""

import logging
import os
import sys
from pathlib import Path

# Allow imports from backend/ directory
sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, send_from_directory, jsonify
from flask_cors import CORS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """Create and configure the Flask application."""
    from config import DIST_DIR, FLASK_DEBUG

    app = Flask(__name__, static_folder=str(DIST_DIR), static_url_path="")

    # Allow cross-origin requests from the Vite dev server during development
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # ── Initialise database ───────────────────────────────────────────────────
    from modules.db import init_db
    try:
        init_db()
        logger.info("Database initialised")
    except Exception as exc:
        logger.error("Failed to initialise database: %s", exc)

    # ── Register blueprints ───────────────────────────────────────────────────
    from api.documents      import bp as docs_bp
    from api.entities       import bp as entities_bp
    from api.search_routes  import bp as search_bp
    from api.ingest_routes  import bp as ingest_bp
    from api.timeline_routes import bp as timeline_bp
    from api.network_routes  import bp as network_bp
    from api.qa_routes       import bp as qa_bp
    from api.export_routes   import bp as export_bp

    for bp in [docs_bp, entities_bp, search_bp, ingest_bp,
               timeline_bp, network_bp, qa_bp, export_bp]:
        app.register_blueprint(bp)

    # ── Stats endpoint ────────────────────────────────────────────────────────
    @app.route("/api/stats")
    def stats():
        from modules.db import get_db
        with get_db() as conn:
            doc_count    = conn.execute("SELECT COUNT(*) as c FROM documents").fetchone()["c"]
            entity_count = conn.execute("SELECT COUNT(*) as c FROM entities").fetchone()["c"]
            txn_count    = conn.execute("SELECT COUNT(*) as c FROM transactions").fetchone()["c"]
            tag_count    = conn.execute("SELECT COUNT(*) as c FROM tags").fetchone()["c"]
            key_count    = conn.execute("SELECT COUNT(*) as c FROM documents WHERE is_key_evidence=1").fetchone()["c"]
        return jsonify({
            "documents":  doc_count,
            "entities":   entity_count,
            "transactions": txn_count,
            "tags":       tag_count,
            "key_evidence": key_count,
        })

    # ── Health check ──────────────────────────────────────────────────────────
    @app.route("/api/health")
    def health():
        from config import ANTHROPIC_API_KEY
        return jsonify({
            "status": "ok",
            "api_key_set": bool(ANTHROPIC_API_KEY),
        })

    # ── React SPA catch-all ───────────────────────────────────────────────────
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_spa(path):
        dist = Path(app.static_folder)
        target = dist / path
        if path and target.exists():
            return send_from_directory(str(dist), path)
        index = dist / "index.html"
        if index.exists():
            return send_from_directory(str(dist), "index.html")
        return jsonify({"error": "Frontend not built yet. Run: cd frontend && npm run build"}), 404

    # ── Error handlers ────────────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(exc):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(exc):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(500)
    def internal_error(exc):
        logger.exception("Internal server error")
        return jsonify({"error": "Internal server error"}), 500

    return app


# ── Entry point ───────────────────────────────────────────────────────────────

app = create_app()

if __name__ == "__main__":
    from config import FLASK_HOST, FLASK_PORT, FLASK_DEBUG
    logger.info("Starting Provenance Archive Wiki on http://%s:%s", FLASK_HOST, FLASK_PORT)
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True)
