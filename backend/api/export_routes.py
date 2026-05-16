"""
export_routes.py – Flask Blueprint for export.

Routes:
  GET  /api/export/timeline        – export timeline events as CSV
  GET  /api/export/entity/:id      – export entity provenance history as PDF
  POST /api/export/selection       – export selected document IDs as PDF dossier
"""

import io
import logging
from datetime import datetime

from flask import Blueprint, abort, jsonify, request, send_file

bp = Blueprint("export", __name__)
logger = logging.getLogger(__name__)


# ── Shared HTML template ──────────────────────────────────────────────────────

def _base_html(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<style>
  @import url('https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,600;1,400&display=swap');
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'EB Garamond', Georgia, serif; font-size: 11pt; color: #2c2416;
          margin: 2cm 2.5cm; line-height: 1.6; }}
  h1 {{ font-size: 22pt; margin-bottom: 0.3cm; border-bottom: 2px solid #c9a84c; padding-bottom: 0.2cm; }}
  h2 {{ font-size: 14pt; margin-top: 0.7cm; margin-bottom: 0.2cm; color: #1a2332; }}
  h3 {{ font-size: 11pt; margin-top: 0.4cm; font-weight: 600; }}
  .meta {{ color: #7a6952; font-size: 9pt; margin-bottom: 0.5cm; }}
  .event {{ border-left: 3px solid #c9a84c; padding-left: 0.5cm; margin-bottom: 0.4cm; }}
  .event.key {{ border-color: #8b1a1a; }}
  .label {{ font-weight: 600; }}
  .date  {{ color: #7a6952; font-size: 9.5pt; }}
  .entity {{ display: inline-block; background: #f5f0e8; border: 1px solid #d4c9a8;
             border-radius: 3px; padding: 1px 6px; margin: 2px; font-size: 9pt; }}
  table  {{ width: 100%; border-collapse: collapse; margin-top: 0.3cm; font-size: 9.5pt; }}
  th, td {{ border: 1px solid #d4c9a8; padding: 4px 8px; text-align: left; }}
  th     {{ background: #f5f0e8; font-weight: 600; }}
  .footer {{ margin-top: 1cm; font-size: 8pt; color: #7a6952; text-align: center; }}
  .page-break {{ page-break-after: always; }}
</style>
</head>
<body>
{body}
<div class="footer">Provenance Archive Wiki · Exported {datetime.now().strftime('%B %d, %Y')}</div>
</body>
</html>"""


def _render_pdf(html: str) -> bytes:
    """Render HTML to PDF bytes using WeasyPrint."""
    try:
        from weasyprint import HTML
        return HTML(string=html).write_pdf()
    except ImportError:
        raise RuntimeError("WeasyPrint is not installed. Run: pip install weasyprint")


# ── Timeline export ───────────────────────────────────────────────────────────

_TIMELINE_CSV_COLUMNS = [
    "date", "type", "label", "doc_id", "doc_title",
    "seller", "buyer", "price", "currency", "auction_house", "lot_number",
    "location", "notes", "score", "entity_names", "medium", "is_key_evidence",
]


@bp.route("/api/export/timeline", methods=["GET"])
def export_timeline():
    """Export the (filtered) timeline's dated events as a CSV file."""
    import csv
    from modules.timeline import get_timeline

    entity_id = request.args.get("entity_id", type=int)
    tag_id    = request.args.get("tag_id",    type=int)
    date_from = request.args.get("date_from")
    date_to   = request.args.get("date_to")

    data = get_timeline(entity_id=entity_id, tag_id=tag_id,
                        date_from=date_from, date_to=date_to)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_TIMELINE_CSV_COLUMNS,
                            extrasaction="ignore")
    writer.writeheader()
    for ev in data["dated_events"]:
        row = dict(ev)
        names = row.get("entity_names")
        if isinstance(names, list):
            row["entity_names"] = "; ".join(names)
        writer.writerow(row)

    # UTF-8 BOM so Excel reads non-ASCII (Chinese, etc.) names correctly.
    payload = ("﻿" + buf.getvalue()).encode("utf-8")
    return send_file(
        io.BytesIO(payload),
        mimetype="text/csv",
        as_attachment=True,
        download_name="provenance_timeline.csv",
    )


# ── Entity history export ─────────────────────────────────────────────────────

@bp.route("/api/export/entity/<int:entity_id>", methods=["GET"])
def export_entity(entity_id):
    from modules.db import get_db, rows_to_list, row_to_dict

    with get_db() as conn:
        entity = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        if not entity:
            abort(404)
        entity = row_to_dict(entity)

        docs = conn.execute(
            """SELECT d.id, d.title, d.date_depicted, d.date_range_start,
                      d.location, d.description, d.is_key_evidence, de.role, de.context
               FROM document_entities de
               JOIN documents d ON d.id = de.document_id
               WHERE de.entity_id = ?
               ORDER BY COALESCE(d.date_depicted, d.date_range_start)""",
            (entity_id,)
        ).fetchall()

        txns = conn.execute(
            """SELECT t.*, d.title as doc_title
               FROM transactions t
               JOIN documents d ON d.id = t.document_id
               JOIN document_entities de ON de.document_id = t.document_id AND de.entity_id=?
               ORDER BY t.date""",
            (entity_id,)
        ).fetchall()

    doc_blocks = ""
    for d in docs:
        d = dict(d)
        key = "★ KEY EVIDENCE · " if d.get("is_key_evidence") else ""
        date = d.get("date_depicted") or d.get("date_range_start") or "Unknown"
        doc_blocks += f"""
            <div class="event">
              <span class="date">{key}{date}</span>
              <div class="label">{d.get('title','Untitled')}</div>
              <div>{d.get('role','').capitalize() or ''}{': ' if d.get('context') else ''}{d.get('context','')}</div>
              <div style="color:#555;font-size:9pt">{(d.get('description') or '')[:200]}</div>
            </div>"""

    txn_rows = ""
    for t in txns:
        t = dict(t)
        txn_rows += f"""<tr>
            <td>{t.get('date','')}</td>
            <td>{t.get('seller','')}</td>
            <td>{t.get('buyer','')}</td>
            <td>{t.get('currency','')} {t.get('price','') or ''}</td>
            <td>{t.get('auction_house','')}</td>
            <td>{t.get('doc_title','')}</td>
        </tr>"""

    html = _base_html(
        f"Entity History: {entity['name']}",
        f"""<h1>{entity['name']}</h1>
        <div class="meta">{entity['type'].capitalize()} · {len(docs)} document(s)</div>
        <h2>Document Appearances</h2>
        {doc_blocks or '<p><em>No documents found.</em></p>'}
        <h2>Transaction History</h2>
        {'<table><tr><th>Date</th><th>Seller</th><th>Buyer</th><th>Price</th><th>Auction House</th><th>Source</th></tr>' + txn_rows + '</table>' if txn_rows else '<p><em>No transactions found.</em></p>'}
        """
    )

    try:
        pdf_bytes = _render_pdf(html)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    safe_name = entity["name"].replace(" ", "_")[:40]
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"entity_{safe_name}.pdf",
    )


# ── Selection export ──────────────────────────────────────────────────────────

@bp.route("/api/export/selection", methods=["POST"])
def export_selection():
    from modules.db import get_db, rows_to_list

    data    = request.get_json(silent=True) or {}
    doc_ids = data.get("doc_ids", [])

    if not doc_ids:
        return jsonify({"error": "doc_ids is required"}), 400

    placeholders = ",".join("?" * len(doc_ids))

    with get_db() as conn:
        docs = conn.execute(
            f"""SELECT d.*, GROUP_CONCAT(e.name, ', ') as entity_names
                FROM documents d
                LEFT JOIN document_entities de ON de.document_id = d.id
                LEFT JOIN entities e ON e.id = de.entity_id
                WHERE d.id IN ({placeholders})
                GROUP BY d.id
                ORDER BY COALESCE(d.date_depicted, d.date_range_start)""",
            doc_ids
        ).fetchall()

    blocks = ""
    for d in docs:
        d = dict(d)
        d.pop("embedding_json", None)
        key = "<strong>★ KEY EVIDENCE</strong><br/>" if d.get("is_key_evidence") else ""
        date = d.get("date_depicted") or d.get("date_range_start") or "Date unknown"
        entities = d.get("entity_names") or ""

        blocks += f"""<div class="event" style="margin-bottom:0.8cm">
            <h3>{key}Doc #{d['id']} — {d.get('title','Untitled')}</h3>
            <div class="date">{date} · {d.get('location','')}</div>
            <div>{d.get('description','')}</div>
            {f'<div style="margin-top:4px">' + ''.join(f'<span class="entity">{e.strip()}</span>' for e in entities.split(',') if e.strip()) + '</div>' if entities else ''}
            {f'<div style="margin-top:4px;font-style:italic;color:#555">Note: {d["annotation"]}</div>' if d.get("annotation") else ''}
        </div>"""

    html = _base_html(
        "Selected Documents Dossier",
        f"""<h1>Research Dossier</h1>
        <div class="meta">{len(docs)} selected document(s) · Exported {datetime.now().strftime('%B %d, %Y')}</div>
        {blocks or '<p><em>No documents.</em></p>'}"""
    )

    try:
        pdf_bytes = _render_pdf(html)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="provenance_dossier.pdf",
    )
