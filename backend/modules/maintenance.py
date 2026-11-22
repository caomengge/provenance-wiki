"""
maintenance.py – User-initiated archive housekeeping.

Unused entities and tags (attached to no document or group, including ones in
the Trash) used to be deleted automatically on every startup and after every
deletion or unlink. That silently discarded curated work: an entity's aliases
and relationship links go with it. They are now kept until the researcher
reviews them and presses "Clean up unused" on the Entities page.
"""

import sqlite3

UNUSED_ENTITIES_SQL = """
    SELECT e.id, e.name, e.type,
           (SELECT COUNT(*) FROM entity_aliases a WHERE a.entity_id = e.id) AS alias_count,
           (SELECT COUNT(*) FROM entity_relationships r
             WHERE r.source_entity_id = e.id OR r.target_entity_id = e.id) AS relationship_count
    FROM entities e
    WHERE NOT EXISTS (SELECT 1 FROM document_entities de WHERE de.entity_id = e.id)
      AND NOT EXISTS (SELECT 1 FROM group_entities ge WHERE ge.entity_id = e.id)
    ORDER BY e.name
"""

UNUSED_TAGS_SQL = """
    SELECT t.id, t.name
    FROM tags t
    WHERE NOT EXISTS (SELECT 1 FROM document_tags dt WHERE dt.tag_id = t.id)
      AND NOT EXISTS (SELECT 1 FROM group_tags gt WHERE gt.tag_id = t.id)
    ORDER BY t.name
"""


def find_unused(conn: sqlite3.Connection) -> dict:
    entities = [dict(r) for r in conn.execute(UNUSED_ENTITIES_SQL)]
    tags = [dict(r) for r in conn.execute(UNUSED_TAGS_SQL)]
    return {
        "entities": entities,
        "tags": tags,
        "alias_count": sum(e["alias_count"] for e in entities),
        "relationship_count": sum(e["relationship_count"] for e in entities),
    }


def remove_unused(conn: sqlite3.Connection, entity_ids, tag_ids) -> dict:
    """Delete only the listed ids, and only those still unused right now.

    The researcher confirms a specific list; anything linked again since the
    preview (e.g. by an ingestion running in the background) is kept.
    """
    still_unused = find_unused(conn)
    ok_entities = {e["id"] for e in still_unused["entities"]} & {int(i) for i in entity_ids or []}
    ok_tags = {t["id"] for t in still_unused["tags"]} & {int(i) for i in tag_ids or []}
    for eid in ok_entities:
        conn.execute("DELETE FROM entities WHERE id = ?", (eid,))   # cascades aliases/relationships
    for tid in ok_tags:
        conn.execute("DELETE FROM tags WHERE id = ?", (tid,))
    return {"removed_entities": len(ok_entities), "removed_tags": len(ok_tags)}
