# Retrieval-Methods Experiment

A reproducible digital-humanities experiment comparing how different representations of the archive (primary-source transcription vs. AI-generated modelling) and different retrieval methods (lexical vs. neural-semantic) affect what a researcher discovers. Built alongside the production app — nothing in the existing application or ingested data was modified.

## Architecture

Four independently selectable conditions, all operating on **retrieval units** (standalone documents + multi-page groups; grouped pages are never retrieved individually):

| Mode | Method | Representation searched |
|---|---|---|
| `keyword_original` | FTS5 BM25 (ranked OR of terms) | Primary-source transcription |
| `semantic_original` | Neural embedding + cosine | Primary-source transcription |
| `semantic_modelled` | Neural embedding + cosine | Deterministic AI representation (title, description, entities+roles+contexts, transactions, tags — **no annotations, no transcription**) |
| `rag` | One named mode above (default `semantic_modelled`) → LLM synthesis | Generator receives **primary transcriptions**; AI descriptions labelled as machine-generated |

Methodological guarantees, enforced in code and covered by tests: no silent fallback between methods (failures raise and are logged as `failed`); retrieval results are logged **before** generation and never influenced by the answer; `source_archive` and record IDs preserved end-to-end; researcher annotations never enter retrieval or RAG context; queries are never rewritten or expanded; RAG citations are validated against the retrieved set only. MODE D answers use a versioned prompt (`rag_discovery_v1`) structured as: potentially relevant evidence / what documents explicitly state / why it may be relevant / what cannot be concluded / archival leads to pursue.

New files: `backend/modules/{experiment_schema,embeddings,experiment_search,experiment_rag}.py`, `backend/scripts/{migrate_experiment,generate_experiment_embeddings}.py`, `backend/experiment.py`, `backend/tests/test_experiment.py`. The production `search.py`/`qa.py` are untouched.

## Database changes (additive only)

- `document_embeddings` — versioned vectors: `(document_id, record_type, representation_type, provider, model_name, dim, embedding_json, source_text_sha256, created_at)`, unique per (unit, record_type, representation, model). The legacy `documents.embedding_json` (512-dim hashed bag-of-words used by the production app) is untouched.
- `experiment_transcription_fts` — FTS5 index over primary-source transcriptions (the production `documents_fts` indexes only AI metadata + annotations).
- `experiment_runs`, `experiment_results`, `experiment_rag_runs` — structured log of every run: query, mode, embedding model, representation, top_k, timestamp, ranked results with scores/titles/archives; for RAG additionally context IDs, full prompt, template version, LLM model, generation params, answer, validated citations.

A pre-experiment snapshot exists: `data/provenance.db.bak-20260811-pre-experiment`.

## Setup

The embedding backend is configurable via env vars (in `.env` or shell):

```
EMBEDDING_PROVIDER=sentence_transformers   # default
EMBEDDING_MODEL=BAAI/bge-m3                # default; multilingual (EN+中文)
```

Install the local model backend (~2.3 GB model download on first use):

```bash
cd backend
pip install sentence-transformers
```

If torch wheels are unavailable for your Python (e.g. 3.14), either create a dedicated venv with Python 3.12, or use an API provider instead — no code changes needed:

```
# Voyage AI:            EMBEDDING_PROVIDER=voyage  VOYAGE_API_KEY=...  (pip install voyageai)
# Any OpenAI-compatible: EMBEDDING_PROVIDER=openai_compatible
#                        EMBEDDINGS_API_URL=https://.../v1  EMBEDDINGS_API_KEY=...  EMBEDDING_MODEL=...
```

Every stored vector and every logged run records which provider/model produced it, so switching models later adds rows rather than overwriting history.

## Commands

```bash
cd backend

# 1. One-time: create experiment tables + build the transcription FTS index
#    (already run: 268 units indexed; documents #170 and #171 have no
#     transcription and are reported as excluded)
python scripts/migrate_experiment.py

# 2. Generate embeddings for both representations (re-runnable; skips
#    unchanged texts, --force re-embeds everything)
python scripts/generate_experiment_embeddings.py
python scripts/generate_experiment_embeddings.py --representations modelled  # subset

# 3. Run the experiment
python experiment.py --queries ../experiments/queries.example.csv \
    --modes keyword_original semantic_original semantic_modelled \
    --top-k 10 --output ../experiments/results/

# Include MODE D (add ANTHROPIC_API_KEY to .env; --no-generate logs retrieval only)
python experiment.py --queries ../experiments/queries.example.csv \
    --modes keyword_original semantic_original semantic_modelled rag \
    --rag-retrieval-mode semantic_modelled --top-k 10 \
    --output ../experiments/results/

# 4. Tests
python -m pytest tests/test_experiment.py -v
```

## Outputs

Each run writes `experiments/results/<UTC timestamp>/`:

- `results.json` — full structured record (queries, conditions, ranked results, errors, RAG answers).
- `results.csv` — one row per (query × mode × rank) with doc_id, record_type, score, title, source_archive.
- `review.csv` — review sheet for manual coding: transcription excerpt per hit plus **blank** `human_relevance` (0–3), `human_interpretive_value` (0–3), `notes` columns. No relevance judgment is automated.
- `rag_answers.md` — MODE D answers with validated citations.

The same data is queryable in SQLite (`experiment_runs` joined to `experiment_results`), e.g. for cross-archive analysis: results retain `source_archive`, so you can measure when a Nelson-Atkins-framed question surfaces Cleveland or Harvard material.

## Known limitations

- Documents #170 and #171 have no transcription: excluded from `keyword_original`/`semantic_original` (reported, never silent), still present in `semantic_modelled` via metadata, and flagged as metadata-only if retrieved into RAG context.
- Long transcriptions are embedded as single vectors (BGE-M3 handles 8k tokens; longer texts are truncated by the model) and truncated at 6,000 chars per document in RAG context. Chunked embedding would be a separate, explicitly named condition.
- `keyword_original` uses OR-of-terms BM25 rather than the production app's exact-phrase matching (which returns zero results for most multi-word research questions). This choice is itself part of the baseline's definition and is documented in `_escape_fts`.
- The corpus's period romanizations (e.g. "Chih-hua" vs. "Zhihua") mean lexical modes can miss documents a modern query targets — expected, and part of what the experiment measures.
- Group representation prefers the group-level transcription, falling back to page concatenation in page order; group-level transcription quality inherits from ingestion.
