"""
qa.py – Retrieval-Augmented Generation Q&A engine.

Pipeline:
  1. Parse the question to extract named entities and date hints.
  2. Retrieve the top-15 most relevant documents via hybrid search
     (FTS5 keyword + optional embedding re-rank).
  3. Pull any matching transactions from those documents.
  4. Assemble a grounded context block and send to Claude claude-sonnet-4-6.
  5. Return {answer, sources: [document_ids], confidence, citations}.

The system prompt instructs Claude to cite source documents and refuse to
speculate beyond the evidence.
"""

import json
import logging
import re
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

QA_SYSTEM_PROMPT = """You are a meticulous museum provenance researcher. Your role is to answer questions about the ownership history of artworks and cultural objects using ONLY the source documents provided.

Rules:
1. Base every claim on the provided source documents. Never speculate or add information from outside the provided context.
2. When you cite a fact, always note the document ID(s) that support it (format: [Doc #N]).
3. If the answer is not in the provided documents, say so clearly: "The available documents do not contain information about this."
4. For ownership chains, present them chronologically.
5. Note any gaps or uncertainties in the provenance record.
6. Preserve non-English names and terms exactly as they appear in the source documents.
7. Be concise but thorough. Prefer bullet points for ownership chains."""

QA_CONTEXT_TEMPLATE = """PROVENANCE DOCUMENTS FOR CONTEXT
=================================
{doc_blocks}

RELATED TRANSACTIONS
====================
{txn_blocks}

=================================
Based ONLY on the above documents, answer the following question:
{question}"""


