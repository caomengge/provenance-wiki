"""
experiment_rag.py – MODE D: experimental RAG for interpretive archival discovery.

Pipeline:
  1. Retrieve top-k units with a NAMED retrieval mode (default:
     semantic_modelled). Retrieval results are returned and logged BEFORE
     generation, so they are inspectable independently of the answer.
  2. Build context from the PRIMARY-SOURCE TRANSCRIPTION of each retrieved
     unit plus basic metadata. AI-generated descriptions are included but
     explicitly labelled as machine-generated. Researcher annotations are
     NEVER included.
  3. Ask the LLM to separate evidence from inference using a versioned
     prompt template. The model is instructed NOT to produce a definitive
     answer when evidence does not justify one, and to send the researcher
     back to the primary sources.

The generated answer never feeds back into retrieval rankings.
"""

import json
import logging
import re

from modules.experiment_search import (
    run_retrieval,
    log_run,
    ExperimentSearchError,
)
from modules.experiment_schema import ensure_experiment_schema

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE_VERSION = "rag_discovery_v1"

RAG_SYSTEM_PROMPT = """You are an archival research assistant supporting a historian of museum provenance. Your purpose is ARCHIVAL DISCOVERY, not automated historical judgment.

You will receive excerpts from primary-source documents (mostly correspondence) held by the Nelson-Atkins Museum of Art, the Cleveland Museum of Art, and Harvard Art Museums, along with a research question.

Core rules:
1. Retrieval relevance is NOT historical interpretation. A retrieved document is potentially relevant evidence, nothing more.
2. Distinguish rigorously between (a) what a source explicitly says, (b) why it may be relevant to the question, and (c) what remains interpretive or uncertain.
3. For interpretive questions (about attitudes, ethics, motives), do NOT assert a definitive answer unless a source states it explicitly. Absence of the question's vocabulary in the sources is normal and important to note.
4. Every factual claim must cite its source as [Doc #ID] or [Group #ID]. Cite ONLY documents provided in the context.
5. Fields marked "machine-generated metadata" were produced by an AI at ingest time; treat them as finding aids, never as evidence. Evidence comes only from the primary-source transcription.
6. Always encourage the researcher to return to the original archival documents.

Structure your answer with exactly these sections:

## Potentially relevant evidence
(list each document with one line on what it is)

## What the documents explicitly state
(quotations or close paraphrase, each with citation)

## Why this may be relevant to the question
(clearly framed as possibility, with citations)

## What cannot be concluded from this evidence
(limits, gaps, alternative readings)

## Questions and archival leads to pursue next
(concrete next steps: documents to re-read, names, dates, other archives)
"""

RAG_USER_TEMPLATE = """RESEARCH QUESTION:
{question}

RETRIEVED ARCHIVAL DOCUMENTS (top {k}, retrieval mode: {mode}):

{doc_blocks}

Answer using the required section structure. Cite only the documents above."""


class ExperimentRagError(RuntimeError):
    pass


def build_context_block(conn, unit_id: int, record_type: str,
                        max_transcription_chars: int = 6000) -> tuple[str, bool]:
    """
    Build one context block. Returns (text, has_transcription).

    Primary-source transcription is the core of the block; title/description
    are included but labelled as machine-generated metadata. Researcher
    annotations are deliberately omitted.
    """
    label = "Doc" if record_type == "document" else "Group"
    table = "documents" if record_type == "document" else "document_groups"
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (unit_id,)).fetchone()
    if row is None:
        return f"[{label} #{unit_id}] (record not found)", False

    text = (row["transcription"] or "").strip()
    if record_type == "group" and not text:
        pages = conn.execute(
            """SELECT page_number, transcription FROM documents
               WHERE group_id=? ORDER BY COALESCE(page_number, id)""",
            (unit_id,),
        ).fetchall()
        text = "\n".join(
            f"[page {p['page_number'] or '?'}] {(p['transcription'] or '').strip()}"
            for p in pages if (p["transcription"] or "").strip()
        )

    lines = [f"[{label} #{unit_id}] Source archive: {row['source_archive'] or 'Unknown'}"]
    if row["title"]:
        lines.append(f"Machine-generated metadata — title: {row['title']}")
    if row["date_depicted"]:
        lines.append(f"Machine-generated metadata — date: {row['date_depicted']}")
    if row["description"]:
        lines.append(f"Machine-generated metadata — description: {row['description']}")

    if text:
        if len(text) > max_transcription_chars:
            text = text[:max_transcription_chars] + "\n[…transcription truncated…]"
        lines.append("PRIMARY-SOURCE TRANSCRIPTION:")
        lines.append(text)
        has_transcription = True
    else:
        lines.append("PRIMARY-SOURCE TRANSCRIPTION: (no transcription available "
                     "for this record — only machine-generated metadata exists)")
        has_transcription = False

    return "\n".join(lines), has_transcription


