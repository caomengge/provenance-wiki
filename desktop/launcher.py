"""
Provenance Wiki desktop launcher (macOS and Windows).

Starts the archive server on this computer only, opens the app in the default
browser once the server is ready, and offers Open / folders / Quit from a
menu-bar (macOS) or system-tray (Windows) icon.

- Server: Waitress (production WSGI server), bound to 127.0.0.1 only.
- Port: 5100 if free, otherwise any free port; the correct URL is opened.
- Single instance: a second launch opens the running app's page and exits.
- Quit: tray menu, the Quit button in Settings, or the OS (Dock / logout).
- Logs: per-user log folder, with API-key-shaped strings masked.
- Never starts the MCP server.

Environment switches (mainly for automated tests):
  PW_NO_BROWSER=1   don't open a browser
  PW_NO_TRAY=1      no menu-bar/tray icon; run until Quit or a signal
  PW_DATA_DIR=...   use another archive folder
"""

import json
import logging
import logging.handlers
import os
import re
import signal
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_ID = "provenance-wiki"
PREFERRED_PORT = 5100

FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    os.environ["PW_PACKAGED"] = "1"
    BACKEND_DIR = Path(sys._MEIPASS)
else:
    BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

log = logging.getLogger("launcher")


# ── Logging with secret redaction ─────────────────────────────────────────────

_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"),          # Anthropic keys
    re.compile(r"\bpa-[A-Za-z0-9_\-]{16,}"),            # Voyage keys
    re.compile(r"(?i)(api[_-]?key[\"'=:\s]+)[^\s\"',]{8,}"),
]


def redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) if m.groups() else "") + "[REDACTED]", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "provenance-wiki.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fmt = RedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    if not FROZEN and sys.stderr:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)
    root.setLevel(logging.INFO)
    # Native crashes (e.g. in PDF libraries) bypass Python logging; record
    # their Python stack in crash.log so they can be diagnosed.
    import faulthandler
    setup_logging.crash_file = open(log_dir / "crash.log", "a", encoding="utf-8")
    faulthandler.enable(file=setup_logging.crash_file, all_threads=True)


# ── Instance discovery ────────────────────────────────────────────────────────

def probe(port: int, timeout: float = 1.0) -> bool:
    """True if a Provenance Wiki server answers on this port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            return json.loads(r.read().decode()).get("app") == APP_ID
    except Exception:
        return False


def read_instance(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def open_browser(url: str) -> None:
    if os.getenv("PW_NO_BROWSER") != "1":
        webbrowser.open(url)


# ── Tray / menu-bar icon ──────────────────────────────────────────────────────

def _icon_image():
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, size - 4, size - 4), radius=12, fill=(27, 38, 59, 255))
    d.rectangle((20, 16, 26, 48), fill=(201, 168, 76, 255))            # P stem
    d.ellipse((20, 14, 46, 36), outline=(201, 168, 76, 255), width=6)   # P bowl
    return img


def _install_mac_reopen_handler(url: str) -> None:
    """Double-clicking the app (or its Dock icon) while it is already running
    sends a 'reopen' Apple event instead of starting a second copy. Answer it
    by opening the app's page again, so a closed browser tab is easy to recover."""
    try:
        import objc
        from AppKit import NSAppleEventManager
        from Foundation import NSObject

        def fourcc(code: bytes) -> int:
            return int.from_bytes(code, "big")

        class ReopenHandler(NSObject):
            @objc.typedSelector(b"v@:@@")
            def handleReopen_withReplyEvent_(self, event, reply):
                log.info("Reopen requested; opening the app page")
                open_browser(url)

        handler = ReopenHandler.alloc().init()
        _install_mac_reopen_handler.handler = handler     # keep a reference alive
        NSAppleEventManager.sharedAppleEventManager() \
            .setEventHandler_andSelector_forEventClass_andEventID_(
                handler, b"handleReopen:withReplyEvent:", fourcc(b"aevt"), fourcc(b"rapp"))
        log.info("Reopen handler installed")
    except Exception:
        log.exception("Could not install the reopen handler")


