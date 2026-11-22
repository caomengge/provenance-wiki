"""
archive_location.py – Where the researcher's archive lives, and moving it safely.

Spec: specs/archive-location.md (Provenance Wiki project).

The archive is ONE folder holding everything: provenance.db, photos/,
thumbnails/, backups/, logs/ and a marker file archive.json. A small, fixed
config folder on the system drive records where that folder is
(location.json) and holds a fallback log and the running-instance file.

States (config.ARCHIVE_STATE):
  ok       archive found and open
  setup    first launch: no archive yet; nothing is created until the
           researcher chooses a location
  missing  location.json points somewhere that isn't reachable (e.g. an
           external drive that is unplugged). A new empty archive is NEVER
           created silently.
  moving   a move is copying/verifying; archive features are paused

Only the packaged (desktop) app uses this. Source mode keeps repository
folders. PW_DATA_DIR still forces a folder (developers, automated tests).

This module must not import config at load time (config imports it).
"""

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ARCHIVE_DIRNAME = "Provenance Wiki Archive"
MARKER = "archive.json"
LOCATION_FILE = "location.json"
SPACE_MARGIN = 500 * 1024 * 1024          # bytes kept free beyond the copy
LOW_SPACE_WARNING = 2 * 1024 ** 3         # warn when a drive has < 2 GB free
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


class ArchiveError(Exception):
    """A refusal with a message meant for the researcher."""


# ── Fixed locations ───────────────────────────────────────────────────────────

