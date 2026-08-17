"""
Tests for the deterministic filename lookup utility (run from backend/):

    python -m pytest tests/test_filename_lookup.py -v

Covers: exact-before-substring precedence, the three query forms the
researcher uses (IMG_4683.JPG / IMG_4683 / 4683), case-insensitivity,
grouped pages and trashed documents, and — critically — that the utility is
a metadata facility that never touches retrieval: search results, their
order and their scores are identical with and without it, it works with the
embedding provider unavailable, and it consults no chunk/embedding table.
"""

import hashlib
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
from modules.filename_lookup import (
    find_by_filename, looks_like_filename_query, filename_match_block,
)
from modules.search import search_documents


class FakeBackend(EmbeddingBackend):
    provider = "fake"
    model_name = "fake-1"

    def embed(self, texts, input_type=None):
        out = []
        for t in texts:
            v = [0.0] * 64
            for tok in (t or "").lower().split():
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 64] += 1.0
            v.append(1.0)
            out.append(v)
        return out


class FailingBackend(EmbeddingBackend):
    provider = "fake"
    model_name = "fake-1"

    def embed(self, texts, input_type=None):
        raise EmbeddingError("simulated provider outage")


@pytest.fixture()
def test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    dbmod.init_db()

    rows = [
        (1, "IMG_4683.JPG", "Postscript letter about a temple ceiling", "Nelson-Atkins",
         "The ceiling was shipped quietly to Kansas City.", None, 0),
        (2, "IMG_46830.JPG", "Unrelated later photograph", "Nelson-Atkins",
         "A photograph of a stone stele.", None, 0),
        (3, "SCAN_4683_back.jpg", "Verso of a mounted print", "Cleveland Museum of Art",
         "Verso inscription in ink.", None, 0),
        (4, "IMG_4684.JPG", "Invoice for shipping", "Harvard Art Museum",
         "Invoice for twelve crates.", None, 0),
        (5, "IMG_9001.JPG", "Trashed duplicate", "Nelson-Atkins",
         "Duplicate scan.", None, 1),
    ]
    with dbmod.get_db() as conn:
        for i, fn, title, arch, tx, gid, trashed in rows:
            conn.execute(
                """INSERT INTO documents (id, filename, sha256, title, source_archive,
                   transcription, group_id, is_trashed)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (i, fn, f"sha{i}", title, arch, tx, gid, trashed))
        # A page that now belongs to a multi-page group
        conn.execute("""INSERT INTO document_groups (id, title, source_archive)
                        VALUES (50,'Multi-page letter','Nelson-Atkins')""")
        conn.execute(
            """INSERT INTO documents (id, filename, sha256, title, source_archive,
               transcription, group_id, page_number, is_trashed)
               VALUES (6,'IMG_7777.JPG','sha6','Page one','Nelson-Atkins',
                       'First page text.',50,1,0)""")

    fb = FakeBackend()
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "fake")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "fake-1")
    monkeypatch.setattr(indexer, "get_embedding_backend", lambda p=None, m=None: fb)
    monkeypatch.setattr(retrieval_mod, "get_embedding_backend", lambda p=None, m=None: fb)
    with dbmod.get_db() as conn:
        indexer.reindex_all(conn)
    return {"db": db_path, "monkeypatch": monkeypatch}


# ── Query recognition ────────────────────────────────────────────────────────

def test_recognises_filename_shaped_queries_only():
    for q in ("IMG_4683.JPG", "IMG_4683", "4683", "img_4683.jpg", "SCAN-221"):
        assert looks_like_filename_query(q), q
    for q in ("temple ceiling", "letter", "", "   ", "a", "IMG_",
              "who bought the ceiling in 1932"):
        assert not looks_like_filename_query(q), q


# ── Match precedence ─────────────────────────────────────────────────────────

def test_all_three_query_forms_find_the_document(test_db):
    with dbmod.get_db() as conn:
        for q in ("IMG_4683.JPG", "IMG_4683", "4683"):
            hits = find_by_filename(conn, q)
            assert hits, q
            assert hits[0]["filename"] == "IMG_4683.JPG", q
            assert hits[0]["id"] == 1


def test_exact_before_stem_number_prefix_substring(test_db):
    with dbmod.get_db() as conn:
        exact = find_by_filename(conn, "IMG_4683.JPG")
        assert [h["filename_match"] for h in exact][0] == "exact"

        stem = find_by_filename(conn, "IMG_4683")
        assert stem[0]["filename_match"] == "stem"
        assert stem[0]["filename"] == "IMG_4683.JPG"
        # IMG_46830.JPG is only a prefix match and must rank below
        assert stem[1]["filename"] == "IMG_46830.JPG"
        assert stem[1]["filename_match"] == "prefix"

        digits = find_by_filename(conn, "4683")
        # a complete number match beats a mere substring (IMG_46830)
        assert digits[0]["filename"] == "IMG_4683.JPG"
        assert digits[0]["filename_match"] == "number"
        assert [h["filename"] for h in digits] == [
            "IMG_4683.JPG", "SCAN_4683_back.jpg", "IMG_46830.JPG"]
        assert digits[-1]["filename_match"] == "substring"


def test_case_insensitive_and_deterministic(test_db):
    with dbmod.get_db() as conn:
        a = find_by_filename(conn, "img_4683.jpg")
        b = find_by_filename(conn, "IMG_4683.JPG")
        c = find_by_filename(conn, "IMG_4683.JPG")
    assert [h["id"] for h in a] == [h["id"] for h in b] == [h["id"] for h in c]
    assert a[0]["filename_match"] == "exact"


def test_trashed_excluded_grouped_page_included(test_db):
    with dbmod.get_db() as conn:
        assert find_by_filename(conn, "IMG_9001.JPG") == []          # trashed
        assert find_by_filename(conn, "IMG_9001.JPG", include_trashed=True)
        page = find_by_filename(conn, "IMG_7777.JPG")
    assert page[0]["id"] == 6
    assert page[0]["group_id"] == 50        # UI can point at the group
    assert page[0]["page_number"] == 1


def test_result_exposes_filename_and_metadata(test_db):
    with dbmod.get_db() as conn:
        hit = find_by_filename(conn, "IMG_4683.JPG")[0]
    for field in ("id", "filename", "title", "source_archive", "group_id",
                  "filename_match", "record_type"):
        assert field in hit
    assert hit["filename"] == "IMG_4683.JPG"
    assert hit["source_archive"] == "Nelson-Atkins"


def test_like_wildcards_are_escaped(test_db):
    with dbmod.get_db() as conn:
        assert find_by_filename(conn, "IMG_%") == []
        assert find_by_filename(conn, "%") == []


# ── Separation from retrieval ────────────────────────────────────────────────

def test_search_attaches_block_without_changing_results(test_db):
    """The ranked results are byte-identical with and without a filename hit."""
    baseline = search_documents("ceiling shipped quietly", mode="hybrid")
    lookup = search_documents("IMG_4683.JPG", mode="hybrid")

    assert baseline["filename_matches"]["applied"] is False
    assert baseline["filename_matches"]["matches"] == []

    assert lookup["filename_matches"]["applied"] is True
    assert lookup["filename_matches"]["matches"][0]["filename"] == "IMG_4683.JPG"
    # the filename hit is NOT merged into the ranked results…
    assert all("filename_match" not in r for r in lookup["results"])
    # …and cannot change ranking: a filename query scores no retrieval hits
    # here, while the prose query's ranking is untouched by the utility.
    ranked = [(r["id"], r.get("score")) for r in baseline["results"]]
    again = [(r["id"], r.get("score"))
             for r in search_documents("ceiling shipped quietly", mode="hybrid")["results"]]
    assert ranked == again


def test_block_present_in_every_mode(test_db):
    for mode in ("keyword", "semantic", "hybrid"):
        out = search_documents("IMG_4683", mode=mode)
        block = out["filename_matches"]
        assert block["applied"] is True, mode
        assert block["matches"][0]["id"] == 1, mode


def test_lookup_works_with_embedding_provider_down(test_db):
    """It is metadata SQL: no embeddings, no index, no provider needed."""
    retrieval_mod_backend = FailingBackend()
    test_db["monkeypatch"].setattr(
        retrieval_mod, "get_embedding_backend", lambda p=None, m=None: retrieval_mod_backend)
    out = search_documents("IMG_4683.JPG", mode="semantic")
    assert out["semantic_available"] is False          # honest degradation
    assert out["filename_matches"]["matches"][0]["id"] == 1


def test_lookup_reads_no_retrieval_tables(test_db):
    """Dropping the chunk index entirely must not affect filename lookup."""
    with dbmod.get_db() as conn:
        conn.execute("DELETE FROM retrieval_embeddings")
        conn.execute("DELETE FROM retrieval_chunks")
        hits = find_by_filename(conn, "IMG_4683.JPG")
        block = filename_match_block(conn, "4683")
    assert hits[0]["id"] == 1
    assert block["applied"] is True and block["total"] == 3
