"""
embeddings.py – Configurable neural embedding backend (production + experiment).

Interface:  backend = get_embedding_backend()
            vectors = backend.embed(["text 1", "text 2"])                  # documents
            q_vec   = backend.embed_one("query", input_type="query")      # queries

Providers (selected via env var EMBEDDING_PROVIDER or explicit config):

  voyage  (default)
      Voyage AI embeddings API. Requires VOYAGE_API_KEY in .env.
      Default model: voyage-4 (override with EMBEDDING_MODEL).
      Uses asymmetric query/document encoding via input_type.
      Install: pip install voyageai

  sentence_transformers
      Local model via the `sentence-transformers` package.
      Default model: BAAI/bge-m3 (multilingual, handles English + Chinese).
      Override with EMBEDDING_MODEL.
      Install: pip install sentence-transformers

  openai_compatible
      Any OpenAI-compatible /v1/embeddings endpoint (OpenAI, Ollama, LM Studio,
      vLLM...). Requires EMBEDDINGS_API_URL (+ EMBEDDINGS_API_KEY if needed)
      and EMBEDDING_MODEL. Uses only `requests`.

Methodological guarantees:
  - NO fallback between providers: if the configured backend fails,
    EmbeddingError is raised. Callers must surface the failure, never
    silently switch retrieval methods.
  - This module never uses the legacy hashed bag-of-words function in
    extractor.py, and never asks an LLM to invent pseudo-embeddings.
  - model_name/provider are exposed so every stored vector and every run
    can record exactly which model produced it.
"""

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "voyage"
DEFAULT_MODELS = {
    "sentence_transformers": "BAAI/bge-m3",
    "voyage": "voyage-4",
    "openai_compatible": None,  # must be set explicitly
}


class EmbeddingError(RuntimeError):
    """Raised when the configured embedding backend cannot produce vectors."""


class EmbeddingBackend:
    provider: str = "base"
    model_name: str = ""

    def embed(self, texts: list[str], input_type: str | None = None) -> list[list[float]]:
        """
        Embed a batch of texts. input_type is 'document' (default when None)
        or 'query'; providers that support asymmetric encoding (Voyage) use
        it, others ignore it.
        """
        raise NotImplementedError

    def embed_one(self, text: str, input_type: str | None = None) -> list[float]:
        try:
            return self.embed([text], input_type=input_type)[0]
        except TypeError:
            # Backwards compatibility with backends implementing embed(texts)
            # without the input_type parameter.
            return self.embed([text])[0]


class SentenceTransformersBackend(EmbeddingBackend):
    provider = "sentence_transformers"

    def __init__(self, model_name: str):
        self.model_name = model_name
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise EmbeddingError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            ) from e
        try:
            self._model = SentenceTransformer(model_name)
        except Exception as e:
            raise EmbeddingError(f"Could not load embedding model {model_name!r}: {e}") from e

    def embed(self, texts: list[str], input_type: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        try:
            vecs = self._model.encode(texts, normalize_embeddings=True,
                                      show_progress_bar=False)
            return [v.tolist() for v in vecs]
        except Exception as e:
            raise EmbeddingError(f"Embedding failed ({self.model_name}): {e}") from e


class VoyageBackend(EmbeddingBackend):
    provider = "voyage"

    def __init__(self, model_name: str, api_key: str | None = None):
        self.model_name = model_name
        try:
            import voyageai
        except ImportError as e:
            raise EmbeddingError("voyageai is not installed. Run: pip install voyageai") from e
        key = api_key or os.getenv("VOYAGE_API_KEY")
        if not key:
            raise EmbeddingError("VOYAGE_API_KEY is not set")
        self._client = voyageai.Client(api_key=key)

    def embed(self, texts: list[str], input_type: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        try:
            resp = self._client.embed(texts, model=self.model_name,
                                      input_type=input_type or "document")
            return resp.embeddings
        except Exception as e:
            raise EmbeddingError(f"Voyage embedding failed ({self.model_name}): {e}") from e


class OpenAICompatibleBackend(EmbeddingBackend):
    provider = "openai_compatible"

    def __init__(self, model_name: str, api_url: str | None = None,
                 api_key: str | None = None):
        if not model_name:
            raise EmbeddingError("EMBEDDING_MODEL must be set for openai_compatible provider")
        self.model_name = model_name
        self._url = (api_url or os.getenv("EMBEDDINGS_API_URL") or "").rstrip("/")
        if not self._url:
            raise EmbeddingError("EMBEDDINGS_API_URL is not set")
        self._key = api_key or os.getenv("EMBEDDINGS_API_KEY", "")

    def embed(self, texts: list[str], input_type: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        import requests
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        try:
            r = requests.post(f"{self._url}/embeddings",
                              json={"model": self.model_name, "input": texts},
                              headers=headers, timeout=120)
            r.raise_for_status()
            data = r.json()["data"]
            data.sort(key=lambda d: d["index"])
            return [d["embedding"] for d in data]
        except Exception as e:
            raise EmbeddingError(f"Embeddings API failed ({self.model_name}): {e}") from e


_backend_cache: dict[tuple, EmbeddingBackend] = {}


def get_embedding_backend(provider: str | None = None,
                          model_name: str | None = None) -> EmbeddingBackend:
    """
    Return a (cached) embedding backend for the given provider/model.
    Defaults come from env vars EMBEDDING_PROVIDER / EMBEDDING_MODEL.
    Raises EmbeddingError on any configuration or load failure — never
    falls back to a different provider.
    """
    provider = provider or os.getenv("EMBEDDING_PROVIDER", DEFAULT_PROVIDER)
    model_name = model_name or os.getenv("EMBEDDING_MODEL") or DEFAULT_MODELS.get(provider)

    key = (provider, model_name)
    if key in _backend_cache:
        return _backend_cache[key]

    if provider == "sentence_transformers":
        backend = SentenceTransformersBackend(model_name)
    elif provider == "voyage":
        backend = VoyageBackend(model_name)
    elif provider == "openai_compatible":
        backend = OpenAICompatibleBackend(model_name)
    else:
        raise EmbeddingError(f"Unknown embedding provider: {provider!r}")

    _backend_cache[key] = backend
    logger.info("Embedding backend ready: %s / %s", backend.provider, backend.model_name)
    return backend


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Plain cosine similarity; no external deps so it works everywhere."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
