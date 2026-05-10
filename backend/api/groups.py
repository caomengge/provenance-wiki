"""
groups.py – Flask Blueprint for multi-page document groups.

Routes:
  POST   /api/groups                  – create group from selected doc IDs + extract
  GET    /api/groups                  – paginated list of groups
  GET    /api/groups/:id              – full group detail + pages
  PATCH  /api/groups/:id              – update editable fields
  DELETE /api/groups/:id              – delete group (pages revert to standalone)
  PATCH  /api/groups/:id/pages        – reorder pages
  POST   /api/groups/:id/re-extract   – re-run extraction with current page order
"""

import json
import logging

from flask import Blueprint, abort, jsonify, request
from modules.ingestor import _normalize_date

bp = Blueprint("groups", __name__)
logger = logging.getLogger(__name__)


def _get_deps():
    from config import PHOTOS_DIR, ANTHROPIC_API_KEY, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, MULTIPAGE_MAX_PAGES
    from modules.db import get_db, rows_to_list, row_to_dict, upsert_entity, get_or_create_tag
    return PHOTOS_DIR, ANTHROPIC_API_KEY, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, MULTIPAGE_MAX_PAGES, get_db, rows_to_list, row_to_dict, upsert_entity, get_or_create_tag


# ── Create group ──────────────────────────────────────────────────────────────

@bp.route("/api/groups", methods=["POST"])
def create_group():
    """
    Body: { "doc_ids": [1, 2, 3], "title": "optional", "vision": false }

    Default behaviour merges the already-extracted data of the child pages and
    runs a tiny text-only Claude call to synthesize a unified title and
    description — no images uploaded, ~1-2s instead of 30-60s.

    Pass {"vision": true} to force a full vision re-extraction (slower, more
    accurate when individual page extractions were poor). The user can also
    trigger this later via the per-group "Re-extract" button.
    """
    PHOTOS_DIR, API_KEY, _, _, MULTIPAGE_MAX_PAGES, get_db, rows_to_list, row_to_dict, upsert_entity, get_or_create_tag = _get_deps()
    from modules.extractor import extract_from_images, generate_text_embedding, synthesize_group_text
    from modules.group_merge import merge_pages_data

    data       = request.get_json(silent=True) or {}
    doc_ids    = data.get("doc_ids", [])
    title      = data.get("title", "").strip() or None
    use_vision = bool(data.get("vision"))

    if not doc_ids or len(doc_ids) < 2:
        return jsonify({"error": "At least 2 documents required to create a group"}), 400
    if len(doc_ids) > MULTIPAGE_MAX_PAGES:
        return jsonify({"error": f"Cannot group more than {MULTIPAGE_MAX_PAGES} pages at once"}), 400

    with get_db() as conn:
        # Validate all docs exist and are not already grouped
        rows = conn.execute(
            f"SELECT id, filename, group_id, source_archive FROM documents WHERE id IN ({','.join('?' * len(doc_ids))})",
            doc_ids
        ).fetchall()

        if len(rows) != len(doc_ids):
            return jsonify({"error": "One or more document IDs not found"}), 404

        already_grouped = [r["id"] for r in rows if r["group_id"] is not None]
        if already_grouped:
            return jsonify({"error": f"Documents {already_grouped} already belong to a group"}), 409

        # Sort filenames lexicographically → natural page order
        pages_sorted = sorted(rows, key=lambda r: r["filename"])
        sorted_ids   = [r["id"] for r in pages_sorted]

        # Merge / extract OUTSIDE the connection where possible — but the merge
        # path needs the connection, so do it here before closing.
        if not use_vision:
            extracted = merge_pages_data(conn, sorted_ids)

    source_archives = [r["source_archive"] for r in pages_sorted if r["source_archive"]]
    inherited_source_archive = source_archives[0] if source_archives else None

    if use_vision:
        image_paths = [PHOTOS_DIR / r["filename"] for r in pages_sorted]
        missing = [str(p) for p in image_paths if not p.exists()]
        if missing:
            return jsonify({"error": f"Image files not found: {missing}"}), 404
        extracted = extract_from_images(image_paths, API_KEY)
    else:
        # Text-only synthesis for a better title/description than page-1 fallback.
        synth = synthesize_group_text(extracted.pop("_pages_summary", []), API_KEY)
        if synth:
            extracted["title"]       = synth["title"]
            extracted["description"] = synth["description"]

    embed_text = " ".join(filter(None, [
        extracted.get("title", ""),
        extracted.get("description", ""),
        " ".join(extracted.get("tags", [])),
    ]))
    embedding = generate_text_embedding(embed_text, API_KEY)

    with get_db() as conn:
        # Insert group record
        cur = conn.execute(
            """INSERT INTO document_groups
               (title, date_depicted, date_range_start, date_range_end,
                location, medium, dimensions, description, language,
                transcription, raw_claude_response, is_key_evidence,
                embedding_json, source_archive)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                title or extracted.get("title"),
                _normalize_date(extracted.get("date_depicted")),
                extracted.get("date_range_start"),
                extracted.get("date_range_end"),
                extracted.get("location"),
                extracted.get("medium"),
                extracted.get("dimensions"),
                extracted.get("description"),
                extracted.get("language"),
                extracted.get("transcription"),
                json.dumps(extracted),
                0,  # is_key_evidence — never set automatically; user flags manually
                json.dumps(embedding) if embedding else None,
                inherited_source_archive,
            )
        )
        group_id = cur.lastrowid

        # Assign pages
        for page_number, row in enumerate(pages_sorted, start=1):
            conn.execute(
                "UPDATE documents SET group_id=?, page_number=? WHERE id=?",
                (group_id, page_number, row["id"])
            )

        # Insert entities
        for ent in (extracted.get("entities") or []):
            if not ent.get("name"):
                continue
            entity_id = upsert_entity(conn, ent["name"], ent.get("type", "unknown"))
            if entity_id:
                conn.execute(
                    "INSERT OR IGNORE INTO group_entities (group_id, entity_id, role, context) VALUES (?,?,?,?)",
                    (group_id, entity_id, ent.get("role"), ent.get("context"))
                )

        # Insert transactions
        for txn in (extracted.get("transactions") or []):
            conn.execute(
                """INSERT INTO group_transactions
                   (group_id, seller, buyer, date, price, currency,
                    auction_house, lot_number, location, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    group_id,
                    txn.get("seller"), txn.get("buyer"), txn.get("date"),
                    _to_float(txn.get("price")), txn.get("currency"),
                    txn.get("auction_house"),
                    str(txn.get("lot_number")) if txn.get("lot_number") else None,
                    txn.get("location"), txn.get("notes"),
                )
            )

        # Insert tags
        for tag_name in (extracted.get("tags") or []):
            if not tag_name:
                continue
            tag_id = get_or_create_tag(conn, str(tag_name).strip())
            if tag_id:
                conn.execute(
                    "INSERT OR IGNORE INTO group_tags (group_id, tag_id) VALUES (?,?)",
                    (group_id, tag_id)
                )

    return jsonify({"group_id": group_id, "title": title or extracted.get("title")}), 201


