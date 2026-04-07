"""
timeline_routes.py – Flask Blueprint for the provenance timeline.

Routes:
  GET /api/timeline  – sorted timeline events with optional filters
"""

from flask import Blueprint, jsonify, request

bp = Blueprint("timeline", __name__)


@bp.route("/api/timeline", methods=["GET"])
def get_timeline():
    from modules.timeline import get_timeline

    entity_id = request.args.get("entity_id", type=int)
    tag_id    = request.args.get("tag_id",    type=int)
    date_from = request.args.get("date_from")
    date_to   = request.args.get("date_to")

    result = get_timeline(
        entity_id=entity_id,
        tag_id=tag_id,
        date_from=date_from,
        date_to=date_to,
    )
    return jsonify(result)
