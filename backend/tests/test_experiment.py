"""
Tests for the retrieval-methods experiment (run from backend/):

    python -m pytest tests/test_experiment.py -v

Covers the methodological requirements from the research spec:
  deterministic ranking, original/modelled separation, no silent fallback,
  source_archive preservation, group handling, result export, citation
  validation, missing-transcription behavior, and embedding-failure behavior.
"""

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND))

import config
from modules import db as dbmod
from modules.embeddings import (
    EmbeddingBackend, EmbeddingError, _backend_cache, cosine_similarity,
)
from modules.experiment_schema import (
    ensure_experiment_schema, rebuild_transcription_fts,
    get_retrieval_units, build_modelled_text,
)
from modules.experiment_search import (
    run_retrieval, semantic_search, keyword_original, log_run,
    ExperimentSearchError, get_transcription_excerpt,
)
from modules.experiment_rag import extract_citations, build_context_block


# ── Fixtures ──────────────────────────────────────────────────────────────────

class FakeBackend(EmbeddingBackend):
    """Deterministic vocabulary-count embeddings (NOT the hashed BoW in extractor)."""
    provider = "fake"
    model_name = "fake-1"
    VOCAB = ["temple", "ceiling", "ethics", "shipping", "painting", "letter"]

    def embed(self, texts):
        out = []
        for t in texts:
            low = (t or "").lower()
            vec = [float(low.count(w)) for w in self.VOCAB] + [1.0]  # avoid zero vectors
            out.append(vec)
        return out


class FailingBackend(EmbeddingBackend):
    provider = "failing"
    model_name = "failing-1"

    def embed(self, texts):
        raise EmbeddingError("simulated API failure")


