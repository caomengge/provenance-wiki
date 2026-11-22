"""
Automated smoke test for the desktop launcher / packaged app.

Usage:
    python desktop/smoke_test.py --data-dir /tmp/pw-smoke -- <command that starts the app>

Examples:
    # source checkout
    python desktop/smoke_test.py --data-dir /tmp/pw -- python desktop/launcher.py
    # built macOS app
    python desktop/smoke_test.py --data-dir /tmp/pw --expect-testing-build \
        -- "dist/Provenance Wiki (Testing).app/Contents/MacOS/ProvenanceWiki"

Checks: server comes up on loopback only, reports packaged/testing status, keeps
the archive in the data folder, refuses foreign hosts/origins, serves the app
for client-side routes, exports a PDF containing the document's text, does not
start a second server on a second launch, and stops completely on Quit.
This is not a substitute for a manual test on a clean machine.
"""

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UI = {"X-Provenance-Wiki": "1", "Content-Type": "application/json"}
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def http(method, url, body=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def wait_for(fn, timeout):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.3)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--expect-testing-build", action="store_true")
    ap.add_argument("--keep-tray", action="store_true", help="don't set PW_NO_TRAY")
    ap.add_argument("--reopen-app", help="macOS: path of the .app; run `open` on it while running "
                                         "and expect the app to handle the reopen event")
    ap.add_argument("--test-keychain", action="store_true",
                    help="use the real OS credential store: save, check, then remove a dummy key")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    cmd = [c for c in args.cmd if c != "--"]
    data = Path(args.data_dir)
    data.mkdir(parents=True, exist_ok=True)

    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY")}
    env.update(PW_DATA_DIR=str(data), PW_NO_BROWSER="1", PW_PACKAGED="1",
               PW_DISABLE_KEYRING="0" if args.test_keychain else "1")
    if not args.keep_tray:
        env["PW_NO_TRAY"] = "1"

    t0 = time.time()
    if args.reopen_app:
        # Launch the way Finder does (through LaunchServices), so macOS knows the
        # app is running and a second open sends it a "reopen" event.
        envargs = [a for k in ("PW_DATA_DIR", "PW_NO_BROWSER", "PW_PACKAGED",
                               "PW_DISABLE_KEYRING", "PW_NO_TRAY") if k in env
                   for a in ("--env", f"{k}={env[k]}")]
        subprocess.run(["open", *envargs, args.reopen_app], check=False)
        proc = None
    else:
        proc = subprocess.Popen(cmd, env=env)
    inst = wait_for(lambda: (data / "instance.json").exists()
                    and json.loads((data / "instance.json").read_text()), 90)
    if not check("app starts and records its port", inst, f"{time.time() - t0:.1f}s"):
        if proc:
            proc.kill()
        return finish()
    port = inst["port"]
    pid = inst["pid"]
    base = f"http://127.0.0.1:{port}"

    s, _, b = http("GET", base + "/api/health")
    h = json.loads(b)
    check("health reports Provenance Wiki", s == 200 and h.get("app") == "provenance-wiki", h.get("version", ""))
    check("runs in packaged mode", h.get("packaged") is True)
    if args.expect_testing_build:
        check("marked as testing build", h.get("testing_build") is True)
    check("no keys configured on a fresh profile",
          h.get("anthropic_key_set") is False and h.get("voyage_key_set") is False)

    s, _, b = http("GET", base + "/api/settings")
    st = json.loads(b)
    check("archive lives in the data folder",
          st["folders"]["data"] == str(data) and (data / "provenance.db").exists(), st["folders"]["data"])

    # Loopback only: the port must not accept connections on another interface.
    other_ip = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("192.0.2.1", 80))      # no traffic is sent
            other_ip = sk.getsockname()[0]
    except OSError:
        pass
    if other_ip and not other_ip.startswith("127."):
        try:
            socket.create_connection((other_ip, port), timeout=2).close()
            reachable = True
        except OSError:
            reachable = False
        check("not reachable from the network", not reachable, other_ip)

    s, _, _ = http("GET", base + "/api/stats", headers={"Host": "attacker.example"})
    check("foreign Host refused", s == 400)
    s, _, _ = http("POST", base + "/api/tags", {"name": "x"},
                   {**UI, "Origin": "https://evil.example"})
    check("foreign website refused", s == 403)
    s, _, b = http("GET", base + "/entities")
    check("app page loads on a direct link", s == 200 and b'id="root"' in b)

    if args.test_keychain:
        dummy = "sk-ant-smoke-test-not-a-real-key-000"
        check("secure key storage available", st.get("secure_store_available") is True,
              st.get("secure_store_name", ""))
        s, _, b = http("PUT", base + "/api/settings/keys/anthropic", {"key": dummy},
                       {**UI, "Origin": base})
        keys = json.loads(b).get("keys", {}) if s == 200 else {}
        check("key saved to the credential store",
              keys.get("anthropic") == {"set": True, "source": "keychain"}, f"HTTP {s}")
        check("saved key not echoed", dummy.encode() not in b)
        s, _, b = http("DELETE", base + "/api/settings/keys/anthropic", headers={**UI, "Origin": base})
        check("key removed again", s == 200 and json.loads(b)["keys"]["anthropic"]["set"] is False)

    if args.reopen_app:
        r = subprocess.run(["open", args.reopen_app], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr.strip())
        log_dir = Path(st["folders"]["logs"])
        seen = wait_for(lambda: any("Reopen requested" in p.read_text(errors="ignore")
                                    for p in log_dir.glob("*.log")), 15)
        check("reopening the running app opens its page", seen)

    # PDF export with a seeded document (no API keys needed)
    con = sqlite3.connect(data / "provenance.db")
    con.execute("INSERT INTO documents (filename, sha256, title, transcription, description) "
                "VALUES ('smoke.jpg', 'smoke-sha', 'Smoke test letter 致敬', "
                "'Dear Mr Sickman, the ceiling from Zhihua Temple…', 'A test document')")
    con.commit()
    doc_id = con.execute("SELECT id FROM documents WHERE sha256='smoke-sha'").fetchone()[0]
    con.close()
    s, hdrs, pdf = http("POST", base + "/api/export/selection",
                        f"doc_ids=[{doc_id}]".encode(),
                        {"Origin": base, "Content-Type": "application/x-www-form-urlencoded"},
                        timeout=180)
    is_pdf = s == 200 and pdf[:4] == b"%PDF"
    text_ok = None
    if is_pdf:
        try:
            from pypdf import PdfReader
            import io
            text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
            text_ok = "Smoke test letter" in text
            # Informational: CJK glyphs depend on the fonts the PDF engine finds.
            print(f"[INFO] PDF renders Chinese characters: {'致敬' in text}", flush=True)
        except ImportError:
            text_ok = None
    check("PDF export works", is_pdf, f"HTTP {s}, {len(pdf)} bytes")
    if text_ok is not None:
        check("PDF contains the document text", text_ok)

    # Second launch must not start another server
    p2 = subprocess.Popen(cmd, env=env) if not args.reopen_app else \
        subprocess.Popen(["open", args.reopen_app])
    try:
        p2.wait(timeout=60)
        second_exited = True
    except subprocess.TimeoutExpired:
        p2.kill()
        second_exited = False
    inst2 = json.loads((data / "instance.json").read_text())
    check("second launch reuses the running app", second_exited and inst2["port"] == port)

    s, _, _ = http("POST", base + "/api/system/quit", headers={**UI, "Origin": base})
    check("Quit accepted", s == 200)
    def alive(p):
        if proc is not None and p == proc.pid:
            return proc.poll() is None        # our child: poll() also reaps it
        try:
            os.kill(p, 0)
            return True
        except OSError:
            return False

    exited = bool(wait_for(lambda: not alive(pid), 20))
    if not exited:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    if proc:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    check("process exits after Quit", exited)
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        stopped = False
    except OSError:
        stopped = True
    check("server stopped after Quit", stopped)
    check("instance file removed", not (data / "instance.json").exists())

    logs = list(Path(st["folders"]["logs"]).glob("*.log"))
    logs = [p for p in logs if p.name != "crash.log" or p.stat().st_size]
    check("log file written", bool(logs), ", ".join(str(p) for p in logs))
    return finish()


def finish():
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
