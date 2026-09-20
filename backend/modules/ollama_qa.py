"""
ollama_qa.py – Local Ollama generation backend for the Q&A engine
(EXPERIMENTAL).

This module covers the FINAL answer-generation step only. It receives the
system prompt and the fully constructed context produced by modules/qa.py
and returns answer text. Retrieval, evidence hydration, context
construction, citation parsing and the API response shape are untouched,
so a Claude run and an Ollama run differ in the generator alone.

Standard-library HTTP only (urllib) — no new dependencies.
"""

import json
import logging
import re
import socket
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# Thinking models may emit an inline reasoning block when thinking cannot be
# disabled server-side; it is stripped so citation parsing sees only the answer.
_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


class OllamaError(RuntimeError):
    """Ollama could not produce an answer. The message is user-facing."""


def _post(url: str, payload: dict, timeout: int) -> dict:
    """POST JSON, return the decoded JSON body, or raise OllamaError."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:                     # server answered
        raise _http_error(exc, payload["model"], url) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise OllamaError(
            f"Ollama timed out after {timeout}s generating with "
            f"'{payload['model']}'. The model may still be loading into VRAM — "
            f"retry, or increase the OLLAMA_QA_TIMEOUT environment setting."
        ) from exc
    except urllib.error.URLError as exc:                      # no server / DNS
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise OllamaError(
                f"Ollama timed out after {timeout}s connecting to {url}."
            ) from exc
        raise OllamaError(
            f"Cannot reach Ollama at {url} ({reason}). Is the Ollama server "
            f"running? Start it with `ollama serve`, or check OLLAMA_BASE_URL."
        ) from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaError(
            f"Ollama returned a non-JSON response from {url}: {raw[:200]}"
        ) from exc


def _http_error(exc: urllib.error.HTTPError, model: str, url: str) -> OllamaError:
    """Map an Ollama HTTP error onto an actionable message."""
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:                                          # noqa: BLE001
        body = ""
    detail = body.strip()
    try:
        detail = json.loads(body).get("error") or detail
    except Exception:                                          # noqa: BLE001
        pass

    if exc.code == 404 or "not found" in detail.lower():
        return OllamaError(
            f"Ollama model '{model}' is not available at {url} ({detail}). "
            f"Pull it first:  ollama pull {model}"
        )
    return OllamaError(f"Ollama request failed (HTTP {exc.code}) at {url}: {detail}")


def generate(system_prompt: str, user_content: str) -> str:
    """
    Send the already-built prompt to Ollama's POST /api/chat and return the
    answer text. Raises OllamaError with an actionable message on failure.
    """
    from config import (OLLAMA_BASE_URL, OLLAMA_QA_MODEL, OLLAMA_QA_TIMEOUT,
                        OLLAMA_NUM_CTX, OLLAMA_QA_THINK, QA_MAX_TOKENS)

    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model":    OLLAMA_QA_MODEL,
        "stream":   False,
        "think":    OLLAMA_QA_THINK,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        "options": {
            "num_ctx":     OLLAMA_NUM_CTX,
            "num_predict": QA_MAX_TOKENS,
        },
    }

    # Ollama silently truncates prompts longer than num_ctx, which would break
    # the "same context for both providers" guarantee — warn loudly instead.
    est_tokens = (len(system_prompt) + len(user_content)) // 4
    if est_tokens > OLLAMA_NUM_CTX - QA_MAX_TOKENS:
        logger.warning(
            "Ollama prompt is ~%d tokens; num_ctx is %d with %d reserved for "
            "output. Ollama may truncate the evidence — raise OLLAMA_NUM_CTX.",
            est_tokens, OLLAMA_NUM_CTX, QA_MAX_TOKENS)

    try:
        data = _post(url, payload, OLLAMA_QA_TIMEOUT)
    except OllamaError as exc:
        # Models without thinking support reject the `think` field outright.
        if OLLAMA_QA_THINK is False and "think" in str(exc).lower():
            payload.pop("think")
            data = _post(url, payload, OLLAMA_QA_TIMEOUT)
        else:
            raise

    if data.get("error"):
        raise OllamaError(f"Ollama error: {data['error']}")

    answer = ((data.get("message") or {}).get("content") or "")
    answer = _THINK_BLOCK.sub("", answer).strip()
    if not answer:
        raise OllamaError(
            f"Ollama model '{OLLAMA_QA_MODEL}' returned an empty answer "
            f"(done_reason={data.get('done_reason')!r}). If the model was "
            f"thinking, its output budget of {QA_MAX_TOKENS} tokens may have "
            f"been spent before the answer began."
        )
    return answer
