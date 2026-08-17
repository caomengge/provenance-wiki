"""
experiment_rag.py – MODE D: RAG on a frozen retrieval condition
(experiment configuration: chunked_retrieval_v1).

For the main experiment, MODE D uses EXACTLY the document/unit ranking
produced by MODE C (semantic_modelled). The pipeline:

  1. Run the named retrieval condition ONCE (default semantic_modelled).
     Its ranking is FROZEN and LOGGED before any generation. D never
     performs another corpus-wide retrieval, never reranks, and never adds
     a unit absent from that ranking — the context is built by iterating
     the frozen result list in order.
  2. PRIMARY-SOURCE EVIDENCE hydration (production
     retrieval.hydrate_evidence): for each already-selected unit, search
     ONLY the transcription chunks belonging to that unit and select the
     top 1–2 most relevant to the ORIGINAL query — semantic similarity
     when available, keyword term-overlap as fallback. These hydrated
     transcription passages are the evidence supplied to Claude.
  3. Build context blocks in the frozen rank order: hydrated passages as
     PRIMARY-SOURCE EVIDENCE; title/date/description labelled
     machine-generated finding aids (never evidence); researcher
     annotations NEVER included; source archive preserved. A unit with no
     transcription is flagged explicitly.
  4. Generate with a versioned prompt that separates evidence from
     inference. Citations are validated against the frozen retrieved set
     only. The answer never feeds back into retrieval rankings.
  5. Log generation + per-unit evidence chunks (evidence_json) so every
     answer can be traced to the exact passages it saw.
"""

import json
import logging
import re

from modules.experiment_search import (
    run_retrieval,
    log_run,
    ExperimentSearchError,
    EXPERIMENT_CONFIG_VERSION,
)
from modules.experiment_schema import ensure_experiment_schema
from modules.retrieval import hydrate_evidence

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE_VERSION = "rag_discovery_v2"   # v2: hydrated chunk evidence
EVIDENCE_TOP_N = 2                             # transcription chunks per unit

RAG_SYSTEM_PROMPT = """You are an archival research assistant supporting a historian of museum provenance. Your purpose is ARCHIVAL DISCOVERY, not automated historical judgment.

You will receive excerpts from primary-source documents held by museum archives, along with a research question.

Core rules:
1. Retrieval relevance is NOT historical interpretation. A retrieved document is potentially relevant evidence, nothing more.
2. Distinguish rigorously between (a) what a source explicitly says, (b) why it may be relevant to the question, and (c) what remains interpretive or uncertain.
3. For interpretive questions (about attitudes, ethics, motives), do NOT assert a definitive answer unless a source states it explicitly. Absence of the question's vocabulary in the sources is normal and important to note.
4. Every factual claim must cite its source as [Doc #ID] or [Group #ID]. Cite ONLY documents provided in the context.
5. The evidentiary basis of your answer is the text under "PRIMARY-SOURCE EVIDENCE" — transcription passages from the archival documents. Fields marked "machine-generated metadata" were produced by an AI at ingest time; treat them as finding aids, never as evidence. A document marked as having no PRIMARY-SOURCE EVIDENCE may be mentioned as a lead, but never grounds a substantive claim.
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

RETRIEVED ARCHIVAL DOCUMENTS (top {k}, retrieval mode: {mode}, in frozen retrieval order):

{doc_blocks}

Answer using the required section structure. Cite only the documents above."""


class ExperimentRagError(RuntimeError):
    pass


