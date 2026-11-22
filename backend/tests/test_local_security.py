"""
Tests for local-only request protection (run from backend/):

    python -m pytest tests/test_local_security.py -v

Reproduces the cross-site attack (a foreign website sending DELETE to the
local server) and DNS rebinding (a foreign Host header), and checks that the
app's own UI, the Vite dev server and the PDF export form still work.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND))

import config
from modules import db as dbmod

UI = {"X-Provenance-Wiki": "1"}
OWN = "http://localhost"          # Flask test client's default Host is "localhost"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "provenance.db")
    monkeypatch.setattr(config, "PHOTOS_DIR", tmp_path / "photos")
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "letter.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    from app import create_app
    app = create_app()
    with dbmod.get_db() as conn:
        conn.execute("INSERT INTO documents (filename, sha256, title) "
                     "VALUES ('letter.jpg', 'abc', 'Dealer letter')")
    return app.test_client(), tmp_path


def _doc_count():
    with dbmod.get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


def test_foreign_website_cannot_delete_a_document(client):
    c, tmp = client
    r = c.delete("/api/documents/1", headers={"Origin": "https://evil.example", **UI})
    assert r.status_code == 403
    assert _doc_count() == 1
    assert (tmp / "photos" / "letter.jpg").exists()


def test_foreign_preflight_gets_no_cors_approval(client):
    c, _ = client
    r = c.open("/api/documents/1", method="OPTIONS",
               headers={"Origin": "https://evil.example",
                        "Access-Control-Request-Method": "DELETE"})
    assert "Access-Control-Allow-Origin" not in r.headers
    r = c.get("/api/stats", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_null_origin_is_refused(client):
    c, _ = client
    r = c.post("/api/tags", json={"name": "x"}, headers={"Origin": "null", **UI})
    assert r.status_code == 403


def test_write_without_ui_header_is_refused(client):
    c, _ = client
    r = c.delete("/api/documents/1", headers={"Origin": OWN})
    assert r.status_code == 403
    assert _doc_count() == 1


def test_own_ui_can_write(client):
    c, _ = client
    r = c.post("/api/tags", json={"name": "ceiling"}, headers={"Origin": OWN, **UI})
    assert r.status_code in (200, 201), r.get_data(as_text=True)


def test_vite_dev_server_origin_can_write(client):
    c, _ = client
    r = c.post("/api/tags", json={"name": "dev"},
               headers={"Origin": "http://localhost:3000", **UI})
    assert r.status_code in (200, 201)


def test_dns_rebinding_host_is_refused(client):
    c, _ = client
    r = c.get("/api/stats", headers={"Host": "attacker.example:5100"})
    assert r.status_code == 400
    r = c.get("/api/stats", headers={"Host": "127.0.0.1:5100"})
    assert r.status_code == 200
    r = c.get("/api/stats", headers={"Host": "[::1]:5100"})
    assert r.status_code == 200


def test_export_form_works_without_header_but_not_cross_site(client):
    c, _ = client
    foreign = c.post("/api/export/selection", data={"doc_ids": "[1]"},
                     headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    own = c.post("/api/export/selection", data={"doc_ids": "[1]"},
                 headers={"Origin": OWN})
    # Past the guard: either a PDF or an export error, never the guard's 403
    assert own.status_code != 403


def test_reads_still_work(client):
    c, _ = client
    assert c.get("/api/documents/1").status_code == 200
    assert c.get("/api/documents/1/image").status_code == 200


def test_default_bind_is_loopback():
    env = {k: v for k, v in os.environ.items() if k != "FLASK_HOST"}
    r = subprocess.run([sys.executable, "-c", "import config; print(config.FLASK_HOST)"],
                       capture_output=True, text=True, env=env, cwd=str(BACKEND))
    assert r.stdout.strip() == "127.0.0.1"


def test_client_side_routes_load_on_reload(client):
    """A reload or bookmark of /entities etc. must serve the app, not a JSON 404."""
    c, _ = client
    if not (Path(config.DIST_DIR) / "index.html").exists():
        pytest.skip("frontend not built")
    for path in ("/gallery", "/entities", "/documents/1", "/search?q=x"):
        r = c.get(path)
        assert r.status_code == 200 and b"<div id=\"root\"" in r.data, path
    assert c.get("/api/does-not-exist").status_code == 404
    assert c.get("/assets/missing.js").status_code == 404


def test_export_form_submission_returns_a_pdf(client):
    """Gallery/Search submit doc_ids as a form field holding a JSON list.
    The route used to read JSON only, so this returned 'doc_ids is required'."""
    c, _ = client
    r = c.post("/api/export/selection", data={"doc_ids": "[1]"}, headers={"Origin": OWN})
    if r.status_code == 500 and b"WeasyPrint" in r.data:
        pytest.skip("WeasyPrint native libraries not installed")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    assert r.data[:4] == b"%PDF"
    assert c.post("/api/export/selection", data={"doc_ids": "nonsense"},
                  headers={"Origin": OWN}).status_code == 400
    # the JSON form still works
    r = c.post("/api/export/selection", json={"doc_ids": [1]},
               headers={"Origin": OWN, "X-Provenance-Wiki": "1"})
    assert r.status_code == 200
