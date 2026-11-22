"""
Tests for choosing, opening and moving the archive (run from backend/):

    python -m pytest tests/test_archive_location.py -v

Spec: specs/archive-location.md. The desktop app decides where the archive is
when it starts, so each scenario runs the app in packaged mode in a separate
Python process, with throwaway config/default folders, and drives it through
the Flask test client exactly as the interface does.
"""

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).parent.parent
UI = {"X-Provenance-Wiki": "1"}

PRELUDE = textwrap.dedent('''
    import json, os, sys, time, hashlib, sqlite3
    from pathlib import Path
    sys.path.insert(0, {backend!r})
    import config
    from app import create_app
    c = create_app().test_client()
    UI = {{"X-Provenance-Wiki": "1"}}
    out = {{}}
    def get(p): r = c.get(p); return r.status_code, r.get_json()
    def post(p, body=None): r = c.post(p, json=body or {{}}, headers=UI); return r.status_code, r.get_json()
    def wait_move():
        for _ in range(400):
            s = c.get("/api/archive/move/status").get_json()
            if s.get("phase") in ("done", "failed", "cancelled"):
                return s
            time.sleep(0.05)
        return s
    def seed(archive):
        con = sqlite3.connect(Path(archive) / "provenance.db")
        for i in range(1, 4):
            con.execute("INSERT INTO documents (filename, sha256, title) VALUES (?,?,?)",
                        (f"letter {{i}} ü.jpg", f"s{{i}}", f"Dealer letter {{i}}"))
        con.execute("INSERT INTO entities (name, normalized_name, type) VALUES ('L. Sickman','l. sickman','person')")
        con.commit(); con.close()
        for i in range(1, 4):
            (Path(archive) / "photos" / f"letter {{i}} ü.jpg").write_bytes(os.urandom(50_000 + i))
        (Path(archive) / "thumbnails" / "s1.jpg").write_bytes(b"thumb")
''')


