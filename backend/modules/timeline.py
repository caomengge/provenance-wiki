"""
timeline.py – Timeline data builder for provenance research.

Returns a sorted events array suitable for the D3 / React Timeline view:
  • dated_events:   list of {date, type, doc_id, title, entity_names, ...}
  • undated_events: documents with no known date

Events derive from two sources:
  1. Transaction records (seller → buyer transfers)
  2. Document dates (date_depicted or date_range_start)

Filtering supported by entity, tag, and date range.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_timeline(
    entity_id:  int | None = None,
    tag_id:     int | None = None,
    date_from:  str | None = None,
    date_to:    str | None = None,
    doc_ids:    list[int] | None = None,
) -> dict:
    """
    Build the full timeline payload.

    Returns:
        {
            dated_events:   [...],   # sorted by date ascending
            undated_events: [...],   # documents without a date
            total_dated:    int,
            total_undated:  int,
        }
    """
    from modules.db import get_db
    from modules.extractor import transaction_score

    with get_db() as conn:
        dated_events   = []
        undated_events = []

        # ── Standalone document transaction events ────────────────────────────
        txn_sql, txn_params = _build_txn_query(entity_id, tag_id, date_from, date_to, doc_ids)
        for row in conn.execute(txn_sql, txn_params).fetchall():
            t = dict(row)
            dated_events.append({
                "type":         "transaction",
                "date":         t["date"],
                "doc_id":       t["document_id"],
                "doc_title":    t["doc_title"],
                "seller":       t.get("seller"),
                "buyer":        t.get("buyer"),
                "price":        t.get("price"),
                "currency":     t.get("currency"),
                "auction_house":t.get("auction_house"),
                "lot_number":   t.get("lot_number"),
                "location":     t.get("location"),
                "notes":        t.get("notes"),
                "label":        _txn_label(t),
                "score":        transaction_score(t),
            })

        # ── Group transaction events ──────────────────────────────────────────
        grp_txn_sql, grp_txn_params = _build_group_txn_query(entity_id, date_from, date_to)
        for row in conn.execute(grp_txn_sql, grp_txn_params).fetchall():
            t = dict(row)
            dated_events.append({
                "type":         "transaction",
                "date":         t["date"],
                "group_id":     t["group_id"],
                "doc_id":       f"grp_{t['group_id']}",
                "doc_title":    t["group_title"],
                "seller":       t.get("seller"),
                "buyer":        t.get("buyer"),
                "price":        t.get("price"),
                "currency":     t.get("currency"),
                "auction_house":t.get("auction_house"),
                "lot_number":   t.get("lot_number"),
                "location":     t.get("location"),
                "notes":        t.get("notes"),
                "label":        _txn_label(t),
                "score":        transaction_score(t),
            })

        # ── Standalone document date events ───────────────────────────────────
        doc_sql, doc_params = _build_doc_query(entity_id, tag_id, date_from, date_to, doc_ids)
        for row in conn.execute(doc_sql, doc_params).fetchall():
            d = dict(row)
            eff_date = d.get("date_depicted")
            entity_names = [e.strip() for e in (d.get("entity_names") or "").split(",") if e.strip()]

            event = {
                "type":          "document",
                "doc_id":        d["id"],
                "doc_title":     d.get("title"),
                "entity_names":  entity_names,
                "location":      d.get("location"),
                "medium":        d.get("medium"),
                "is_key_evidence": bool(d.get("is_key_evidence")),
                "label":         d.get("title") or d.get("filename"),
            }

            if eff_date:
                event["date"] = eff_date
                dated_events.append(event)
            else:
                undated_events.append(event)

        # ── Group date events ─────────────────────────────────────────────────
        grp_doc_sql, grp_doc_params = _build_group_doc_query(entity_id, date_from, date_to)
        for row in conn.execute(grp_doc_sql, grp_doc_params).fetchall():
            g = dict(row)
            eff_date = g.get("date_depicted")
            entity_names = [e.strip() for e in (g.get("entity_names") or "").split(",") if e.strip()]

            event = {
                "type":          "document",
                "group_id":      g["id"],
                "doc_id":        f"grp_{g['id']}",
                "doc_title":     g.get("title"),
                "entity_names":  entity_names,
                "location":      g.get("location"),
                "medium":        g.get("medium"),
                "is_key_evidence": bool(g.get("is_key_evidence")),
                "label":         g.get("title") or f"Group #{g['id']}",
            }

            if eff_date:
                event["date"] = eff_date
                dated_events.append(event)
            else:
                undated_events.append(event)

        # Sort dated events chronologically
        dated_events.sort(key=lambda e: (e.get("date") or ""))

    return {
        "dated_events":   dated_events,
        "undated_events": undated_events,
        "total_dated":    len(dated_events),
        "total_undated":  len(undated_events),
    }


# ── SQL builders ──────────────────────────────────────────────────────────────

def _build_txn_query(entity_id, tag_id, date_from, date_to, doc_ids):
    sql = """
        SELECT t.*, d.title as doc_title
        FROM transactions t
        JOIN documents d ON d.id = t.document_id
    """
    joins, wheres, params = [], [], []

    if entity_id:
        joins.append("JOIN document_entities de ON de.document_id = t.document_id AND de.entity_id = ?")
        params.append(entity_id)
    if tag_id:
        joins.append("JOIN document_tags dt ON dt.document_id = t.document_id AND dt.tag_id = ?")
        params.append(tag_id)

    wheres.append("d.is_trashed = 0")
    wheres.append("d.group_id IS NULL")
    wheres.append("t.date IS NOT NULL")

    if date_from:
        wheres.append("t.date >= ?")
        params.append(date_from)
    if date_to:
        wheres.append("t.date <= ?")
        params.append(date_to)
    if doc_ids:
        placeholders = ",".join("?" * len(doc_ids))
        wheres.append(f"t.document_id IN ({placeholders})")
        params.extend(doc_ids)

    sql += " " + " ".join(joins)
    if wheres:
        sql += " WHERE " + " AND ".join(wheres)
    sql += " ORDER BY t.date"
    return sql, params


def _build_doc_query(entity_id, tag_id, date_from, date_to, doc_ids):
    sql = """
        SELECT d.id, d.title, d.filename, d.date_depicted, d.date_range_start,
               d.location, d.medium, d.is_key_evidence,
               GROUP_CONCAT(e.name) as entity_names
        FROM documents d
        LEFT JOIN document_entities de ON de.document_id = d.id
        LEFT JOIN entities e ON e.id = de.entity_id
    """
    joins, wheres, params = [], [], []

    if entity_id:
        wheres.append("de.entity_id = ?")
        params.append(entity_id)
    if tag_id:
        joins.append("JOIN document_tags dt ON dt.document_id = d.id AND dt.tag_id = ?")
        params.insert(0, tag_id)  # join params come before where params

    wheres.append("d.is_trashed = 0")
    wheres.append("d.group_id IS NULL")

    eff_date_expr = "COALESCE(d.date_depicted, d.date_range_start)"
    if date_from:
        wheres.append(f"{eff_date_expr} >= ?")
        params.append(date_from)
    if date_to:
        wheres.append(f"{eff_date_expr} <= ?")
        params.append(date_to)
    if doc_ids:
        placeholders = ",".join("?" * len(doc_ids))
        wheres.append(f"d.id IN ({placeholders})")
        params.extend(doc_ids)

    sql += " " + " ".join(joins)
    if wheres:
        sql += " WHERE " + " AND ".join(wheres)
    sql += " GROUP BY d.id ORDER BY " + eff_date_expr

    return sql, params


def _build_group_txn_query(entity_id, date_from, date_to):
    sql = """
        SELECT gt.*, g.title as group_title
        FROM group_transactions gt
        JOIN document_groups g ON g.id = gt.group_id
    """
    joins, wheres, params = [], ["g.is_trashed = 0", "gt.date IS NOT NULL"], []
    if entity_id:
        joins.append("JOIN group_entities ge ON ge.group_id = g.id AND ge.entity_id = ?")
        params.append(entity_id)
    if date_from:
        wheres.append("gt.date >= ?")
        params.append(date_from)
    if date_to:
        wheres.append("gt.date <= ?")
        params.append(date_to)
    sql += " " + " ".join(joins)
    sql += " WHERE " + " AND ".join(wheres) + " ORDER BY gt.date"
    return sql, params


def _build_group_doc_query(entity_id, date_from, date_to):
    sql = """
        SELECT g.id, g.title, g.date_depicted, g.date_range_start,
               g.location, g.medium, g.is_key_evidence,
               GROUP_CONCAT(e.name) as entity_names
        FROM document_groups g
        LEFT JOIN group_entities ge ON ge.group_id = g.id
        LEFT JOIN entities e ON e.id = ge.entity_id
    """
    wheres = ["g.is_trashed = 0"]
    params = []
    if entity_id:
        # Restrict to groups that have at least one row for this entity in the
        # LEFT JOIN above; using the LEFT JOIN's column in WHERE effectively
        # turns it into an INNER JOIN for matching rows only.
        wheres.append("g.id IN (SELECT group_id FROM group_entities WHERE entity_id = ?)")
        params.append(entity_id)
    eff_date_expr = "COALESCE(g.date_depicted, g.date_range_start)"
    if date_from:
        wheres.append(f"{eff_date_expr} >= ?")
        params.append(date_from)
    if date_to:
        wheres.append(f"{eff_date_expr} <= ?")
        params.append(date_to)
    sql += " WHERE " + " AND ".join(wheres)
    sql += " GROUP BY g.id ORDER BY " + eff_date_expr
    return sql, params


def _txn_label(t: dict) -> str:
    """Short human-readable label for a transaction event."""
    parts = []
    if t.get("auction_house"):
        parts.append(t["auction_house"])
    if t.get("seller") and t.get("buyer"):
        parts.append(f"{t['seller']} → {t['buyer']}")
    elif t.get("seller"):
        parts.append(f"Sold by {t['seller']}")
    elif t.get("buyer"):
        parts.append(f"Acquired by {t['buyer']}")
    if t.get("price"):
        currency = t.get("currency") or ""
        parts.append(f"{currency} {t['price']:,.0f}".strip())
    return " · ".join(parts) if parts else "Transaction"