def run_tray(url: str, quit_app, config) -> bool:
    """Blocks on the main thread until Quit. Returns False if no tray is possible."""
    try:
        import pystray
    except Exception:
        log.exception("Menu-bar/tray icon unavailable")
        return False

    def open_folder(path):
        def _open(icon=None, item=None):
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", str(path)])
            elif sys.platform == "win32":
                os.startfile(str(path))  # noqa: S606
        return _open

    def on_quit(icon, item):
        quit_app()

    icon = pystray.Icon(
        "ProvenanceWiki", _icon_image(), "Provenance Wiki",
        menu=pystray.Menu(
            pystray.MenuItem("Open Provenance Wiki", lambda i, it: webbrowser.open(url), default=True),
            pystray.MenuItem("Open Photos Folder", open_folder(config.PHOTOS_DIR)),
            pystray.MenuItem("Open Archive Folder", open_folder(config.DATA_DIR)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Provenance Wiki", on_quit),
        ))
    run_tray.icon = icon

    def setup(icon):
        icon.visible = True
        if sys.platform == "darwin":
            _install_mac_reopen_handler(url)

    try:
        icon.run(setup=setup)
    except Exception:
        log.exception("Menu-bar/tray icon failed")
        return False
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    import config
    setup_logging(config.LOG_DIR)
    log.info("Provenance Wiki %s starting (packaged=%s, data=%s)",
             config.APP_VERSION, config.PACKAGED, config.DATA_DIR)

    instance_file = config.DATA_DIR / "instance.json"
    existing = read_instance(instance_file)
    for port in filter(None, [existing and existing.get("port"), PREFERRED_PORT]):
        if probe(int(port)):
            log.info("Already running on port %s; opening it", port)
            open_browser(f"http://127.0.0.1:{port}/")
            return 0

    from waitress import create_server
    from app import app as flask_app      # runs create_app(): DB init, routes

    try:
        server = create_server(flask_app, host="127.0.0.1", port=PREFERRED_PORT, threads=8)
    except OSError:
        log.info("Port %s is busy; using another free port", PREFERRED_PORT)
        server = create_server(flask_app, host="127.0.0.1", port=0, threads=8)
    port = server.effective_port
    url = f"http://127.0.0.1:{port}/"

    stopping = threading.Event()

    def shutdown():
        if stopping.is_set():
            return
        stopping.set()
        log.info("Stopping")
        try:
            if read_instance(instance_file) and read_instance(instance_file).get("pid") == os.getpid():
                instance_file.unlink()
        except OSError:
            pass
        try:
            server.close()
        except Exception:
            pass
        icon = getattr(run_tray, "icon", None)
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        logging.shutdown()
        # Guarantee no background server is left running.
        threading.Timer(2.0, lambda: os._exit(0)).start()

    # The Settings "Quit" button: reply to the request first, then stop.
    flask_app.config["PW_QUIT"] = lambda: threading.Timer(0.3, shutdown).start()

    threading.Thread(target=server.run, name="waitress", daemon=True).start()

    deadline = time.time() + 30
    while not probe(port, timeout=0.5):
        if time.time() > deadline:
            log.error("Server did not become ready on port %s", port)
            shutdown()
            return 1
        time.sleep(0.2)

    instance_file.write_text(json.dumps({"port": port, "pid": os.getpid()}))
    log.info("Ready at %s", url)
    open_browser(url)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: shutdown())
        except (ValueError, OSError):
            pass

    if os.getenv("PW_NO_TRAY") == "1" or not run_tray(url, shutdown, config):
        while not stopping.wait(0.5):
            pass

    shutdown()
    time.sleep(2.5)   # shutdown's timer exits the process
    return 0


if __name__ == "__main__":
    sys.exit(main())
