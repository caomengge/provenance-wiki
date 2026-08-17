# Retrieval-Methods Experiment (`chunked_retrieval_v1`)

A reproducible digital-humanities experiment comparing how different representations of the archive (primary-source transcription vs. AI-generated modelling) and different retrieval methods (lexical vs. neural-semantic) affect what a researcher discovers.

As of configuration **`chunked_retrieval_v1`**, the experiment runs on the **production chunk-level retrieval infrastructure** (`retrieval_chunks` / `retrieval_chunks_fts` / `retrieval_embeddings`, built by `scripts/reindex.py`) with the **production embedding model — Voyage `voyage-4` by default**. There is no separate experimental retrieval architecture: conditions A–C are thin, isolated wrappers over the production candidate generators (`retrieval.keyword_candidates` / `retrieval.semantic_candidates`), and D's evidence stage is the production `retrieval.hydrate_evidence`. Every run records the embedding provider/model, the index schema version, and the `chunked_retrieval_v1` configuration label, so results are tied to the exact retrieval architecture that produced them.

## Conditions

All conditions operate on **retrieval units** (standalone documents + multi-page groups; grouped pages are never retrieved individually). Retrieval is chunk-level: chunks are ranked by the condition's own scorer, then a unit's rank is the rank of its best chunk. Transcriptions are chunked by the production chunker (paragraph packing ≤1,500 chars, 200-char overlap); oversized generated representations are split line-wise — **no text is embedded as one single long vector**.

| Mode | Method | Chunks searched |
|---|---|---|
| A `keyword_original` | FTS5 BM25 (ranked OR of terms) | `representation_type="transcription"` ONLY |
| B `semantic_original` | Voyage cosine similarity | `representation_type="transcription"` ONLY |
| C `semantic_modelled` | Voyage cosine similarity | `representation_type="generated"` ONLY (description, entities+roles, transactions, tags, dates, source metadata — **no transcription, no annotations**) |
| D `rag` | C's **frozen** document ranking → within-document PRIMARY-SOURCE EVIDENCE hydration → Claude synthesis | discovery via C; evidence via each selected unit's own transcription chunks |

**MODE D in detail.** D runs the underlying condition (default C) exactly once; the ranking is logged **before** generation and is never re-retrieved, reranked, or extended — the LLM context iterates the frozen result list in order. Then, for each already-selected unit that has transcription, the top 1–2 transcription chunks most relevant to the **original query** are selected *from within that unit* (semantic similarity when available, keyword term-overlap fallback) and supplied to Claude as PRIMARY-SOURCE EVIDENCE. Generated representations may discover a document but remain labelled machine-generated finding aids; a unit with no transcription is flagged and grounds no substantive claim. The hydrated passages are logged per answer (`evidence_json`), so "why the document was found" (discovery chunk) and "what the model was given as evidence" (evidence chunks) are separately inspectable.

Methodological guarantees, enforced in code and covered by tests: the identical original query goes to A, B, and C; no query rewriting or expansion; no silent semantic→keyword fallback (failures raise and are logged as `failed`); retrieval results are logged before generation and never influenced by the answer; `source_archive` and record IDs are preserved end-to-end; researcher annotations never enter any representation, retrieval, or RAG context; RAG citations are validated against the frozen retrieved set only; no LLM judges retrieval relevance. MODE D answers use a versioned prompt (`rag_discovery_v2`) structured as: potentially relevant evidence / what documents explicitly state / why it may be relevant / what cannot be concluded / archival leads to pursue.

Files: `backend/modules/{experiment_schema,experiment_search,experiment_rag}.py`, `backend/experiment.py`, `backend/tests/test_experiment.py`. Production modules reused: `modules/{retrieval,representations,indexer,vector_store,embeddings}.py`.

## Database

Run logs (additive, in `experiment_schema.py`):

