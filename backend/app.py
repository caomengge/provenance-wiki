"""
app.py – Flask application factory for Provenance Archive Wiki.

Registers all API blueprints, applies local-only request checks, serves the React SPA
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """Create and configure the Flask application."""
    from config import DIST_DIR, FLASK_DEBUG

    app = Flask(__name__, static_folder=str(DIST_DIR), static_url_path="")

    # Local-only request checks (Host, Origin, X-Provenance-Wiki header).
    # No CORS: the built UI is same-origin and the Vite dev server proxies /api.
    from modules.local_security import install_local_guard
    install_local_guard(app)

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
    from api.groups          import bp as groups_bp
    from api.transactions    import bp as transactions_bp

    for bp in [docs_bp, entities_bp, search_bp, ingest_bp,
               timeline_bp, network_bp, qa_bp, export_bp, groups_bp, transactions_bp]:
        app.register_blueprint(bp)

    # ── Stats endpoint ────────────────────────────────────────────────────────
    @app.route("/api/stats")
    def stats():
        """Sidebar summary. All counts exclude trashed documents and groups.

        - documents: each logical document — i.e. a standalone (ungrouped)
                     document OR a multi-page group, counted once each.
                     A 5-page group adds 1, not 5.
        - entities:  entities with at least one reference from a non-trashed
                     document or non-trashed group
        - transactions: per-doc + per-group transactions, excluding any whose
                        parent is trashed
        - tags:      tags with at least one reference from a non-trashed
                     document or non-trashed group
        - key_evidence: docs OR groups flagged is_key_evidence (non-trashed)
        """
        from modules.db import get_db
        with get_db() as conn:
            standalone = conn.execute(
                "SELECT COUNT(*) as c FROM documents "
                "WHERE group_id IS NULL AND is_trashed = 0"
            ).fetchone()["c"]

            group_count = conn.execute(
                "SELECT COUNT(*) as c FROM document_groups WHERE is_trashed = 0"
            ).fetchone()["c"]

            doc_count = standalone + group_count

            entity_count = conn.execute("""
                SELECT COUNT(*) AS c FROM entities e
                WHERE EXISTS (
                    SELECT 1 FROM document_entities de
                    JOIN documents d ON d.id = de.document_id
                    WHERE de.entity_id = e.id AND d.is_trashed = 0
                )
                OR EXISTS (
                    SELECT 1 FROM group_entities ge
                    JOIN document_groups g ON g.id = ge.group_id
                    WHERE ge.entity_id = e.id AND g.is_trashed = 0
                )
            """).fetchone()["c"]

            doc_txn = conn.execute(
                "SELECT COUNT(*) AS c FROM transactions t "
                "JOIN documents d ON d.id = t.document_id "
                "WHERE d.is_trashed = 0"
            ).fetchone()["c"]
            grp_txn = conn.execute(
                "SELECT COUNT(*) AS c FROM group_transactions gt "
                "JOIN document_groups g ON g.id = gt.group_id "
                "WHERE g.is_trashed = 0"
            ).fetchone()["c"]
            txn_count = doc_txn + grp_txn

            tag_count = conn.execute("""
                SELECT COUNT(*) AS c FROM tags t
                WHERE EXISTS (
                    SELECT 1 FROM document_tags dt
                    JOIN documents d ON d.id = dt.document_id
                    WHERE dt.tag_id = t.id AND d.is_trashed = 0
                )
                OR EXISTS (
                    SELECT 1 FROM group_tags gt
                    JOIN document_groups g ON g.id = gt.group_id
                    WHERE gt.tag_id = t.id AND g.is_trashed = 0
                )
            """).fetchone()["c"]

            doc_key = conn.execute(
                "SELECT COUNT(*) AS c FROM documents "
                "WHERE is_key_evidence = 1 AND is_trashed = 0 AND group_id IS NULL"
            ).fetchone()["c"]
            grp_key = conn.execute(
                "SELECT COUNT(*) AS c FROM document_groups "
                "WHERE is_key_evidence = 1 AND is_trashed = 0"
            ).fetchone()["c"]
            key_count = doc_key + grp_key

        return jsonify({
            "documents":    doc_count,      # standalone + groups
            "groups":       group_count,    # kept for callers that want the breakdown
            "entities":     entity_count,
            "transactions": txn_count,
            "tags":         tag_count,
            "key_evidence": key_count,
        })

    # ── Health check ──────────────────────────────────────────────────────────
    @app.route("/api/health")
    def health():
        # Booleans only — never return any part of a key.
        from config import ANTHROPIC_API_KEY, VOYAGE_API_KEY
        return jsonify({
            "status": "ok",
            "anthropic_key_set": bool(ANTHROPIC_API_KEY),
            "voyage_key_set": bool(VOYAGE_API_KEY),
            # Deprecated alias of anthropic_key_set, kept for older clients.
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
        # Flask's static route ("/<path:filename>", static_url_path="") wins
        # over serve_spa for paths like /entities, so a reload or bookmarked
        # link to any client-side route 404'd. Serve the SPA for those.
        from flask import request
        index = Path(app.static_folder) / "index.html"
        is_file_request = "." in request.path.rsplit("/", 1)[-1]   # e.g. a missing .js asset
        if (request.method == "GET" and not request.path.startswith("/api/")
                and not is_file_request and index.exists()):
            return send_from_directory(str(Path(app.static_folder)), "index.html")
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
    from modules.local_security import is_loopback_bind
    if not is_loopback_bind(FLASK_HOST):
        logger.warning("FLASK_HOST=%s exposes this archive to other computers on the "
                       "network with no login. Use 127.0.0.1 unless you intend this.",
                       FLASK_HOST)
    logger.info("Starting Provenance Archive Wiki on http://%s:%s", FLASK_HOST, FLASK_PORT)
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True)
