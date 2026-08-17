"""
qa.py – Retrieval-Augmented Generation Q&A engine.

Pipeline:
  1. Retrieve the most relevant retrieval units via TRUE hybrid retrieval
     (modules/retrieval.py): BM25 keyword + semantic-over-transcription +
     semantic-over-generated, fused with reciprocal rank fusion. If the
     embedding provider is unavailable, retrieval degrades to keyword-only
     and the response says so — never a silent substitution.
  2. Build a grounded context whose PRIMARY EVIDENCE is the actual
     transcription excerpts retrieved from the archival documents. Each
     context item preserves: document/group ID, title, source archive,
     date, the relevant excerpt, and its representation type. AI-generated
     descriptions supplement the evidence but are explicitly labelled as
     machine-generated and never replace transcription text.
  3. Pull matching transactions for the retrieved units.
  4. Send to Claude with a system prompt that requires citation of
     [Doc #N] / [Group #N] and forbids speculation beyond the evidence.
  5. Return {answer, sources, confidence, citations, retrieval} so the
     underlying documents can be inspected directly.
"""

import logging
import re
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

QA_SYSTEM_PROMPT = """You are a meticulous museum provenance researcher. Your role is to answer questions about the ownership history of artworks and cultural objects using ONLY the source documents provided.

Rules:
1. Base every claim on the provided source documents. Never speculate or add information from outside the provided context.
2. Primary evidence is the text under "PRIMARY-SOURCE EXCERPT" — the transcribed archival document itself. Fields labelled "machine-generated" were produced by an AI at ingest time; treat them as finding aids, not as evidence.
3. When you cite a fact, always note the document ID(s) that support it (format: [Doc #N] for documents, [Group #N] for multi-page groups). Cite only documents provided in the context.
4. If the answer is not in the provided documents, say so clearly: "The available documents do not contain information about this."
5. For ownership chains, present them chronologically.
6. Note any gaps or uncertainties in the provenance record, and mention the source archive a document comes from when it matters.
7. Preserve non-English names and terms exactly as they appear in the source documents.
8. Be concise but thorough. Prefer bullet points for ownership chains."""

QA_CONTEXT_TEMPLATE = """PROVENANCE DOCUMENTS FOR CONTEXT
=================================
{doc_blocks}

RELATED TRANSACTIONS (machine-extracted)
========================================
{txn_blocks}

=================================
Based ONLY on the above documents, answer the following question:
{question}"""

# Cap on transcription text per context block, so ~15 excerpts stay well
# within the model context while remaining substantial evidence.
MAX_EXCERPT_CHARS = 4000


def _context_block(conn, unit: dict) -> str:
    """
    One context block for a retrieved unit. The retrieved transcription
    excerpt is the primary evidence; generated metadata is labelled.
    """
    label = "Doc" if unit["record_type"] == "document" else "Group"
    lines = [f"[{label} #{unit['unit_id']}] {unit.get('title') or 'Untitled'}"]
    lines.append(f"Source archive: {unit.get('source_archive') or 'Unknown'}")
    lines.append(f"Date: {unit.get('date_display') or 'Unknown'}")

    # Primary evidence: transcription chunks retrieved for this unit
    # (deduplicated, in chunk order), else the unit's best excerpt.
    t_chunks = {}
    for c in unit.get("matched_chunks", []):
        if c["representation_type"] == "transcription":
            t_chunks.setdefault(c["chunk_index"], c["text"])
    excerpt = "\n[…]\n".join(t_chunks[i] for i in sorted(t_chunks))
    if not excerpt and unit.get("representation_type") == "transcription":
        excerpt = unit.get("excerpt") or ""
    if excerpt:
        if len(excerpt) > MAX_EXCERPT_CHARS:
            excerpt = excerpt[:MAX_EXCERPT_CHARS] + "\n[…excerpt truncated…]"
        lines.append("PRIMARY-SOURCE EXCERPT (transcription, retrieved passage):")
        lines.append(excerpt)
    else:
        lines.append("PRIMARY-SOURCE EXCERPT: (no transcription passage retrieved "
                     "for this record)")

    # Supplementary, clearly labelled machine-generated description.
    table = "documents" if unit["record_type"] == "document" else "document_groups"
    row = conn.execute(f"SELECT description, annotation FROM {table} WHERE id=?",
                       (unit["unit_id"],)).fetchone()
    if row and row["description"]:
        lines.append(f"Machine-generated description (finding aid, not evidence): "
                     f"{row['description']}")
    if row and row["annotation"]:
        lines.append(f"Researcher note: {row['annotation']}")

    return "\n".join(lines)


