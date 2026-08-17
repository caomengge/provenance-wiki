"""
Tests for the retrieval-methods experiment, chunked_retrieval_v1
(run from backend/):

    python -m pytest tests/test_experiment.py -v

The experiment now runs on the PRODUCTION chunk-level retrieval index.
Covers the methodological requirements:
  condition isolation (A/B/C search exactly one representation each),
  deterministic ranking, no silent fallback, identical unrewritten queries,
  source_archive preservation, group handling, annotation exclusion,
  D frozen on C's exact ranking (no rerank / no additions), within-unit
  PRIMARY-SOURCE EVIDENCE hydration, generated text never used as primary
  evidence, citation validation against the frozen set, provider/model/
  index-version/config-version metadata, result export, and
  missing-transcription behavior.
"""

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND))

import config
from modules import db as dbmod
from modules import indexer
from modules import retrieval as retrieval_mod
from modules.embeddings import EmbeddingBackend, EmbeddingError
from modules.representations import build_generated_text, get_retrieval_units
from modules.experiment_search import (
    run_retrieval, semantic_search, keyword_original, log_run,
    ExperimentSearchError, get_transcription_excerpt,
    EXPERIMENT_CONFIG_VERSION,
)
from modules.experiment_rag import (
    run_rag, extract_citations, build_context_block, ExperimentRagError,
    PROMPT_TEMPLATE_VERSION,
)


# ── Fakes ─────────────────────────────────────────────────────────────────────

class FakeBackend(EmbeddingBackend):
    """Deterministic lexical-overlap embeddings (NOT the legacy hashed BoW)."""
    provider = "fake"

    def __init__(self, model_name="fake-1"):
        self.model_name = model_name
        self.queries_embedded: list[str] = []

    def embed(self, texts, input_type=None):
        if input_type == "query":
            self.queries_embedded.extend(texts)
        return [self._vec(t) for t in texts]

    def _vec(self, text):
        # Pure lexical-overlap vectors: zero shared tokens → cosine 0.0,
        # so "cannot be found semantically" is directly observable.
        # Wide hash space so distinct tokens never share a bucket in the
        # small, fixed test corpus (deterministic — no per-run variance).
        dim = 65536
        vec = [0.0] * dim
        for tok in (text or "").lower().split():
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % dim] += 1.0
        if not any(vec):
            vec[dim - 1] = 1e-9      # only for empty text; keeps cosine defined
        return vec


class FailingBackend(EmbeddingBackend):
    provider = "fake"
    model_name = "fake-1"

    def embed(self, texts, input_type=None):
        raise EmbeddingError("simulated API failure")


