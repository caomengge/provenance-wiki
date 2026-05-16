"""
entities.py – Flask Blueprint for entity management.

Routes:
  GET    /api/entities               – paginated list with type filter
  GET    /api/entities/:id           – entity detail + linked documents
  PATCH  /api/entities/:id          – update name and/or type
  DELETE /api/entities/:id          – delete entity and its document links
  POST   /api/entities/merge         – merge two entities into one
  GET    /api/entities/:id/documents – documents mentioning this entity
  POST   /api/entities/:id/aliases   – add an alternate name
  DELETE /api/entities/:id/aliases/:alias_id – remove an alternate name
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

    allowed_types = {"person", "object", "institution", "place", "unknown"}

    with get_db() as conn:
        wheres, params = [], []

        if type_filter and type_filter in allowed_types:
            wheres.append("e.type = ?")
            params.append(type_filter)
        if q:
            # Match the entity's own name OR any of its recorded aliases, so
            # searching an alternate name ("Larry") finds the merged entity.
            wheres.append(
                """(e.normalized_name LIKE ?
                    OR EXISTS (SELECT 1 FROM entity_aliases a
                               WHERE a.entity_id = e.id
                                 AND a.normalized_name LIKE ?))"""
            )
            params.extend([f"%{q.lower()}%", f"%{q.lower()}%"])

        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""

        total = conn.execute(
            f"SELECT COUNT(*) as cnt FROM entities e {where_sql}", params
        ).fetchone()["cnt"]

        # doc_count rolls each grouped child document up into its parent group,
        # so a group counts as one record no matter how many child pages mention
        # the entity. UNION (not UNION ALL) dedupes the case where an entity is
        # attached to both a child page and the group itself.
        rows = conn.execute(
            f"""SELECT e.id, e.name, e.type, e.normalized_name, e.created_at,
                       (SELECT GROUP_CONCAT(a.name, ', ')
                        FROM entity_aliases a WHERE a.entity_id = e.id) AS aliases,
                       (SELECT COUNT(*) FROM (
                          SELECT d.id AS rid, 'd' AS kind
                          FROM document_entities de
                          JOIN documents d ON d.id = de.document_id
                          WHERE de.entity_id = e.id
                            AND d.is_trashed = 0
                            AND d.group_id IS NULL
                          UNION
                          SELECT d.group_id AS rid, 'g' AS kind
                          FROM document_entities de
                          JOIN documents d ON d.id = de.document_id
                          JOIN document_groups g ON g.id = d.group_id
                          WHERE de.entity_id = e.id
                            AND d.is_trashed = 0
                            AND d.group_id IS NOT NULL
                            AND g.is_trashed = 0
                          UNION
                          SELECT ge.group_id AS rid, 'g' AS kind
                          FROM group_entities ge
                          JOIN document_groups g ON g.id = ge.group_id
                          WHERE ge.entity_id = e.id AND g.is_trashed = 0
                       )) AS doc_count
                FROM entities e
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

        # Standalone documents (not in any group)
        docs = conn.execute(
            """SELECT d.id, d.title, d.filename, d.date_depicted, de.role,
                      'document' as record_type
               FROM document_entities de
               JOIN documents d ON d.id = de.document_id
               WHERE de.entity_id = ? AND d.is_trashed = 0 AND d.group_id IS NULL
               ORDER BY d.date_depicted NULLS LAST
               LIMIT 50""",
            (entity_id,)
        ).fetchall()

        # Groups reached either via group_entities OR via any child page that
        # mentions the entity. role is taken from group_entities when present.
        groups = conn.execute(
            """SELECT g.id, g.title, g.date_depicted,
                      (SELECT ge.role FROM group_entities ge
                       WHERE ge.group_id = g.id AND ge.entity_id = ?) AS role,
                      'group' as record_type
               FROM document_groups g
               WHERE g.is_trashed = 0 AND (
                   g.id IN (SELECT group_id FROM group_entities WHERE entity_id = ?)
                   OR g.id IN (SELECT d.group_id FROM document_entities de
                               JOIN documents d ON d.id = de.document_id
                               WHERE de.entity_id = ? AND d.is_trashed = 0
                                 AND d.group_id IS NOT NULL)
               )
               ORDER BY g.date_depicted NULLS LAST
               LIMIT 50""",
            (entity_id, entity_id, entity_id)
        ).fetchall()

        all_docs = rows_to_list(docs) + rows_to_list(groups)

        # Co-occurring entities: only count co-occurrences within standalone
        # documents and within groups (treating each group as one bucket of
        # entities = group_entities ∪ entities of its child pages).
        co_entities = conn.execute(
            """SELECT e.id, e.name, e.type, COUNT(*) as co_count FROM (
                   -- standalone-document co-occurrences
                   SELECT de2.entity_id AS eid
                   FROM document_entities de1
                   JOIN documents d ON d.id = de1.document_id
                   JOIN document_entities de2 ON de2.document_id = de1.document_id
                       AND de2.entity_id != de1.entity_id
                   WHERE de1.entity_id = ? AND d.is_trashed = 0
                     AND d.group_id IS NULL

                   UNION ALL

                   -- group co-occurrences: union the entity sets reached via
                   -- group_entities and via child documents, then pair each
                   -- such entity with every other entity in the same group's
                   -- combined set.
                   SELECT other.entity_id AS eid
                   FROM (
                       SELECT g.id AS gid
                       FROM document_groups g
                       WHERE g.is_trashed = 0 AND (
                           g.id IN (SELECT group_id FROM group_entities WHERE entity_id = ?)
                           OR g.id IN (SELECT d.group_id FROM document_entities de
                                       JOIN documents d ON d.id = de.document_id
                                       WHERE de.entity_id = ? AND d.is_trashed = 0
                                         AND d.group_id IS NOT NULL)
                       )
                   ) src
                   JOIN (
                       SELECT g.id AS gid, ge.entity_id
                       FROM document_groups g
                       JOIN group_entities ge ON ge.group_id = g.id
                       WHERE g.is_trashed = 0
                       UNION
                       SELECT d.group_id AS gid, de.entity_id
                       FROM documents d
                       JOIN document_entities de ON de.document_id = d.id
                       WHERE d.is_trashed = 0 AND d.group_id IS NOT NULL
                   ) other ON other.gid = src.gid AND other.entity_id != ?
               ) co
               JOIN entities e ON e.id = co.eid
               GROUP BY e.id
               ORDER BY co_count DESC
               LIMIT 20""",
            (entity_id, entity_id, entity_id, entity_id)
        ).fetchall()

        aliases = conn.execute(
            """SELECT id, name, source, created_at FROM entity_aliases
               WHERE entity_id = ? ORDER BY name""",
            (entity_id,)
        ).fetchall()

        entity["documents"]    = all_docs
        entity["doc_count"]    = len(all_docs)
        entity["co_entities"]  = rows_to_list(co_entities)
        entity["aliases"]      = rows_to_list(aliases)

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

        doc_count = conn.execute(
            """SELECT COUNT(*) as cnt FROM document_entities de
               JOIN documents d ON d.id = de.document_id
               WHERE de.entity_id = ? AND d.is_trashed = 0
                 AND d.group_id IS NULL""",
            (entity_id,)
        ).fetchone()["cnt"]

        grp_count = conn.execute(
            """SELECT COUNT(*) as cnt FROM document_groups g
               WHERE g.is_trashed = 0 AND (
                   g.id IN (SELECT group_id FROM group_entities WHERE entity_id = ?)
                   OR g.id IN (SELECT d.group_id FROM document_entities de
                               JOIN documents d ON d.id = de.document_id
                               WHERE de.entity_id = ? AND d.is_trashed = 0
                                 AND d.group_id IS NOT NULL)
               )""",
            (entity_id, entity_id)
        ).fetchone()["cnt"]

        total = doc_count + grp_count

        doc_rows = conn.execute(
            """SELECT d.id, d.title, d.filename, d.date_depicted, d.date_range_start,
                      d.is_key_evidence, d.medium, de.role, de.context,
                      'document' as record_type
               FROM document_entities de
               JOIN documents d ON d.id = de.document_id
               WHERE de.entity_id = ? AND d.is_trashed = 0
                 AND d.group_id IS NULL
               ORDER BY d.date_depicted NULLS LAST, d.created_at""",
            (entity_id,)
        ).fetchall()

        # role/context come from group_entities when the entity is explicitly
        # attached to the group; otherwise they're NULL (the link comes from a
        # child page, whose per-page role lives on the document detail view).
        grp_rows = conn.execute(
            """SELECT g.id, g.title, NULL as filename,
                      g.date_depicted, g.date_range_start,
                      g.is_key_evidence, g.medium,
                      (SELECT ge.role FROM group_entities ge
                       WHERE ge.group_id = g.id AND ge.entity_id = ?) AS role,
                      (SELECT ge.context FROM group_entities ge
                       WHERE ge.group_id = g.id AND ge.entity_id = ?) AS context,
                      'group' as record_type
               FROM document_groups g
               WHERE g.is_trashed = 0 AND (
                   g.id IN (SELECT group_id FROM group_entities WHERE entity_id = ?)
                   OR g.id IN (SELECT d.group_id FROM document_entities de
                               JOIN documents d ON d.id = de.document_id
                               WHERE de.entity_id = ? AND d.is_trashed = 0
                                 AND d.group_id IS NOT NULL)
               )
               ORDER BY g.date_depicted NULLS LAST, g.created_at""",
            (entity_id, entity_id, entity_id, entity_id)
        ).fetchall()

        all_rows = sorted(
            rows_to_list(doc_rows) + rows_to_list(grp_rows),
            key=lambda r: r.get("date_depicted") or r.get("date_range_start") or ""
        )
        rows = all_rows[offset: offset + per_page]

    return jsonify({
        "documents": rows,
        "total":     total,
        "page":      page,
        "per_page":  per_page,
    })