def answer_question(question: str, api_key: str) -> dict[str, Any]:
    """
    Run the full Q&A pipeline for a provenance research question.

    Returns:
        {
            answer:     str,
            sources:    [document_ids],
            confidence: 'high' | 'medium' | 'low' | 'none',
            citations:  [{doc_id, title, snippet}],
        }
    """
    from modules.search import search_documents
    from modules.db import get_db
    from config import QA_CONTEXT_DOCS, QA_MAX_TOKENS

    # 1. Retrieve relevant documents via keyword search
    results = search_documents(question, mode="keyword", page=1, per_page=QA_CONTEXT_DOCS)
    docs = results.get("results", [])

    if not docs:
        # Try semantic as fallback
        results = search_documents(question, mode="semantic", page=1, per_page=QA_CONTEXT_DOCS)
        docs = results.get("results", [])

    if not docs:
        return {
            "answer":     "No relevant documents were found in the archive for this question.",
            "sources":    [],
            "confidence": "none",
            "citations":  [],
        }

    # Separate standalone docs from group results
    solo_docs  = [d for d in docs if d.get("record_type") != "group"]
    group_hits = [d for d in docs if d.get("record_type") == "group"]

    doc_ids   = [d["id"] for d in solo_docs]
    group_ids = [g["id"] for g in group_hits]

    # 2. Pull transactions and full details
    with get_db() as conn:
        txn_blocks_raw = []

        if doc_ids:
            placeholders = ",".join("?" * len(doc_ids))
            txns = conn.execute(
                f"""SELECT t.*, d.title as doc_title
                    FROM transactions t
                    JOIN documents d ON d.id = t.document_id
                    WHERE t.document_id IN ({placeholders})
                    ORDER BY t.date""",
                doc_ids,
            ).fetchall()
            txn_blocks_raw.extend(txns)

            full_docs = conn.execute(
                f"SELECT * FROM documents WHERE id IN ({placeholders})",
                doc_ids,
            ).fetchall()
            full_docs_map = {d["id"]: dict(d) for d in full_docs}
        else:
            full_docs_map = {}

        if group_ids:
            gplaceholders = ",".join("?" * len(group_ids))
            group_txns = conn.execute(
                f"""SELECT gt.*, g.title as doc_title, gt.group_id as document_id
                    FROM group_transactions gt
                    JOIN document_groups g ON g.id = gt.group_id
                    WHERE gt.group_id IN ({gplaceholders})
                    ORDER BY gt.date""",
                group_ids,
            ).fetchall()
            txn_blocks_raw.extend(group_txns)

            full_groups = conn.execute(
                f"SELECT * FROM document_groups WHERE id IN ({gplaceholders})",
                group_ids,
            ).fetchall()
            full_groups_map = {g["id"]: dict(g) for g in full_groups}
        else:
            full_groups_map = {}

    # 3. Build context blocks
    doc_blocks = []
    for doc in solo_docs:
        full = full_docs_map.get(doc["id"], doc)
        block_lines = [
            f"[Doc #{doc['id']}] {full.get('title', 'Untitled')}",
            f"Date: {full.get('date_depicted') or full.get('date_range_start') or 'Unknown'}",
            f"Location: {full.get('location') or 'Unknown'}",
            f"Description: {full.get('description') or ''}",
        ]
        if full.get("annotation"):
            block_lines.append(f"Researcher note: {full['annotation']}")
        doc_blocks.append("\n".join(block_lines))

    for grp in group_hits:
        full = full_groups_map.get(grp["id"], grp)
        block_lines = [
            f"[Doc #{grp['id']}] (multi-page group) {full.get('title', 'Untitled')}",
            f"Date: {full.get('date_depicted') or full.get('date_range_start') or 'Unknown'}",
            f"Location: {full.get('location') or 'Unknown'}",
            f"Description: {full.get('description') or ''}",
        ]
        if full.get("annotation"):
            block_lines.append(f"Researcher note: {full['annotation']}")
        doc_blocks.append("\n".join(block_lines))

    txn_blocks = []
    for txn in txn_blocks_raw:
        t = dict(txn)
        parts = [f"[Doc #{t['document_id']}]"]
        if t.get("date"):        parts.append(f"Date: {t['date']}")
        if t.get("seller"):      parts.append(f"Seller: {t['seller']}")
        if t.get("buyer"):       parts.append(f"Buyer: {t['buyer']}")
        if t.get("price"):
            currency = t.get("currency") or ""
            parts.append(f"Price: {t['price']} {currency}")
        if t.get("auction_house"): parts.append(f"Auction: {t['auction_house']} lot {t.get('lot_number','')}")
        if t.get("location"):    parts.append(f"Location: {t['location']}")
        if t.get("notes"):       parts.append(f"Notes: {t['notes']}")
        txn_blocks.append(" | ".join(parts))

    context = QA_CONTEXT_TEMPLATE.format(
        doc_blocks="\n\n".join(doc_blocks) or "No documents.",
        txn_blocks="\n".join(txn_blocks) or "No transactions recorded.",
        question=question,
    )

    # 4. Call Claude
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=QA_MAX_TOKENS,
            system=QA_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": context}],
        )
        answer_text = response.content[0].text.strip()
    except Exception as exc:
        logger.exception("Claude Q&A failed")
        return {
            "answer":     f"Error calling Claude API: {exc}",
            "sources":    doc_ids,
            "confidence": "none",
            "citations":  [],
        }

    # 5. Extract cited doc IDs from the answer
    cited_ids = list(set(int(m) for m in re.findall(r"\[Doc #(\d+)\]", answer_text)))

    # Confidence heuristic: high if ≥3 docs cited, medium if 1-2, low if none
    if len(cited_ids) >= 3:
        confidence = "high"
    elif len(cited_ids) >= 1:
        confidence = "medium"
    elif "do not contain" in answer_text.lower() or "no information" in answer_text.lower():
        confidence = "none"
    else:
        confidence = "low"

    citations = []
    for doc in docs:
        if doc["id"] in cited_ids:
            citations.append({
                "doc_id":       doc["id"],
                "title":        doc.get("title", "Untitled"),
                "snippet":      doc.get("description", "")[:200],
                "record_type":  doc.get("record_type", "document"),
            })

    all_source_ids = doc_ids + group_ids
    return {
        "answer":     answer_text,
        "sources":    all_source_ids,
        "confidence": confidence,
        "citations":  citations,
    }
