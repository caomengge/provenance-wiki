"""
export_routes.py – Flask Blueprint for export.

Routes:
  GET  /api/export/timeline        – export timeline events as CSV

PDF exports (entity history, selected-document dossier) were removed in
release v1 by decision of the project owner: they were not essential and
required bundling native PDF libraries (WeasyPrint/Pango) in the desktop
apps. They remain in git history if they are wanted again.
"""

import io

from flask import Blueprint, request, send_file

bp = Blueprint("export", __name__)


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
