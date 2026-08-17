# Retrieval Refactor — Pre-Implementation Design (A–H)

Milestone: incremental semantic retrieval foundation for the September usability demo.

## A. Current architecture (relevant parts)

**Stack.** Flask backend (`backend/`), SQLite at `data/provenance.db` (675 documents, 129 groups, 3 source archives: Nelson-Atkins, Cleveland, Harvard), React frontend. Ingestion photographs → Claude Vision extraction → `documents` rows carrying `transcription`, `description`, entities, transactions, tags, `source_archive`. Multi-page documents merge into `document_groups`.

**Retrieval today (production path).**
- `documents.embedding_json` / `document_groups.embedding_json` hold a **512-dim hashed bag-of-words** vector produced by `extractor.generate_text_embedding()` (MD5 token hashing) — not a semantic embedding, though `search.py` labels it "semantic".
- `/api/search` mode `semantic` cosine-ranks those hashed vectors, built only from *title + description + tags* (never the transcription), and silently **falls back to keyword** when embedding fails.
- `documents_fts` / `groups_fts` (FTS5) index only `title, description, annotation, raw_claude_response` — **transcriptions are not in the production FTS index at all**.
- Q&A (`qa.py`): keyword search first; semantic only if keyword returns nothing (fallback, not hybrid). Context blocks contain **title/date/location/description/annotation only — no transcription text**. So answers are grounded in AI-generated descriptions, not primary sources.

**Experiment layer (added 2026-08-11, additive, not in the production path).** This is a big head start:
- `modules/embeddings.py` — clean provider abstraction (`sentence_transformers` / `voyage` / `openai_compatible`), no silent fallback, provider+model exposed. Voyage support already exists (default `voyage-3`).
- `document_embeddings` table — unit-level (not chunk-level) vectors keyed by `(unit, record_type, representation_type ∈ original|modelled, model_name)` with `source_text_sha256` skip-if-unchanged logic in `scripts/generate_experiment_embeddings.py`.
- `experiment_transcription_fts` — FTS over transcriptions; `experiment_search.py` modes A/B/C; `experiment_rag.py` builds RAG context from actual transcriptions.

**Plan of record:** promote and extend the experiment layer's concepts into the production retrieval path, adding chunk-level representations, full incremental bookkeeping, hybrid fusion, and ingestion integration. The experiment tables themselves stay untouched so past experiment runs remain reproducible.

## B. Files and tables that change

New files:
- `backend/modules/representations.py` — build transcription chunks + generated representation text (deterministic, versioned).
- `backend/modules/indexer.py` — incremental indexing engine (hashing, diffing, embedding, status, backfill/reindex).
- `backend/modules/vector_store.py` — vector search behind a minimal interface (SQLite + Python cosine now; swappable later).
- `backend/modules/retrieval.py` — hybrid retrieval with reciprocal rank fusion.
- `backend/scripts/reindex.py` — manual backfill/reindex/retry/status CLI.
- `backend/tests/test_representations.py`, `test_indexer.py`, `test_retrieval.py`, `test_qa_context.py`.

Modified files:
- `modules/embeddings.py` — default provider → `voyage`, default model → `voyage-4` (both env-overridable); Voyage `input_type` for query vs document.
- `modules/db.py` — additive migration creating the three new tables + FTS (existing `_migrate` pattern; no destructive change).
- `modules/search.py` — `semantic` mode now uses real chunk embeddings via `retrieval.py`; new `hybrid` mode; keyword mode unchanged; explicit `semantic_available` flag instead of silent hashed-vector fallback.
- `modules/qa.py` — retrieval via hybrid fusion; context built from retrieved transcription excerpts (+ labelled generated metadata); source archive/date/representation type in every block and citation.
- `modules/ingestor.py`, `api/groups.py` — stop writing hashed `embedding_json`; call `indexer.index_unit()` after ingest/re-extract/group create.
- `api/documents.py`, `api/entities.py`, `api/transactions.py`, `api/search_routes.py`, `api/qa_routes.py` — reindex hooks after content mutations; search/QA API surface additions (non-breaking).
- `config.py`, `.env.example`, `requirements.txt` (`voyageai`), `README.md`.

