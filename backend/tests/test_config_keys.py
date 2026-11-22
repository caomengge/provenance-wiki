"""
Tests for API-key configuration (run from backend/):

    python -m pytest tests/test_config_keys.py -v

Placeholder values copied from .env.example must not count as configured
keys, and /api/health must report each provider separately as booleans
without exposing any part of a key. Each case runs in a fresh interpreter
because config.py reads the environment at import time.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).parent.parent

PROBE = r"""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, {backend!r})
import config
tmp = Path(tempfile.mkdtemp())
config.DB_PATH = tmp / "provenance.db"
import modules.db as d
if hasattr(d, "DB_PATH"): d.DB_PATH = config.DB_PATH
from app import create_app
health = create_app().test_client().get("/api/health")
print(json.dumps({{
    "anthropic": config.ANTHROPIC_API_KEY,
    "voyage": config.VOYAGE_API_KEY,
    "voyage_env": os.environ.get("VOYAGE_API_KEY"),
    "health": health.get_json(),
    "health_text": health.get_data(as_text=True),
}}))
"""


def _probe(anthropic, voyage):
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY")}
    if anthropic is not None:
        env["ANTHROPIC_API_KEY"] = anthropic
    if voyage is not None:
        env["VOYAGE_API_KEY"] = voyage
    r = subprocess.run([sys.executable, "-c", PROBE.format(backend=str(BACKEND))],
                       capture_output=True, text=True, env=env, cwd=str(BACKEND))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("placeholder", ["YOUR_KEY_HERE", "  your_key_here  ", "", "   "])
def test_placeholder_keys_count_as_unset(placeholder):
    out = _probe(placeholder, placeholder)
    assert out["anthropic"] == ""
    assert out["voyage"] == ""
    # embeddings.py reads os.environ directly; the placeholder must be gone there too
    assert out["voyage_env"] is None
    assert out["health"]["anthropic_key_set"] is False
    assert out["health"]["voyage_key_set"] is False
    assert out["health"]["api_key_set"] is False


def test_real_looking_keys_are_kept_and_never_echoed():
    a, v = "sk-ant-test-abc123SECRET", "pa-test-xyz789SECRET"
    out = _probe(a, v)
    assert out["anthropic"] == a and out["voyage"] == v
    assert out["health"]["anthropic_key_set"] is True
    assert out["health"]["voyage_key_set"] is True
    assert "SECRET" not in out["health_text"]


def test_each_provider_reported_separately():
    out = _probe("sk-ant-test-abc", None)
    assert out["health"]["anthropic_key_set"] is True
    assert out["health"]["voyage_key_set"] is False