@pytest.fixture()
def test_db(tmp_path, monkeypatch):
    """Fresh production-schema DB + small corpus, indexed via the real
    production indexer (chunk representations + fake embeddings)."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    dbmod.init_db()

    with dbmod.get_db() as conn:
        conn.execute(
            """INSERT INTO documents (id, filename, sha256, title, description,
               transcription, annotation, source_archive)
               VALUES (1,'a.jpg','sha1','Letter about temple ceiling',
                       'AI description: acquisition of a temple ceiling',
                       'Dear Sir, the ceiling of the temple was purchased quietly.',
                       'RESEARCHER_SECRET_NOTE this proves unease', 'Nelson-Atkins')"""
        )
        conn.execute(
            """INSERT INTO documents (id, filename, sha256, title, description,
               transcription, source_archive)
               VALUES (2,'b.jpg','sha2','Shipping invoice',
                       'AI description: shipping of paintings',
                       'Invoice for shipping twelve painting crates to Kansas City.',
                       'Cleveland Museum of Art')"""
        )
        # Document with NO transcription (generated representation only)
        conn.execute(
            """INSERT INTO documents (id, filename, sha256, title, description, source_archive)
               VALUES (3,'c.jpg','sha3','Untranscribed photo',
                       'AI description only', 'Harvard Art Museums')"""
        )
        # Multi-page group with pages 2 and 1 (out of order on purpose)
        conn.execute(
            """INSERT INTO document_groups (id, title, description, source_archive)
               VALUES (10,'Multi-page letter about ethics','AI group description',
                       'Nelson-Atkins')"""
        )
        conn.execute(
            """INSERT INTO documents (id, filename, sha256, title, transcription,
               group_id, page_number, source_archive)
               VALUES (4,'p2.jpg','sha4','page 2','Second page: the ethics question remains.',
                       10, 2, 'Nelson-Atkins')"""
        )
        conn.execute(
            """INSERT INTO documents (id, filename, sha256, title, transcription,
               group_id, page_number, source_archive)
               VALUES (5,'p1.jpg','sha5','page 1','First page: regarding the temple matter.',
                       10, 1, 'Nelson-Atkins')"""
        )
        # Entity + tag + transaction on doc 1 (for the generated representation).
        # 'Zzyzxconcept' exists ONLY in generated representations (entity name).
        conn.execute("INSERT INTO entities (id,name,normalized_name,type) VALUES (100,'Laurence Sickman','laurence sickman','person')")
        conn.execute("INSERT INTO document_entities (document_id,entity_id,role,context) VALUES (1,100,'author','wrote the letter')")
        conn.execute("INSERT INTO entities (id,name,normalized_name,type) VALUES (101,'Zzyzxconcept Dealer','zzyzxconcept dealer','person')")
        conn.execute("INSERT INTO document_entities (document_id,entity_id,role) VALUES (2,101,'dealer')")
        conn.execute("INSERT INTO tags (id,name) VALUES (200,'acquisition')")
        conn.execute("INSERT INTO document_tags (document_id,tag_id) VALUES (1,200)")
        conn.execute(
            """INSERT INTO transactions (document_id,seller,buyer,date,price,currency)
               VALUES (1,'Monk of the temple','Museum','1932-05-01',5000,'USD')"""
        )

    # Index through the production pipeline with the fake provider.
    fb = FakeBackend()
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "fake")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "fake-1")
    monkeypatch.setattr(indexer, "get_embedding_backend", lambda p=None, m=None: fb)
    monkeypatch.setattr(retrieval_mod, "get_embedding_backend", lambda p=None, m=None: fb)
    with dbmod.get_db() as conn:
        indexer.reindex_all(conn)

    return {"db_path": db_path, "backend": fb, "monkeypatch": monkeypatch}


def _fail_semantic(monkeypatch):
    monkeypatch.setattr(retrieval_mod, "get_embedding_backend",
                        lambda p=None, m=None: FailingBackend())


# ── Condition isolation ──────────────────────────────────────────────────────

def test_A_searches_transcription_chunks_only(test_db):
    with dbmod.get_db() as conn:
        # 'Zzyzxconcept' exists only in doc 2's generated representation
        out = keyword_original(conn, "Zzyzxconcept", 10)
        assert out["results"] == []
        # matching content in transcriptions is found, tagged as transcription
        out = keyword_original(conn, "shipping crates", 10)
        assert out["results"]
        assert all(r["representation_type"] == "transcription" for r in out["results"])
        assert out["embedding_provider"] is None      # no semantic component


def test_B_semantic_over_transcription_chunks_only(test_db):
    with dbmod.get_db() as conn:
        out = semantic_search(conn, "temple ceiling purchased", "transcription", 10)
    assert out["mode"] == "semantic_original"
    assert out["results"]
    assert all(r["representation_type"] == "transcription" for r in out["results"])
    assert out["results"][0]["doc_id"] == 1           # transcription match ranks first


def test_C_semantic_over_generated_chunks_only(test_db):
    with dbmod.get_db() as conn:
        out = semantic_search(conn, "acquisition of a temple ceiling", "generated", 10)
    assert out["mode"] == "semantic_modelled"
    assert out["results"]
    assert all(r["representation_type"] == "generated" for r in out["results"])


def test_generated_only_concept_retrieved_by_C_not_B(test_db):
    with dbmod.get_db() as conn:
        c = semantic_search(conn, "Zzyzxconcept", "generated", 10)
        b = semantic_search(conn, "Zzyzxconcept", "transcription", 10)
    # C: doc 2's generated representation contains the entity name → top hit,
    # discovered through the generated text itself
    assert c["results"][0]["doc_id"] == 2
    assert c["results"][0]["representation_type"] == "generated"
    assert "Zzyzxconcept" in c["results"][0]["excerpt"]
    # B: the concept exists in NO transcription — every B score is zero
    # (nothing to find) and no B result contains the concept, while C's
    # top hit has positive similarity through the generated text
    assert all("Zzyzxconcept" not in r["excerpt"] for r in b["results"])
    assert all(r["score"] == 0.0 for r in b["results"])
    assert c["results"][0]["score"] > 0.0


def test_annotations_never_in_any_chunk(test_db):
    with dbmod.get_db() as conn:
        rows = conn.execute("SELECT text FROM retrieval_chunks").fetchall()
        gen = build_generated_text(conn, 1, "document")
    assert all("RESEARCHER_SECRET_NOTE" not in r["text"] for r in rows)
    assert "RESEARCHER_SECRET_NOTE" not in gen


# ── Determinism, queries, metadata ───────────────────────────────────────────

def test_deterministic_ranking(test_db):
    with dbmod.get_db() as conn:
        runs = [semantic_search(conn, "temple ceiling", "transcription", 10)
                for _ in range(3)]
    orders = [[(r["record_type"], r["doc_id"], r["score"]) for r in run["results"]]
              for run in runs]
    assert orders[0] == orders[1] == orders[2]
    assert orders[0][0][1] == 1 and orders[0][0][0] == "document"


def test_identical_unrewritten_query_reaches_A_B_C(test_db):
    query = "temple ceiling purchased quietly"
    fb = test_db["backend"]
    fb.queries_embedded.clear()
    with dbmod.get_db() as conn:
        outs = [run_retrieval(conn, query, m, 5)
                for m in ("keyword_original", "semantic_original", "semantic_modelled")]
    # every condition records the exact original query string
    assert all(o["query"] == query for o in outs)
    # the embedded query text (B and C) is the verbatim query — no rewriting
    assert fb.queries_embedded == [query, query]


def test_provider_model_and_versions_recorded(test_db):
    with dbmod.get_db() as conn:
        for mode in ("keyword_original", "semantic_original", "semantic_modelled"):
            out = run_retrieval(conn, "temple", mode, 5)
            assert out["config_version"] == EXPERIMENT_CONFIG_VERSION == "chunked_retrieval_v1"
            assert out["index_schema_version"] == config.INDEX_SCHEMA_VERSION
            if mode == "keyword_original":
                assert out["embedding_provider"] is None
            else:
                assert out["embedding_provider"] == "fake"
                assert out["embedding_model"] == "fake-1"
            run_id = log_run(conn, "temple", mode, 5, out)
            row = conn.execute("SELECT * FROM experiment_runs WHERE id=?",
                               (run_id,)).fetchone()
            assert row["config_version"] == "chunked_retrieval_v1"
            assert row["index_schema_version"] == config.INDEX_SCHEMA_VERSION
            res = conn.execute("SELECT * FROM experiment_results WHERE run_id=? "
                               "ORDER BY rank", (run_id,)).fetchall()
            assert res
            assert all(r["chunk_id"] is not None for r in res)
            assert all(r["representation_type"] in ("transcription", "generated")
                       for r in res)
            assert all(r["excerpt"] for r in res)


# ── No silent fallback ───────────────────────────────────────────────────────

def test_no_fallback_on_backend_failure(test_db):
    _fail_semantic(test_db["monkeypatch"])
    with dbmod.get_db() as conn:
        with pytest.raises(ExperimentSearchError, match="no fallback"):
            semantic_search(conn, "temple", "transcription", 5)


def test_no_fallback_when_no_embeddings(test_db, monkeypatch):
    # a model with no stored vectors → explicit error, not keyword results
    fb2 = FakeBackend(model_name="fake-2")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "fake-2")
    monkeypatch.setattr(retrieval_mod, "get_embedding_backend",
                        lambda p=None, m=None: fb2)
    with dbmod.get_db() as conn:
        with pytest.raises(ExperimentSearchError, match="No stored embeddings"):
            semantic_search(conn, "temple", "transcription", 5)


def test_failed_run_is_logged_as_failed(test_db):
    _fail_semantic(test_db["monkeypatch"])
    with dbmod.get_db() as conn:
        try:
            semantic_search(conn, "temple", "transcription", 5)
        except ExperimentSearchError as e:
            log_run(conn, "temple", "semantic_original", 5, None, error=str(e))
        row = conn.execute("SELECT status, error FROM experiment_runs").fetchone()
    assert row["status"] == "failed" and "simulated API failure" in row["error"]


# ── source_archive & groups ──────────────────────────────────────────────────

def test_source_archive_preserved(test_db):
    with dbmod.get_db() as conn:
        for mode in ("keyword_original", "semantic_original", "semantic_modelled"):
            out = run_retrieval(conn, "shipping painting", mode, 10)
            archives = {r["doc_id"]: r["source_archive"] for r in out["results"]}
            assert archives.get(2) == "Cleveland Museum of Art", mode
            assert all(r["source_archive"] for r in out["results"]), mode


def test_group_retrieved_as_single_unit_pages_in_order(test_db):
    with dbmod.get_db() as conn:
        units = {(u["record_type"], u["unit_id"]): u for u in get_retrieval_units(conn)}
        out = keyword_original(conn, "ethics", 10)
    g = units[("group", 10)]["transcription"]
    assert g.index("First page") < g.index("Second page")
    hits = [(r["record_type"], r["doc_id"]) for r in out["results"]]
    assert ("group", 10) in hits
    assert ("document", 4) not in hits and ("document", 5) not in hits


def test_missing_transcription_absent_from_A_and_B(test_db):
    with dbmod.get_db() as conn:
        a = keyword_original(conn, "Untranscribed", 10)
        b = semantic_search(conn, "Untranscribed photo", "transcription", 10)
        c = semantic_search(conn, "Untranscribed photo", "generated", 10)
    assert all(r["doc_id"] != 3 for r in a["results"])
    assert all(r["doc_id"] != 3 for r in b["results"])   # no transcription chunks exist
    assert any(r["doc_id"] == 3 for r in c["results"])   # discoverable via generated only


# ── MODE D: frozen C ranking + evidence hydration ────────────────────────────

def test_D_preserves_C_exact_ordering_and_cannot_add_documents(test_db):
    with dbmod.get_db() as conn:
        c = run_retrieval(conn, "temple ceiling acquisition", "semantic_modelled", 5)
        d = run_rag(conn, "temple ceiling acquisition",
                    retrieval_mode="semantic_modelled", top_k=5, generate=False)
    c_order = [(r["record_type"], r["doc_id"]) for r in c["results"]]
    d_order = [(r["record_type"], r["doc_id"]) for r in d["retrieval"]["results"]]
    assert d_order == c_order                      # identical ranking, no rerank
    ev_units = [(e["record_type"], e["doc_id"]) for e in d["evidence"]]
    assert ev_units == c_order                     # evidence for exactly C's set
    assert d["config_version"] == "chunked_retrieval_v1"


def test_D_hydrates_transcription_evidence_from_selected_units(test_db):
    with dbmod.get_db() as conn:
        d = run_rag(conn, "temple ceiling purchased",
                    retrieval_mode="semantic_modelled", top_k=5, generate=False)
        chunk_units = {}
        for e in d["evidence"]:
            for cch in e["evidence_chunks"]:
                row = conn.execute(
                    "SELECT unit_id, record_type, representation_type "
                    "FROM retrieval_chunks WHERE id=?", (cch["chunk_id"],)).fetchone()
                chunk_units[cch["chunk_id"]] = row
    by_unit = {(e["record_type"], e["doc_id"]): e for e in d["evidence"]}
    doc1 = by_unit[("document", 1)]
    assert doc1["has_transcription"] is True
    assert doc1["evidence_chunks"]
    assert "purchased quietly" in doc1["evidence_chunks"][0]["text"]
    assert doc1["evidence_chunks"][0]["method"] in ("semantic", "keyword")
    # evidence chunks always come from the SELECTED unit's own transcription
    for chunk_id, row in chunk_units.items():
        assert row["representation_type"] == "transcription"
    for e in d["evidence"]:
        for cch in e["evidence_chunks"]:
            row = chunk_units[cch["chunk_id"]]
            assert (row["record_type"], row["unit_id"]) == (e["record_type"], e["doc_id"])
    # a unit with no transcription is reported, not silently given evidence
    if ("document", 3) in by_unit:
        assert by_unit[("document", 3)]["evidence_chunks"] == []
        assert by_unit[("document", 3)]["has_transcription"] is False


def test_D_context_uses_evidence_not_generated_text(test_db, monkeypatch):
    captured = {}

    class _FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            class _Resp:
                content = [type("B", (), {"text": "Quiet purchase [Doc #1]. "
                                                  "Hallucinated [Doc #99]."})()]
            return _Resp()

    class _FakeAnthropic:
        def __init__(self, api_key=None):
            self.messages = _FakeMessages()

    import modules.experiment_rag as rag_mod
    import anthropic as real_anthropic
    monkeypatch.setattr(real_anthropic, "Anthropic", _FakeAnthropic)

    with dbmod.get_db() as conn:
        d = run_rag(conn, "temple ceiling purchased",
                    retrieval_mode="semantic_modelled", top_k=5, generate=True)

    prompt = captured["messages"][0]["content"]
    system = captured["system"]
    # hydrated transcription passage is the labelled evidence
    assert "PRIMARY-SOURCE EVIDENCE" in prompt
    assert "purchased quietly" in prompt
    # generated fields are present ONLY as labelled machine-generated metadata
    assert "Machine-generated metadata — description: AI description" in prompt
    assert "machine-generated metadata" in system
    assert "never as evidence" in system
    # annotations never reach the model
    assert "RESEARCHER_SECRET_NOTE" not in prompt
    # citations validated against the frozen retrieved set only
    assert {(c["record_type"], c["doc_id"]) for c in d["citations"]} == {("document", 1)}
    # context = frozen set, same order, nothing added
    frozen = [(r["record_type"], r["doc_id"]) for r in d["retrieval"]["results"]]
    ctx = [(c["record_type"], c["doc_id"]) for c in d["context_ids"]]
    assert ctx == frozen
    assert d["prompt_template_version"] == PROMPT_TEMPLATE_VERSION
    # evidence is persisted alongside the generation record
    with dbmod.get_db() as conn:
        row = conn.execute("SELECT evidence_json, context_ids_json "
                           "FROM experiment_rag_runs").fetchone()
    assert json.loads(row["evidence_json"])
    assert [(c["record_type"], c["doc_id"]) for c in json.loads(row["context_ids_json"])] == frozen


def test_citations_only_from_retrieved():
    retrieved = [{"doc_id": 1, "record_type": "document"},
                 {"doc_id": 10, "record_type": "group"}]
    answer = ("The letter [Doc #1] shows caution. The group [Group #10] adds context. "
              "A hallucinated source [Doc #99] and [Group #1] should be dropped.")
    cites = extract_citations(answer, retrieved)
    assert {(c["record_type"], c["doc_id"]) for c in cites} == {
        ("document", 1), ("group", 10)}


def test_D_context_block_flags_missing_transcription(test_db):
    with dbmod.get_db() as conn:
        unit = {"doc_id": 3, "record_type": "document",
                "source_archive": "Harvard Art Museums",
                "evidence_chunks": [], "has_transcription": False}
        block, has_ev = build_context_block(conn, unit)
    assert not has_ev
    assert "PRIMARY-SOURCE EVIDENCE: none" in block
    assert "finding aid" in block


# ── Export ────────────────────────────────────────────────────────────────────

def test_experiment_runner_export(test_db, tmp_path):
    qfile = tmp_path / "queries.csv"
    qfile.write_text(
        "query_id,query,query_type,notes\n"
        "T1,temple ceiling,interpretive,test note\n", encoding="utf-8")
    outdir = tmp_path / "results"
    r = subprocess.run(
        [sys.executable, "experiment.py", "--queries", str(qfile),
         "--modes", "keyword_original", "--top-k", "5",
         "--output", str(outdir), "--db", str(test_db["db_path"])],
        cwd=str(BACKEND), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    run_dir = next(outdir.iterdir())
    manifest = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert manifest["config_version"] == "chunked_retrieval_v1"

    with open(run_dir / "results.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows
    for row in rows:
        assert row["config_version"] == "chunked_retrieval_v1"
        assert row["discovery_representation_type"] == "transcription"
        assert row["discovery_chunk_id"]
        assert row["discovery_excerpt"]

    with open(run_dir / "review.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows, "review.csv should contain ranked results"
    for row in rows:
        assert row["human_relevance"] == ""
        assert row["human_interpretive_value"] == ""
        assert row["notes"] == ""
        assert row["source_archive"]
        assert row["transcription_excerpt"]
        assert row["retrieval_mode"] == "keyword_original"

    with dbmod.get_db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM experiment_runs").fetchone()["c"]
    assert n >= 1