def run(tmp_path, body, extra_env=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PW_DATA_DIR", "ANTHROPIC_API_KEY", "VOYAGE_API_KEY")}
    env.update({
        "PW_PACKAGED": "1",
        "PW_DISABLE_KEYRING": "1",
        "PW_CONFIG_DIR": str(tmp_path / "config"),
        "PW_DEFAULT_ARCHIVE_DIR": str(tmp_path / "default"),
    })
    env.update(extra_env or {})
    script = PRELUDE.format(backend=str(BACKEND)) + textwrap.dedent(body) + "\nprint('@@' + json.dumps(out, default=str))\n"
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                       env=env, cwd=str(BACKEND), timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    line = [l for l in r.stdout.splitlines() if l.startswith("@@")][-1]
    return json.loads(line[2:])


def location_file(tmp_path):
    return json.loads((tmp_path / "config" / "location.json").read_text())


# ── First launch ──────────────────────────────────────────────────────────────

def test_first_launch_creates_nothing_until_a_location_is_chosen(tmp_path):
    out = run(tmp_path, '''
        out["health"] = get("/api/health")[1]["archive_state"]
        out["docs"] = get("/api/documents")
        out["stats"] = get("/api/stats")[0]
        out["archives"] = get("/api/archives")[0]
        out["loc"] = get("/api/archive/location")[1]
        out["settings_keys_ok"] = get("/api/settings")[0]
    ''')
    assert out["health"] == "setup"
    assert out["docs"][0] == 409 and out["docs"][1]["archive_state"] == "setup"
    assert out["stats"] == 409
    assert out["archives"] == 409       # /api/archives (source list) is not /api/archive/
    assert out["loc"]["state"] == "setup" and out["loc"]["path"] is None
    assert out["settings_keys_ok"] == 200
    assert not (tmp_path / "default").exists()
    assert not list((tmp_path / "config").glob("*.db"))
    assert not (tmp_path / "config" / "location.json").exists()


def test_setup_at_default_location(tmp_path):
    out = run(tmp_path, '''
        out["setup"] = post("/api/archive/setup", {"use_default": True})
        out["docs"] = get("/api/documents")[0]
        out["state"] = get("/api/health")[1]["archive_state"]
    ''')
    assert out["setup"][0] == 200, out
    assert out["docs"] == 200 and out["state"] == "ok"
    default = tmp_path / "default"
    assert (default / "provenance.db").exists() and (default / "photos").is_dir()
    assert (default / "logs").is_dir()
    marker = json.loads((default / "archive.json").read_text())
    assert location_file(tmp_path)["archive_id"] == marker["archive_id"]


def test_setup_in_a_chosen_folder_creates_named_subfolder(tmp_path):
    chosen = tmp_path / "D drive" / "Research"
    chosen.mkdir(parents=True)
    out = run(tmp_path, f'''
        out["setup"] = post("/api/archive/setup", {{"path": {str(chosen)!r}}})
        out["photos"] = str(config.PHOTOS_DIR)
    ''')
    assert out["setup"][0] == 200, out
    archive = chosen / "Provenance Wiki Archive"
    assert (archive / "provenance.db").exists()
    assert out["photos"] == str(archive / "photos")
    assert not (tmp_path / "default").exists()


def test_interface_flow_check_then_create_with_the_final_path(tmp_path):
    """The screens check the chosen folder, then send back the final path they
    displayed (a subfolder that doesn't exist yet). Same for moving."""
    chosen = tmp_path / "Big Drive"
    chosen.mkdir()
    second = tmp_path / "Second Drive"
    second.mkdir()
    out = run(tmp_path, f'''
        chk = post("/api/archive/check", {{"path": {str(chosen)!r}, "purpose": "setup"}})[1]
        out["final"] = chk["final_path"]
        out["setup"] = post("/api/archive/setup", {{"path": chk["final_path"]}})[0]
        chk2 = post("/api/archive/check", {{"path": {str(second)!r}, "purpose": "move"}})[1]
        out["move"] = post("/api/archive/move", {{"path": chk2["final_path"]}})[0]
        out["status"] = wait_move()["phase"]
        out["now"] = str(config.DATA_DIR)
    ''')
    assert out["final"] == str(chosen / "Provenance Wiki Archive")
    assert out["setup"] == 200 and out["move"] == 202 and out["status"] == "done"
    assert out["now"] == str(second / "Provenance Wiki Archive")


def test_existing_archive_from_an_earlier_build_is_adopted(tmp_path):
    default = tmp_path / "default"
    (default / "photos").mkdir(parents=True)
    sqlite3.connect(default / "provenance.db").close()
    out = run(tmp_path, '''
        out["state"] = get("/api/health")[1]["archive_state"]
    ''')
    assert out["state"] == "ok"
    assert (default / "archive.json").exists()
    assert location_file(tmp_path)["archive_path"] == str(default)


def test_existing_archive_can_be_opened_from_first_launch(tmp_path):
    other = tmp_path / "old computer" / "Provenance Wiki Archive"
    other.mkdir(parents=True)
    (other / "archive.json").write_text(json.dumps({"archive_id": "abc-123"}))
    sqlite3.connect(other / "provenance.db").close()
    out = run(tmp_path, f'''
        out["locate"] = post("/api/archive/locate", {{"path": {str(other.parent)!r}}})
        out["state"] = get("/api/health")[1]["archive_state"]
    ''')
    assert out["locate"][0] == 200 and out["state"] == "ok"
    assert location_file(tmp_path)["archive_id"] == "abc-123"


# ── Destination rules ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expect", [
    ("Dropbox", "synced by Dropbox"),
    ("OneDrive - Royal Museum", "synced by OneDrive"),
    ("Google Drive", "synced by Google Drive"),
])
def test_cloud_folders_are_refused(tmp_path, name, expect):
    target = tmp_path / name
    target.mkdir()
    out = run(tmp_path, f'''
        out["check"] = post("/api/archive/check", {{"path": {str(target)!r}, "purpose": "setup"}})[1]
        out["setup"] = post("/api/archive/setup", {{"path": {str(target)!r}}})
    ''')
    assert out["check"]["ok"] is False and expect in out["check"]["refusal"]
    assert out["setup"][0] == 400
    assert not (target / "Provenance Wiki Archive").exists()