def build_context_block(conn, unit: dict) -> tuple[str, bool]:
    """
    One context block for an already-selected unit. Returns
    (text, has_evidence).

    `unit` is a result dict from the frozen retrieval outcome, augmented by
    hydrate_evidence with `evidence_chunks` / `has_transcription`. The
    hydrated transcription passages are the PRIMARY-SOURCE EVIDENCE;
    machine-generated fields are labelled finding aids; researcher
    annotations are deliberately omitted.
    """
    label = "Doc" if unit["record_type"] == "document" else "Group"
    unit_id = unit["doc_id"]

    lines = [f"[{label} #{unit_id}] Source archive: "
             f"{unit.get('source_archive') or 'Unknown'}"]

    row = conn.execute(
        f"""SELECT title, date_depicted, description FROM
            {'documents' if unit['record_type'] == 'document' else 'document_groups'}
            WHERE id=?""", (unit_id,)).fetchone()
    if row:
        if row["title"]:
            lines.append(f"Machine-generated metadata — title: {row['title']}")
        if row["date_depicted"]:
            lines.append(f"Machine-generated metadata — date: {row['date_depicted']}")
        if row["description"]:
            lines.append(f"Machine-generated metadata — description: {row['description']}")

    evidence = sorted(unit.get("evidence_chunks") or [],
                      key=lambda c: c["chunk_index"])
    if evidence:
        lines.append("PRIMARY-SOURCE EVIDENCE (transcription passages most "
                     "relevant to the question):")
        lines.append("\n[…]\n".join(c["text"] for c in evidence))
        has_evidence = True
    else:
        lines.append("PRIMARY-SOURCE EVIDENCE: none — no transcription exists "
                     "for this record. Only machine-generated metadata is "
                     "available; treat as a finding aid, not as evidence.")
        has_evidence = False

    return "\n".join(lines), has_evidence


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
    Run MODE D on the FROZEN ranking of one named retrieval condition
    (default semantic_modelled = MODE C). Retrieval happens exactly once,
    is logged before generation, and is never reranked or extended.

    Set generate=False to run and log retrieval + evidence hydration only.
    """
    if retrieval_mode == "rag":
        raise ExperimentRagError("rag mode must specify an underlying retrieval mode")

    ensure_experiment_schema(conn)

    # 1. The one and only retrieval pass (may raise — no fallback).
    outcome = run_retrieval(conn, question, retrieval_mode, top_k, provider, model_name)
    run_id = log_run(conn, question, f"rag[{retrieval_mode}]", top_k, outcome,
                     query_id=query_id, query_type=query_type, query_notes=query_notes)
    frozen = outcome["results"]                     # frozen ranked unit set

    # 2. Within-unit PRIMARY-SOURCE EVIDENCE hydration (production function;
    #    searches only each selected unit's own transcription chunks;
    #    semantic when available, keyword term-overlap fallback).
    units = [{**r, "unit_id": r["doc_id"]} for r in frozen]
    hydrate_evidence(conn, question, units, top_n=EVIDENCE_TOP_N,
                     provider=outcome.get("embedding_provider"),
                     model_name=outcome.get("embedding_model"))

    evidence_log = [{
        "doc_id": u["doc_id"],
        "record_type": u["record_type"],
        "has_transcription": u.get("has_transcription", False),
        "evidence_chunks": [{
            "chunk_id": c["chunk_id"], "chunk_index": c["chunk_index"],
            "method": c["method"], "score": c["score"], "text": c["text"],
        } for c in u.get("evidence_chunks", [])],
    } for u in units]

    result = {
        "run_id": run_id,
        "retrieval_mode": retrieval_mode,
        "retrieval": outcome,
        "evidence": evidence_log,
        "config_version": EXPERIMENT_CONFIG_VERSION,
        "answer": None,
        "citations": [],
        "llm_model": None,
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
    }
    if not generate:
        return result

    # 3. Context in frozen rank order — no additions, no reordering.
    blocks, context_ids, missing = [], [], []
    for u in units:
        block, has_ev = build_context_block(conn, u)
        blocks.append(block)
        context_ids.append({"doc_id": u["doc_id"], "record_type": u["record_type"]})
        if not has_ev:
            missing.append({"doc_id": u["doc_id"], "record_type": u["record_type"]})

    if not blocks:
        raise ExperimentRagError("Retrieval returned no documents; nothing to pass to the LLM.")

    prompt = RAG_USER_TEMPLATE.format(
        question=question, k=len(blocks), mode=retrieval_mode,
        doc_blocks="\n\n---\n\n".join(blocks),
    )

    # 4. Generate.
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
                system_prompt, llm_model, generation_params_json, answer,
                citations_json, evidence_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (run_id, json.dumps(context_ids), PROMPT_TEMPLATE_VERSION, prompt,
             RAG_SYSTEM_PROMPT, llm_model, json.dumps(gen_params),
             None, json.dumps([]), json.dumps(evidence_log)),
        )
        raise ExperimentRagError(f"LLM generation failed: {e}") from e

    # 5. Citations validated against the frozen retrieved set only.
    citations = extract_citations(answer, frozen)

    conn.execute(
        """INSERT INTO experiment_rag_runs
           (run_id, context_ids_json, prompt_template_version, full_prompt,
            system_prompt, llm_model, generation_params_json, answer,
            citations_json, evidence_json)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (run_id, json.dumps(context_ids), PROMPT_TEMPLATE_VERSION, prompt,
         RAG_SYSTEM_PROMPT, llm_model, json.dumps(gen_params),
         answer, json.dumps(citations), json.dumps(evidence_log)),
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
