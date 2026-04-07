"""
qa_routes.py – Flask Blueprint for the Q&A/RAG engine.

Routes:
  POST /api/qa  – ask a provenance research question
"""

from flask import Blueprint, jsonify, request

bp = Blueprint("qa", __name__)


@bp.route("/api/qa", methods=["POST"])
def ask():
    from modules.qa import answer_question
    from config import ANTHROPIC_API_KEY

    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY is not set in .env"}), 500

    data     = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()

    if not question:
        return jsonify({"error": "question is required"}), 400
    if len(question) > 2000:
        return jsonify({"error": "question is too long (max 2000 characters)"}), 400

    result = answer_question(question, ANTHROPIC_API_KEY)
    return jsonify(result)