def test_network_folder_is_refused(tmp_path):
    target = tmp_path / "share"
    target.mkdir()
    out = run(tmp_path, f'''
        from modules import archive_location as al
        al.is_network_path = lambda p: True
        out["check"] = post("/api/archive/check", {{"path": {str(target)!r}}})[1]
    ''')
    assert out["check"]["ok"] is False and "network folder" in out["check"]["refusal"]


def test_other_refusals(tmp_path):
    full = tmp_path / "full"
    (full / "Provenance Wiki Archive").mkdir(parents=True)
    (full / "Provenance Wiki Archive" / "notes.txt").write_text("x")
    out = run(tmp_path, f'''
        def chk(p, purpose="setup"):
            return post("/api/archive/check", {{"path": p, "purpose": purpose}})[1]
        out["missing"] = chk({str(tmp_path / "nope")!r})
        out["relative"] = chk("Research")
        out["nonempty"] = chk({str(full)!r})
        out["config"] = chk({str(tmp_path / "config")!r})
        out["empty"] = chk("")
    ''')
    assert "doesn't exist" in out["missing"]["refusal"]
    assert "full folder path" in out["relative"]["refusal"]
    assert "isn't empty" in out["nonempty"]["refusal"]
    assert "can't store" in out["config"]["refusal"]
    assert out["empty"]["refusal"]


def test_move_refused_without_space_or_into_itself(tmp_path):
    dest = tmp_path / "small drive"
    dest.mkdir()
    out = run(tmp_path, f'''
        post("/api/archive/setup", {{"use_default": True}})
        from modules import archive_location as al
        real_free = al.free_space
        al.free_space = lambda p: 1000
        out["space"] = post("/api/archive/check", {{"path": {str(dest)!r}, "purpose": "move"}})[1]
        out["move"] = post("/api/archive/move", {{"path": {str(dest)!r}}})
        al.free_space = real_free
        out["inside"] = post("/api/archive/check", {{"path": str(config.DATA_DIR / "photos"), "purpose": "move"}})[1]
    ''')
    assert "Not enough space" in out["space"]["refusal"]
    assert out["move"][0] == 400
    assert "current archive" in out["inside"]["refusal"]


# ── Moving ────────────────────────────────────────────────────────────────────

def test_move_copies_verifies_and_switches(tmp_path):
    dest = tmp_path / "Ext Drive ü" / ("long folder name " * 6).strip()
    dest.mkdir(parents=True)
    out = run(tmp_path, f'''
        post("/api/archive/setup", {{"use_default": True}})
        seed(config.DATA_DIR)
        before = {{p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (config.DATA_DIR / "photos").iterdir()}}
        out["start"] = post("/api/archive/move", {{"path": {str(dest)!r}}})
        out["during_docs"] = get("/api/documents")[0] if out["start"][0] == 202 else None
        out["status"] = wait_move()
        new = config.DATA_DIR
        out["new"] = str(new)
        out["photos_match"] = before == {{p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (new / "photos").iterdir()}}
        out["docs"] = len(get("/api/documents")[1]["documents"])
        r = c.patch("/api/documents/1", json={{"title": "Edited after move"}}, headers=UI)
        out["edit"] = r.status_code
        con = sqlite3.connect(new / "provenance.db")
        out["edited_in_new"] = con.execute("SELECT title FROM documents WHERE id=1").fetchone()[0]
        con.close()
        loc = get("/api/archive/location")[1]
        out["previous"] = loc["previous_copy"]
        out["logs_in_new"] = str(config.LOG_DIR).startswith(str(new))
    ''')
    assert out["start"][0] == 202, out
    assert out["status"]["phase"] == "done", out["status"]
    assert out["new"] == str(dest / "Provenance Wiki Archive")
    assert out["photos_match"] and out["docs"] == 3
    assert out["edit"] == 200 and out["edited_in_new"] == "Edited after move"
    assert out["previous"]["path"] == str(tmp_path / "default")
    assert out["logs_in_new"]
    loc = location_file(tmp_path)
    assert loc["archive_path"] == out["new"] and loc["previous_path"] == str(tmp_path / "default")
    # The old copy is untouched until the researcher deletes it
    assert (tmp_path / "default" / "provenance.db").exists()


