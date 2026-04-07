"""
search_routes.py – Flask Blueprint for full-text and semantic search.

Routes:
  GET /api/search   – keyword or semantic search with pagination
  GET /api/tags     – list all tags
  POST /api/tags    – create a tag
  PATCH /api/tags/:id – update tag name/color
  DELETE /api/tags/:id – delete a tag
  POST /api/documents/:id/tags  – add tag to document
  DELETE /api/documents/:id/tags/:tag_id – remove tag from document
"""

from flask import Blueprint, abort, jsonify, request

bp = Blueprint("search", __name__)


# ── Search ────────────────────────────────────────────────────────────────────

@bp.route("/api/search", methods=["GET"])
def search():
    from modules.search import search_documents
    from config import DEFAULT_PAGE_SIZE

    q              = request.args.get("q", "").strip()
    mode           = request.args.get("mode", "keyword")
    page           = max(1, int(request.args.get("page", 1)))
    per_page       = int(request.args.get("per_page", DEFAULT_PAGE_SIZE))
    tag_ids        = [int(t) for t in request.args.getlist("tag_id") if t.isdigit()]
    entity_id      = request.args.get("entity_id", type=int)
    source_archive = request.args.get("source_archive") or None

    if mode not in ("keyword", "semantic"):
        mode = "keyword"

    result = search_documents(
        query=q,
        mode=mode,
        page=page,
        per_page=per_page,
        tag_ids=tag_ids or None,
        entity_id=entity_id,
        source_archive=source_archive,
    )
    return jsonify(result)


# ── Tags CRUD ─────────────────────────────────────────────────────────────────

@bp.route("/api/tags", methods=["GET"])
def list_tags():
    from modules.db import get_db, rows_to_list

    with get_db() as conn:
        rows = conn.execute(
            """SELECT t.id, t.name, t.color, t.created_at,
                      COUNT(dt.document_id) as doc_count
               FROM tags t
               LEFT JOIN document_tags dt ON dt.tag_id = t.id
               GROUP BY t.id
               ORDER BY doc_count DESC, t.name"""
        ).fetchall()

    return jsonify({"tags": rows_to_list(rows)})


@bp.route("/api/tags", methods=["POST"])
def create_tag():
    from modules.db import get_db, get_or_create_tag

    data  = request.get_json(silent=True) or {}
    name  = data.get("name", "").strip()
    color = data.get("color", "#c9a84c").strip()

    if not name:
        return jsonify({"error": "name is required"}), 400

    with get_db() as conn:
        tag_id = get_or_create_tag(conn, name, color)
        tag    = conn.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()

    from modules.db import row_to_dict
    return jsonify(row_to_dict(tag)), 201


@bp.route("/api/tags/<int:tag_id>", methods=["PATCH"])
def update_tag(tag_id):
    from modules.db import get_db

    data  = request.get_json(silent=True) or {}
    name  = data.get("name", "").strip()
    color = data.get("color", "").strip()

    if not name and not color:
        return jsonify({"error": "name or color required"}), 400

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM tags WHERE id=?", (tag_id,)).fetchone():
            abort(404)
        if name:
            conn.execute("UPDATE tags SET name=? WHERE id=?", (name, tag_id))
        if color:
            conn.execute("UPDATE tags SET color=? WHERE id=?", (color, tag_id))

    return jsonify({"ok": True})


@bp.route("/api/tags/<int:tag_id>", methods=["DELETE"])
def delete_tag(tag_id):
    from modules.db import get_db

    with get_db() as conn:
        cur = conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
        if cur.rowcount == 0:
            abort(404)

    return jsonify({"ok": True})


# ── Document tag management ───────────────────────────────────────────────────

@bp.route("/api/documents/<int:doc_id>/tags", methods=["POST"])
def add_doc_tag(doc_id):
    from modules.db import get_db, get_or_create_tag

    data   = request.get_json(silent=True) or {}
    tag_id = data.get("tag_id")
    name   = data.get("name", "").strip()
    color  = data.get("color", "#c9a84c")

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone():
            abort(404)

        if not tag_id and name:
            tag_id = get_or_create_tag(conn, name, color)
        if not tag_id:
            return jsonify({"error": "tag_id or name required"}), 400

        try:
            conn.execute(
                "INSERT OR IGNORE INTO document_tags (document_id, tag_id) VALUES (?,?)",
                (doc_id, tag_id)
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True, "tag_id": tag_id}), 201


@bp.route("/api/documents/<int:doc_id>/tags/<int:tag_id>", methods=["DELETE"])
def remove_doc_tag(doc_id, tag_id):
    from modules.db import get_db

    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM document_tags WHERE document_id=? AND tag_id=?",
            (doc_id, tag_id)
        )
        if cur.rowcount == 0:
            abort(404)

    return jsonify({"ok": True})
