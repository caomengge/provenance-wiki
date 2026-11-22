"""
local_security.py – Keep the local web server usable only by this computer's
own Provenance Wiki interface.

The app runs an unauthenticated HTTP server on the researcher's machine. Without
these checks, any website open in the same browser could send requests to it
(for example a DELETE that removes a document and its photograph), and a
malicious domain pointed at 127.0.0.1 ("DNS rebinding") could read the archive.

Three checks, applied to every request:

1. Host check (all requests). The Host header must be a loopback name
   (localhost, 127.0.0.1, [::1]) or listed in ALLOWED_HOSTS. Blocks DNS
   rebinding, where the attacker's own domain resolves to 127.0.0.1.

2. Origin check (state-changing requests: POST, PUT, PATCH, DELETE). If the
   browser sends an Origin header it must be this server's own origin or a
   configured development origin (the Vite dev server). Blocks cross-site
   form posts and fetches.

3. Custom header (state-changing /api/ requests). The frontend sends
   X-Provenance-Wiki: 1. Browsers will not attach a custom header to a
   cross-site request unless the server approves a CORS preflight, which this
   server never does. Defence in depth for browsers that omit Origin.

No CORS headers are sent at all: the built app is served from the same origin
as the API, and the Vite dev server proxies /api, so neither needs CORS.
"""

import logging
import os
from urllib.parse import urlsplit

from flask import Flask, jsonify, request

logger = logging.getLogger(__name__)

CSRF_HEADER = "X-Provenance-Wiki"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


DEFAULT_DEV_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"


def _split_csv(value: str) -> set[str]:
    return {v.strip().lower() for v in (value or "").split(",") if v.strip()}


def _hostname(host_header: str) -> str:
    """'localhost:5100' -> 'localhost'; '[::1]:5100' -> '::1'."""
    try:
        return (urlsplit("//" + host_header).hostname or "").lower()
    except ValueError:
        return ""


def is_loopback_bind(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def install_local_guard(app: Flask) -> None:
    allowed_hosts = LOOPBACK_HOSTS | _split_csv(os.getenv("ALLOWED_HOSTS", ""))
    dev_origins = _split_csv(os.getenv("DEV_ORIGINS", DEFAULT_DEV_ORIGINS))

    @app.before_request
    def _guard():
        host = request.headers.get("Host", "")
        if _hostname(host) not in allowed_hosts:
            logger.warning("Rejected request with unrecognised Host header")
            return jsonify({"error": "Unrecognised Host. Open Provenance Wiki at "
                                     "http://127.0.0.1 or http://localhost."}), 400

        if request.method not in UNSAFE_METHODS:
            return None

        origin = (request.headers.get("Origin") or "").lower()
        own_origin = f"{request.scheme}://{host}".lower()
        if origin and origin != own_origin and origin not in dev_origins:
            logger.warning("Rejected cross-origin %s %s", request.method, request.path)
            return jsonify({"error": "Cross-origin request refused."}), 403

        if request.path.startswith("/api/") and request.headers.get(CSRF_HEADER) != "1":
            return jsonify({"error": f"Missing {CSRF_HEADER} header."}), 403
        return None
