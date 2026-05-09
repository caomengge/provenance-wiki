"""
transactions.py – CRUD endpoints for individual transactions.

Routes (standalone documents):
  POST   /api/documents/<doc_id>/transactions      – add a transaction
  PATCH  /api/transactions/<txn_id>                – update a transaction
  DELETE /api/transactions/<txn_id>                – delete a transaction

Routes (document groups):
  POST   /api/groups/<group_id>/transactions       – add a group transaction
  PATCH  /api/group_transactions/<txn_id>          – update a group transaction
  DELETE /api/group_transactions/<txn_id>          – delete a group transaction
"""

from flask import Blueprint, abort, jsonify, request
from modules.db import get_db

bp = Blueprint("transactions", __name__)

_ALLOWED = {"seller", "buyer", "date", "price", "currency",
            "auction_house", "lot_number", "location", "notes"}


def _to_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _coerce(data: dict) -> dict:
    out = {k: v for k, v in data.items() if k in _ALLOWED}
    if "price" in out:
        out["price"] = _to_float(out["price"])
    if "lot_number" in out and out["lot_number"] is not None:
        out["lot_number"] = str(out["lot_number"])
    return out


# ── Standalone document transactions ─────────────────────────────────────────

@bp.post("/api/documents/<int:doc_id>/transactions")
def create_doc_transaction(doc_id):
    data = _coerce(request.get_json(force=True) or {})
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone():
            abort(404)
        cur = conn.execute(
            """INSERT INTO transactions
               (document_id, seller, buyer, date, price, currency,
                auction_house, lot_number, location, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, data.get("seller"), data.get("buyer"), data.get("date"),
             data.get("price"), data.get("currency"), data.get("auction_house"),
             data.get("lot_number"), data.get("location"), data.get("notes")),
        )
        row = conn.execute("SELECT * FROM transactions WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@bp.patch("/api/transactions/<int:txn_id>")
def update_doc_transaction(txn_id):
    updates = _coerce(request.get_json(force=True) or {})
    if not updates:
        abort(400)
    sets = ", ".join(f"{k} = ?" for k in updates)
    with get_db() as conn:
        conn.execute(f"UPDATE transactions SET {sets} WHERE id=?",
                     [*updates.values(), txn_id])
        row = conn.execute("SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone()
    if not row:
        abort(404)
    return jsonify(dict(row))


@bp.delete("/api/transactions/<int:txn_id>")
def delete_doc_transaction(txn_id):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM transactions WHERE id=?", (txn_id,))
    if cur.rowcount == 0:
        abort(404)
    return jsonify({"ok": True})


# ── Group transactions ────────────────────────────────────────────────────────

@bp.post("/api/groups/<int:group_id>/transactions")
def create_group_transaction(group_id):
    data = _coerce(request.get_json(force=True) or {})
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM document_groups WHERE id=?", (group_id,)).fetchone():
            abort(404)
        cur = conn.execute(
            """INSERT INTO group_transactions
               (group_id, seller, buyer, date, price, currency,
                auction_house, lot_number, location, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (group_id, data.get("seller"), data.get("buyer"), data.get("date"),
             data.get("price"), data.get("currency"), data.get("auction_house"),
             data.get("lot_number"), data.get("location"), data.get("notes")),
        )
        row = conn.execute("SELECT * FROM group_transactions WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@bp.patch("/api/group_transactions/<int:txn_id>")
def update_group_transaction(txn_id):
    updates = _coerce(request.get_json(force=True) or {})
    if not updates:
        abort(400)
    sets = ", ".join(f"{k} = ?" for k in updates)
    with get_db() as conn:
        conn.execute(f"UPDATE group_transactions SET {sets} WHERE id=?",
                     [*updates.values(), txn_id])
        row = conn.execute("SELECT * FROM group_transactions WHERE id=?", (txn_id,)).fetchone()
    if not row:
        abort(404)
    return jsonify(dict(row))


@bp.delete("/api/group_transactions/<int:txn_id>")
def delete_group_transaction(txn_id):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM group_transactions WHERE id=?", (txn_id,))
    if cur.rowcount == 0:
        abort(404)
    return jsonify({"ok": True})
