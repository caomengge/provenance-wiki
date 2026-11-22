"""
Tests for API-key storage, the Settings API, and packaged-mode paths
(run from backend/):

    python -m pytest tests/test_settings_keys.py -v

A fake in-memory credential store stands in for the macOS Keychain / Windows
Credential Manager. Keys must go only there, take effect without a restart,
never appear in API responses, files, or logs, and saving must fail (not fall
back to plaintext) when no secure store exists.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import keyring
import keyring.backend
import pytest

BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND))

import config
from modules import embeddings, secrets_store

UI = {"X-Provenance-Wiki": "1"}
SECRET_A = "sk-ant-test-SECRETVALUE-aaaa"
SECRET_V = "pa-test-SECRETVALUE-vvvv"


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


@pytest.fixture()
def fake_store(monkeypatch):
    kr = MemoryKeyring()
    old = keyring.get_keyring()
    keyring.set_keyring(kr)
    monkeypatch.setenv("PW_DISABLE_KEYRING", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "VOYAGE_API_KEY", "")
    yield kr
    keyring.set_keyring(old)
    secrets_store._sources.clear()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "provenance.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    from app import create_app
    return create_app().test_client()


def _files_contain(root: Path, needle: str) -> bool:
    for p in root.rglob("*"):
        if p.is_file() and needle.encode() in p.read_bytes():
            return True
    return False


def test_save_goes_to_secure_store_and_takes_effect(fake_store, client, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    embeddings._backend_cache[("voyage", "voyage-4")] = object()   # a stale cached client
    r = client.put("/api/settings/keys/anthropic", json={"key": SECRET_A}, headers=UI)
    assert r.status_code == 200
    r = client.put("/api/settings/keys/voyage", json={"key": SECRET_V}, headers=UI)
    assert r.status_code == 200
    assert fake_store.store[("ProvenanceWiki", "ANTHROPIC_API_KEY")] == SECRET_A
    assert fake_store.store[("ProvenanceWiki", "VOYAGE_API_KEY")] == SECRET_V
    # live without restart
    assert config.ANTHROPIC_API_KEY == SECRET_A and config.VOYAGE_API_KEY == SECRET_V
    assert embeddings._backend_cache == {}
    health = client.get("/api/health").get_json()
    assert health["anthropic_key_set"] and health["voyage_key_set"]
    # never echoed, written to disk, or logged
    for path in ("/api/settings", "/api/health"):
        assert "SECRETVALUE" not in client.get(path).get_data(as_text=True)
    assert "SECRETVALUE" not in r.get_data(as_text=True)
    assert not _files_contain(tmp_path, "SECRETVALUE")
    assert "SECRETVALUE" not in caplog.text


def test_status_reports_source(fake_store, client):
    client.put("/api/settings/keys/voyage", json={"key": SECRET_V}, headers=UI)
    keys = client.get("/api/settings").get_json()["keys"]
    assert keys["voyage"] == {"set": True, "source": "keychain"}
    assert keys["anthropic"]["set"] is False


def test_no_plaintext_fallback_when_store_missing(client, tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DISABLE_KEYRING", "1")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    r = client.put("/api/settings/keys/voyage", json={"key": SECRET_V}, headers=UI)
    assert r.status_code == 409
    assert "not saved" in r.get_json()["error"]
    assert "VOYAGE_API_KEY" not in os.environ
    assert not _files_contain(tmp_path, "SECRETVALUE")


@pytest.mark.parametrize("bad", ["", "YOUR_KEY_HERE", "two words", "x" * 600])
def test_rejects_non_keys(fake_store, client, bad):
    r = client.put("/api/settings/keys/anthropic", json={"key": bad}, headers=UI)
    assert r.status_code == 400
    assert not fake_store.store


def test_remove_key(fake_store, client):
    client.put("/api/settings/keys/anthropic", json={"key": SECRET_A}, headers=UI)
    r = client.delete("/api/settings/keys/anthropic", headers=UI)
    assert r.status_code == 200
    assert not fake_store.store
    assert config.ANTHROPIC_API_KEY == ""
    assert client.get("/api/health").get_json()["anthropic_key_set"] is False


def test_saving_requires_the_ui_header(fake_store, client):
    r = client.put("/api/settings/keys/anthropic", json={"key": SECRET_A})
    assert r.status_code == 403
    assert not fake_store.store


def test_stored_key_overrides_environment(fake_store, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-dotenv")
    fake_store.set_password("ProvenanceWiki", "ANTHROPIC_API_KEY", "sk-ant-from-keychain")
    secrets_store.load_into_environ()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-keychain"
    assert secrets_store.status()["anthropic"]["source"] == "keychain"


def test_connection_check_classifies_without_echoing(fake_store, client, monkeypatch):
    client.put("/api/settings/keys/anthropic", json={"key": SECRET_A}, headers=UI)

    class AuthenticationError(Exception):
        status_code = 401

    class FakeModels:
        def list(self, **kw):
            raise AuthenticationError(f"invalid x-api-key {SECRET_A}")

    class FakeClient:
        def __init__(self, **kw):
            self.models = FakeModels()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    r = client.post("/api/settings/keys/anthropic/test", headers=UI)
    body = r.get_json()
    assert body["status"] == "invalid"
    assert "SECRETVALUE" not in r.get_data(as_text=True)


def test_connection_check_with_no_key(fake_store, client):
    body = client.post("/api/settings/keys/voyage/test", headers=UI).get_json()
    assert body["status"] == "missing"


def test_quit_only_in_desktop_app(client):
    assert client.post("/api/system/quit", headers=UI).status_code == 409
    called = []
    client.application.config["PW_QUIT"] = lambda: called.append(True)
    assert client.post("/api/system/quit", headers=UI).status_code == 200
    assert called == [True]


PROBE = "import json, config; print(json.dumps({k: str(getattr(config, k)) for k in " \
        "('PACKAGED','DATA_DIR','PHOTOS_DIR','DB_PATH','DIST_DIR','RESOURCE_DIR')}))"


def _probe(env):
    base = {k: v for k, v in os.environ.items() if not k.startswith("PW_")}
    base["PW_DISABLE_KEYRING"] = "1"
    r = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True,
                       env={**base, **env}, cwd=str(BACKEND))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_packaged_mode_keeps_user_data_out_of_the_program(tmp_path):
    data = tmp_path / "Prov Wiki data — ünïcode"
    out = _probe({"PW_PACKAGED": "1", "PW_DATA_DIR": str(data)})
    assert out["PACKAGED"] == "True"
    for k in ("DATA_DIR", "PHOTOS_DIR", "DB_PATH"):
        assert out[k].startswith(str(data)), k
    assert out["DIST_DIR"].startswith(out["RESOURCE_DIR"])
    assert (data / "photos").is_dir()


def test_source_mode_unchanged():
    out = _probe({})
    root = str(BACKEND.parent)
    assert out["PACKAGED"] == "False"
    assert out["DATA_DIR"] == str(Path(root) / "data")
    assert out["PHOTOS_DIR"] == str(Path(root) / "photos")
    assert out["DIST_DIR"] == str(Path(root) / "dist")
