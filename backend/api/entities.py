"""
entities.py – Flask Blueprint for entity management.

Routes:
  GET    /api/entities               – paginated list with type filter
  GET    /api/entities/:id           – entity detail + linked documents
  PATCH  /api/entities/:id          – update name and/or type
  DELETE /api/entities/:id          – delete entity and its document links
  POST   /api/entities/merge         – merge two entities into one
  GET    /api/entities/:id/documents – documents mentioning this entity
"""

from flask import Blueprint, abort, jsonify, request

bp = Blueprint("entities", __name__)


def _get_deps():
    from config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
    from modules.db import get_db, rows_to_list, row_to_dict, normalize_entity_name
    return DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, get_db, rows_to_list, row_to_dict, normalize_entity_name


# ── List ──────────────────────────────────────────────────────────────────────

@bp.route("/api/entities", methods=["GET"])
def list_entities():
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, get_db, rows_to_list, row_to_dict, _ = _get_deps()

    page      = max(1, int(request.args.get("page", 1)))
    per_page  = min(int(request.args.get("per_page", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
    offset    = (page - 1) * per_page
    type_filter = request.args.get("type")           # person | object | institution
    q         = request.args.get("q", "").strip()    # name search

    allowed_types = {"person", "object", "institution", "unknown"}

    with get_db() as conn:
        wheres, params = [], []

        if type_filter and type_filter in allowed_types:
            wheres.append("e.type = ?")
            params.append(type_filter)
        if q:
            wheres.append("e.normalized_name LIKE ?")
            params.append(f"%{q.lower()}%")

        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""

        total = conn.execute(
            f"SELECT COUNT(*) as cnt FROM entities e {where_sql}", params
        ).fetchone()["cnt"]

        rows = conn.execute(
            f"""SELECT e.id, e.name, e.type, e.normalized_name, e.created_at,
                       COUNT(DISTINCT de.document_id) as doc_count
                FROM entities e
                LEFT JOIN document_entities de ON de.entity_id = e.id
                {where_sql}
                GROUP BY e.id
                ORDER BY doc_count DESC, e.name
                LIMIT ? OFFSET ?""",
            params + [per_page, offset]
        ).fetchall()

    return jsonify({
        "entities": rows_to_list(rows),
        "total":    total,
        "page":     page,
        "per_page": per_page,
    })


# ── Detail ────────────────────────────────────────────────────────────────────

@bp.route("/api/entities/<int:entity_id>", methods=["GET"])
def get_entity(entity_id):
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, get_db, rows_to_list, row_to_dict, _ = _get_deps()

    with get_db() as conn:
        entity = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        if not entity:
            abort(404)

        entity = row_to_dict(entity)

        # Count and sample of documents
        docs = conn.execute(
            """SELECT d.id, d.title, d.filename, d.date_depicted, de.role
               FROM document_entities de
               JOIN documents d ON d.id = de.document_id
               WHERE de.entity_id = ?
               ORDER BY d.date_depicted NULLS LAST
               LIMIT 50""",
            (entity_id,)
        ).fetchall()

        # Co-occurring entities
        co_entities = conn.execute(
            """SELECT e.id, e.name, e.type, COUNT(*) as co_count
               FROM document_entities de1
               JOIN document_entities de2 ON de2.document_id = de1.document_id
                   AND de2.entity_id != de1.entity_id
               JOIN entities e ON e.id = de2.entity_id
               WHERE de1.entity_id = ?
               GROUP BY e.id
               ORDER BY co_count DESC
               LIMIT 20""",
            (entity_id,)
        ).fetchall()

        entity["documents"]    = rows_to_list(docs)
        entity["doc_count"]    = len(entity["documents"])
        entity["co_entities"]  = rows_to_list(co_entities)

    return jsonify(entity)


# ── Documents for entity (paginated) ─────────────────────────────────────────

@bp.route("/api/entities/<int:entity_id>/documents", methods=["GET"])
def entity_documents(entity_id):
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, get_db, rows_to_list, row_to_dict, _ = _get_deps()

    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(int(request.args.get("per_page", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
    offset   = (page - 1) * per_page

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM entities WHERE id=?", (entity_id,)).fetchone():
            abort(404)

        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM document_entities WHERE entity_id=?",
            (entity_id,)
        ).fetchone()["cnt"]

        rows = conn.execute(
            """SELECT d.id, d.title, d.filename, d.date_depicted, d.date_range_start,
                      d.is_key_evidence, d.medium, de.role, de.context
               FROM document_entities de
               JOIN documents d ON d.id = de.document_id
               WHERE de.entity_id = ?
               ORDER BY d.date_depicted NULLS LAST, d.created_at
               LIMIT ? OFFSET ?""",
            (entity_id, per_page, offset)
        ).fetchall()

    return jsonify({
        "documents": rows_to_list(rows),
        "total":     total,
        "page":      page,
        "per_page":  per_page,
    })


# ── Update ────────────────────────────────────────────────────────────────────

ALLOWED_TYPES = {"person", "object", "institution", "unknown"}

@bp.route("/api/entities/<int:entity_id>", methods=["PATCH"])
def update_entity(entity_id):
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, get_db, rows_to_list, row_to_dict, normalize_entity_name = _get_deps()

    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip() or None
    entity_type = data.get("type", "").strip() or None

    if name is None and entity_type is None:
        return jsonify({"error": "name or type is required"}), 400
    if entity_type and entity_type not in ALLOWED_TYPES:
        return jsonify({"error": f"type must be one of {sorted(ALLOWED_TYPES)}"}), 400

    with get_db() as conn:
        row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        if not row:
            abort(404)
        new_name = name if name else row["name"]
        new_type = entity_type if entity_type else row["type"]
        norm = normalize_entity_name(new_name)
        conn.execute(
            "UPDATE entities SET name=?, normalized_name=?, type=? WHERE id=?",
            (new_name, norm, new_type, entity_id)
        )

    return jsonify({"ok": True})


# ── Delete ────────────────────────────────────────────────────────────────────

@bp.route("/api/entities/<int:entity_id>", methods=["DELETE"])
def delete_entity(entity_id):
    _, _, get_db, _, _, _ = _get_deps()

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM entities WHERE id=?", (entity_id,)).fetchone():
            abort(404)
        conn.execute("DELETE FROM document_entities WHERE entity_id=?", (entity_id,))
        conn.execute("DELETE FROM entities WHERE id=?", (entity_id,))

    return jsonify({"ok": True})


# ── Merge ─────────────────────────────────────────────────────────────────────

@bp.route("/api/entities/merge", methods=["POST"])
def merge_entities():
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, get_db, rows_to_list, row_to_dict, _ = _get_deps()

    data       = request.get_json(silent=True) or {}
    keep_id    = data.get("keep_id")
    discard_id = data.get("discard_id")

    if not keep_id or not discard_id:
        return jsonify({"error": "keep_id and discard_id are required"}), 400
    if keep_id == discard_id:
        return jsonify({"error": "Cannot merge an entity with itself"}), 400

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM entities WHERE id=?", (keep_id,)).fetchone():
            return jsonify({"error": f"Entity {keep_id} not found"}), 404
        if not conn.execute("SELECT 1 FROM entities WHERE id=?", (discard_id,)).fetchone():
            return jsonify({"error": f"Entity {discard_id} not found"}), 404

        # Re-point all document_entities rows
        conn.execute(
            """UPDATE OR IGNORE document_entities SET entity_id=? WHERE entity_id=?""",
            (keep_id, discard_id)
        )
        # Delete any that caused UNIQUE conflicts (already linked to keep_id)
        conn.execute(
            "DELETE FROM document_entities WHERE entity_id=?", (discard_id,)
        )
        conn.execute("DELETE FROM entities WHERE id=?", (discard_id,))

    return jsonify({"ok": True, "merged_into": keep_id})