def test_old_copy_deleted_only_on_confirmation(tmp_path):
    dest = tmp_path / "D"
    dest.mkdir()
    out = run(tmp_path, f'''
        post("/api/archive/setup", {{"use_default": True}})
        seed(config.DATA_DIR)
        post("/api/archive/move", {{"path": {str(dest)!r}}}); wait_move()
        out["unconfirmed"] = post("/api/archive/old-copy/delete")[0]
        out["confirmed"] = post("/api/archive/old-copy/delete", {{"confirm": True}})
    ''')
    assert out["unconfirmed"] == 409
    assert out["confirmed"][0] == 200 and out["confirmed"][1]["previous_copy"] is None
    assert not (tmp_path / "default").exists()
    assert (dest / "Provenance Wiki Archive" / "provenance.db").exists()
    assert location_file(tmp_path)["previous_path"] is None


def test_failed_copy_leaves_everything_as_it_was(tmp_path):
    dest = tmp_path / "D"
    dest.mkdir()
    out = run(tmp_path, f'''
        post("/api/archive/setup", {{"use_default": True}})
        seed(config.DATA_DIR)
        import shutil
        real = shutil.copy2
        calls = []
        def flaky(a, b, *k, **kw):
            calls.append(a)
            if len(calls) == 2:
                raise OSError("disk unplugged")
            return real(a, b, *k, **kw)
        shutil.copy2 = flaky
        post("/api/archive/move", {{"path": {str(dest)!r}}})
        out["status"] = wait_move()
        out["state"] = get("/api/health")[1]["archive_state"]
        out["data_dir"] = str(config.DATA_DIR)
        out["docs"] = get("/api/documents")[0]
    ''')
    assert out["status"]["phase"] == "failed" and "not changed" in out["status"]["error"]
    assert out["state"] == "ok" and out["docs"] == 200
    assert out["data_dir"] == str(tmp_path / "default")
    assert list(dest.iterdir()) == []                      # partial copy removed
    assert location_file(tmp_path)["archive_path"] == str(tmp_path / "default")


def test_cancel_leaves_everything_as_it_was(tmp_path):
    dest = tmp_path / "D"
    dest.mkdir()
    out = run(tmp_path, f'''
        post("/api/archive/setup", {{"use_default": True}})
        seed(config.DATA_DIR)
        import shutil
        from modules import archive_location as al
        real = shutil.copy2
        def slow(a, b, *k, **kw):
            al.cancel_move()
            return real(a, b, *k, **kw)
        shutil.copy2 = slow
        post("/api/archive/move", {{"path": {str(dest)!r}}})
        out["status"] = wait_move()
        out["data_dir"] = str(config.DATA_DIR)
    ''')
    assert out["status"]["phase"] == "cancelled"
    assert out["data_dir"] == str(tmp_path / "default")
    assert list(dest.iterdir()) == []


def test_move_refused_while_ingesting(tmp_path):
    dest = tmp_path / "D"
    dest.mkdir()
    out = run(tmp_path, f'''
        post("/api/archive/setup", {{"use_default": True}})
        from modules import ingestor
        ingestor._is_ingesting = True
        out["move"] = post("/api/archive/move", {{"path": {str(dest)!r}}})
    ''')
    assert out["move"][0] == 400 and "ingested" in out["move"][1]["error"]


# ── Archive not found ─────────────────────────────────────────────────────────

