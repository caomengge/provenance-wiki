"""
Tests for the "Add your photos" helpers (run from backend/):

    python -m pytest tests/test_ingest_pending.py -v
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND))

import config
from modules import db as dbmod

UI = {"X-Provenance-Wiki": "1"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "provenance.db")
    photos = tmp_path / "photos"
    photos.mkdir()
    monkeypatch.setattr(config, "PHOTOS_DIR", photos)
    from app import create_app
    app = create_app()
    for name in ("letter 1.jpg", "letter 2.JPG", "receipt.tiff", "IMG_0001.HEIC", "scan.pdf", ".DS_Store"):
        (photos / name).write_bytes(b"x")
    with dbmod.get_db() as conn:
        conn.execute("INSERT INTO documents (filename, sha256, title) VALUES ('letter 1.jpg','s','x')")
    return app.test_client()


def test_pending_counts_new_images_and_unreadable_files(client):
    body = client.get("/api/ingest/pending").get_json()
    assert body["images"] == 3                      # jpg, JPG, tiff
    assert body["new_images"] == 2                  # letter 1.jpg is already a document
    assert sorted(body["examples"]) == ["letter 2.JPG", "receipt.tiff"]
    assert {u["ext"]: u["count"] for u in body["unsupported"]} == {".heic": 1, ".pdf": 1}


def test_ingest_explains_missing_key_and_source(client, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    r = client.post("/api/ingest", json={"source_archive": "Test"}, headers=UI)
    assert r.status_code == 409 and "Anthropic API key" in r.get_json()["error"]
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-test")
    r = client.post("/api/ingest", json={"source_archive": "  "}, headers=UI)
    assert r.status_code == 400 and "Source" in r.get_json()["error"]