Unchanged: `extractor.generate_text_embedding()` is retired from all call sites and its docstring rewritten to say "legacy lexical hash — NOT a semantic embedding; retained only so old data remains interpretable". `documents.embedding_json` columns are left in place (no destructive migration) but no longer written or read.

## C. Proposed schema additions (all additive)

```sql
CREATE TABLE retrieval_chunks (            -- one row per retrievable representation
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id             INTEGER NOT NULL,              -- documents.id or document_groups.id
    record_type         TEXT NOT NULL CHECK(record_type IN ('document','group')),
    representation_type TEXT NOT NULL CHECK(representation_type IN ('transcription','generated')),
    chunk_index         INTEGER NOT NULL,              -- 0-based order within unit+representation
    chunk_count         INTEGER NOT NULL,
    text                TEXT NOT NULL,                 -- the EXACT text that gets embedded
    content_sha256      TEXT NOT NULL,                 -- sha256(text)
    chunking_version    TEXT NOT NULL,                 -- e.g. 'v1'
    title               TEXT,                          -- denormalised for retrieval results
    source_archive      TEXT,
    date_display        TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(unit_id, record_type, representation_type, chunk_index)
);

CREATE TABLE retrieval_embeddings (        -- one row per chunk × provider × model × schema version
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id             INTEGER NOT NULL REFERENCES retrieval_chunks(id) ON DELETE CASCADE,
    provider             TEXT NOT NULL,
    model_name           TEXT NOT NULL,
    index_schema_version INTEGER NOT NULL,             -- INDEX_SCHEMA_VERSION constant
    content_sha256       TEXT NOT NULL,                -- hash of the text actually embedded
    dim                  INTEGER,
    embedding_json       TEXT,                         -- NULL until status='ok'
    status               TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','ok','failed')),
    error                TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(chunk_id, provider, model_name, index_schema_version)
);

CREATE VIRTUAL TABLE retrieval_chunks_fts USING fts5(   -- chunk-level keyword index
    text, chunk_id UNINDEXED, tokenize='unicode61 remove_diacritics 0'
);
```

Notes: embeddings stay as JSON text in SQLite for transparency (matches existing convention); `vector_store.py` is the only module that reads them, so a later FAISS/pgvector backend replaces one file. Chunk-level FTS finally puts transcriptions into keyword search (chunk keyword retrieval covers both representation types). Group-membership rule follows the experiment layer: retrieval units = standalone documents + groups; grouped pages are not retrieved individually.

## D. Incremental indexing design

Identity and validity of every embedding = `(chunk content hash, provider, model, index schema version)`.