# ── Update ────────────────────────────────────────────────────────────────────

ALLOWED_TYPES = {"person", "object", "institution", "place", "unknown"}

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
    from modules.db import add_entity_alias, record_audit

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
        discard = conn.execute(
            "SELECT * FROM entities WHERE id=?", (discard_id,)
        ).fetchone()
        if not discard:
            return jsonify({"error": f"Entity {discard_id} not found"}), 404

        # Preserve the discarded entity's name as an alias of the survivor so
        # the alternate name is not lost, then carry over any aliases the
        # discarded entity itself had collected. UPDATE OR IGNORE skips rows
        # that would collide on the (entity_id, normalized_name) UNIQUE
        # constraint; the DELETE clears those leftovers before the CASCADE.
        if add_entity_alias(conn, keep_id, discard["name"], source="merge"):
            record_audit(conn, entity_type="entity", entity_id=keep_id,
                         action="add_alias", new=discard["name"])
        conn.execute(
            "UPDATE OR IGNORE entity_aliases SET entity_id=? WHERE entity_id=?",
            (keep_id, discard_id)
        )
        conn.execute(
            "DELETE FROM entity_aliases WHERE entity_id=?", (discard_id,)
        )

        # Re-point all document_entities rows to the surviving entity.
        # UPDATE OR IGNORE skips rows that would violate the
        # (document_id, entity_id, role) UNIQUE constraint (i.e. the same
        # document already had both entities in the same role); the DELETE
        # below then removes those leftover duplicates.
        conn.execute(
            "UPDATE OR IGNORE document_entities SET entity_id=? WHERE entity_id=?",
            (keep_id, discard_id)
        )
        conn.execute(
            "DELETE FROM document_entities WHERE entity_id=?", (discard_id,)
        )

        # Same treatment for group_entities so multi-page groups also keep
        # their link to the surviving entity instead of losing it to the
        # CASCADE when the discarded entity is deleted below.
        conn.execute(
            "UPDATE OR IGNORE group_entities SET entity_id=? WHERE entity_id=?",
            (keep_id, discard_id)
        )
        conn.execute(
            "DELETE FROM group_entities WHERE entity_id=?", (discard_id,)
        )

        conn.execute("DELETE FROM entities WHERE id=?", (discard_id,))

    return jsonify({"ok": True, "merged_into": keep_id})