- `experiment_runs` — one row per (query × condition): query, mode, embedding provider/model, representation type, top_k, status, **`index_schema_version`**, **`config_version`** (`chunked_retrieval_v1`).
- `experiment_results` — ranked results with doc/unit ID, record type, score, title, source archive, and the **discovery chunk** (`chunk_id`, `representation_type`, `excerpt`).
- `experiment_rag_runs` — MODE D generation record: frozen context IDs, prompt template version, full prompt, LLM model, generation params, answer, validated citations, and **`evidence_json`** (the hydrated PRIMARY-SOURCE EVIDENCE passages per unit, with chunk IDs, method, and scores).

Legacy objects from the pre-chunk experiment (`document_embeddings` whole-unit vectors, `experiment_transcription_fts`) are **no longer used but preserved untouched**, so historical runs (pre-2026-08-17) remain reproducible. `scripts/generate_experiment_embeddings.py` is deprecated accordingly.

## Setup

The experiment uses the same retrieval index and embedding configuration as the production app:

```
VOYAGE_API_KEY=...              # in .env (defaults: provider voyage, model voyage-4)
# optional overrides: EMBEDDING_PROVIDER / EMBEDDING_MODEL
```

```bash
cd backend

# 1. Build/refresh the chunk index (incremental; the production backfill)
python scripts/reindex.py

# 2. Ensure the experiment run-log tables exist (additive, safe to re-run)
python scripts/migrate_experiment.py
```

## Running the experiment

```bash
cd backend

# Conditions A, B, C
python experiment.py --queries ../experiments/queries.example.csv \
    --modes keyword_original semantic_original semantic_modelled \
    --top-k 10 --output ../experiments/results/

# All four conditions (D = C's frozen ranking + evidence hydration + Claude;
# requires ANTHROPIC_API_KEY; --no-generate logs retrieval + evidence only)
python experiment.py --queries ../experiments/queries.example.csv \
    --modes keyword_original semantic_original semantic_modelled rag \
    --rag-retrieval-mode semantic_modelled --top-k 10 \
    --output ../experiments/results/

# Tests
python -m pytest tests/test_experiment.py -v
```

## Outputs

Each run writes `experiments/results/<UTC timestamp>/`:

- `results.json` — full structured record (queries, conditions, ranked results, discovery chunks, evidence, errors, RAG answers), stamped with `config_version`.
- `results.csv` — one row per (query × mode × rank) with doc_id, record_type, score, title, source_archive, plus `config_version`, embedding provider/model, `index_schema_version`, and the discovery chunk (`discovery_chunk_id`, `discovery_representation_type`, `discovery_excerpt`).
- `review.csv` — review sheet for manual coding: transcription excerpt per hit plus **blank** `human_relevance` (0–3), `human_interpretive_value` (0–3), `notes` columns. No relevance judgment is automated.
- `rag_answers.md` — MODE D answers with validated citations and the PRIMARY-SOURCE EVIDENCE chunks supplied per document.

The same data is queryable in SQLite (`experiment_runs` joined to `experiment_results`), e.g. for cross-archive analysis: results retain `source_archive`, so you can measure when a Nelson-Atkins-framed question surfaces Cleveland or Harvard material.

## Known limitations

- Documents #170 and #171 have no transcription: absent from `keyword_original`/`semantic_original` (they have no transcription chunks), still discoverable in `semantic_modelled` via metadata, and explicitly flagged as metadata-only finding aids if retrieved into RAG context.
- `keyword_original` uses OR-of-terms BM25 (the production chunk-keyword behaviour) rather than exact-phrase matching, which returns zero results for most multi-word research questions. This choice is part of the baseline's definition.
- The corpus's period romanizations (e.g. "Chih-hua" vs. "Zhihua") mean lexical modes can miss documents a modern query targets — expected, and part of what the experiment measures.
- Group representation prefers the group-level transcription, falling back to page concatenation in page order; group-level transcription quality inherits from ingestion.
- Chunk-level ranking means a unit's rank reflects its best-matching chunk; per-chunk discovery excerpts in `results.csv` show exactly which passage produced each rank.
