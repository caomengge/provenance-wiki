"""
Tests for the production retrieval refactor (run from backend/):

    python -m pytest tests/test_retrieval_index.py -v

Covers the milestone requirements:
  embedding provider success/failure; one-time backfill; incremental
  indexing of new documents; unchanged content skipped; changed
  transcription regenerating only affected transcription chunks; changed
  generated metadata regenerating only the generated representation;
  model/version changes detected; transcription chunking; content-hash
  stability; source-archive preservation; semantic retrieval over
  transcription and generated representations; hybrid retrieval / RRF;
  QA receiving actual transcription excerpts; graceful keyword fallback
  when the embedding provider is unavailable; non-destructive migration.
"""

import hashlib
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND))

import config
from modules import db as dbmod
from modules.embeddings import EmbeddingBackend, EmbeddingError
from modules.representations import (
    chunk_transcription, sha256_text, build_representations,
    build_generated_text, get_retrieval_units,
)
from modules import indexer
from modules import retrieval as retrieval_mod
from modules import vector_store


# ── Fakes ─────────────────────────────────────────────────────────────────────

class CountingFakeBackend(EmbeddingBackend):
    """Deterministic lexical-overlap embeddings that count every API call."""
    provider = "fake"

    def __init__(self, model_name="fake-1"):
        self.model_name = model_name
        self.embedded_texts: list[str] = []   # every text sent to the "API"

    def embed(self, texts, input_type=None):
        self.embedded_texts.extend(texts)
        return [self._vec(t) for t in texts]

    def _vec(self, text):
        dim = 64
        vec = [0.0] * dim
        for tok in (text or "").lower().split():
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % dim] += 1.0
        vec.append(1.0)  # avoid zero vectors
        return vec


class FailingBackend(EmbeddingBackend):
    provider = "fake"
    model_name = "fake-1"

    def embed(self, texts, input_type=None):
        raise EmbeddingError("simulated provider outage")