def test_missing_archive_is_never_replaced_silently(tmp_path):
    ext = tmp_path / "E"
    ext.mkdir()
    run(tmp_path, f'''
        post("/api/archive/setup", {{"path": {str(ext)!r}}})
        seed(config.DATA_DIR)
    ''')
    archive = ext / "Provenance Wiki Archive"
    unplugged = tmp_path / "unplugged"
    archive.rename(unplugged)                           # the drive is gone
    out = run(tmp_path, '''
        out["health"] = get("/api/health")[1]["archive_state"]
        out["loc"] = get("/api/archive/location")[1]
        out["docs"] = get("/api/documents")[0]
        out["new_without_confirm"] = post("/api/archive/setup", {"use_default": True})[0]
    ''')
    assert out["health"] == "missing"
    assert out["loc"]["expected_path"] == str(archive)
    assert out["docs"] == 409
    assert out["new_without_confirm"] == 409
    assert not (tmp_path / "default").exists()          # nothing created on C:

    # "Try again" after reconnecting the drive while the app is running
    out = run(tmp_path, f'''
        out["before"] = get("/api/health")[1]["archive_state"]
        import shutil
        shutil.move({str(unplugged)!r}, {str(archive)!r})
        out["retry"] = post("/api/archive/retry")[1]["state"]
        out["docs"] = get("/api/documents")[0]
    ''')
    assert out["before"] == "missing" and out["retry"] == "ok" and out["docs"] == 200
    archive.rename(unplugged)                           # unplug again for the next part

    # "Locate my archive" — e.g. the drive came back with another letter
    other = tmp_path / "F"
    other.mkdir()
    (unplugged).rename(other / "Provenance Wiki Archive")
    out = run(tmp_path, f'''
        out["locate"] = post("/api/archive/locate", {{"path": {str(other)!r}}})
        out["docs"] = len(get("/api/documents")[1]["documents"])
    ''')
    assert out["locate"][0] == 200 and out["docs"] == 3
    assert location_file(tmp_path)["archive_path"] == str(other / "Provenance Wiki Archive")


def test_locating_a_different_archive_needs_confirmation(tmp_path):
    run(tmp_path, '''
        post("/api/archive/setup", {"use_default": True})
    ''')
    (tmp_path / "default").rename(tmp_path / "gone")
    stranger = tmp_path / "X" / "Provenance Wiki Archive"
    stranger.mkdir(parents=True)
    (stranger / "archive.json").write_text(json.dumps({"archive_id": "someone-else"}))
    sqlite3.connect(stranger / "provenance.db").close()
    out = run(tmp_path, f'''
        out["plain"] = post("/api/archive/locate", {{"path": {str(stranger)!r}}})
        out["allowed"] = post("/api/archive/locate", {{"path": {str(stranger)!r}, "allow_different": True}})[0]
    ''')
    assert out["plain"][0] == 409 and out["plain"][1]["code"] == "different_archive"
    assert out["allowed"] == 200


def test_start_new_archive_from_missing_state_needs_confirmation(tmp_path):
    run(tmp_path, '''post("/api/archive/setup", {"use_default": True})''')
    (tmp_path / "default").rename(tmp_path / "gone")
    out = run(tmp_path, '''
        out["plain"] = post("/api/archive/setup", {"use_default": True})[0]
        out["confirmed"] = post("/api/archive/setup", {"use_default": True, "confirm_new": True})[0]
        out["state"] = get("/api/health")[1]["archive_state"]
    ''')
    assert out["plain"] == 409 and out["confirmed"] == 200 and out["state"] == "ok"


def test_developer_override_still_works(tmp_path):
    out = run(tmp_path, '''
        out["state"] = get("/api/health")[1]["archive_state"]
        out["data"] = str(config.DATA_DIR)
    ''', extra_env={"PW_DATA_DIR": str(tmp_path / "dev")})
    assert out["state"] == "ok" and out["data"] == str(tmp_path / "dev")
    assert not (tmp_path / "config" / "location.json").exists()
