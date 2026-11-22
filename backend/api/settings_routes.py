"""
settings_routes.py – API keys, folders, version, and Quit for the desktop app.

Routes:
  GET    /api/settings                         – version, key status (booleans), folders
  PUT    /api/settings/keys/<provider>         – save a key to the OS credential store
  DELETE /api/settings/keys/<provider>         – remove a saved key
  POST   /api/settings/keys/<provider>/test    – check the active key with the provider
  POST   /api/system/open-folder               – open the photos/data/logs folder
  POST   /api/system/quit                      – stop the desktop app (packaged mode)

provider is "anthropic" or "voyage". No route ever returns key material, and
error messages from providers are summarised, not echoed. All writes are
covered by modules/local_security.py (same-origin + X-Provenance-Wiki header).
"""

import logging
import os
import subprocess
import sys

from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("settings", __name__)
logger = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "voyage")


def _settings_payload() -> dict:
    import config
    from modules import secrets_store
    from version import BUILD_INFO
    return {
        "version": config.APP_VERSION,
        "testing_build": config.TESTING_BUILD,
        "build": BUILD_INFO,
        "packaged": config.PACKAGED,
        "keys": secrets_store.status(),
        "secure_store_available": secrets_store.store_available(),
        "secure_store_name": secrets_store.store_name(),
        "folders": {
            "data": str(config.DATA_DIR),
            "photos": str(config.PHOTOS_DIR),
            "logs": str(config.LOG_DIR),
        },
    }


@bp.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(_settings_payload())


@bp.route("/api/settings/keys/<provider>", methods=["PUT"])
def save_key(provider):
    from modules import secrets_store
    if provider not in PROVIDERS:
        return jsonify({"error": "Unknown provider"}), 404
    data = request.get_json(silent=True) or {}
    try:
        secrets_store.save(provider, data.get("key", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except secrets_store.SecretStoreUnavailable as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception:
        logger.exception("Saving %s key failed", provider)  # no key in the message
        return jsonify({"error": "The key could not be saved to secure storage."}), 500
    logger.info("%s key saved to %s", provider, secrets_store.store_name())
    return jsonify(_settings_payload())


@bp.route("/api/settings/keys/<provider>", methods=["DELETE"])
def remove_key(provider):
    from modules import secrets_store
    if provider not in PROVIDERS:
        return jsonify({"error": "Unknown provider"}), 404
    secrets_store.remove(provider)
    logger.info("%s key removed", provider)
    return jsonify(_settings_payload())


def _classify(exc: Exception) -> tuple[str, str]:
    """Turn a provider SDK exception into (status, plain-language message)."""
    name = type(exc).__name__
    code = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    text = str(exc).lower()
    if name == "AuthenticationError" or code == 401:
        return "invalid", "The key was rejected. Check that you copied the whole key."
    if name == "PermissionDeniedError" or code == 403:
        return "invalid", "This key is not allowed to use this service."
    if (code in (402, 429) or "RateLimit" in name or "SpendLimit" in name
            or "PaymentRequired" in name or "credit" in text or "billing" in text
            or "quota" in text):
        return "quota", "The key works, but the account has hit a usage, billing, or rate limit."
    if "Connection" in name or "Timeout" in name:
        return "network", "Could not reach the service. Check your internet connection."
    return "error", f"The check failed ({name})."


@bp.route("/api/settings/keys/<provider>/test", methods=["POST"])
def test_key(provider):
    if provider not in PROVIDERS:
        return jsonify({"error": "Unknown provider"}), 404
    env_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "VOYAGE_API_KEY"
    key = os.environ.get(env_name, "")
    if not key:
        return jsonify({"status": "missing", "message": "No key is saved."})
    try:
        if provider == "anthropic":
            # Listing models is free: no tokens are used.
            import anthropic
            anthropic.Anthropic(api_key=key, timeout=20, max_retries=0).models.list(limit=1)
            msg = "Anthropic accepted the key. (This check is free; it cannot confirm account credit.)"
        else:
            # Embeds two words: a tiny, billable request.
            import voyageai
            voyageai.Client(api_key=key, max_retries=0, timeout=20).embed(
                ["connection test"], model="voyage-4", input_type="query")
            msg = "Voyage AI accepted the key."
        return jsonify({"status": "ok", "message": msg})
    except Exception as exc:
        status, message = _classify(exc)
        logger.warning("%s key check failed: %s", provider, type(exc).__name__)
        return jsonify({"status": status, "message": message})


@bp.route("/api/system/open-folder", methods=["POST"])
def open_folder():
    import config
    which = (request.get_json(silent=True) or {}).get("which")
    folders = {"data": config.DATA_DIR, "photos": config.PHOTOS_DIR, "logs": config.LOG_DIR}
    if which not in folders:
        return jsonify({"error": "Unknown folder"}), 400
    path = folders[which]
    path.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 — opening a known local folder
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        return jsonify({"error": "Could not open the folder", "path": str(path)}), 500
    return jsonify({"opened": str(path)})


@bp.route("/api/system/quit", methods=["POST"])
def quit_app():
    quit_cb = current_app.config.get("PW_QUIT")
    if quit_cb is None:
        return jsonify({"error": "Quit is only available in the desktop app."}), 409
    quit_cb()
    return jsonify({"quitting": True})