# ── List groups ───────────────────────────────────────────────────────────────

@bp.route("/api/groups", methods=["GET"])
def list_groups():
    _, _, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, _, get_db, rows_to_list, _, _, _ = _get_deps()

    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(int(request.args.get("per_page", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
    offset   = (page - 1) * per_page

    key_only       = request.args.get("key_evidence", "").lower() == "true"
    tag_id         = request.args.get("tag_id",    type=int)
    entity_id      = request.args.get("entity_id", type=int)
    date_from      = request.args.get("date_from")
    date_to        = request.args.get("date_to")
    source_archive = request.args.get("source_archive")

    sort  = request.args.get("sort", "created_at")
    order = "DESC" if request.args.get("order", "desc").lower() == "desc" else "ASC"
    allowed_sort = {"created_at", "date_depicted", "title", "updated_at"}
    if sort not in allowed_sort:
        sort = "created_at"

    # date_depicted can be NULL, so fall back to date_range_start and push
    # empty values to the end regardless of sort direction.
    if sort == "date_depicted":
        order_sql = (
            f"ORDER BY COALESCE(g.date_depicted, g.date_range_start) IS NULL, "
            f"COALESCE(g.date_depicted, g.date_range_start) {order}"
        )
    elif sort == "title":
        order_sql = f"ORDER BY g.title IS NULL, g.title COLLATE NOCASE {order}"
    else:
        order_sql = f"ORDER BY g.{sort} {order}"

    joins, wheres, params = [], ["g.is_trashed = 0"], []

    if key_only:
        wheres.append("g.is_key_evidence = 1")
    if tag_id:
        joins.append("JOIN group_tags gt ON gt.group_id = g.id AND gt.tag_id = ?")
        params.append(tag_id)
    if entity_id:
        joins.append("JOIN group_entities ge ON ge.group_id = g.id AND ge.entity_id = ?")
        params.append(entity_id)
    if date_from:
        wheres.append("COALESCE(g.date_depicted, g.date_range_start) >= ?")
        params.append(date_from)
    if date_to:
        wheres.append("COALESCE(g.date_depicted, g.date_range_start) <= ?")
        params.append(date_to)
    if source_archive:
        if source_archive == "__none__":
            wheres.append("(g.source_archive IS NULL OR g.source_archive = '')")
        else:
            wheres.append("g.source_archive = ?")
            params.append(source_archive)

    join_sql  = " ".join(joins)
    where_sql = "WHERE " + " AND ".join(wheres)

    with get_db() as conn:
        total = conn.execute(
            f"SELECT COUNT(DISTINCT g.id) as c FROM document_groups g {join_sql} {where_sql}",
            params
        ).fetchone()["c"]

        rows = conn.execute(
            f"""SELECT g.id, g.title, g.date_depicted, g.date_range_start,
                       g.date_range_end, g.location, g.medium,
                       g.is_key_evidence, g.source_archive, g.created_at, g.updated_at,
                       COUNT(d.id) as page_count,
                       MIN(d.id) as first_page_id
                FROM document_groups g
                {join_sql}
                LEFT JOIN documents d ON d.group_id = g.id
                {where_sql}
                GROUP BY g.id
                {order_sql}
                LIMIT ? OFFSET ?""",
            params + [per_page, offset]
        ).fetchall()

    return jsonify({
        "groups":   rows_to_list(rows),
        "total":    total,
        "page":     page,
        "per_page": per_page,
    })


# ── Group detail ──────────────────────────────────────────────────────────────

@bp.route("/api/groups/<int:group_id>", methods=["GET"])
def get_group(group_id):
    _, _, _, _, _, get_db, rows_to_list, row_to_dict, _, _ = _get_deps()

    with get_db() as conn:
        # Explicit column list — skip embedding_json and raw_claude_response,
        # neither of which the frontend uses but which together can be 30KB+.
        group = conn.execute(
            """SELECT id, title, date_depicted, date_range_start, date_range_end,
                      location, medium, dimensions, description, language,
                      transcription, annotation, is_key_evidence, is_trashed,
                      source_archive, created_at, updated_at
               FROM document_groups WHERE id=?""",
            (group_id,)
        ).fetchone()
        if not group:
            abort(404)

        group = row_to_dict(group)

        group["pages"] = rows_to_list(conn.execute(
            """SELECT id, filename, page_number, title, date_depicted, medium
               FROM documents WHERE group_id=? ORDER BY page_number ASC""",
            (group_id,)
        ).fetchall())

        # Union of entities explicitly attached to the group AND entities from
        # any child page. Deduped by entity_id, preferring the group-level
        # role/context when both exist. source_page_id is set when the entity
        # is reached via a child page only — useful for the UI to link back to
        # the originating page.
        group["entities"] = rows_to_list(conn.execute(
            """SELECT e.id, e.name, e.type,
                      ge.role  AS role,
                      ge.context AS context,
                      NULL AS source_page_id
               FROM group_entities ge
               JOIN entities e ON e.id = ge.entity_id
               WHERE ge.group_id = ?

               UNION ALL

               -- One row per entity reached only via a child page. If the same
               -- entity appears on multiple child pages, pick the earliest
               -- page (MIN page_number, then MIN id) for source_page_id and
               -- its role/context. MIN(de.role)/MIN(de.context) just makes
               -- the choice deterministic when ties exist.
               SELECT e.id, e.name, e.type,
                      MIN(de.role)    AS role,
                      MIN(de.context) AS context,
                      (SELECT d2.id FROM documents d2
                       JOIN document_entities de2 ON de2.document_id = d2.id
                       WHERE d2.group_id = ? AND d2.is_trashed = 0
                         AND de2.entity_id = e.id
                       ORDER BY d2.page_number, d2.id LIMIT 1) AS source_page_id
               FROM documents d
               JOIN document_entities de ON de.document_id = d.id
               JOIN entities e ON e.id = de.entity_id
               WHERE d.group_id = ? AND d.is_trashed = 0
                 AND e.id NOT IN (SELECT entity_id FROM group_entities
                                  WHERE group_id = ?)
               GROUP BY e.id, e.name, e.type""",
            (group_id, group_id, group_id, group_id)
        ).fetchall())

        group["transactions"] = rows_to_list(conn.execute(
            "SELECT * FROM group_transactions WHERE group_id=? ORDER BY date",
            (group_id,)
        ).fetchall())

        group["tags"] = rows_to_list(conn.execute(
            """SELECT t.id, t.name, t.color FROM group_tags gt
               JOIN tags t ON t.id = gt.tag_id WHERE gt.group_id=?""",
            (group_id,)
        ).fetchall())

    return jsonify(group)


# ── Update group ──────────────────────────────────────────────────────────────

@bp.route("/api/groups/<int:group_id>", methods=["PATCH"])
def update_group(group_id):
    _, _, _, _, _, get_db, _, _, _, _ = _get_deps()

    data    = request.get_json(silent=True) or {}
    allowed = {"title", "date_depicted", "date_range_start", "date_range_end",
               "location", "medium", "dimensions", "description", "language",
               "transcription", "annotation", "is_key_evidence", "is_trashed", "source_archive"}
    updates = {k: v for k, v in data.items() if k in allowed}

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM document_groups WHERE id=?", (group_id,)).fetchone():
            abort(404)
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE document_groups SET {set_clause}, updated_at=datetime('now') WHERE id=?",
            list(updates.values()) + [group_id]
        )

    return jsonify({"ok": True, "updated": list(updates.keys())})


# ── Delete group ──────────────────────────────────────────────────────────────

@bp.route("/api/groups/<int:group_id>", methods=["DELETE"])
def delete_group(group_id):
    _, _, _, _, _, get_db, _, _, _, _ = _get_deps()

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM document_groups WHERE id=?", (group_id,)).fetchone():
            abort(404)
        # ON DELETE SET NULL frees the pages automatically; CASCADE removes
        # group_entities, group_transactions, and group_tags.
        conn.execute("DELETE FROM document_groups WHERE id=?", (group_id,))

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

    return jsonify({"ok": True, "deleted": group_id})


# ── Group–entity associations ─────────────────────────────────────────────────

@bp.route("/api/groups/<int:group_id>/entities", methods=["POST"])
def add_group_entity(group_id):
    _, _, _, _, _, get_db, _, row_to_dict, upsert_entity, _ = _get_deps()

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
        if not conn.execute("SELECT 1 FROM document_groups WHERE id=?", (group_id,)).fetchone():
            abort(404)

        entity_id = upsert_entity(conn, name, type_)
        if not entity_id:
            return jsonify({"error": "Failed to create entity"}), 500

        conn.execute(
            "INSERT OR IGNORE INTO group_entities (group_id, entity_id, role) VALUES (?,?,?)",
            (group_id, entity_id, role),
        )
        entity = conn.execute(
            "SELECT id, name, type FROM entities WHERE id=?", (entity_id,)
        ).fetchone()

    return jsonify({"ok": True, "entity": row_to_dict(entity), "role": role}), 201


@bp.route("/api/groups/<int:group_id>/entities/<int:entity_id>", methods=["DELETE"])
def remove_group_entity(group_id, entity_id):
    _, _, _, _, _, get_db, _, _, _, _ = _get_deps()

    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM group_entities WHERE group_id=? AND entity_id=?",
            (group_id, entity_id),
        )
        if cur.rowcount == 0:
            abort(404)
        conn.execute(
            """DELETE FROM entities WHERE id=?
               AND id NOT IN (SELECT entity_id FROM document_entities)
               AND id NOT IN (SELECT entity_id FROM group_entities)""",
            (entity_id,),
        )

    return jsonify({"ok": True})


# ── Reorder pages ─────────────────────────────────────────────────────────────

@bp.route("/api/groups/<int:group_id>/pages", methods=["PATCH"])
def reorder_pages(group_id):
    _, _, _, _, _, get_db, _, _, _, _ = _get_deps()

    data       = request.get_json(silent=True) or {}
    page_order = data.get("page_order", [])

    if not page_order:
        return jsonify({"error": "page_order is required"}), 400

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM document_groups WHERE id=?", (group_id,)).fetchone():
            abort(404)
        for page_number, doc_id in enumerate(page_order, start=1):
            conn.execute(
                "UPDATE documents SET page_number=? WHERE id=? AND group_id=?",
                (page_number, doc_id, group_id)
            )

    return jsonify({"ok": True})


# ── Re-extract ────────────────────────────────────────────────────────────────

@bp.route("/api/groups/<int:group_id>/re-extract", methods=["POST"])
def re_extract_group(group_id):
    PHOTOS_DIR, API_KEY, _, _, _, get_db, rows_to_list, row_to_dict, upsert_entity, get_or_create_tag = _get_deps()
    from modules.extractor import extract_from_images, generate_text_embedding

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM document_groups WHERE id=?", (group_id,)).fetchone():
            abort(404)
        pages = conn.execute(
            "SELECT id, filename FROM documents WHERE group_id=? ORDER BY page_number ASC",
            (group_id,)
        ).fetchall()

    if not pages:
        return jsonify({"error": "No pages in this group"}), 400

    image_paths = [PHOTOS_DIR / p["filename"] for p in pages]
    missing = [str(p) for p in image_paths if not p.exists()]
    if missing:
        return jsonify({"error": f"Image files not found: {missing}"}), 404

    extracted = extract_from_images(image_paths, API_KEY)
    embed_text = " ".join(filter(None, [
        extracted.get("title", ""),
        extracted.get("description", ""),
        " ".join(extracted.get("tags", [])),
    ]))
    embedding = generate_text_embedding(embed_text, API_KEY)

    with get_db() as conn:
        # Preserve is_key_evidence (a user-set flag) across re-extraction.
        conn.execute(
            """UPDATE document_groups SET
                title=?, date_depicted=?, date_range_start=?, date_range_end=?,
                location=?, medium=?, dimensions=?, description=?, language=?,
                transcription=?, raw_claude_response=?,
                embedding_json=?, updated_at=datetime('now')
               WHERE id=?""",
            (
                extracted.get("title"),
                _normalize_date(extracted.get("date_depicted")), extracted.get("date_range_start"),
                extracted.get("date_range_end"), extracted.get("location"),
                extracted.get("medium"), extracted.get("dimensions"),
                extracted.get("description"), extracted.get("language"),
                extracted.get("transcription"), json.dumps(extracted),
                json.dumps(embedding) if embedding else None,
                group_id,
            )
        )
        # Replace entities and transactions
        conn.execute("DELETE FROM group_entities     WHERE group_id=?", (group_id,))
        conn.execute("DELETE FROM group_transactions WHERE group_id=?", (group_id,))
        conn.execute("DELETE FROM group_tags         WHERE group_id=?", (group_id,))

        for ent in (extracted.get("entities") or []):
            if not ent.get("name"):
                continue
            entity_id = upsert_entity(conn, ent["name"], ent.get("type", "unknown"))
            if entity_id:
                conn.execute(
                    "INSERT OR IGNORE INTO group_entities (group_id, entity_id, role, context) VALUES (?,?,?,?)",
                    (group_id, entity_id, ent.get("role"), ent.get("context"))
                )

        for txn in (extracted.get("transactions") or []):
            conn.execute(
                """INSERT INTO group_transactions
                   (group_id, seller, buyer, date, price, currency,
                    auction_house, lot_number, location, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    group_id,
                    txn.get("seller"), txn.get("buyer"), txn.get("date"),
                    _to_float(txn.get("price")), txn.get("currency"),
                    txn.get("auction_house"),
                    str(txn.get("lot_number")) if txn.get("lot_number") else None,
                    txn.get("location"), txn.get("notes"),
                )
            )

        for tag_name in (extracted.get("tags") or []):
            if not tag_name:
                continue
            tag_id = get_or_create_tag(conn, str(tag_name).strip())
            if tag_id:
                conn.execute(
                    "INSERT OR IGNORE INTO group_tags (group_id, tag_id) VALUES (?,?)",
                    (group_id, tag_id)
                )

    return jsonify({"ok": True})


# ── Group tag management ──────────────────────────────────────────────────────

@bp.route("/api/groups/<int:group_id>/tags", methods=["POST"])
def add_group_tag(group_id):
    _, _, _, _, _, get_db, _, _, _, get_or_create_tag = _get_deps()

    data   = request.get_json(silent=True) or {}
    tag_id = data.get("tag_id")

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM document_groups WHERE id=?", (group_id,)).fetchone():
            abort(404)
        if tag_id is None:
            return jsonify({"error": "tag_id is required"}), 400
        if not conn.execute("SELECT 1 FROM tags WHERE id=?", (tag_id,)).fetchone():
            return jsonify({"error": "Tag not found"}), 404
        conn.execute(
            "INSERT OR IGNORE INTO group_tags (group_id, tag_id) VALUES (?,?)",
            (group_id, tag_id)
        )
        tag = conn.execute("SELECT id, name, color FROM tags WHERE id=?", (tag_id,)).fetchone()

    return jsonify(dict(tag)), 201


@bp.route("/api/groups/<int:group_id>/tags/<int:tag_id>", methods=["DELETE"])
def remove_group_tag(group_id, tag_id):
    _, _, _, _, _, get_db, _, _, _, _ = _get_deps()

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM document_groups WHERE id=?", (group_id,)).fetchone():
            abort(404)
        conn.execute(
            "DELETE FROM group_tags WHERE group_id=? AND tag_id=?",
            (group_id, tag_id)
        )

    return jsonify({"ok": True})


# ── Helper ────────────────────────────────────────────────────────────────────

def _to_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None