@pytest.fixture()
def test_db(tmp_path, monkeypatch):
    """Fresh SQLite DB with the production schema plus small test corpus."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    dbmod.init_db()

    with dbmod.get_db() as conn:
        # Standalone documents
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
        # Document with NO transcription
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
        # Entity + tag on doc 1 (for modelled representation)
        conn.execute("INSERT INTO entities (id,name,normalized_name,type) VALUES (100,'Laurence Sickman','laurence sickman','person')")
        conn.execute("INSERT INTO document_entities (document_id,entity_id,role,context) VALUES (1,100,'author','wrote the letter')")
        conn.execute("INSERT INTO tags (id,name) VALUES (200,'acquisition')")
        conn.execute("INSERT INTO document_tags (document_id,tag_id) VALUES (1,200)")
        conn.execute(
            """INSERT INTO transactions (document_id,seller,buyer,date,price,currency)
               VALUES (1,'Monk of the temple','Museum','1932-05-01',5000,'USD')"""
        )

        ensure_experiment_schema(conn)
        rebuild_transcription_fts(conn)

    _backend_cache[("fake", "fake-1")] = FakeBackend()
    _backend_cache[("failing", "failing-1")] = FailingBackend()
    yield db_path
    _backend_cache.clear()


def _store_fake_embeddings(representations=("original", "modelled")):
    """Embed all units with FakeBackend and store rows."""
    backend = FakeBackend()
    with dbmod.get_db() as conn:
        for u in get_retrieval_units(conn):
            for rep in representations:
                text = (u["original_text"] if rep == "original"
                        else build_modelled_text(conn, u["unit_id"], u["record_type"]))
                if not text.strip():
                    continue
                vec = backend.embed_one(text)
                conn.execute(
                    """INSERT OR REPLACE INTO document_embeddings
                       (document_id, record_type, representation_type, provider,
                        model_name, dim, embedding_json)
                       VALUES (?,?,?,?,?,?,?)""",
                    (u["unit_id"], u["record_type"], rep, backend.provider,
                     backend.model_name, len(vec), json.dumps(vec)),
                )


# ── 1. Deterministic ranking ─────────────────────────────────────────────────

def test_deterministic_ranking(test_db):
    _store_fake_embeddings()
    with dbmod.get_db() as conn:
        runs = [semantic_search(conn, "temple ceiling", "original", 10,
                                provider="fake", model_name="fake-1")
                for _ in range(3)]
    orders = [[(r["record_type"], r["doc_id"], r["score"]) for r in run["results"]]
              for run in runs]
    assert orders[0] == orders[1] == orders[2]
    # Doc 1 mentions both terms; must rank first
    assert orders[0][0][1] == 1 and orders[0][0][0] == "document"


# ── 2. Original vs modelled separation ───────────────────────────────────────

def test_representation_separation(test_db):
    with dbmod.get_db() as conn:
        modelled = build_modelled_text(conn, 1, "document")
        units = {(u["record_type"], u["unit_id"]): u for u in get_retrieval_units(conn)}
        original = units[("document", 1)]["original_text"]

    # Modelled = AI metadata; never contains the primary transcription
    assert "purchased quietly" not in modelled
    assert "AI description" in modelled
    assert "Laurence Sickman" in modelled and "Transaction:" in modelled
    # Researcher annotations must never leak into either representation
    assert "RESEARCHER_SECRET_NOTE" not in modelled
    assert "RESEARCHER_SECRET_NOTE" not in original
    # Original = transcription; not the AI description
    assert "purchased quietly" in original
    assert "AI description" not in original


def test_stored_embeddings_keyed_by_representation_and_model(test_db):
    _store_fake_embeddings()
    with dbmod.get_db() as conn:
        rows = conn.execute(
            """SELECT representation_type, COUNT(*) c FROM document_embeddings
               WHERE model_name='fake-1' GROUP BY representation_type"""
        ).fetchall()
        counts = {r["representation_type"]: r["c"] for r in rows}
    # original: docs 1,2 + group 10 (doc 3 has no transcription) = 3
    # modelled: docs 1,2,3 + group 10 = 4
    assert counts == {"original": 3, "modelled": 4}


# ── 3. No silent fallback ────────────────────────────────────────────────────

def test_no_fallback_when_no_embeddings(test_db):
    with dbmod.get_db() as conn:
        with pytest.raises(ExperimentSearchError, match="No stored embeddings"):
            semantic_search(conn, "temple", "original", 5,
                            provider="fake", model_name="fake-1")


def test_no_fallback_on_backend_failure(test_db):
    with dbmod.get_db() as conn:
        with pytest.raises(ExperimentSearchError, match="no fallback"):
            semantic_search(conn, "temple", "original", 5,
                            provider="failing", model_name="failing-1")


def test_failed_run_is_logged_as_failed(test_db):
    with dbmod.get_db() as conn:
        try:
            semantic_search(conn, "temple", "original", 5,
                            provider="failing", model_name="failing-1")
        except ExperimentSearchError as e:
            log_run(conn, "temple", "semantic_original", 5, None, error=str(e))
        row = conn.execute("SELECT status, error FROM experiment_runs").fetchone()
    assert row["status"] == "failed" and "simulated API failure" in row["error"]


# ── 4. source_archive preservation ───────────────────────────────────────────

def test_source_archive_preserved(test_db):
    _store_fake_embeddings()
    with dbmod.get_db() as conn:
        for mode in ("keyword_original", "semantic_original", "semantic_modelled"):
            out = run_retrieval(conn, "shipping painting", mode, 10,
                                provider="fake", model_name="fake-1")
            archives = {r["doc_id"]: r["source_archive"] for r in out["results"]}
            assert archives.get(2) == "Cleveland Museum of Art", mode
            assert all(r["source_archive"] for r in out["results"]), mode


# ── 5. Group handling ────────────────────────────────────────────────────────

def test_group_concatenates_pages_in_order(test_db):
    with dbmod.get_db() as conn:
        units = {(u["record_type"], u["unit_id"]): u for u in get_retrieval_units(conn)}
    g = units[("group", 10)]["original_text"]
    assert g.index("First page") < g.index("Second page")


def test_group_retrieved_as_single_unit(test_db):
    with dbmod.get_db() as conn:
        out = keyword_original(conn, "ethics", 10)
    hits = [(r["record_type"], r["doc_id"]) for r in out["results"]]
    assert ("group", 10) in hits
    # Grouped pages (docs 4, 5) must never surface as standalone results
    assert ("document", 4) not in hits and ("document", 5) not in hits


# ── 6. Export ────────────────────────────────────────────────────────────────

def test_experiment_runner_export(test_db, tmp_path):
    qfile = tmp_path / "queries.csv"
    qfile.write_text(
        "query_id,query,query_type,notes\n"
        "T1,temple ceiling,interpretive,test note\n", encoding="utf-8")
    outdir = tmp_path / "results"
    r = subprocess.run(
        [sys.executable, "experiment.py", "--queries", str(qfile),
         "--modes", "keyword_original", "--top-k", "5",
         "--output", str(outdir), "--db", str(test_db)],
        cwd=str(BACKEND), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    run_dir = next(outdir.iterdir())
    assert (run_dir / "results.json").exists()
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
    # Runs are also logged in the DB
    with dbmod.get_db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM experiment_runs").fetchone()["c"]
    assert n >= 1


# ── 7. RAG citations restricted to retrieved documents ───────────────────────

def test_citations_only_from_retrieved(test_db):
    retrieved = [{"doc_id": 1, "record_type": "document"},
                 {"doc_id": 10, "record_type": "group"}]
    answer = ("The letter [Doc #1] shows caution. The group [Group #10] adds context. "
              "A hallucinated source [Doc #99] and [Group #1] should be dropped.")
    cites = extract_citations(answer, retrieved)
    assert {(c["record_type"], c["doc_id"]) for c in cites} == {
        ("document", 1), ("group", 10)}


def test_rag_context_uses_transcription_and_labels_metadata(test_db):
    with dbmod.get_db() as conn:
        block, has_t = build_context_block(conn, 1, "document")
    assert has_t
    assert "purchased quietly" in block                      # primary source present
    assert "Machine-generated metadata" in block             # AI fields labelled
    assert "RESEARCHER_SECRET_NOTE" not in block             # annotations excluded


# ── 8. Missing transcription behavior ────────────────────────────────────────

def test_missing_transcription_reported_not_silent(test_db):
    with dbmod.get_db() as conn:
        stats = rebuild_transcription_fts(conn)
        assert ("document", 3) in stats["missing_transcription"]
        # And it is genuinely absent from keyword results
        out = keyword_original(conn, "Untranscribed", 10)
        assert all(r["doc_id"] != 3 for r in out["results"])
        # RAG context for it states the absence explicitly
        block, has_t = build_context_block(conn, 3, "document")
        assert not has_t and "no transcription available" in block


# ── 9. Cosine sanity ─────────────────────────────────────────────────────────

def test_cosine_similarity_basics():
    assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([0, 0], [1, 1]) == 0.0
