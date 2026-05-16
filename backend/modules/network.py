"""
network.py – Provenance network graph builder for D3 force layout.

Returns {nodes: [...], edges: [...]} where:
  • nodes are entities (person | object | institution | place | unknown)
  • edges connect two entities that co-occur in the same document or group
  • edge weight = number of shared documents/groups

Documents themselves are not nodes — the graph is a pure entity-relationship
view. The `types` filter selects which entity types appear; the view defaults
to people only. Optional filtering by entity, tag, date range, or seed document.
"""

import logging

logger = logging.getLogger(__name__)

ALLOWED_TYPES = ("person", "object", "institution", "place", "unknown")


def get_network(
    types:       tuple = ("person",),
    entity_id:   int | None = None,
    tag_id:      int | None = None,
    doc_id:      int | None = None,
    date_from:   str | None = None,
    date_to:     str | None = None,
    max_nodes:   int = 400,
    min_weight:  int = 1,
) -> dict:
    """
    Build the entity co-occurrence network payload.

    Returns:
        {
            nodes: [{id, db_id, type, label, doc_count}],
            edges: [{source, target, weight, relationship_type}],
            stats: {total_nodes, total_edges},
        }
    """
    from modules.db import get_db

    types = tuple(t for t in types if t in ALLOWED_TYPES) or ("person",)

    with get_db() as conn:
        doc_ids   = _get_relevant_doc_ids(conn, entity_id, tag_id, doc_id, date_from, date_to)
        group_ids = _get_relevant_group_ids(conn, entity_id, tag_id, doc_id, date_from, date_to)

        nodes: dict = {}
        edges: dict = {}
        type_ph = ",".join("?" * len(types))
        type_params = list(types)

        # ── Entity nodes ──────────────────────────────────────────────────────
        # One node per entity of a selected type appearing anywhere in scope.
        # doc_count is the number of records (documents + groups) it appears in.
        def _add_entity_nodes(rows):
            for r in rows:
                key = f"ent_{r['entity_id']}"
                node = nodes.get(key)
                if node is None:
                    nodes[key] = {
                        "id":        key,
                        "db_id":     r["entity_id"],
                        "type":      r["type"],
                        "label":     r["name"],
                        "doc_count": r["cnt"],
                    }
                else:
                    node["doc_count"] += r["cnt"]

        if doc_ids:
            dph = ",".join("?" * len(doc_ids))
            _add_entity_nodes(conn.execute(
                f"""SELECT de.entity_id, e.name, e.type,
                           COUNT(DISTINCT de.document_id) AS cnt
                    FROM document_entities de
                    JOIN entities e ON e.id = de.entity_id
                    WHERE de.document_id IN ({dph}) AND e.type IN ({type_ph})
                    GROUP BY de.entity_id""",
                doc_ids + type_params,
            ).fetchall())
        if group_ids:
            gph = ",".join("?" * len(group_ids))
            _add_entity_nodes(conn.execute(
                f"""SELECT ge.entity_id, e.name, e.type,
                           COUNT(DISTINCT ge.group_id) AS cnt
                    FROM group_entities ge
                    JOIN entities e ON e.id = ge.entity_id
                    WHERE ge.group_id IN ({gph}) AND e.type IN ({type_ph})
                    GROUP BY ge.entity_id""",
                group_ids + type_params,
            ).fetchall())

        # ── Co-occurrence edges ───────────────────────────────────────────────
        def _add_cooc(rows):
            for r in rows:
                ka, kb = f"ent_{r['ea']}", f"ent_{r['eb']}"
                if ka not in nodes or kb not in nodes:
                    continue
                k = f"{ka}__{kb}"
                if k in edges:
                    edges[k]["weight"] += r["cnt"]
                else:
                    edges[k] = {
                        "source":            ka,
                        "target":            kb,
                        "weight":            r["cnt"],
                        "relationship_type": "co-occurrence",
                    }

        if doc_ids:
            dph = ",".join("?" * len(doc_ids))
            _add_cooc(conn.execute(
                f"""SELECT a.entity_id AS ea, b.entity_id AS eb, COUNT(*) AS cnt
                    FROM document_entities a
                    JOIN document_entities b
                      ON a.document_id = b.document_id AND a.entity_id < b.entity_id
                    JOIN entities ea ON ea.id = a.entity_id
                    JOIN entities eb ON eb.id = b.entity_id
                    WHERE a.document_id IN ({dph})
                      AND ea.type IN ({type_ph}) AND eb.type IN ({type_ph})
                    GROUP BY a.entity_id, b.entity_id
                    HAVING cnt >= ?""",
                doc_ids + type_params + type_params + [min_weight],
            ).fetchall())
        if group_ids:
            gph = ",".join("?" * len(group_ids))
            _add_cooc(conn.execute(
                f"""SELECT a.entity_id AS ea, b.entity_id AS eb, COUNT(*) AS cnt
                    FROM group_entities a
                    JOIN group_entities b
                      ON a.group_id = b.group_id AND a.entity_id < b.entity_id
                    JOIN entities ea ON ea.id = a.entity_id
                    JOIN entities eb ON eb.id = b.entity_id
                    WHERE a.group_id IN ({gph})
                      AND ea.type IN ({type_ph}) AND eb.type IN ({type_ph})
                    GROUP BY a.entity_id, b.entity_id
                    HAVING cnt >= ?""",
                group_ids + type_params + type_params + [min_weight],
            ).fetchall())

        # ── Cap node count: keep the highest-degree entities ──────────────────
        if len(nodes) > max_nodes:
            keep = sorted(nodes.values(), key=lambda n: n["doc_count"], reverse=True)[:max_nodes]
            nodes = {n["id"]: n for n in keep}
            edges = {k: e for k, e in edges.items()
                     if e["source"] in nodes and e["target"] in nodes}

    node_list = list(nodes.values())
    edge_list = list(edges.values())

    return {
        "nodes": node_list,
        "edges": edge_list,
        "stats": {
            "total_nodes": len(node_list),
            "total_edges": len(edge_list),
        },
    }