@pytest.fixture()
def test_db(tmp_path, monkeypatch):
    """Fresh DB via the real init_db migration, with a small corpus."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    dbmod.init_db()

    long_para = ("The bronze vessel was crated in Shanghai and shipped to Kansas City "
                 "by the dealer in the spring. " * 12).strip()
    with dbmod.get_db() as conn:
        conn.execute(
            """INSERT INTO documents (id, filename, sha256, title, description,
               transcription, date_depicted, source_archive)
               VALUES (1,'a.jpg','sha1','Letter about temple ceiling',
                       'AI description: acquisition of a temple ceiling',
                       'Dear Sir, the ceiling of the temple was purchased quietly in Peking.',
                       '1932-05-01','Nelson-Atkins')""")
        conn.execute(
            f"""INSERT INTO documents (id, filename, sha256, title, description,
               transcription, source_archive)
               VALUES (2,'b.jpg','sha2','Invoice for bronze vessel',
                       'AI description: invoice for a bronze vessel',
                       'First paragraph about the invoice.\n\n{long_para}\n\nFinal remark about payment.',
                       'Cleveland Museum of Art')""")
        # Group with two pages; group transcription left NULL → concatenation
        conn.execute(
            """INSERT INTO document_groups (id, title, description, source_archive)
               VALUES (10,'Correspondence about jade','AI description: letters about jade',
                       'Harvard Art Museum')""")
        conn.execute(
            """INSERT INTO documents (id, filename, sha256, title, transcription,
               group_id, page_number, source_archive)
               VALUES (3,'c1.jpg','sha3','p1','Page one discusses the jade carving.',10,1,
                       'Harvard Art Museum')""")
        conn.execute(
            """INSERT INTO documents (id, filename, sha256, title, transcription,
               group_id, page_number, source_archive)
               VALUES (4,'c2.jpg','sha4','p2','Page two mentions the museum purchase.',10,2,
                       'Harvard Art Museum')""")
        # Document without transcription (generated representation only)
        conn.execute(
            """INSERT INTO documents (id, filename, sha256, title, description,
               source_archive)
               VALUES (5,'d.jpg','sha5','Photograph of a stele',
                       'AI description: photograph of a stone stele','Nelson-Atkins')""")
        # Entities / transactions / tags for generated representations
        conn.execute("INSERT INTO entities (id, name, normalized_name, type) "
                     "VALUES (100,'C. T. Loo','c. t. loo','person')")
        conn.execute("INSERT INTO document_entities (document_id, entity_id, role) "
                     "VALUES (1,100,'dealer')")
        conn.execute("INSERT INTO transactions (document_id, seller, buyer, date, price, currency) "
                     "VALUES (1,'C. T. Loo','Nelson Trust','1932-06-01',5000,'USD')")
        conn.execute("INSERT INTO tags (id, name) VALUES (200,'architecture')")
        conn.execute("INSERT INTO document_tags (document_id, tag_id) VALUES (1,200)")
    return db_path


@pytest.fixture()
def fake_backend(monkeypatch):
    """Route all embedding through a counting fake with model fake-1."""
    fb = CountingFakeBackend()
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "fake")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "fake-1")
    monkeypatch.setattr(indexer, "get_embedding_backend", lambda p=None, m=None: fb)
    monkeypatch.setattr(retrieval_mod, "get_embedding_backend", lambda p=None, m=None: fb)
    return fb


def _backfill(fake_backend):
    with dbmod.get_db() as conn:
        result = indexer.reindex_all(conn)
    return result


# ── Chunking & hashing ────────────────────────────────────────────────────────

def test_chunking_deterministic_and_bounded():
    text = "Para one.\n\nPara two is a bit longer than one.\n\nPara three."
    a = chunk_transcription(text)
    b = chunk_transcription(text)
    assert a == b                       # reproducible
    assert a == [text]                  # short text → single chunk
    long_text = "\n\n".join(f"Paragraph {i} " + ("lorem ipsum " * 30) for i in range(8))
    chunks = chunk_transcription(long_text)
    assert len(chunks) > 1
    max_len = config.CHUNK_MAX_CHARS + config.CHUNK_OVERLAP_CHARS + 10
    assert all(len(c) <= max_len for c in chunks)
    # overlap: each later chunk carries a tail of the previous packing
    assert all(c.startswith("… ") for c in chunks[1:])


def test_chunking_cjk_sentences_and_empty():
    assert chunk_transcription("") == []
    assert chunk_transcription(None) == []
    cjk = "这是第一句。这是第二句！这是第三句？" * 120   # one giant "paragraph"
    chunks = chunk_transcription(cjk)
    assert len(chunks) > 1
    assert "第一句" in chunks[0]


def test_oversized_generated_representation_is_chunked():
    from modules.representations import chunk_generated
    header = "Title: Big group\nDate: 1935\nDescription: many letters"
    body = "\n".join(f"Entity: Person {i} (person), role: correspondent" for i in range(400))
    chunks = chunk_generated(f"{header}\n{body}")
    assert len(chunks) > 1
    assert all(len(c) <= config.GENERATED_MAX_CHARS + 100 for c in chunks)
    assert all(c.startswith("Title: Big group") for c in chunks)  # self-describing
    assert chunk_generated("Title: small") == ["Title: small"]    # small → single


def test_content_hash_stability():
    assert sha256_text("abc") == sha256_text("abc")
    assert sha256_text("abc") != sha256_text("abd")
    assert sha256_text("") == sha256_text(None)  # both hash the empty string


# ── Representations ───────────────────────────────────────────────────────────

def test_representations_and_source_archive(test_db):
    with dbmod.get_db() as conn:
        reps = build_representations(conn, 1, "document")
        types = {r["representation_type"] for r in reps}
        assert types == {"transcription", "generated"}
        assert all(r["source_archive"] == "Nelson-Atkins" for r in reps)
        assert all(r["title"] == "Letter about temple ceiling" for r in reps)
        gen = next(r for r in reps if r["representation_type"] == "generated")
        assert "C. T. Loo" in gen["text"]           # entities included
        assert "architecture" in gen["text"]        # tags included
        assert "Nelson Trust" in gen["text"]        # transactions included
        # transcription chunk contains the primary source, not the AI description
        tr = next(r for r in reps if r["representation_type"] == "transcription")
        assert "purchased quietly" in tr["text"]
        assert "AI description" not in tr["text"]


def test_group_units_concatenate_pages(test_db):
    with dbmod.get_db() as conn:
        units = get_retrieval_units(conn)
        keyed = {(u["record_type"], u["unit_id"]): u for u in units}
        # grouped pages are NOT standalone units
        assert ("document", 3) not in keyed
        assert ("document", 4) not in keyed
        g = keyed[("group", 10)]
        assert "Page one" in g["transcription"] and "Page two" in g["transcription"]
        assert g["source_archive"] == "Harvard Art Museum"


# ── Backfill & incremental indexing ──────────────────────────────────────────

def test_backfill_embeds_everything_once(test_db, fake_backend):
    result = _backfill(fake_backend)
    assert result["units"] == 4          # docs 1,2,5 + group 10
    emb = result["embedding"]
    assert emb["failed"] == 0 and emb["error"] is None
    assert emb["embedded"] == len(fake_backend.embedded_texts) > 0
    with dbmod.get_db() as conn:
        status = indexer.index_status(conn)
        assert status["semantic_ready"] is True
        assert status["chunks_needing_embedding"] == 0
        # provider/model/version stored with every embedding
        row = conn.execute("SELECT DISTINCT provider, model_name, index_schema_version "
                           "FROM retrieval_embeddings").fetchall()
        assert [tuple(r) for r in row] == [("fake", "fake-1", config.INDEX_SCHEMA_VERSION)]


def test_unchanged_content_skipped(test_db, fake_backend):
    _backfill(fake_backend)
    calls_before = len(fake_backend.embedded_texts)
    result = _backfill(fake_backend)     # "reindex all" on unchanged corpus
    assert result["chunks_changed"] == 0
    assert result["embedding"]["embedded"] == 0
    assert len(fake_backend.embedded_texts) == calls_before   # zero API calls


def test_new_document_indexed_incrementally(test_db, fake_backend):
    _backfill(fake_backend)
    calls_before = len(fake_backend.embedded_texts)
    with dbmod.get_db() as conn:
        conn.execute(
            """INSERT INTO documents (id, filename, sha256, title, transcription,
               source_archive)
               VALUES (6,'e.jpg','sha6','New letter','A new letter about porcelain.',
                       'Cleveland Museum of Art')""")
        indexer.index_unit(conn, 6, "document")
    new_texts = fake_backend.embedded_texts[calls_before:]
    assert new_texts                                  # only the new doc embedded
    assert all("porcelain" in t or "New letter" in t for t in new_texts)


def test_changed_transcription_regenerates_only_affected_chunks(test_db, fake_backend):
    _backfill(fake_backend)
    calls_before = len(fake_backend.embedded_texts)
    with dbmod.get_db() as conn:
        conn.execute("UPDATE documents SET transcription="
                     "'Dear Sir, the ceiling of the temple was purchased openly in Peking.' "
                     "WHERE id=1")
        indexer.index_unit(conn, 1, "document")
    new_texts = fake_backend.embedded_texts[calls_before:]
    assert len(new_texts) == 1                        # one transcription chunk only
    assert "purchased openly" in new_texts[0]
    assert not any("Entity:" in t for t in new_texts) # generated rep untouched


def test_changed_metadata_regenerates_only_generated_rep(test_db, fake_backend):
    _backfill(fake_backend)
    calls_before = len(fake_backend.embedded_texts)
    with dbmod.get_db() as conn:
        conn.execute("INSERT INTO entities (id,name,normalized_name,type) "
                     "VALUES (101,'Laurence Sickman','laurence sickman','person')")
        conn.execute("INSERT INTO document_entities (document_id, entity_id, role) "
                     "VALUES (1,101,'curator')")
        indexer.index_unit(conn, 1, "document")
    new_texts = fake_backend.embedded_texts[calls_before:]
    assert len(new_texts) == 1                        # only the generated rep
    assert "Laurence Sickman" in new_texts[0]
    assert "Dear Sir" not in new_texts[0]             # transcription chunks untouched


def test_model_change_detected_and_never_mixed(test_db, fake_backend, monkeypatch):
    _backfill(fake_backend)
    n_chunks = None
    with dbmod.get_db() as conn:
        n_chunks = conn.execute("SELECT COUNT(*) c FROM retrieval_chunks").fetchone()["c"]

    fb2 = CountingFakeBackend(model_name="fake-2")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "fake-2")
    monkeypatch.setattr(indexer, "get_embedding_backend", lambda p=None, m=None: fb2)
    monkeypatch.setattr(retrieval_mod, "get_embedding_backend", lambda p=None, m=None: fb2)

    with dbmod.get_db() as conn:
        # all chunks need embedding under the new model
        status = indexer.index_status(conn)
        assert status["chunks_needing_embedding"] == n_chunks
        emb = indexer.embed_pending(conn)
        assert emb["embedded"] == n_chunks
        # both models' vectors coexist; search never mixes them
        models = {(r["provider"], r["model_name"])
                  for r in conn.execute("SELECT DISTINCT provider, model_name "
                                        "FROM retrieval_embeddings")}
        assert models == {("fake", "fake-1"), ("fake", "fake-2")}
        hits = vector_store.search_vectors(
            conn, fb2._vec("temple ceiling"), provider="fake", model_name="fake-2",
            schema_version=config.INDEX_SCHEMA_VERSION)
        assert hits  # results exist for the active model only


def test_provider_failure_preserves_data_and_allows_retry(test_db, monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "fake")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "fake-1")
    failing = FailingBackend()
    monkeypatch.setattr(indexer, "get_embedding_backend", lambda p=None, m=None: failing)

    with dbmod.get_db() as conn:
        result = indexer.reindex_all(conn)
        # chunks + FTS exist even though embedding failed
        assert conn.execute("SELECT COUNT(*) c FROM retrieval_chunks").fetchone()["c"] > 0
        assert result["embedding"]["failed"] > 0
        assert result["embedding"]["error"]
        # failures recorded, marked for retry — content data untouched
        st = indexer.index_status(conn)
        assert st["semantic_ready"] is False
        assert st["chunks_needing_embedding"] > 0
        assert conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"] == 5

    # retry with a working backend picks up exactly the pending chunks
    fb = CountingFakeBackend()
    monkeypatch.setattr(indexer, "get_embedding_backend", lambda p=None, m=None: fb)
    with dbmod.get_db() as conn:
        emb = indexer.embed_pending(conn)
        assert emb["failed"] == 0 and emb["embedded"] > 0
        assert indexer.index_status(conn)["semantic_ready"] is True


# ── Retrieval ─────────────────────────────────────────────────────────────────

def test_semantic_retrieval_over_transcription(test_db, fake_backend):
    _backfill(fake_backend)
    with dbmod.get_db() as conn:
        hits = retrieval_mod.semantic_candidates(conn, "temple ceiling purchased",
                                                 "transcription", limit=5)
    assert hits[0]["unit_id"] == 1
    assert hits[0]["representation_type"] == "transcription"
    assert hits[0]["source_archive"] == "Nelson-Atkins"
    assert "purchased quietly" in hits[0]["text"]


def test_semantic_retrieval_over_generated(test_db, fake_backend):
    _backfill(fake_backend)
    with dbmod.get_db() as conn:
        hits = retrieval_mod.semantic_candidates(conn, "photograph stone stele",
                                                 "generated", limit=5)
    assert hits[0]["unit_id"] == 5                    # doc without transcription
    assert hits[0]["representation_type"] == "generated"


def test_hybrid_retrieval_rrf(test_db, fake_backend):
    _backfill(fake_backend)
    with dbmod.get_db() as conn:
        out = retrieval_mod.hybrid_retrieve(conn, "temple ceiling purchased", top_k=5)
    assert out["semantic_available"] is True
    assert out["fusion"]["method"] == "reciprocal_rank_fusion"
    top = out["results"][0]
    assert (top["record_type"], top["unit_id"]) == ("document", 1)
    # transparency: per-list ranks + fused score, all metadata preserved
    assert top["keyword_rank"] == 1
    assert top["semantic_transcription_rank"] == 1
    assert top["source_archive"] == "Nelson-Atkins"
    assert top["date_display"] == "1932-05-01"
    assert top["representation_type"] == "transcription"   # excerpt = primary source
    assert "purchased quietly" in top["excerpt"]
    # RRF arithmetic: sum of 1/(k + best rank) over the lists the unit appears in
    k = config.RRF_K
    expected = sum(1.0 / (k + r) for r in (
        top["keyword_rank"], top["semantic_transcription_rank"],
        top["semantic_generated_rank"]) if r is not None)
    assert abs(top["fused_score"] - expected) < 1e-9


def test_hybrid_degrades_to_keyword_when_provider_down(test_db, fake_backend, monkeypatch):
    _backfill(fake_backend)   # index built while provider was up
    monkeypatch.setattr(retrieval_mod, "get_embedding_backend",
                        lambda p=None, m=None: FailingBackend())
    with dbmod.get_db() as conn:
        out = retrieval_mod.hybrid_retrieve(conn, "temple ceiling", top_k=5)
    assert out["semantic_available"] is False
    assert "outage" in out["semantic_error"]
    assert out["results"]                              # keyword hits still returned
    assert all(u["keyword_rank"] is not None for u in out["results"])
    # legacy hashed vectors were NOT substituted: no semantic ranks at all
    assert all(u["semantic_transcription_rank"] is None for u in out["results"])


def test_search_documents_semantic_and_fallback(test_db, fake_backend, monkeypatch):
    _backfill(fake_backend)
    from modules.search import search_documents
    res = search_documents("temple ceiling purchased", mode="semantic")
    assert res["mode"] == "semantic" and res["semantic_available"] is True
    assert res["results"][0]["id"] == 1
    assert "purchased" in res["results"][0]["snippet"]

    monkeypatch.setattr(retrieval_mod, "get_embedding_backend",
                        lambda p=None, m=None: FailingBackend())
    res = search_documents("temple ceiling", mode="semantic")
    assert res["semantic_available"] is False          # honest, labelled fallback
    assert res["mode"] == "keyword"
    assert res["requested_mode"] == "semantic"


def test_keyword_channel_searches_transcription_only(test_db, fake_backend):
    """A term appearing ONLY in an AI-generated representation must not
    produce a hit through the keyword-transcription channel."""
    _backfill(fake_backend)
    with dbmod.get_db() as conn:
        # 'stele' appears only in doc #5's generated representation
        # (description/title); doc #5 has no transcription at all.
        default_hits = retrieval_mod.keyword_candidates(conn, "stele")
        assert default_hits == []                       # production default
        # explicit opt-in for experiments still reaches generated text
        exp_hits = retrieval_mod.keyword_candidates(conn, "stele",
                                                    representation_type=None)
        assert any(h["unit_id"] == 5 and h["representation_type"] == "generated"
                   for h in exp_hits)
        # hybrid fusion records the restricted keyword scope, and doc #5 can
        # only ever arrive via the semantic-generated channel — its keyword
        # rank must be None even though 'stele' appears in its generated text
        out = retrieval_mod.hybrid_retrieve(conn, "stele", top_k=10)
        assert out["fusion"]["keyword_representation"] == "transcription"
        assert all(u["keyword_rank"] is None for u in out["results"])
        doc5 = next(u for u in out["results"]
                    if (u["record_type"], u["unit_id"]) == ("document", 5))
        assert doc5["semantic_generated_rank"] is not None


def test_keyword_only_fallback_cannot_surface_generated_terms(test_db, fake_backend, monkeypatch):
    """With the provider down (keyword-only degradation), a generated-only
    term finds nothing — generated text never leaks into keyword retrieval."""
    _backfill(fake_backend)
    monkeypatch.setattr(retrieval_mod, "get_embedding_backend",
                        lambda p=None, m=None: FailingBackend())
    with dbmod.get_db() as conn:
        out = retrieval_mod.hybrid_retrieve(conn, "stele", top_k=5)
    assert out["semantic_available"] is False
    assert out["results"] == []


# ── Evidence hydration ────────────────────────────────────────────────────────

def test_hydration_picks_most_relevant_chunk_semantic_and_keyword(test_db, fake_backend, monkeypatch):
    """Within a multi-chunk unit, hydration returns the chunks most relevant
    to the query — semantically when available, by term overlap otherwise."""
    _backfill(fake_backend)
    with dbmod.get_db() as conn:
        unit = {"unit_id": 2, "record_type": "document"}   # doc 2 has 3+ chunks
        retrieval_mod.hydrate_evidence(conn, "bronze vessel crated Shanghai",
                                       [unit], top_n=1)
        assert unit["has_transcription"] is True
        assert unit["evidence_chunks"][0]["method"] == "semantic"
        assert "Shanghai" in unit["evidence_chunks"][0]["text"]

        # keyword fallback when the provider is down
        monkeypatch.setattr(retrieval_mod, "get_embedding_backend",
                            lambda p=None, m=None: FailingBackend())
        unit2 = {"unit_id": 2, "record_type": "document"}
        retrieval_mod.hydrate_evidence(conn, "payment final remark", [unit2],
                                       top_n=1)
        assert unit2["evidence_chunks"][0]["method"] == "keyword"
        assert "payment" in unit2["evidence_chunks"][0]["text"]


def test_generated_only_discovery_still_yields_primary_evidence(test_db, fake_backend, monkeypatch):
    """A document discovered ONLY through its generated representation must
    still supply its most query-relevant transcription passage to Q&A."""
    with dbmod.get_db() as conn:
        # Transcription (two chunks after packing) never mentions the dealer;
        # the dealer's name exists only in the generated representation.
        para1 = "The weather in Peking has been unusually cold this winter. " * 8
        para2 = "The kiln fired celadon glaze wares arrived without damage. " * 8
        conn.execute(
            f"""INSERT INTO documents (id, filename, sha256, title, description,
                transcription, source_archive)
                VALUES (7,'g.jpg','sha7','Letter concerning shipment',
                        'AI description: letter about wares',
                        '{para1}\n\n{para2}','Cleveland Museum of Art')""")
        conn.execute("INSERT INTO entities (id,name,normalized_name,type) "
                     "VALUES (102,'Zzyzx Marchant','zzyzx marchant','person')")
        conn.execute("INSERT INTO document_entities (document_id, entity_id, role) "
                     "VALUES (7,102,'dealer')")
    _backfill(fake_backend)

    with dbmod.get_db() as conn:
        out = retrieval_mod.hybrid_retrieve(conn, "Zzyzx Marchant", top_k=3)
        top = out["results"][0]
        # discovered exclusively via the generated representation:
        assert (top["record_type"], top["unit_id"]) == ("document", 7)
        assert top["keyword_rank"] is None                       # name absent from transcription
        assert top["semantic_generated_rank"] == 1
        assert all(m["representation_type"] == "generated"
                   for m in top["discovery_matches"]
                   if m["rank"] == 1 and m["list"] == "semantic_generated")

        # …but hydration still attaches primary-source passages:
        retrieval_mod.hydrate_evidence(conn, "Zzyzx Marchant celadon wares",
                                       out["results"],
                                       query_vec=out["_query_vec"])
        assert top["has_transcription"] is True
        assert top["evidence_chunks"]
        assert "celadon" in top["evidence_chunks"][0]["text"]    # most relevant chunk
        # discovery and evidence remain distinct records
        assert top["evidence_chunks"][0]["chunk_id"] not in {
            m["chunk_id"] for m in top["discovery_matches"]
            if m["representation_type"] == "generated"}

    # And end-to-end: Q&A passes that passage to Claude as primary evidence.
    captured = {}

    class _FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            class _Resp:
                content = [type("B", (), {"text": "See [Doc #7]."})()]
            return _Resp()

    class _FakeAnthropic:
        def __init__(self, api_key=None):
            self.messages = _FakeMessages()

    from modules import qa as qa_mod
    monkeypatch.setattr(qa_mod.anthropic, "Anthropic", _FakeAnthropic)
    out = qa_mod.answer_question("What did Zzyzx Marchant handle?", "test-key")
    prompt = captured["messages"][0]["content"]
    assert "celadon glaze wares" in prompt               # hydrated primary source
    item = next(i for i in out["context_items"]
                if (i["record_type"], i["id"]) == ("document", 7))
    assert item["evidence_chunks"] and item["has_transcription"]


# ── Q&A context ───────────────────────────────────────────────────────────────

def test_qa_receives_actual_transcription_excerpts(test_db, fake_backend, monkeypatch):
    _backfill(fake_backend)

    captured = {}

    class _FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            class _Resp:
                content = [type("B", (), {"text": "The ceiling was bought [Doc #1]."})()]
            return _Resp()

    class _FakeAnthropic:
        def __init__(self, api_key=None):
            self.messages = _FakeMessages()

    from modules import qa as qa_mod
    monkeypatch.setattr(qa_mod.anthropic, "Anthropic", _FakeAnthropic)

    out = qa_mod.answer_question("Who purchased the temple ceiling?", "test-key")
    prompt = captured["messages"][0]["content"]
    # actual primary-source excerpt, with archive + labelled generated metadata
    assert "purchased quietly in Peking" in prompt
    assert "PRIMARY-SOURCE EVIDENCE" in prompt
    assert "Source archive: Nelson-Atkins" in prompt
    assert "Machine-generated description" in prompt
    assert out["retrieval"]["mode"] == "hybrid"
    assert out["citations"][0]["doc_id"] == 1
    assert out["citations"][0]["record_type"] == "document"
    assert out["citations"][0]["source_archive"] == "Nelson-Atkins"
    # confidence is DEPRECATED (was citation-count-derived); replaced by
    # source_count + retrieval metadata
    assert out["confidence"] is None
    assert out["source_count"] == len(out["sources"]) > 0
    assert out["retrieval"]["semantic_available"] is True
    assert out["retrieval"]["provider"] == "fake"
    # discovery vs evidence separation is exposed for the frontend
    item = next(i for i in out["context_items"]
                if (i["record_type"], i["id"]) == ("document", 1))
    assert item["has_transcription"] is True
    assert item["discovery_matches"] and item["evidence_chunks"]
    assert item["evidence_chunks"][0]["method"] == "semantic"
    assert "purchased quietly" in item["evidence_chunks"][0]["text"]


# ── Migration safety ──────────────────────────────────────────────────────────

def test_migration_is_additive_and_idempotent(test_db, fake_backend):
    _backfill(fake_backend)
    with dbmod.get_db() as conn:
        docs_before = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        emb_before = conn.execute("SELECT COUNT(*) c FROM retrieval_embeddings").fetchone()["c"]
    dbmod.init_db()   # re-run migration on a populated database
    with dbmod.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"] == docs_before
        assert conn.execute("SELECT COUNT(*) c FROM retrieval_embeddings").fetchone()["c"] == emb_before
        # legacy column preserved (data preservation, even though unused)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(documents)")]
        assert "embedding_json" in cols


def test_trashed_and_grouped_units_removed(test_db, fake_backend):
    _backfill(fake_backend)
    with dbmod.get_db() as conn:
        conn.execute("UPDATE documents SET is_trashed=1 WHERE id=2")
        indexer.reindex_all(conn)
        left = {(r["record_type"], r["unit_id"]) for r in conn.execute(
            "SELECT DISTINCT record_type, unit_id FROM retrieval_chunks")}
    assert ("document", 2) not in left
    assert ("group", 10) in left
