# -*- mode: python ; coding: utf-8 -*-
# PyInstaller recipe for the Provenance Wiki desktop app (macOS and Windows).
#
#   python desktop/make_icons.py build
#   pyinstaller desktop/provenance_wiki.spec --noconfirm
#
# Expects the built frontend in dist/ (Vite output) and build/build_info.json.
# Bundles: backend code, built frontend, Python runtime and dependencies.
# Excludes: research experiment code, MCP server, tests, the experiments/
# folder, any .env, data/ and photos/.
import json
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
BACKEND = os.path.join(ROOT, "backend")
BUILD = os.path.join(ROOT, "build")
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

build_info = json.load(open(os.path.join(BUILD, "build_info.json")))
TESTING = bool(build_info.get("testing_build"))
APP_NAME = "Provenance Wiki (Testing)" if TESTING else "Provenance Wiki"

sys.path.insert(0, BACKEND)
from version import __version__  # noqa: E402

EXCLUDED_APP_MODULES = [
    "experiment", "mcp_server",
    "modules.experiment_rag", "modules.experiment_search", "modules.experiment_schema",
]

hiddenimports = (
    collect_submodules("api")
    + [m for m in collect_submodules("modules") if m not in EXCLUDED_APP_MODULES]
    + ["app", "config", "version", "waitress", "keyring", "keyring.backends"]
)
if IS_MAC:
    hiddenimports += ["keyring.backends.macOS", "pystray._darwin"]
if IS_WIN:
    hiddenimports += ["keyring.backends.Windows", "pystray._win32"] \
        + collect_submodules("win32ctypes")   # Windows Credential Manager access

datas = [
    (os.path.join(ROOT, "dist"), "dist"),                       # built React app
    (os.path.join(BUILD, "build_info.json"), "."),
    (os.path.join(ROOT, "LICENSE"), "."),
]

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[BACKEND],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=EXCLUDED_APP_MODULES + ["mcp", "pytest", "tkinter", "IPython", "matplotlib",
                                     "weasyprint"],   # PDF export removed in v1
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ProvenanceWiki",
    console=False,                      # no Terminal / command window
    icon=os.path.join(BUILD, "icon.icns" if IS_MAC else "icon.ico"),
    codesign_identity=None,             # testing builds: ad-hoc signature only
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ProvenanceWiki")

if IS_MAC:
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=os.path.join(BUILD, "icon.icns"),
        bundle_identifier="org.provenancewiki.desktop",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "15.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "See LICENSE",
        },
    )