def config_dir() -> Path:
    """Small fixed folder on the system drive (PW_CONFIG_DIR overrides, for tests)."""
    override = os.getenv("PW_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "ProvenanceWiki Config"
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ProvenanceWiki Config"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "provenance-wiki"


def default_archive_dir() -> Path:
    """Where the archive goes unless the researcher chooses otherwise.
    Unchanged from earlier builds, so existing testing archives are found."""
    override = os.getenv("PW_DEFAULT_ARCHIVE_DIR")
    if override:
        return Path(override).expanduser()
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "ProvenanceWiki"
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ProvenanceWiki"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "provenance-wiki"


def _lp(path) -> str:
    """Path string usable for very long Windows paths (> 260 characters)."""
    s = str(Path(path).absolute())
    if IS_WIN and not s.startswith("\\\\?\\"):
        s = "\\\\?\\UNC\\" + s[2:] if s.startswith("\\\\") else "\\\\?\\" + s
    return s


def _same_or_inside(child: Path, parent: Path) -> bool:
    try:
        child = Path(os.path.realpath(child))
        parent = Path(os.path.realpath(parent))
    except OSError:
        return False
    if IS_WIN or IS_MAC:                       # case-insensitive file systems
        c, p = str(child).lower(), str(parent).lower()
        return c == p or c.startswith(p.rstrip("\\/") + os.sep)
    return child == parent or parent in child.parents


# ── location.json and the marker ──────────────────────────────────────────────

def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_location() -> dict | None:
    try:
        data = json.loads((config_dir() / LOCATION_FILE).read_text(encoding="utf-8"))
        return data if data.get("archive_path") else None
    except (OSError, ValueError):
        return None


def write_location(archive_path: Path, archive_id: str, previous_path: str | None = None) -> None:
    _atomic_write_json(config_dir() / LOCATION_FILE, {
        "archive_path": str(archive_path),
        "archive_id": archive_id,
        "previous_path": previous_path,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    if IS_WIN:                                 # lets the uninstaller find the archive
        try:
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\ProvenanceWiki") as k:
                winreg.SetValueEx(k, "ArchivePath", 0, winreg.REG_SZ, str(archive_path))
                winreg.SetValueEx(k, "ConfigDir", 0, winreg.REG_SZ, str(config_dir()))
        except OSError:
            logger.warning("Could not record the archive path in the registry")


def read_marker(folder: Path) -> dict | None:
    try:
        data = json.loads((Path(folder) / MARKER).read_text(encoding="utf-8"))
        return data if data.get("archive_id") else None
    except (OSError, ValueError):
        return None


def write_marker(folder: Path, archive_id: str | None = None) -> dict:
    from version import __version__
    marker = {
        "app": "provenance-wiki",
        "archive_id": archive_id or str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "created_by_version": __version__,
    }
    _atomic_write_json(Path(folder) / MARKER, marker)
    return marker


# ── Resolving the archive at startup ──────────────────────────────────────────

def resolve() -> dict:
    """{'state': ok|setup|missing, 'path': Path|None, 'expected': str|None}"""
    override = os.getenv("PW_DATA_DIR")
    if override:
        return {"state": "ok", "path": Path(override).expanduser(), "expected": None}

    loc = read_location()
    if loc:
        path = Path(loc["archive_path"])
        marker = read_marker(path)
        if marker and loc.get("archive_id") and marker["archive_id"] != loc["archive_id"]:
            logger.warning("Archive at %s has a different marker; treating as missing", path)
            return {"state": "missing", "path": None, "expected": str(path)}
        if path.is_dir() and (marker or (path / "provenance.db").exists()):
            if not marker:
                write_marker(path, loc.get("archive_id"))
            return {"state": "ok", "path": path, "expected": None}
        return {"state": "missing", "path": None, "expected": str(path)}

    default = default_archive_dir()
    if (default / "provenance.db").exists():
        # An archive made by an earlier build, before locations were recorded.
        marker = read_marker(default) or write_marker(default)
        write_location(default, marker["archive_id"])
        return {"state": "ok", "path": default, "expected": None}
    return {"state": "setup", "path": None, "expected": None}


# ── Sizes and drives ──────────────────────────────────────────────────────────

def folder_size(folder: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(_lp(folder)):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _existing_ancestor(path: Path) -> Path:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def free_space(path: Path) -> int:
    try:
        return shutil.disk_usage(str(_existing_ancestor(path))).free
    except OSError:
        return 0


def drive_label(path: Path) -> str:
    p = Path(os.path.realpath(_existing_ancestor(path)))
    if IS_WIN:
        return p.drive or str(p)
    if IS_MAC and len(p.parts) > 2 and p.parts[1] == "Volumes":
        return p.parts[2]
    return "this computer"


def _mounts() -> list[tuple[str, str]]:
    """[(mount_point, fs_type)] on Mac/Linux, longest mount points first."""
    out = []
    try:
        if IS_MAC:
            text = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=5).stdout
            for line in text.splitlines():           # "//u@srv/share on /Volumes/share (smbfs, ...)"
                if " on " in line and " (" in line:
                    mp = line.split(" on ", 1)[1].rsplit(" (", 1)[0]
                    fstype = line.rsplit(" (", 1)[1].split(",")[0].strip(" )")
                    out.append((mp, fstype))
        elif os.path.exists("/proc/mounts"):
            for line in open("/proc/mounts", encoding="utf-8"):
                parts = line.split()
                if len(parts) >= 3:
                    out.append((parts[1].replace("\\040", " "), parts[2]))
    except Exception:
        pass
    return sorted(out, key=lambda m: len(m[0]), reverse=True)


def _mount_of(path: Path) -> tuple[str, str] | None:
    real = os.path.realpath(_existing_ancestor(path))
    for mp, fstype in _mounts():
        if real == mp or real.startswith(mp.rstrip("/") + "/"):
            return mp, fstype
    return None


NETWORK_FS = {"smbfs", "afpfs", "nfs", "nfs4", "webdav", "cifs", "smb3", "ftp",
              "fuse.sshfs", "sshfs", "davfs", "fuse.davfs2"}


def is_network_path(path: Path) -> bool:
    s = str(path)
    if IS_WIN:
        if s.startswith("\\\\") or s.startswith("//"):
            return True
        try:
            import ctypes
            root = Path(os.path.realpath(_existing_ancestor(path))).anchor
            return ctypes.windll.kernel32.GetDriveTypeW(root) == 4      # DRIVE_REMOTE
        except Exception:
            return False
    m = _mount_of(path)
    return bool(m and m[1].lower() in NETWORK_FS)


def is_removable_path(path: Path) -> bool:
    if IS_WIN:
        try:
            import ctypes
            root = Path(os.path.realpath(_existing_ancestor(path))).anchor
            return ctypes.windll.kernel32.GetDriveTypeW(root) == 2     # DRIVE_REMOVABLE
        except Exception:
            return False
    real = os.path.realpath(_existing_ancestor(path))
    if IS_MAC:
        return real.startswith("/Volumes/")
    return real.startswith(("/media/", "/run/media/", "/mnt/"))


CLOUD_NAMES = {"onedrive", "dropbox", "google drive", "my drive", "shared drives",
               "icloud drive", "iclouddrive", "box", "box sync"}


def _windows_cloud_roots() -> list[Path]:
    roots = [Path(os.environ[v]) for v in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")
             if os.environ.get(v)]
    for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        try:
            info = json.loads((Path(base) / "Dropbox" / "info.json").read_text(encoding="utf-8"))
            roots += [Path(v["path"]) for v in info.values() if isinstance(v, dict) and v.get("path")]
        except (OSError, ValueError):
            pass
    profile = os.environ.get("USERPROFILE")
    if profile:
        roots.append(Path(profile) / "iCloudDrive")
    return roots


def cloud_service(path: Path) -> str | None:
    """Name of the sync service that would sync this folder, or None."""
    real = Path(os.path.realpath(_existing_ancestor(path)))
    home = Path.home()
    if IS_MAC:
        if _same_or_inside(real, home / "Library" / "Mobile Documents"):
            return "iCloud Drive"
        if _same_or_inside(real, home / "Library" / "CloudStorage"):
            rel = real.relative_to(Path(os.path.realpath(home / "Library" / "CloudStorage")))
            name = rel.parts[0] if rel.parts else ""
            for svc in ("GoogleDrive", "OneDrive", "Dropbox", "Box"):
                if name.startswith(svc):
                    return {"GoogleDrive": "Google Drive"}.get(svc, svc)
            return "a cloud storage service"
        # "Desktop & Documents Folders" synced to iCloud
        icloud_docs = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        for name in ("Desktop", "Documents"):
            if (icloud_docs / name).exists() and _same_or_inside(real, home / name):
                return "iCloud Drive"
    if IS_WIN:
        for root in _windows_cloud_roots():
            if root.exists() and _same_or_inside(real, root):
                n = root.name.lower()
                return ("OneDrive" if "onedrive" in n else "Dropbox" if "dropbox" in n
                        else "iCloud Drive" if "icloud" in n else "a cloud storage service")
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(261)
            ctypes.windll.kernel32.GetVolumeInformationW(real.anchor, buf, 261, None, None, None, None, 0)
            if "google drive" in buf.value.lower():
                return "Google Drive"
        except Exception:
            pass
    for part in real.parts:
        p = part.lower()
        if p in CLOUD_NAMES or p.startswith("onedrive - ") or p.startswith("dropbox ("):
            return {"my drive": "Google Drive", "shared drives": "Google Drive",
                    "iclouddrive": "iCloud Drive", "box sync": "Box"}.get(p, part.split(" - ")[0])
    return None


def _forbidden_roots() -> list[Path]:
    roots = [config_dir()]
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).parent)
        if IS_MAC:                                         # the .app bundle
            roots.append(Path(sys.executable).parents[2])
    if IS_WIN:
        for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
            if os.environ.get(var):
                roots.append(Path(os.environ[var]))
    elif IS_MAC:
        # Not all of /private or /var: user folders (e.g. temporary ones) live there.
        roots += [Path(p) for p in ("/System", "/Applications", "/Library", "/usr", "/bin",
                                    "/sbin", "/etc", "/private/etc", "/private/var/db",
                                    "/private/var/root", "/opt", "/cores")]
    else:
        roots += [Path(p) for p in ("/usr", "/bin", "/sbin", "/etc", "/proc", "/sys", "/dev",
                                    "/boot", "/lib", "/var/lib")]
    return roots


# ── Checking a destination ────────────────────────────────────────────────────

def _gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


def final_path_for(chosen: Path, purpose: str) -> Path:
    """The folder the archive will occupy for a folder the researcher picked."""
    chosen = Path(chosen)
    if purpose == "setup" and os.path.realpath(chosen) == os.path.realpath(default_archive_dir()):
        return chosen
    if chosen.name == ARCHIVE_DIRNAME:
        return chosen
    return chosen / ARCHIVE_DIRNAME


def check_destination(chosen: str, purpose: str, current: Path | None = None,
                      archive_bytes: int = 0) -> dict:
    """Apply the destination rules. Never raises; returns a result for the UI.

    {ok, final_path, refusal, warnings, free_bytes, needed_bytes, drive,
     existing_archive}
    """
    result = {"ok": False, "final_path": None, "refusal": None, "warnings": [],
              "free_bytes": 0, "needed_bytes": 0, "drive": None, "existing_archive": None}
    raw = (chosen or "").strip().strip('"')
    if not raw:
        result["refusal"] = "Choose a folder."
        return result
    chosen_p = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not chosen_p.is_absolute():
        result["refusal"] = "Enter a full folder path, e.g. D:\\Research or /Volumes/Archive."
        return result
    is_default = os.path.realpath(chosen_p) == os.path.realpath(default_archive_dir())
    # The interface sends back the final path it was shown (".../Provenance Wiki
    # Archive"), which doesn't exist yet: its parent must.
    is_named_new = chosen_p.name == ARCHIVE_DIRNAME and chosen_p.parent.is_dir()
    if not chosen_p.is_dir() and not (purpose == "setup" and is_default) and not is_named_new:
        result["refusal"] = "That folder doesn't exist. Choose an existing folder."
        return result

    final = final_path_for(chosen_p, purpose)
    result["final_path"] = str(final)
    result["drive"] = drive_label(final)

    service = cloud_service(final)
    if service:
        result["refusal"] = (f"This folder is synced by {service}. Synced folders can corrupt the "
                             "archive's database. Choose a folder that is not synced.")
        return result
    if is_network_path(final):
        result["refusal"] = ("This is a network folder. Network folders can corrupt the archive's "
                             "database. Choose a folder on this computer or a directly connected drive.")
        return result
    for root in _forbidden_roots():
        if _same_or_inside(final, root):
            result["refusal"] = "Provenance Wiki can't store its archive in that folder."
            return result
    if current is not None:
        if _same_or_inside(final, current) or _same_or_inside(current, final):
            result["refusal"] = ("That folder is your current archive, or inside it. "
                                 "Choose a different folder.")
            return result

    marker = read_marker(final) if final.is_dir() else None
    if marker:
        result["existing_archive"] = {"archive_id": marker["archive_id"],
                                      "created_at": marker.get("created_at")}
    if final.exists() and any(final.iterdir()):
        result["refusal"] = ("That folder already contains a Provenance Wiki archive." if marker
                             else "That folder isn't empty. Choose an empty folder, or a new one.")
        return result

    # Writable?
    probe_dir = final if final.exists() else _existing_ancestor(final)
    try:
        fd, probe = tempfile.mkstemp(prefix=".pw-write-test-", dir=str(probe_dir))
        os.close(fd)
        os.remove(probe)
    except OSError:
        result["refusal"] = "Provenance Wiki can't write to this folder."
        return result

    free = free_space(final)
    result["free_bytes"] = free
    if purpose == "move":
        needed = int(archive_bytes * 1.1) + SPACE_MARGIN
        result["needed_bytes"] = needed
        if free < needed:
            result["refusal"] = (f"Not enough space on {result['drive']} "
                                 f"(needs {_gb(needed)}, {_gb(free)} free).")
            return result
    if free < LOW_SPACE_WARNING + archive_bytes:
        result["warnings"].append(
            f"Only {_gb(free)} free on {result['drive']}. Your archive grows as you add photographs.")
    if is_removable_path(final):
        result["warnings"].append(
            "This looks like a removable or external drive. Keep it connected while you work, "
            "and quit Provenance Wiki before unplugging it.")
    result["ok"] = True
    return result


# ── Switching the running app to an archive ───────────────────────────────────

def _activate(path: Path) -> None:
    import config
    config.apply_archive(path)
    from modules.db import init_db
    init_db()


def setup_archive(chosen: str) -> dict:
    """First launch (or 'start a new archive'): create and open an archive."""
    import config
    check = check_destination(chosen, "setup")
    if not check["ok"]:
        raise ArchiveError(check["refusal"])
    final = Path(check["final_path"])
    final.mkdir(parents=True, exist_ok=True)
    marker = write_marker(final)
    write_location(final, marker["archive_id"])
    _activate(final)
    logger.info("Archive created at %s", final)
    return {"path": str(final), "archive_id": marker["archive_id"], "warnings": check["warnings"]}


def locate_archive(chosen: str, allow_different: bool = False) -> dict:
    """Open an existing archive (drive letter changed, folder moved by hand, new computer)."""
    raw = Path(os.path.expanduser((chosen or "").strip().strip('"')))
    candidates = [raw, raw / ARCHIVE_DIRNAME]
    found = next((c for c in candidates if read_marker(c)), None)
    if not found:
        raise ArchiveError("No Provenance Wiki archive found in that folder. Choose the folder "
                           f"named \"{ARCHIVE_DIRNAME}\" (or the folder that contains it).")
    if cloud_service(found) or is_network_path(found):
        raise ArchiveError("That archive is in a synced or network folder. Move the folder to a "
                           "local or directly connected drive first.")
    marker = read_marker(found)
    loc = read_location()
    if loc and loc.get("archive_id") and marker["archive_id"] != loc["archive_id"] and not allow_different:
        raise ArchiveError("DIFFERENT_ARCHIVE")
    write_location(found, marker["archive_id"])
    _activate(found)
    logger.info("Archive located at %s", found)
    return {"path": str(found), "archive_id": marker["archive_id"]}


def retry() -> str:
    """Look for the archive again (e.g. after reconnecting its drive)."""
    import config
    res = resolve()
    if res["state"] == "ok":
        _activate(res["path"])
    else:
        config.ARCHIVE_STATE = res["state"]
        config.ARCHIVE_EXPECTED = res["expected"]
    return config.ARCHIVE_STATE


# ── Moving ────────────────────────────────────────────────────────────────────

_job_lock = threading.Lock()
_job: dict | None = None
_cancel = threading.Event()


def archive_busy() -> str | None:
    """Reason the archive can't be moved right now, or None."""
    try:
        from modules import ingestor
        if getattr(ingestor, "_is_ingesting", False):
            return "Photos are being ingested. Wait until ingestion finishes, then try again."
    except Exception:
        pass
    try:
        from modules import relationships
        if relationships.is_running():
            return "Relationships are being refreshed. Wait until that finishes, then try again."
    except Exception:
        pass
    return None


def move_status() -> dict | None:
    with _job_lock:
        return dict(_job) if _job else None


def _set(**kw) -> None:
    with _job_lock:
        _job.update(kw)


def start_move(chosen: str) -> dict:
    import config
    global _job
    if config.ARCHIVE_STATE != "ok":
        raise ArchiveError("The archive can't be moved right now.")
    busy = archive_busy()
    if busy:
        raise ArchiveError(busy)
    current = Path(config.DATA_DIR)
    size = folder_size(current)
    check = check_destination(chosen, "move", current=current, archive_bytes=size)
    if not check["ok"]:
        raise ArchiveError(check["refusal"])
    with _job_lock:
        if _job and _job.get("phase") in ("preparing", "copying", "checking", "switching"):
            raise ArchiveError("A move is already in progress.")
        _job = {"phase": "preparing", "from": str(current), "to": check["final_path"],
                "files_done": 0, "files_total": 0, "bytes_done": 0, "bytes_total": size,
                "current_file": None, "error": None, "started_at": time.time()}
    _cancel.clear()
    config.ARCHIVE_STATE = "moving"
    threading.Thread(target=_move_worker, args=(current, Path(check["final_path"])),
                     name="archive-move", daemon=True).start()
    return move_status()


def cancel_move() -> None:
    _cancel.set()


class _Cancelled(Exception):
    pass


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


DB_FILES = {"provenance.db", "provenance.db-wal", "provenance.db-shm", "provenance.db-journal"}
COUNT_TABLES = ("documents", "document_groups", "entities", "entity_aliases", "transactions",
                "tags", "audit_events")


def _row_counts(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    try:
        counts = {}
        for t in COUNT_TABLES:
            try:
                counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                pass
        return counts
    finally:
        conn.close()


def _move_worker(src: Path, dst: Path) -> None:
    import config
    created = not dst.exists()
    try:
        # Plan: every file except the live database (copied with SQLite's backup API).
        plan = []
        for root, _dirs, files in os.walk(_lp(src)):
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, _lp(src))
                if rel in DB_FILES:
                    continue
                plan.append((rel, os.path.getsize(full)))
        _set(phase="copying", files_total=len(plan) + 1,
             bytes_total=sum(s for _, s in plan) + (src / "provenance.db").stat().st_size)
        os.makedirs(_lp(dst), exist_ok=True)

        # 1. Database: consistent snapshot even with WAL; writes are paused (state=moving).
        _set(current_file="provenance.db")
        src_db = sqlite3.connect(str(src / "provenance.db"))
        dst_db = sqlite3.connect(_lp(dst / "provenance.db") if IS_WIN else str(dst / "provenance.db"))
        try:
            src_db.backup(dst_db)
        finally:
            dst_db.close()
            src_db.close()
        done_bytes = (dst / "provenance.db").stat().st_size
        _set(files_done=1, bytes_done=done_bytes)

        # 2. Everything else.
        for i, (rel, size) in enumerate(plan, start=2):
            if _cancel.is_set():
                raise _Cancelled()
            _set(current_file=rel)
            target = os.path.join(_lp(dst), rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(os.path.join(_lp(src), rel), target)
            done_bytes += size
            _set(files_done=i, bytes_done=done_bytes)

        # 3. Verify.
        _set(phase="checking", current_file=None)
        for rel, size in plan:
            if _cancel.is_set():
                raise _Cancelled()
            a, b = os.path.join(_lp(src), rel), os.path.join(_lp(dst), rel)
            if not os.path.exists(b) or os.path.getsize(b) != size:
                raise RuntimeError(f"Copy check failed for {rel}")
            if rel.replace("\\", "/").startswith("photos/") and _sha256(a) != _sha256(b):
                raise RuntimeError(f"Copy check failed for {rel} (content differs)")
        conn = sqlite3.connect(str(dst / "provenance.db"))
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if ok != "ok":
            raise RuntimeError("The copied database failed its integrity check")
        if _row_counts(src / "provenance.db") != _row_counts(dst / "provenance.db"):
            raise RuntimeError("The copied database doesn't match the original")
        marker = read_marker(dst)
        if not marker:
            raise RuntimeError("The copied archive marker is missing")

        if _cancel.is_set():
            raise _Cancelled()

        # 4. Switch (the point of no return is writing location.json).
        _set(phase="switching")
        write_location(dst, marker["archive_id"], previous_path=str(src))
        try:
            _activate(dst)
        except Exception:
            write_location(src, marker["archive_id"])           # roll back
            config.apply_archive(src)
            raise
        _set(phase="done", finished_at=time.time())
        logger.info("Archive moved from %s to %s", src, dst)
    except _Cancelled:
        _cleanup(dst, created)
        config.ARCHIVE_STATE = "ok"
        _set(phase="cancelled", error="The move was cancelled. Your archive was not changed.")
        logger.info("Archive move cancelled")
    except Exception as exc:
        logger.exception("Archive move failed")
        _cleanup(dst, created)
        config.ARCHIVE_STATE = "ok"
        _set(phase="failed",
             error=f"The move failed and your archive was not changed. ({exc})")


def _cleanup(dst: Path, created: bool) -> None:
    """Remove a partial copy. Only ever touches a folder that was empty before."""
    try:
        if created:
            shutil.rmtree(_lp(dst), ignore_errors=True)
        else:
            for child in Path(dst).iterdir():
                if child.is_dir():
                    shutil.rmtree(_lp(child), ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not fully remove the partial copy at %s", dst)


# ── The old copy after a move ─────────────────────────────────────────────────

def previous_copy() -> dict | None:
    loc = read_location()
    if not loc or not loc.get("previous_path"):
        return None
    prev = Path(loc["previous_path"])
    if not prev.exists():
        return None
    return {"path": str(prev), "size_bytes": folder_size(prev)}


def delete_previous_copy() -> None:
    import config
    loc = read_location()
    if not loc or not loc.get("previous_path"):
        raise ArchiveError("There is no old copy to delete.")
    prev, current = Path(loc["previous_path"]), Path(config.DATA_DIR)
    marker = read_marker(prev)
    if (not marker or marker["archive_id"] != loc.get("archive_id")
            or _same_or_inside(prev, current) or _same_or_inside(current, prev)
            or Path(os.path.realpath(prev)) in (Path(os.path.realpath(Path.home())),
                                                Path(Path(os.path.realpath(prev)).anchor))):
        raise ArchiveError("That folder doesn't look like the old copy of this archive, "
                           "so it was not deleted.")
    shutil.rmtree(_lp(prev))
    write_location(current, loc["archive_id"])
    logger.info("Deleted the old archive copy at %s", prev)


def forget_previous_copy() -> None:
    import config
    loc = read_location()
    if loc:
        write_location(Path(config.DATA_DIR), loc["archive_id"])


# ── Native folder picker ──────────────────────────────────────────────────────

class PickerUnavailable(Exception):
    pass


_picker_lock = threading.Lock()
_picker_proc: subprocess.Popen | None = None
_picker_cancelled = threading.Event()


def cancel_folder_picker() -> bool:
    """Close a folder dialog that is open (or stuck). True if one was closed."""
    with _picker_lock:
        proc = _picker_proc
    if proc is not None and proc.poll() is None:
        _picker_cancelled.set()
        proc.kill()
        return True
    return False


def _run_picker(cmd: list[str], **kw) -> tuple[int, str, str]:
    """Run a picker helper. stdin is an empty input, not inherited: the desktop
    app has no console, and PowerShell can hang on an invalid stdin handle."""
    global _picker_proc
    cancel_folder_picker()                     # only one dialog at a time
    _picker_cancelled.clear()
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, **kw)
    with _picker_lock:
        _picker_proc = proc
    try:
        out, err = proc.communicate(timeout=900)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    finally:
        with _picker_lock:
            if _picker_proc is proc:
                _picker_proc = None
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def choose_folder(prompt: str) -> str | None:
    """Show the system's folder picker on this computer; None if cancelled."""
    if IS_MAC:
        script = f'POSIX path of (choose folder with prompt "{prompt}")'
        code, out, err = _run_picker(["osascript", "-e", script])
        if code != 0:
            if _picker_cancelled.is_set() or "-128" in err or "cancel" in err.lower():
                return None                    # cancelled by the user or by the app
            raise PickerUnavailable(err.strip())
        return out.strip().rstrip("/") or "/"
    if IS_WIN:
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            # Hidden always-on-top owner window, so the dialog can't open
            # behind the browser.
            "$f=New-Object System.Windows.Forms.Form -Property @{TopMost=$true;ShowInTaskbar=$false};"
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            f"$d.Description='{prompt}';$d.ShowNewFolderButton=$true;"
            "if($d.ShowDialog($f) -eq 'OK'){ [Console]::Out.Write($d.SelectedPath) }"
        )
        code, out, err = _run_picker(
            ["powershell", "-NoProfile", "-NonInteractive", "-STA", "-OutputFormat", "Text",
             "-Command", ps],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if code != 0:
            if _picker_cancelled.is_set():     # closed by the app's Cancel button
                return None
            raise PickerUnavailable(err.strip())
        return out.strip() or None
    if shutil.which("zenity"):
        code, out, _err = _run_picker(["zenity", "--file-selection", "--directory", f"--title={prompt}"])
        return out.strip() or None
    raise PickerUnavailable("No folder picker available")
