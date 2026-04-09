"""
documents.py – Flask Blueprint for document CRUD and image serving.

Routes:
  GET    /api/documents              – paginated list with filters
  GET    /api/documents/:id          – full document detail
  GET    /api/documents/:id/image    – serve the source photo
  PATCH  /api/documents/:id         – update editable fields
  DELETE /api/documents/:id         – permanently delete record + photo file
  GET    /api/documents/:id/entities – entities linked to this document
  GET    /api/documents/:id/transactions – transactions for this document
  GET    /api/documents/:id/links    – linked documents
  POST   /api/documents/:id/links    – create a link between two documents
  DELETE /api/documents/:id/links/:link_id – remove a document link
"""

import mimetypes
import os
from pathlib import Path

from flask import Blueprint, abort, jsonify, request, send_file

bp = Blueprint("documents", __name__)


def _get_deps():
    from config import PHOTOS_DIR, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
    from modules.db import get_db, rows_to_list, row_to_dict
    return PHOTOS_DIR, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, get_db, rows_to_list, row_to_dict


# ── List / filter ─────────────────────────────────────────────────────────────

@bp.route("/api/documents", methods=["GET"])
def list_documents():
    PHOTOS_DIR, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, get_db, rows_to_list, row_to_dict = _get_deps()

    page         = max(1, int(request.args.get("page", 1)))
    per_page     = min(int(request.args.get("per_page", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
    offset       = (page - 1) * per_page
    key_only     = request.args.get("key_evidence", "").lower() == "true"
    show_trashed  = request.args.get("show_trashed", "").lower() == "true"
    hide_grouped  = request.args.get("hide_grouped", "1") != "0"
    tag_id       = request.args.get("tag_id", type=int)
    entity_id    = request.args.get("entity_id", type=int)
    date_from    = request.args.get("date_from")
    date_to      = request.args.get("date_to")
    sort            = request.args.get("sort", "created_at")
    order           = "DESC" if request.args.get("order", "desc").lower() == "desc" else "ASC"
    source_archive  = request.args.get("source_archive")

    # Whitelist sort columns
    allowed_sort = {"created_at", "date_depicted", "title", "updated_at"}
    if sort not in allowed_sort:
        sort = "created_at"

    with get_db() as conn:
        trash_filter = "d.is_trashed = 1" if show_trashed else "d.is_trashed = 0"
        joins, wheres, params = [], [trash_filter], []

        if hide_grouped:
            wheres.append("d.group_id IS NULL")
        if key_only:
            wheres.append("d.is_key_evidence = 1")
        if tag_id:
            joins.append("JOIN document_tags dt ON dt.document_id = d.id AND dt.tag_id = ?")
            params.insert(0, tag_id)
        if entity_id:
            joins.append("JOIN document_entities de ON de.document_id = d.id AND de.entity_id = ?")
            params.insert(0, entity_id)
        if date_from:
            wheres.append("COALESCE(d.date_depicted, d.date_range_start) >= ?")
            params.append(date_from)
        if date_to:
            wheres.append("COALESCE(d.date_depicted, d.date_range_start) <= ?")
            params.append(date_to)
        if source_archive:
            if source_archive == "__none__":
                wheres.append("(d.source_archive IS NULL OR d.source_archive = '')")
            else:
                wheres.append("d.source_archive = ?")
                params.append(source_archive)

        join_sql  = " ".join(joins)
        where_sql = "WHERE " + " AND ".join(wheres)

        # date_depicted can be NULL, so fall back to date_range_start and push
        # empty values to the end regardless of sort direction.
        if sort == "date_depicted":
            order_sql = (
                f"ORDER BY COALESCE(d.date_depicted, d.date_range_start) IS NULL, "
                f"COALESCE(d.date_depicted, d.date_range_start) {order}"
            )
        elif sort == "title":
            order_sql = f"ORDER BY d.title IS NULL, d.title COLLATE NOCASE {order}"
        else:
            order_sql = f"ORDER BY d.{sort} {order}"

        total = conn.execute(
            f"SELECT COUNT(DISTINCT d.id) as cnt FROM documents d {join_sql} {where_sql}",
            params
        ).fetchone()["cnt"]

        rows = conn.execute(
            f"""SELECT DISTINCT d.id, d.filename, d.title, d.date_depicted,
                       d.date_range_start, d.date_range_end, d.location,
                       d.medium, d.is_key_evidence, d.annotation,
                       d.source_archive, d.created_at, d.updated_at
                FROM documents d {join_sql} {where_sql}
                {order_sql}
                LIMIT ? OFFSET ?""",
            params + [per_page, offset]
        ).fetchall()

    return jsonify({
        "documents": rows_to_list(rows),
        "total":     total,
        "page":      page,
        "per_page":  per_page,
    })


# ── Single document ───────────────────────────────────────────────────────────

@bp.route("/api/documents/<int:doc_id>", methods=["GET"])
def get_document(doc_id):
    PHOTOS_DIR, _, _, get_db, rows_to_list, row_to_dict = _get_deps()

    with get_db() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            abort(404)

        doc = row_to_dict(doc)
        doc.pop("embedding_json", None)   # strip internal field

        # Enrich with related data
        doc["entities"] = rows_to_list(conn.execute(
            """SELECT e.id, e.name, e.type, de.role, de.context
               FROM document_entities de JOIN entities e ON e.id = de.entity_id
               WHERE de.document_id = ?""", (doc_id,)
        ).fetchall())

        doc["transactions"] = rows_to_list(conn.execute(
            "SELECT * FROM transactions WHERE document_id = ? ORDER BY date",
            (doc_id,)
        ).fetchall())

        doc["tags"] = rows_to_list(conn.execute(
            """SELECT t.id, t.name, t.color FROM document_tags dt
               JOIN tags t ON t.id = dt.tag_id WHERE dt.document_id = ?""",
            (doc_id,)
        ).fetchall())

        doc["links"] = rows_to_list(conn.execute(
            """SELECT dl.id, dl.source_id, dl.target_id, dl.relationship_type, dl.notes,
                      src.title as source_title, tgt.title as target_title
               FROM document_links dl
               JOIN documents src ON src.id = dl.source_id
               JOIN documents tgt ON tgt.id = dl.target_id
               WHERE dl.source_id = ? OR dl.target_id = ?""",
            (doc_id, doc_id)
        ).fetchall())

    return jsonify(doc)


# ── Image serving ─────────────────────────────────────────────────────────────

@bp.route("/api/documents/<int:doc_id>/image", methods=["GET"])
def get_document_image(doc_id):
    PHOTOS_DIR, _, _, get_db, _, row_to_dict = _get_deps()

    with get_db() as conn:
        doc = conn.execute("SELECT filename FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not doc:
        abort(404)

    image_path = PHOTOS_DIR / doc["filename"]
    if not image_path.exists():
        abort(404)

    mime, _ = mimetypes.guess_type(str(image_path))
    return send_file(str(image_path), mimetype=mime or "image/jpeg")


# ── Update ────────────────────────────────────────────────────────────────────

@bp.route("/api/documents/<int:doc_id>", methods=["PATCH"])
def update_document(doc_id):
    PHOTOS_DIR, _, _, get_db, _, row_to_dict = _get_deps()

    data = request.get_json(silent=True) or {}
    allowed = {"annotation", "is_key_evidence", "is_trashed", "title", "date_depicted",
               "date_range_start", "date_range_end", "location", "medium",
               "description", "language", "dimensions", "source_archive"}
    updates = {k: v for k, v in data.items() if k in allowed}

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    with get_db() as conn:
        exists = conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not exists:
            abort(404)
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE documents SET {set_clause}, updated_at=datetime('now') WHERE id=?",
            list(updates.values()) + [doc_id]
        )

    return jsonify({"ok": True, "updated": list(updates.keys())})


# ── Document links ────────────────────────────────────────────────────────────

@bp.route("/api/documents/<int:doc_id>/links", methods=["POST"])
def create_link(doc_id):
    PHOTOS_DIR, _, _, get_db, _, _ = _get_deps()
    data = request.get_json(silent=True) or {}
    target_id         = data.get("target_id")
    relationship_type = data.get("relationship_type", "related")
    notes             = data.get("notes")

    if not target_id:
        return jsonify({"error": "target_id is required"}), 400

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone():
            abort(404)
        if not conn.execute("SELECT 1 FROM documents WHERE id=?", (target_id,)).fetchone():
            return jsonify({"error": f"Document {target_id} not found"}), 404

        try:
            cur = conn.execute(
                """INSERT INTO document_links (source_id, target_id, relationship_type, notes)
                   VALUES (?,?,?,?)""",
                (doc_id, target_id, relationship_type, notes)
            )
            link_id = cur.lastrowid
        except Exception as exc:
            if "UNIQUE" in str(exc):
                return jsonify({"error": "Link already exists"}), 409
            raise

    return jsonify({"ok": True, "link_id": link_id}), 201


# ── Hard delete ───────────────────────────────────────────────────────────────

@bp.route("/api/documents/<int:doc_id>", methods=["DELETE"])
def delete_document(doc_id):
    PHOTOS_DIR, _, _, get_db, _, row_to_dict = _get_deps()

    with get_db() as conn:
        doc = conn.execute(
            "SELECT filename FROM documents WHERE id=?", (doc_id,)
        ).fetchone()
        if not doc:
            abort(404)

        filename = doc["filename"]

        # Delete DB record — CASCADE removes document_entities, transactions, links, document_tags
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))

        # Clean up entities and tags that are no longer referenced by any
        # document or group (orphans from this deletion).
        conn.execute("""
            DELETE FROM entities
            WHERE id NOT IN (SELECT entity_id FROM document_entities)
              AND id NOT IN (SELECT entity_id FROM group_entities)
        """)
        conn.execute("""
            DELETE FROM tags
            WHERE id NOT IN (SELECT tag_id FROM document_tags)
              AND id NOT IN (SELECT tag_id FROM group_tags)
        """)

    # Delete photo from disk (best-effort; non-fatal if already missing)
    photo_path = PHOTOS_DIR / filename
    try:
        photo_path.unlink(missing_ok=True)
    except Exception:
        pass

    return jsonify({"ok": True, "deleted": doc_id})


# ── Document–entity associations ──────────────────────────────────────────────

@bp.route("/api/documents/<int:doc_id>/entities", methods=["POST"])
def add_document_entity(doc_id):
    PHOTOS_DIR, _, _, get_db, _, row_to_dict = _get_deps()
    from modules.db import upsert_entity

    data  = request.get_json(silent=True) or {}
    name  = data.get("name", "").strip()
    type_ = data.get("type", "unknown")
    role  = data.get("role", "").strip() or None

    if not name:
        return jsonify({"error": "name is required"}), 400

    allowed_types = {"person", "object", "institution", "unknown"}
    if type_ not in allowed_types:
        type_ = "unknown"

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone():
            abort(404)

        entity_id = upsert_entity(conn, name, type_)
        if not entity_id:
            return jsonify({"error": "Failed to create entity"}), 500

        conn.execute(
            "INSERT OR IGNORE INTO document_entities (document_id, entity_id, role) VALUES (?,?,?)",
            (doc_id, entity_id, role),
        )

        entity = conn.execute(
            "SELECT id, name, type FROM entities WHERE id=?", (entity_id,)
        ).fetchone()

    return jsonify({"ok": True, "entity": row_to_dict(entity), "role": role}), 201


@bp.route("/api/documents/<int:doc_id>/entities/<int:entity_id>", methods=["DELETE"])
def remove_document_entity(doc_id, entity_id):
    PHOTOS_DIR, _, _, get_db, _, _ = _get_deps()

    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM document_entities WHERE document_id=? AND entity_id=?",
            (doc_id, entity_id),
        )
        if cur.rowcount == 0:
            abort(404)

    return jsonify({"ok": True})


