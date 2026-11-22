"""Application version and build information.

Shown in Settings and reported by /api/health.

The desktop build writes build_info.json into the app bundle, for example
{"testing_build": true, "commit": "abc1234", "built_at": "2026-11-22T15:00Z",
 "platform": "macOS-arm64"}. testing_build marks builds that are not signed and
notarized for public distribution; the app shows a banner for them.
Source checkouts have no build_info.json and are never testing builds.
"""
import json
import sys
from pathlib import Path

__version__ = "1.0.0-test.1"


def _load_build_info() -> dict:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    try:
        return json.loads((base / "build_info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


BUILD_INFO = _load_build_info()
TESTING_BUILD = bool(BUILD_INFO.get("testing_build"))