# ── Aliases ───────────────────────────────────────────────────────────────────

@bp.route("/api/entities/<int:entity_id>/aliases", methods=["POST"])
def add_alias(entity_id):
    _, _, get_db, _, _, _ = _get_deps()
    from modules.db import add_entity_alias, record_audit

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM entities WHERE id=?", (entity_id,)).fetchone():
            abort(404)
        if not add_entity_alias(conn, entity_id, name, source="manual"):
            return jsonify({"error": "Alias is blank, duplicates an "
                            "existing alias, or matches the entity's own name"}), 409
        record_audit(conn, entity_type="entity", entity_id=entity_id,
                     action="add_alias", new=name)

    return jsonify({"ok": True})


@bp.route("/api/entities/<int:entity_id>/aliases/<int:alias_id>", methods=["DELETE"])
def delete_alias(entity_id, alias_id):
    _, _, get_db, _, _, _ = _get_deps()
    from modules.db import record_audit

    with get_db() as conn:
        row = conn.execute(
            "SELECT name FROM entity_aliases WHERE id=? AND entity_id=?",
            (alias_id, entity_id)
        ).fetchone()
        if not row:
            abort(404)
        conn.execute("DELETE FROM entity_aliases WHERE id=?", (alias_id,))
        record_audit(conn, entity_type="entity", entity_id=entity_id,
                     action="remove_alias", old=row["name"])

    return jsonify({"ok": True})