`index_unit(conn, unit_id, record_type)` — the single entry point, called from ingestion, mutation hooks, and reindex:
1. Build both representations deterministically (transcription chunks via chunker; one generated representation from title/date/location/description/entities/transactions/tags — annotation excluded, exactly like the experiment's `build_modelled_text`).
2. `sha256` each chunk text.
3. Diff against stored `retrieval_chunks`: unchanged hash → untouched; changed → update row + bump `updated_at`; new → insert; stale indexes (shrunk chunk count, or unit trashed/absorbed into a group) → delete (cascades to embeddings). FTS kept in sync.
4. For the **active** (provider, model, schema version): every chunk whose matching embedding row is missing, `pending`, `failed`, or whose `content_sha256` differs → embed (batched); matching `ok` rows → **skipped, no API call**.
5. On `EmbeddingError`: chunk rows and FTS are already committed — keyword search works immediately; embedding rows are recorded as `pending` with the error; nothing is destroyed; `reindex --retry` (or the next `index_unit` call) picks them up. The hashed bag-of-words vector is never substituted.

Consequences: 100 new documents → 100 documents embedded; one corrected transcription → only that unit's changed transcription chunks re-embedded (generated representation untouched); edited entity/tag/transaction → only the generated representation re-embedded; `reindex` over 10 000 unchanged records → all inspected, zero API calls.

**Model/version migration:** vectors from different models are never mixed — `vector_store` filters on exact `(provider, model, schema_version)`. Switching models = set env (or pass `--provider/--model`) and run `scripts/reindex.py`; new rows are added alongside old ones (old model's vectors remain for the planned retrieval-method comparisons). Retrieval always uses the configured active model and reports embedding coverage rather than silently mixing.

**Backfill** = the same `reindex.py` run once after deploying this milestone: builds chunks + embeddings for all 675 documents + 129 groups from existing DB content — no image re-extraction, no Claude Vision calls.

## E. Chunking strategy (transparent, reproducible — `chunking_version = 'v1'`)

Corpus check: transcription lengths median ≈ 1 700 chars, max ≈ 4 700, so most units yield 1–4 chunks.
1. Normalise line endings; split transcription on blank lines into paragraphs (natural units in correspondence).
2. Greedily pack consecutive paragraphs into a chunk up to `CHUNK_MAX_CHARS = 1500`.
3. A single paragraph longer than the cap is split at sentence boundaries (`. ! ? 。 ！ ？` — CJK-aware), never mid-word.
4. Each chunk after the first is prefixed with the last `CHUNK_OVERLAP_CHARS = 200` characters of the previous chunk (marked with `… `) so clauses spanning a boundary stay retrievable.
5. Deterministic: same text + same constants (in `config.py`) → byte-identical chunks; constants are part of `chunking_version`, so changing them is an explicit reindex event, detected by hash change.
The generated representation is embedded as a single chunk (it is short, structured text). Whole-unit texts far exceeding voyage-4's context are never possible at these sizes but the chunker enforces a hard per-chunk cap anyway.

## F. Hybrid rank fusion — Reciprocal Rank Fusion (RRF)

Three ranked candidate lists per query: (1) BM25 over `retrieval_chunks_fts`; (2) cosine over transcription-chunk embeddings; (3) cosine over generated-representation embeddings. Fused per retrieval unit:

`score(unit) = Σ_lists 1 / (RRF_K + best_rank_in_list)`, `RRF_K = 60` (the standard Cormack/Clarke constant).

Why RRF: rank-based, so BM25 scores and cosine similarities need no calibration onto a common scale; one transparent formula, one documented constant, no tuned weights — exactly the "no opaque heuristics" requirement, and trivially extended to per-condition comparison later. Every result carries: unit id, chunk id, representation type, excerpt, source archive, title, date, per-list rank (`keyword_rank`, `semantic_transcription_rank`, `semantic_generated_rank`), raw scores, and fused score. If the embedding provider is down, fusion degrades to the keyword list alone and the response says so (`semantic_available: false`) — never a silent substitution. The four experimental conditions (keyword / semantic-transcription / semantic-generated / hybrid) remain individually runnable because each list is computed by its own function.

## G. "Archive = person/project" assumptions found

The schema is largely clean — no table equates the database with Laurence Sickman:
1. `source_archive` is a free-text column on `documents`/`document_groups` meaning *institution where the document was photographed* (Nelson-Atkins, Cleveland, Harvard). That is source provenance, not a research project — correct semantics; I will carry it through every new table, retrieval result, and QA context block untouched. It is single-valued free text, so a later normalisation into `repositories` + collection/fonds/box-folder tables and a many-to-many `project_documents` junction is a clean additive step (documents keep stable ids; nothing in this milestone assumes one document ↔ one corpus).
2. `documents.filename` is globally `UNIQUE` — an implicit "one folder of photos = one corpus" assumption that could collide when two institutions use the same filename. Flagged, not changed (out of scope; `sha256` already provides identity).
3. The experiment's `RAG_SYSTEM_PROMPT` hardcodes the three current museums by name; the new production QA prompt will describe sources generically and name archives dynamically from retrieved metadata.
4. README/UI copy says "the archive" (singular) in places — cosmetic, untouched.
No schema redesign needed for this milestone; nothing blocks the future multi-project model.

## H. Implementation plan (one focused milestone)

1. `embeddings.py`: Voyage as default provider, `voyage-4` default model, query/document `input_type`; keep abstraction and no-fallback guarantee.
2. `db.py` migration: create the three retrieval tables (additive, `CREATE TABLE IF NOT EXISTS`, existing `_migrate` pattern).
3. `representations.py`: chunker v1 + generated-representation builder + hashing.
4. `indexer.py`: diff/skip/regenerate logic, batched embedding, pending/failed status, `index_unit` + `reindex_all` + `retry_failed` + `index_status`.
5. `vector_store.py`: store/search interface (SQLite JSON + Python cosine implementation).
6. `retrieval.py`: three candidate lists + RRF fusion + unit-level assembly.
7. Rewire `search.py` (semantic/hybrid modes, honest fallback flag) and `qa.py` (hybrid retrieval, transcription-excerpt context, labelled generated metadata).
8. Ingestion integration: `ingestor.py`, `groups.py`, mutation hooks in `documents.py`/`entities.py`/`transactions.py`/`search_routes.py` tag endpoints; remove hashed-vector writes.
9. `scripts/reindex.py` CLI (backfill, incremental reindex, retry, status, explicit `--provider/--model` migration path).
10. Tests (fake embedding provider; temp DBs through the real `init_db` migration) covering the full required list, then run the whole suite including the existing experiment tests.
11. Deliver files back to the working folder; you run `pip install voyageai`, add `VOYAGE_API_KEY` to `.env` (it is not there yet), and run `python scripts/reindex.py` once as the backfill.

---

## Addendum — corrections applied 2026-08-17 (post-review)

**1. Keyword channel restricted to primary-source transcription.**
`keyword_candidates()` now defaults to `representation_type="transcription"`, and `hybrid_retrieve()` exposes `keyword_representation` (default `"transcription"`; pass `None` or `"generated"` explicitly for experiments). AI-generated representations can no longer produce a keyword hit in production hybrid retrieval or in its keyword-only degradation path. The applied scope is recorded in the response (`fusion.keyword_representation`). Production hybrid = keyword-over-transcription + semantic-over-transcription + semantic-over-generated.

**2. Evidence hydration for RAG (`retrieval.hydrate_evidence`).**
After fusion selects the top units, Q&A hydrates evidence per unit: for every selected unit that has transcription, the top 1–2 transcription chunks *from within that unit* are retrieved against the original query — by cosine similarity when embeddings are available, by deterministic term-overlap otherwise (ties break on document order, so a unit always yields its opening passage rather than nothing). The result keeps two distinct records on every unit: `discovery_matches` (why the unit was retrieved — list, rank, score, representation type, snippet) and `evidence_chunks` (passages supplied for historical interpretation — chunk id/index, method, score, full text). The QA context template now feeds hydrated passages as "PRIMARY-SOURCE EVIDENCE"; a unit with no transcription is explicitly flagged, the system prompt forbids substantive claims resting solely on machine-generated metadata, and generated descriptions remain labelled finding aids. The full discovery/evidence metadata is returned as `context_items` for future frontend display. The query embedding computed during retrieval is reused (`_query_vec`) so hydration adds no extra API call.

**3. Citation-count confidence removed.**
The high/medium/low `confidence` derived from the number of cited documents is deprecated: the field is retained as `null` only because the current QA frontend reads `entry.confidence` (its falsiness guard hides the chip cleanly). The response now carries `source_count`, and `retrieval.{mode, semantic_available, semantic_error, provider, model, fusion}` as the honest description of retrieval conditions. The MCP server's answer formatting reports sources consulted and retrieval mode instead of confidence.

Tests: 39 passing (4 new — keyword-transcription regression incl. degraded mode, hydration relevance ranking with semantic and keyword fallback, generated-only-discovery still yielding primary evidence end-to-end through QA). Files changed in this correction: `modules/retrieval.py`, `modules/qa.py`, `mcp_server.py`, `tests/test_retrieval_index.py`, `README.md`, this addendum. No schema changes; no frontend changes.