def extract_citations(answer: str, allowed: list[dict]) -> list[dict]:
    """
    Extract [Doc #N] / [Group #N] citations and keep ONLY those that refer
    to retrieved units actually provided in context.
    """
    allowed_keys = {(a["record_type"], a["doc_id"]) for a in allowed}
    cited = set()
    for label, num in re.findall(r"\[(Doc|Group)\s*#(\d+)\]", answer):
        rt = "document" if label == "Doc" else "group"
        key = (rt, int(num))
        if key in allowed_keys:
            cited.add(key)
    return [{"record_type": rt, "doc_id": did} for rt, did in sorted(cited)]


def run_rag(conn, question: str, retrieval_mode: str = "semantic_modelled",
            top_k: int = 10, provider: str | None = None,
            model_name: str | None = None, llm_model: str | None = None,
            max_tokens: int = 3000, generate: bool = True,
            query_id: str | None = None, query_type: str | None = None,
            query_notes: str | None = None) -> dict:
    """
    Run MODE D. Retrieval happens first and is logged/returned regardless of
    whether generation succeeds, so rankings are inspectable before (and
    independent of) the LLM answer.

    Set generate=False to run and log only the retrieval stage.
    """
    if retrieval_mode == "rag":
        raise ExperimentRagError("rag mode must specify an underlying retrieval mode")

    ensure_experiment_schema(conn)

    # 1. Retrieval (may raise ExperimentSearchError — no fallback)
    outcome = run_retrieval(conn, question, retrieval_mode, top_k, provider, model_name)
    run_id = log_run(conn, question, f"rag[{retrieval_mode}]", top_k, outcome,
                     query_id=query_id, query_type=query_type, query_notes=query_notes)

    result = {
        "run_id": run_id,
        "retrieval_mode": retrieval_mode,
        "retrieval": outcome,
        "answer": None,
        "citations": [],
        "llm_model": None,
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
    }
    if not generate:
        return result

    # 2. Build context from primary sources
    blocks, context_ids, missing = [], [], []
    for r in outcome["results"]:
        block, has_t = build_context_block(conn, r["doc_id"], r["record_type"])
        blocks.append(block)
        context_ids.append({"doc_id": r["doc_id"], "record_type": r["record_type"]})
        if not has_t:
            missing.append({"doc_id": r["doc_id"], "record_type": r["record_type"]})

    if not blocks:
        raise ExperimentRagError("Retrieval returned no documents; nothing to pass to the LLM.")

    prompt = RAG_USER_TEMPLATE.format(
        question=question, k=len(blocks), mode=retrieval_mode,
        doc_blocks="\n\n---\n\n".join(blocks),
    )

    # 3. Generate
    from config import ANTHROPIC_API_KEY, REASONING_MODEL
    llm_model = llm_model or REASONING_MODEL
    gen_params = {"max_tokens": max_tokens, "temperature": 0}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=llm_model,
            max_tokens=max_tokens,
            temperature=0,
            system=RAG_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = resp.content[0].text.strip()
    except Exception as e:
        # Log the failed generation but keep the retrieval run intact.
        conn.execute(
            """INSERT INTO experiment_rag_runs
               (run_id, context_ids_json, prompt_template_version, full_prompt,
                system_prompt, llm_model, generation_params_json, answer, citations_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (run_id, json.dumps(context_ids), PROMPT_TEMPLATE_VERSION, prompt,
             RAG_SYSTEM_PROMPT, llm_model, json.dumps(gen_params),
             None, json.dumps([])),
        )
        raise ExperimentRagError(f"LLM generation failed: {e}") from e

    citations = extract_citations(answer, outcome["results"])

    conn.execute(
        """INSERT INTO experiment_rag_runs
           (run_id, context_ids_json, prompt_template_version, full_prompt,
            system_prompt, llm_model, generation_params_json, answer, citations_json)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (run_id, json.dumps(context_ids), PROMPT_TEMPLATE_VERSION, prompt,
         RAG_SYSTEM_PROMPT, llm_model, json.dumps(gen_params),
         answer, json.dumps(citations)),
    )

    result.update({
        "answer": answer,
        "citations": citations,
        "llm_model": llm_model,
        "context_ids": context_ids,
        "context_missing_transcription": missing,
        "generation_params": gen_params,
    })
    return result
