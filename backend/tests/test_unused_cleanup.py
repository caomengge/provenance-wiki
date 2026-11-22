"""
Tests for user-initiated cleanup of unused entities and tags (run from backend/):

    python -m pytest tests/test_unused_cleanup.py -v

Unused entities and tags must survive startup, permanent deletion of their
last document or group, and unlinking. They are removed only through the
cleanup endpoint, and only the ids the researcher confirmed that are still
unused at that moment.
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
    monkeypatch.setattr(config, "PHOTOS_DIR", tmp_path / "photos")
    (tmp_path / "photos").mkdir()
    from app import create_app
    app = create_app()
    with dbmod.get_db() as conn:
        for i, trashed in ((1, 0), (2, 0), (3, 1)):
            (tmp_path / "photos" / f"p{i}.jpg").write_bytes(b"\xff\xd8\xff\xd9")
            conn.execute("INSERT INTO documents (id, filename, sha256, title, is_trashed) "
                         "VALUES (?, ?, ?, ?, ?)", (i, f"p{i}.jpg", f"s{i}", f"Doc {i}", trashed))
        conn.execute("INSERT INTO document_groups (id, title) VALUES (1, 'Group')")
        ents = [(1, "Laurence Sickman", "person"), (2, "Nelson-Atkins", "institution"),
                (3, "Peking Dealer", "person"), (4, "Group Only", "person")]
        for eid, name, typ in ents:
            conn.execute("INSERT INTO entities (id, name, normalized_name, type) VALUES (?,?,?,?)",
                         (eid, name, name.lower(), typ))
        conn.execute("INSERT INTO document_entities (document_id, entity_id) VALUES (1, 1)")
        conn.execute("INSERT INTO document_entities (document_id, entity_id) VALUES (2, 2)")
        conn.execute("INSERT INTO document_entities (document_id, entity_id) VALUES (3, 3)")  # trashed doc
        conn.execute("INSERT INTO group_entities (group_id, entity_id) VALUES (1, 4)")
        conn.execute("INSERT INTO entity_aliases (entity_id, name, normalized_name, source) "
                     "VALUES (1, 'L. Sickman', 'l. sickman', 'manual')")
        conn.execute("INSERT INTO entity_relationships (source_entity_id, target_entity_id, verb) "
                     "VALUES (1, 2, 'corresponded with')")
        conn.execute("INSERT INTO tags (id, name) VALUES (1, 'ceiling')")
        conn.execute("INSERT INTO document_tags (document_id, tag_id) VALUES (1, 1)")
    return app.test_client()


def _ids(table):
    with dbmod.get_db() as conn:
        return {r[0] for r in conn.execute(f"SELECT id FROM {table}")}


def test_deleting_last_document_keeps_entity_alias_and_tag(client):
    assert client.delete("/api/documents/1", headers=UI).status_code == 200
    assert 1 in _ids("entities")
    assert _ids("entity_aliases")
    assert _ids("entity_relationships")
    assert 1 in _ids("tags")


def test_unlinking_keeps_entity(client):
    assert client.delete("/api/documents/2/entities/2", headers=UI).status_code == 200
    assert 2 in _ids("entities")
    assert client.delete("/api/groups/1/entities/4", headers=UI).status_code == 200
    assert 4 in _ids("entities")


def test_deleting_group_keeps_entity(client):
    assert client.delete("/api/groups/1", headers=UI).status_code == 200
    assert 4 in _ids("entities")


def test_startup_does_not_delete_unused(client):
    client.delete("/api/documents/1", headers=UI)
    dbmod.init_db()
    assert 1 in _ids("entities") and 1 in _ids("tags") and _ids("entity_aliases")


def test_preview_lists_only_unused(client):
    client.delete("/api/documents/1", headers=UI)
    body = client.get("/api/maintenance/unused").get_json()
    assert [e["id"] for e in body["entities"]] == [1]       # 3 is on a trashed doc: kept
    assert [t["id"] for t in body["tags"]] == [1]
    assert body["alias_count"] == 1 and body["relationship_count"] == 1


def test_cleanup_removes_confirmed_items_with_aliases(client):
    client.delete("/api/documents/1", headers=UI)
    r = client.post("/api/maintenance/cleanup-unused", json={"entity_ids": [1], "tag_ids": [1]},
                    headers=UI)
    assert r.get_json() == {"removed_entities": 1, "removed_tags": 1}
    assert 1 not in _ids("entities") and not _ids("entity_aliases")
    assert not _ids("entity_relationships")
    assert {2, 3, 4} <= _ids("entities")


def test_cleanup_refuses_items_that_are_in_use(client):
    # Ids that are linked (directly, via a trashed doc, or via a group) are never removed
    r = client.post("/api/maintenance/cleanup-unused",
                    json={"entity_ids": [1, 2, 3, 4], "tag_ids": [1]}, headers=UI)
    assert r.get_json() == {"removed_entities": 0, "removed_tags": 0}
    assert {1, 2, 3, 4} <= _ids("entities")


def test_cleanup_requires_the_ui_header(client):
    client.delete("/api/documents/1", headers=UI)
    r = client.post("/api/maintenance/cleanup-unused", json={"entity_ids": [1]})
    assert r.status_code == 403
    assert 1 in _ids("entities")


def test_cleanup_rejects_bad_ids(client):
    r = client.post("/api/maintenance/cleanup-unused", json={"entity_ids": ["x"]}, headers=UI)
    assert r.status_code == 400
