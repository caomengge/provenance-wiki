"""
secrets_store.py – API keys in the operating system's credential store.

macOS: Keychain. Windows: Credential Manager (DPAPI-protected). Linux: the
Secret Service, if one is running. Accessed through the `keyring` package.

Rules:
- Keys saved in the app's Settings screen are stored ONLY here, never in a
  file. In packaged mode, if no secure store is available, saving fails with a
  clear error instead of falling back to plaintext.
- Precedence: credential store, then the process environment (which in source
  mode includes .env). Developers' .env workflow keeps working.
- Values are never logged or returned by any API; callers get booleans.
- PW_DISABLE_KEYRING=1 turns the store off entirely (the test suite sets it so
  tests never read or write a developer's real Keychain).

This module must not import config (config imports it at load time).
"""

import logging
import os

logger = logging.getLogger(__name__)

SERVICE = "ProvenanceWiki"
KEY_NAMES = {"anthropic": "ANTHROPIC_API_KEY", "voyage": "VOYAGE_API_KEY"}

# Where each currently active key came from: "keychain" | "environment" | None
_sources: dict[str, str | None] = {}


class SecretStoreUnavailable(RuntimeError):
    pass


def _is_placeholder(value: str | None) -> bool:
    v = (value or "").strip()
    return not v or v.upper().startswith("YOUR_") or v.upper() in {"CHANGEME", "REPLACE_ME", "XXX"}


def _keyring():
    """Return the keyring module if a real (secure) backend is usable, else None."""
    if os.getenv("PW_DISABLE_KEYRING") == "1":
        return None
    try:
        import keyring
        from keyring.backends import fail
        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return None
        # Refuse plaintext/"insecure" backends (e.g. keyrings.alt PlaintextKeyring)
        if (getattr(backend, "priority", 1) <= 0
                or type(backend).__module__.startswith("keyrings.alt")
                or "plaintext" in type(backend).__name__.lower()):
            return None
        return keyring
    except Exception:  # no backend, import failure, locked store, …
        return None


def store_available() -> bool:
    return _keyring() is not None


def store_name() -> str:
    import sys
    if sys.platform == "darwin":
        return "macOS Keychain"
    if sys.platform == "win32":
        return "Windows Credential Manager"
    return "system keyring"


def _read_stored(env_name: str) -> str | None:
    kr = _keyring()
    if kr is None:
        return None
    try:
        value = kr.get_password(SERVICE, env_name)
    except Exception:
        logger.warning("Could not read %s from the %s", env_name, store_name())
        return None
    return None if _is_placeholder(value) else value.strip()


def load_into_environ() -> None:
    """Called once by config at import: stored keys override the environment."""
    for env_name in KEY_NAMES.values():
        stored = _read_stored(env_name)
        if stored:
            os.environ[env_name] = stored
            _sources[env_name] = "keychain"
        elif not _is_placeholder(os.environ.get(env_name)):
            _sources[env_name] = "environment"
        else:
            os.environ.pop(env_name, None)
            _sources[env_name] = None


def status() -> dict:
    """Booleans and sources only — never key material."""
    return {
        provider: {"set": bool(os.environ.get(env_name)), "source": _sources.get(env_name)}
        for provider, env_name in KEY_NAMES.items()
    }


def _apply_runtime() -> None:
    """Make a changed key take effect without restarting the app."""
    import config
    config.ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    config.VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
    # The Voyage client captures its key when created; drop cached clients.
    from modules import embeddings
    embeddings._backend_cache.clear()


def save(provider: str, value: str) -> None:
    env_name = KEY_NAMES[provider]
    value = (value or "").strip()
    if _is_placeholder(value):
        raise ValueError("That doesn't look like an API key.")
    if any(c.isspace() for c in value) or len(value) > 500:
        raise ValueError("That doesn't look like an API key.")
    kr = _keyring()
    if kr is None:
        raise SecretStoreUnavailable(
            f"Secure key storage ({store_name()}) is not available, so the key was not saved.")
    kr.set_password(SERVICE, env_name, value)
    os.environ[env_name] = value
    _sources[env_name] = "keychain"
    _apply_runtime()


def remove(provider: str) -> None:
    env_name = KEY_NAMES[provider]
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(SERVICE, env_name)
        except Exception:
            pass  # not stored — nothing to delete
    os.environ.pop(env_name, None)
    _sources[env_name] = None
    _apply_runtime()
