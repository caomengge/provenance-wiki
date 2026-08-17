"""
qa.py – Retrieval-Augmented Generation Q&A engine.

Pipeline:
  1. Retrieve the most relevant retrieval units via TRUE hybrid retrieval
     (modules/retrieval.py): BM25 keyword over transcriptions +
     semantic-over-transcription + semantic-over-generated, fused with
     reciprocal rank fusion. If the embedding provider is unavailable,
     retrieval degrades to keyword-only and the response says so — never a
     silent substitution.
  2. EVIDENCE HYDRATION (retrieval.hydrate_evidence): for every selected
     unit that has transcription, fetch the most query-relevant
     transcription chunks from within that unit — even when the unit was
     discovered only through its AI-generated representation. Discovery
     and evidence are kept distinct: `discovery_matches` records why a
     unit was retrieved; `evidence_chunks` are the primary-source passages
     supplied for historical interpretation.
  3. Build a grounded context whose PRIMARY-SOURCE EVIDENCE is those
     hydrated transcription passages. Each context item preserves:
     document/group ID, title, source archive, date, excerpt, and
     representation type. AI-generated descriptions supplement the
     evidence but are explicitly labelled machine-generated finding aids
     and never replace transcription text; a unit with no transcription is
     flagged so no substantive historical claim rests on generated
     metadata alone.
  4. Pull matching transactions for the retrieved units.
  5. Send to Claude with a system prompt that requires citation of
     [Doc #N] / [Group #N] and forbids speculation beyond the evidence.
  6. Return {answer, sources, source_count, citations, context_items,
     retrieval} so the underlying documents can be inspected directly and
     the frontend can later show both "why this document was found" and
     "passage used as evidence". The legacy `confidence` field is
     DEPRECATED (always null): citation count is not a valid measure of
     evidentiary confidence.
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
7. A document marked "no transcription available" has only machine-generated metadata. You may mention it as a lead worth consulting, but never make a substantive historical claim that rests solely on its machine-generated metadata.
8. Preserve non-English names and terms exactly as they appear in the source documents.
9. Be concise but thorough. Prefer bullet points for ownership chains."""

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
    One context block for a retrieved unit. The HYDRATED transcription
    passages (evidence_chunks — the most query-relevant passages from
    within this unit, regardless of how the unit was discovered) are the
    primary evidence; generated metadata is a labelled finding aid.
    """
    label = "Doc" if unit["record_type"] == "document" else "Group"
    lines = [f"[{label} #{unit['unit_id']}] {unit.get('title') or 'Untitled'}"]
    lines.append(f"Source archive: {unit.get('source_archive') or 'Unknown'}")
    lines.append(f"Date: {unit.get('date_display') or 'Unknown'}")

    # Primary evidence: hydrated transcription passages, in document order.
    evidence = sorted(unit.get("evidence_chunks") or [],
                      key=lambda c: c["chunk_index"])
    excerpt = "\n[…]\n".join(c["text"] for c in evidence)
    if excerpt:
        if len(excerpt) > MAX_EXCERPT_CHARS:
            excerpt = excerpt[:MAX_EXCERPT_CHARS] + "\n[…excerpt truncated…]"
        lines.append("PRIMARY-SOURCE EVIDENCE (transcription passages most "
                     "relevant to the question):")
        lines.append(excerpt)
    else:
        lines.append("PRIMARY-SOURCE EVIDENCE: none — no transcription available "
                     "for this record. Only machine-generated metadata exists; "
                     "treat this record as a finding aid, not as evidence.")

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


def _context_item_meta(unit: dict) -> dict:
    """
    Serialisable discovery/evidence metadata for one context unit, so the
    frontend can later show "why this document was found" alongside
    "passage used as evidence".
    """
    discovery = [{
        "list": c["list"],
        "rank": c["rank"],
        "score": c["score"],
        "representation_type": c["representation_type"],
        "chunk_index": c["chunk_index"],
        "snippet": (c["text"] or "")[:300],
    } for c in unit.get("discovery_matches", [])]
    return {
        "id": unit["unit_id"],
        "record_type": unit["record_type"],
        "title": unit.get("title"),
        "source_archive": unit.get("source_archive"),
        "date": unit.get("date_display"),
        "fused_rank": unit.get("fused_rank"),
        "fused_score": unit.get("fused_score"),
        "keyword_rank": unit.get("keyword_rank"),
        "semantic_transcription_rank": unit.get("semantic_transcription_rank"),
        "semantic_generated_rank": unit.get("semantic_generated_rank"),
        "has_transcription": unit.get("has_transcription", False),
        "discovery_matches": discovery,
        "evidence_chunks": [{
            "chunk_id": c["chunk_id"],
            "chunk_index": c["chunk_index"],
            "chunk_count": c["chunk_count"],
            "method": c["method"],
            "score": c["score"],
            "text": c["text"],
        } for c in unit.get("evidence_chunks", [])],
    }


def answer_question(question: str, api_key: str) -> dict[str, Any]:
    """
    Run the full Q&A pipeline for a provenance research question.

    Returns:
        {
            answer:        str,
            sources:       [{id, record_type}],
            source_count:  int,
            confidence:    None (DEPRECATED — kept for frontend
                           compatibility only; citation count is not a
                           valid measure of evidentiary confidence),
            citations:     [{doc_id, record_type, title, source_archive,
                             snippet}],
            context_items: [{id, record_type, ..., discovery_matches,
                             evidence_chunks, has_transcription}],
            retrieval:     {mode, semantic_available, semantic_error,
                            fusion, provider, model},
        }
    """
    from modules.db import get_db
    from modules.retrieval import hybrid_retrieve, hydrate_evidence
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
                "answer":       "No relevant documents were found in the archive for this question.",
                "sources":      [],
                "source_count": 0,
                "confidence":   None,   # DEPRECATED
                "citations":    [],
                "context_items": [],
                "retrieval":    retrieval_meta,
            }

        # Evidence hydration: attach the most query-relevant transcription
        # passages from within each selected unit (reusing the query
        # embedding when semantic retrieval ran; keyword fallback otherwise).
        hydrate_evidence(conn, question, units,
                         query_vec=retrieval.get("_query_vec"))

        doc_ids   = [u["unit_id"] for u in units if u["record_type"] == "document"]
        group_ids = [u["unit_id"] for u in units if u["record_type"] == "group"]

        # Context blocks grounded in the hydrated transcription passages.
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
            "answer":       f"Error calling Claude API: {exc}",
            "sources":      [{"id": u["unit_id"], "record_type": u["record_type"]}
                             for u in units],
            "source_count": len(units),
            "confidence":   None,   # DEPRECATED
            "citations":    [],
            "context_items": [_context_item_meta(u) for u in units],
            "retrieval":    retrieval_meta,
        }

    # Extract cited IDs, keeping doc/group namespaces distinct.
    cited = set()
    for label, num in re.findall(r"\[(Doc|Group)\s*#(\d+)\]", answer_text):
        cited.add(("document" if label == "Doc" else "group", int(num)))

    citations = []
    for u in units:
        if (u["record_type"], u["unit_id"]) in cited:
            evidence = u.get("evidence_chunks") or []
            snippet = (evidence[0]["text"] if evidence
                       else (u.get("excerpt") or ""))[:200]
            citations.append({
                "doc_id":              u["unit_id"],
                "record_type":         u["record_type"],
                "title":               u.get("title") or "Untitled",
                "source_archive":      u.get("source_archive"),
                "date":                u.get("date_display"),
                "representation_type": u.get("representation_type"),
                "has_transcription":   u.get("has_transcription", False),
                "snippet":             snippet,
            })

    return {
        "answer":       answer_text,
        "sources":      [{"id": u["unit_id"], "record_type": u["record_type"]}
                         for u in units],
        "source_count": len(units),
        # DEPRECATED: always None. The old high/medium/low value was derived
        # from citation count, which is not a valid measure of evidentiary
        # confidence. Kept (as null) only so existing frontend code that
        # checks `entry.confidence` degrades gracefully.
        "confidence":   None,
        "citations":    citations,
        "context_items": [_context_item_meta(u) for u in units],
        "retrieval":    retrieval_meta,
    }