@bp.route("/api/archives", methods=["GET"])
def list_archives():
    """Return all distinct source_archive values (from documents and groups)
    for autocomplete and filter dropdowns."""
    PHOTOS_DIR, _, _, get_db, _, _ = _get_deps()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT source_archive FROM (
                   SELECT source_archive FROM documents
                   WHERE source_archive IS NOT NULL AND source_archive != ''
                   UNION
                   SELECT source_archive FROM document_groups
                   WHERE source_archive IS NOT NULL AND source_archive != ''
               )
               ORDER BY source_archive COLLATE NOCASE"""
        ).fetchall()
    return jsonify({"archives": [r["source_archive"] for r in rows]})


@bp.route("/api/documents/<int:doc_id>/wipe", methods=["POST"])
def wipe_document_metadata(doc_id):
    """
    Clear all Claude-extracted metadata from a document in-place.
    Preserves: photo file, annotation, tags, document links.
    Clears: title, date, location, medium, dimensions, description,
            language, transcription, raw_claude_response, key_evidence,
            all entities, all transactions.
    Sets description to trigger re-extraction on next ingest run.
    """
    PHOTOS_DIR, _, _, get_db, _, _ = _get_deps()

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone():
            abort(404)

        # Clear all extracted fields; keep annotation, tags, links untouched
        conn.execute(
            """UPDATE documents SET
                title                = filename,
                date_depicted        = NULL,
                date_range_start     = NULL,
                date_range_end       = NULL,
                location             = NULL,
                medium               = NULL,
                dimensions           = NULL,
                description          = 'Extraction failed: metadata wiped by user',
                language             = NULL,
                transcription        = NULL,
                raw_claude_response  = NULL,
                is_key_evidence      = 0,
                updated_at           = datetime('now')
               WHERE id = ?""",
            (doc_id,)
        )
        conn.execute("DELETE FROM document_entities WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM transactions       WHERE document_id = ?", (doc_id,))

    return jsonify({"ok": True})


@bp.route("/api/documents/<int:doc_id>/rotate", methods=["POST"])
def rotate_document_image(doc_id):
    """Rotate the source photo 90° clockwise or counter-clockwise and update sha256."""
    from PIL import Image as PILImage
    import hashlib

    PHOTOS_DIR, _, _, get_db, _, row_to_dict = _get_deps()

    data      = request.get_json(silent=True) or {}
    direction = data.get("direction", "cw")   # "cw" or "ccw"
    degrees   = -90 if direction == "cw" else 90   # PIL rotates counter-clockwise

    with get_db() as conn:
        doc = conn.execute(
            "SELECT filename FROM documents WHERE id=?", (doc_id,)
        ).fetchone()
        if not doc:
            abort(404)

    image_path = PHOTOS_DIR / doc["filename"]
    if not image_path.exists():
        abort(404)

    with PILImage.open(image_path) as img:
        rotated = img.rotate(degrees, expand=True)
        rotated.save(str(image_path))

    # Recompute sha256 for the modified file
    h = hashlib.sha256()
    with image_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    new_sha = h.hexdigest()

    with get_db() as conn:
        conn.execute(
            "UPDATE documents SET sha256=?, updated_at=datetime('now') WHERE id=?",
            (new_sha, doc_id)
        )

    return jsonify({"ok": True})


@bp.route("/api/documents/<int:doc_id>/links/<int:link_id>", methods=["DELETE"])
def delete_link(doc_id, link_id):
    PHOTOS_DIR, _, _, get_db, _, _ = _get_deps()

    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM document_links WHERE id=? AND (source_id=? OR target_id=?)",
            (link_id, doc_id, doc_id)
        )
        if cur.rowcount == 0:
            abort(404)

    return jsonify({"ok": True})