def _get_relevant_doc_ids(conn, entity_id, tag_id, doc_id, date_from, date_to):
    """Return standalone document IDs (not in a group) matching the filters."""
    sql = "SELECT d.id FROM documents d"
    joins, wheres, params = [], ["d.is_trashed = 0", "d.group_id IS NULL"], []

    if entity_id:
        joins.append("JOIN document_entities de ON de.document_id = d.id AND de.entity_id = ?")
        params.append(entity_id)
    if tag_id:
        joins.append("JOIN document_tags dt ON dt.document_id = d.id AND dt.tag_id = ?")
        params.append(tag_id)
    if doc_id:
        # Seed: the focal document + every document sharing an entity with it.
        wheres.append("""(d.id = ? OR d.id IN (
            SELECT de2.document_id FROM document_entities de2
            WHERE de2.entity_id IN (
                SELECT entity_id FROM document_entities WHERE document_id = ?
            )))""")
        params.extend([doc_id, doc_id])
    if date_from:
        wheres.append("COALESCE(d.date_depicted, d.date_range_start) >= ?")
        params.append(date_from)
    if date_to:
        wheres.append("COALESCE(d.date_depicted, d.date_range_start) <= ?")
        params.append(date_to)

    sql += " " + " ".join(joins)
    if wheres:
        sql += " WHERE " + " AND ".join(wheres)

    return [r["id"] for r in conn.execute(sql, params).fetchall()]


def _get_relevant_group_ids(conn, entity_id, tag_id, doc_id, date_from, date_to):
    """Return document-group IDs matching the same filters."""
    wheres, params = ["g.is_trashed = 0"], []

    if entity_id:
        # Relevant if the entity is attached to the group or to a child page.
        wheres.append("""(g.id IN (SELECT group_id FROM group_entities WHERE entity_id = ?)
            OR g.id IN (SELECT d.group_id FROM document_entities de
                        JOIN documents d ON d.id = de.document_id
                        WHERE de.entity_id = ? AND d.group_id IS NOT NULL))""")
        params.extend([entity_id, entity_id])
    if tag_id:
        wheres.append("g.id IN (SELECT group_id FROM group_tags WHERE tag_id = ?)")
        params.append(tag_id)
    if doc_id:
        wheres.append("""g.id IN (SELECT d.group_id FROM document_entities de
            JOIN documents d ON d.id = de.document_id
            WHERE d.group_id IS NOT NULL AND de.entity_id IN (
                SELECT entity_id FROM document_entities WHERE document_id = ?
            ))""")
        params.append(doc_id)
    if date_from:
        wheres.append("COALESCE(g.date_depicted, g.date_range_start) >= ?")
        params.append(date_from)
    if date_to:
        wheres.append("COALESCE(g.date_depicted, g.date_range_start) <= ?")
        params.append(date_to)

    sql = "SELECT g.id FROM document_groups g WHERE " + " AND ".join(wheres)
    return [r["id"] for r in conn.execute(sql, params).fetchall()]
