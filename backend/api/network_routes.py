"""
network_routes.py – Flask Blueprint for the provenance network graph
and LLM-inferred relationship edges.

Routes:
  GET  /api/network                  – {nodes, edges, typed_edges?} payload
  POST /api/relationships/refresh    – kick off the batch backfill (async)
  GET  /api/relationships/status     – progress of current/last refresh
"""

from flask import Blueprint, jsonify, request

bp = Blueprint("network", __name__)


@bp.route("/api/network", methods=["GET"])
def get_network():
    from modules.network import get_network, ALLOWED_TYPES

    entity_id = request.args.get("entity_id", type=int)
    tag_id    = request.args.get("tag_id",    type=int)
    doc_id    = request.args.get("doc_id",    type=int)
    date_from = request.args.get("date_from")
    date_to   = request.args.get("date_to")
    max_nodes = min(int(request.args.get("max_nodes", 400)), 800)
    min_weight = max(int(request.args.get("min_weight", 1)), 1)

    relationships = request.args.get("relationships", "false").lower() in ("1", "true", "yes")

    # `types` is a comma-separated list of entity types; defaults to people.
    raw_types = request.args.get("types", "person")
    types = tuple(t.strip() for t in raw_types.split(",")
                  if t.strip() in ALLOWED_TYPES) or ("person",)

    result = get_network(
        types=types,
        entity_id=entity_id,
        tag_id=tag_id,
        doc_id=doc_id,
        date_from=date_from,
        date_to=date_to,
        max_nodes=max_nodes,
        min_weight=min_weight,
        relationships=relationships,
    )
    return jsonify(result)


@bp.route("/api/relationships/refresh", methods=["POST"])
def refresh_relationships():
    """Start a background refresh. Returns immediately with status."""
    from modules.relationships import refresh_in_background, is_running, get_status

    if is_running():
        payload = {"error": "A refresh is already running", **get_status()}
        return jsonify(payload), 409
    try:
        status = refresh_in_background()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify(status)


@bp.route("/api/relationships/status", methods=["GET"])
def relationship_status():
    from modules.relationships import get_status
    return jsonify(get_status())