def answer_question(question: str, api_key: str) -> dict[str, Any]:
    """
    Run the full Q&A pipeline for a provenance research question.

    Returns:
        {
            answer:     str,
            sources:    [{id, record_type}],
            confidence: 'high' | 'medium' | 'low' | 'none',
            citations:  [{doc_id, record_type, title, source_archive, snippet}],
            retrieval:  {mode, semantic_available, semantic_error, fusion,
                         provider, model},
        }
    """
    from modules.db import get_db
    from modules.retrieval import hybrid_retrieve
    from config import QA_CONTEXT_DOCS, QA_MAX_TOKENS

    with get_db() as conn:
        retrieval = hybrid_retrieve(conn, question, top_k=QA_CONTEXT_DOCS)
        units = retrieval["results"]

        retrieval_meta = {
            "mode": "hybrid" if retrieval["semantic_available"] else "keyword_only",
            "semantic_available": retrieval["semantic_available"],
            "semantic_error": retrieval["semantic_error"],
            "fusion": retrieval["fusion"],
            "provider": retrieval.get("provider"),
            "model": retrieval.get("model"),
        }

        if not units:
            return {
                "answer":     "No relevant documents were found in the archive for this question.",
                "sources":    [],
                "confidence": "none",
                "citations":  [],
                "retrieval":  retrieval_meta,
            }

        doc_ids   = [u["unit_id"] for u in units if u["record_type"] == "document"]
        group_ids = [u["unit_id"] for u in units if u["record_type"] == "group"]

        # Context blocks grounded in retrieved transcription excerpts.
        doc_blocks = [_context_block(conn, u) for u in units]

        # Matching transactions for the retrieved units.
        txn_rows = []
        if doc_ids:
            ph = ",".join("?" * len(doc_ids))
            txn_rows += [("Doc", dict(t)) for t in conn.execute(
                f"""SELECT * FROM transactions
                    WHERE document_id IN ({ph}) ORDER BY date""", doc_ids)]
        if group_ids:
            ph = ",".join("?" * len(group_ids))
            txn_rows += [("Group", {**dict(t), "document_id": t["group_id"]})
                         for t in conn.execute(
                f"""SELECT * FROM group_transactions
                    WHERE group_id IN ({ph}) ORDER BY date""", group_ids)]

    txn_blocks = []
    for label, t in txn_rows:
        parts = [f"[{label} #{t['document_id']}]"]
        if t.get("date"):          parts.append(f"Date: {t['date']}")
        if t.get("seller"):        parts.append(f"Seller: {t['seller']}")
        if t.get("buyer"):         parts.append(f"Buyer: {t['buyer']}")
        if t.get("price"):
            parts.append(f"Price: {t['price']} {t.get('currency') or ''}".rstrip())
        if t.get("auction_house"): parts.append(
            f"Auction: {t['auction_house']} lot {t.get('lot_number') or ''}".rstrip())
        if t.get("location"):      parts.append(f"Location: {t['location']}")
        if t.get("notes"):         parts.append(f"Notes: {t['notes']}")
        txn_blocks.append(" | ".join(parts))

    context = QA_CONTEXT_TEMPLATE.format(
        doc_blocks="\n\n---\n\n".join(doc_blocks) or "No documents.",
        txn_blocks="\n".join(txn_blocks) or "No transactions recorded.",
        question=question,
    )

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
            "sources":    [{"id": u["unit_id"], "record_type": u["record_type"]}
                           for u in units],
            "confidence": "none",
            "citations":  [],
            "retrieval":  retrieval_meta,
        }

    # Extract cited IDs, keeping doc/group namespaces distinct.
    cited = set()
    for label, num in re.findall(r"\[(Doc|Group)\s*#(\d+)\]", answer_text):
        cited.add(("document" if label == "Doc" else "group", int(num)))

    if len(cited) >= 3:
        confidence = "high"
    elif len(cited) >= 1:
        confidence = "medium"
    elif ("do not contain" in answer_text.lower()
          or "no information" in answer_text.lower()):
        confidence = "none"
    else:
        confidence = "low"

    citations = []
    for u in units:
        if (u["record_type"], u["unit_id"]) in cited:
            snippet = (u.get("excerpt") or "")[:200]
            citations.append({
                "doc_id":              u["unit_id"],
                "record_type":         u["record_type"],
                "title":               u.get("title") or "Untitled",
                "source_archive":      u.get("source_archive"),
                "date":                u.get("date_display"),
                "representation_type": u.get("representation_type"),
                "snippet":             snippet,
            })

    return {
        "answer":     answer_text,
        "sources":    [{"id": u["unit_id"], "record_type": u["record_type"]}
                       for u in units],
        "confidence": confidence,
        "citations":  citations,
        "retrieval":  retrieval_meta,
    }
