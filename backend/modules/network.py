"""
network.py – Provenance network graph builder for D3 force layout.

Returns {nodes: [...], edges: [...]} where:
  • nodes have types: document | person | object | institution
  • edges connect documents to entities (and optionally entity-to-entity
    when they co-occur in the same document)
  • edge weight = number of co-occurrences

Optional filtering by entity, tag, date range, or a seed document.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_network(
    entity_id:   int | None = None,
    tag_id:      int | None = None,
    doc_id:      int | None = None,
    date_from:   str | None = None,
    date_to:     str | None = None,
    max_nodes:   int = 200,
) -> dict:
    """
    Build the network graph payload.

    Returns:
        {
            nodes: [{id, type, label, doc_count, is_key_evidence, ...}],
            edges: [{source, target, weight, relationship_type}],
            stats: {total_nodes, total_edges},
        }
    """
    from modules.db import get_db

    with get_db() as conn:
        # ── 1. Fetch relevant document IDs ────────────────────────────────────
        doc_ids = _get_relevant_doc_ids(conn, entity_id, tag_id, doc_id, date_from, date_to, max_nodes)

        nodes: dict = {}
        edges: dict = {}
        entity_doc_count: dict[int, int] = {}

        if doc_ids:
            placeholders = ",".join("?" * len(doc_ids))

            # ── 2. Document nodes ─────────────────────────────────────────────
            doc_rows = conn.execute(
                f"""SELECT id, title, filename, date_depicted, date_range_start,
                           is_key_evidence, medium
                    FROM documents WHERE id IN ({placeholders})""",
                doc_ids,
            ).fetchall()

            for d in doc_rows:
                nodes[f"doc_{d['id']}"] = {
                    "id":              f"doc_{d['id']}",
                    "db_id":           d["id"],
                    "type":            "document",
                    "label":           d["title"] or d["filename"],
                    "date":            d["date_depicted"] or d["date_range_start"],
                    "is_key_evidence": bool(d["is_key_evidence"]),
                    "medium":          d["medium"],
                }

            # ── 3. Entity nodes + document-entity edges ───────────────────────
            de_rows = conn.execute(
                f"""SELECT de.document_id, de.entity_id, de.role,
                           e.name, e.type
                    FROM document_entities de
                    JOIN entities e ON e.id = de.entity_id
                    WHERE de.document_id IN ({placeholders})""",
                doc_ids,
            ).fetchall()

            for de in de_rows:
                eid = de["entity_id"]
                node_key = f"ent_{eid}"

                entity_doc_count[eid] = entity_doc_count.get(eid, 0) + 1

                if node_key not in nodes:
                    nodes[node_key] = {
                        "id":        node_key,
                        "db_id":     eid,
                        "type":      de["type"],
                        "label":     de["name"],
                        "doc_count": 0,
                    }
                nodes[node_key]["doc_count"] = entity_doc_count[eid]

                edge_key = f"doc_{de['document_id']}__ent_{eid}"
                if edge_key not in edges:
                    edges[edge_key] = {
                        "source":            f"doc_{de['document_id']}",
                        "target":            node_key,
                        "weight":            0,
                        "relationship_type": de["role"] or "mentions",
                    }
                edges[edge_key]["weight"] += 1

            # ── 4. Entity co-occurrence edges ─────────────────────────────────
            cooc_sql = f"""
                SELECT a.entity_id as ea, b.entity_id as eb, COUNT(*) as cnt
                FROM document_entities a
                JOIN document_entities b
                  ON a.document_id = b.document_id AND a.entity_id < b.entity_id
                WHERE a.document_id IN ({placeholders})
                GROUP BY a.entity_id, b.entity_id
                HAVING cnt >= 1
            """
            for row in conn.execute(cooc_sql, doc_ids).fetchall():
                ea, eb = row["ea"], row["eb"]
                k = f"ent_{ea}__ent_{eb}"
                edges[k] = {
                    "source":            f"ent_{ea}",
                    "target":            f"ent_{eb}",
                    "weight":            row["cnt"],
                    "relationship_type": "co-occurrence",
                }

            # ── 5. Document link edges ────────────────────────────────────────
            link_rows = conn.execute(
                f"""SELECT dl.source_id, dl.target_id, dl.relationship_type
                    FROM document_links dl
                    WHERE dl.source_id IN ({placeholders})
                       OR dl.target_id IN ({placeholders})""",
                doc_ids + doc_ids,
            ).fetchall()

            for link in link_rows:
                k = f"doc_{link['source_id']}__doc_{link['target_id']}"
                edges[k] = {
                    "source":            f"doc_{link['source_id']}",
                    "target":            f"doc_{link['target_id']}",
                    "weight":            2,
                    "relationship_type": link["relationship_type"] or "linked",
                }

        # ── 6. Group nodes + group-entity edges ───────────────────────────────
        group_rows = conn.execute(
            """SELECT g.id, g.title, g.date_depicted, g.is_key_evidence,
                      COUNT(d.id) as page_count
               FROM document_groups g
               LEFT JOIN documents d ON d.group_id = g.id
               WHERE g.is_trashed = 0
               GROUP BY g.id
               LIMIT ?""",
            (max_nodes,),
        ).fetchall()

        group_ids = [g["id"] for g in group_rows]
        for g in group_rows:
            nodes[f"grp_{g['id']}"] = {
                "id":              f"grp_{g['id']}",
                "db_id":           g["id"],
                "type":            "document",
                "label":           g["title"] or f"Group #{g['id']}",
                "date":            g["date_depicted"],
                "is_key_evidence": bool(g["is_key_evidence"]),
                "page_count":      g["page_count"],
            }

        if group_ids:
            gplaceholders = ",".join("?" * len(group_ids))
            ge_rows = conn.execute(
                f"""SELECT ge.group_id, ge.entity_id, ge.role,
                           e.name, e.type
                    FROM group_entities ge
                    JOIN entities e ON e.id = ge.entity_id
                    WHERE ge.group_id IN ({gplaceholders})""",
                group_ids,
            ).fetchall()

            for ge in ge_rows:
                eid = ge["entity_id"]
                node_key = f"ent_{eid}"
                entity_doc_count[eid] = entity_doc_count.get(eid, 0) + 1

                if node_key not in nodes:
                    nodes[node_key] = {
                        "id":        node_key,
                        "db_id":     eid,
                        "type":      ge["type"],
                        "label":     ge["name"],
                        "doc_count": 0,
                    }
                nodes[node_key]["doc_count"] = entity_doc_count[eid]

                edge_key = f"grp_{ge['group_id']}__ent_{eid}"
                if edge_key not in edges:
                    edges[edge_key] = {
                        "source":            f"grp_{ge['group_id']}",
                        "target":            node_key,
                        "weight":            0,
                        "relationship_type": ge["role"] or "mentions",
                    }
                edges[edge_key]["weight"] += 1

    node_list = list(nodes.values())
    edge_list  = list(edges.values())

    return {
        "nodes": node_list,
        "edges": edge_list,
        "stats": {
            "total_nodes": len(node_list),
            "total_edges": len(edge_list),
        },
    }


def _get_relevant_doc_ids(conn, entity_id, tag_id, doc_id, date_from, date_to, max_nodes):
    """Return a list of document IDs for the network, applying filters."""
    sql = "SELECT d.id FROM documents d"
    joins, wheres, params = [], ["d.is_trashed = 0", "d.group_id IS NULL"], []

    if entity_id:
        joins.append("JOIN document_entities de ON de.document_id = d.id AND de.entity_id = ?")
        params.append(entity_id)
    if tag_id:
        joins.append("JOIN document_tags dt ON dt.document_id = d.id AND dt.tag_id = ?")
        params.append(tag_id)
    if doc_id:
        # Seed: get the focal document + all documents sharing an entity with it
        wheres.append("""d.id = ? OR d.id IN (
            SELECT de2.document_id FROM document_entities de2
            WHERE de2.entity_id IN (
                SELECT entity_id FROM document_entities WHERE document_id = ?
            )
        )""")
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
    sql += f" LIMIT {max_nodes}"

    rows = conn.execute(sql, params).fetchall()
    return [r["id"] for r in rows]
