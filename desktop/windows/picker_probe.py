"""Windows folder-picker probe (CI diagnostic).

Starts a PowerShell folder dialog the way the app does, finds its window,
reports whether it is visible and always-on-top (so it can't hide behind the
browser), then closes it and checks the picker returns "cancelled".

    python desktop/windows/picker_probe.py [current|fixed|module]
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import threading
import time
from pathlib import Path

user32 = ctypes.windll.user32
WS_EX_TOPMOST = 0x8
GWL_EXSTYLE = -20
WM_CLOSE = 0x10

CURRENT = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "Add-Type -AssemblyName System.Windows.Forms;"
    "$f=New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
    "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
    "$d.Description='Probe';$d.ShowNewFolderButton=$true;"
    "if($d.ShowDialog($f) -eq 'OK'){ Write-Output $d.SelectedPath }"
)


def windows_of(pid):
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            found.append({
                "hwnd": hwnd, "title": title.value, "class": cls.value,
                "visible": bool(user32.IsWindowVisible(hwnd)),
                "topmost": bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST),
            })
        return True

    user32.EnumWindows(cb, 0)
    return found


def probe_script(name, script):
    p = subprocess.Popen(["powershell", "-NoProfile", "-STA", "-Command", script],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    dialog = None
    for _ in range(100):
        wins = [w for w in windows_of(p.pid) if w["visible"]]
        dialog = next((w for w in wins if w["class"] == "#32770"), None)
        if dialog:
            break
        time.sleep(0.2)
    print(f"[{name}] all windows:", windows_of(p.pid))
    print(f"[{name}] dialog:", dialog)
    if dialog:
        user32.PostMessageW(dialog["hwnd"], WM_CLOSE, 0, 0)
    try:
        out, err = p.communicate(timeout=20)
        print(f"[{name}] exit={p.returncode} stdout={out!r} stderr={err[-300:]!r}")
    except subprocess.TimeoutExpired:
        p.kill()
        print(f"[{name}] did not finish after closing")
    return dialog


def probe_module():
    """The app's own choose_folder(), with its cancel function."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from modules import archive_location as al
    result = {}
    t = threading.Thread(target=lambda: result.update(value=al.choose_folder("Probe")))
    t.start()
    dialog = None
    for _ in range(100):
        proc = getattr(al, "_picker_proc", None)
        if proc is not None:
            wins = [w for w in windows_of(proc.pid) if w["visible"]]
            dialog = next((w for w in wins if w["class"] == "#32770"), None)
            if dialog:
                break
        time.sleep(0.2)
    print("[module] dialog:", dialog)
    if hasattr(al, "cancel_folder_picker"):
        al.cancel_folder_picker()
    t.join(30)
    print("[module] returned:", result, "thread alive:", t.is_alive())
    return dialog and dialog["topmost"] and not t.is_alive() and result.get("value") is None


def probe_no_console():
    """Like the installed app: the picker is started from a process without a
    console (pythonw.exe). The dialog must appear, be on top, and the app's
    Cancel must close it."""
    import json
    import os
    import tempfile
    backend = str(Path(__file__).resolve().parents[2] / "backend")
    out = Path(tempfile.mkdtemp()) / "result.json"
    code = (
        "import sys, json, threading, time\n"
        f"sys.path.insert(0, {backend!r})\n"
        "from modules import archive_location as al\n"
        "res = {}\n"
        "t = threading.Thread(target=lambda: res.update(value=al.choose_folder('Probe')))\n"
        "t.start(); time.sleep(12); al.cancel_folder_picker(); t.join(20)\n"
        f"open({str(out)!r}, 'w').write(json.dumps({{'value': res.get('value', 'MISSING'), 'alive': t.is_alive()}}))\n"
    )
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    p = subprocess.Popen([str(pythonw), "-c", code])
    dialog = None
    for _ in range(50):
        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(hwnd, _):
            nonlocal dialog
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            if title.value == "Browse For Folder" and user32.IsWindowVisible(hwnd):
                dialog = {"visible": True,
                          "topmost": bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)}
            return True
        user32.EnumWindows(cb, 0)
        if dialog:
            break
        time.sleep(0.2)
    print("[no-console] dialog:", dialog)
    p.wait(60)
    result = json.loads(out.read_text()) if out.exists() else None
    print("[no-console] result:", result)
    return bool(dialog and dialog["topmost"] and result and result["value"] is None and not result["alive"])


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "current"
    if which == "current":
        d = probe_script("current", CURRENT)
        sys.exit(0)        # diagnostic only
    ok = probe_no_console() if which == "no-console" else probe_module()
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
