"""
network_routes.py – Flask Blueprint for the provenance network graph.

Routes:
  GET /api/network  – {nodes, edges} payload for D3 force layout
"""

from flask import Blueprint, jsonify, request

bp = Blueprint("network", __name__)


@bp.route("/api/network", methods=["GET"])
def get_network():
    from modules.network import get_network

    entity_id = request.args.get("entity_id", type=int)
    tag_id    = request.args.get("tag_id",    type=int)
    doc_id    = request.args.get("doc_id",    type=int)
    date_from = request.args.get("date_from")
    date_to   = request.args.get("date_to")
    max_nodes = min(int(request.args.get("max_nodes", 200)), 500)

    result = get_network(
        entity_id=entity_id,
        tag_id=tag_id,
        doc_id=doc_id,
        date_from=date_from,
        date_to=date_to,
        max_nodes=max_nodes,
    )
    return jsonify(result)
