"""
extractor.py – Claude Vision API integration for document extraction.

Sends a photo to Claude claude-sonnet-4-6 and returns structured JSON with:
  title, date, entities, transactions, tags, and provenance description.

Preserves Chinese and other non-ASCII text exactly.
Handles API errors with exponential back-off (3 retries).
"""

import base64
import io
import json
import time
import logging
from pathlib import Path

import anthropic
from PIL import Image

logger = logging.getLogger(__name__)

# ── Extraction prompt ─────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """You are an expert museum archivist and provenance researcher specializing in identifying and documenting the ownership history of artworks and cultural objects.

Analyze this document image carefully and extract ALL provenance-related information you can identify.

Return ONLY a valid JSON object with this exact structure (no other text before or after):
{
  "title": "a short descriptive title for this document (e.g., 'Sale receipt, Sotheby's London, 1938')",
  "date_depicted": "specific date shown or discussed in document, ISO format YYYY-MM-DD if clearly indicated; if no date is clearly present in the document use the string \"date unknown\"",
  "date_range_start": "earliest date this document could relate to, YYYY-MM-DD or null",
  "date_range_end": "latest date this document could relate to, YYYY-MM-DD or null",
  "location": "primary location mentioned or associated with this document",
  "medium": "type of document (e.g., 'auction catalog page', 'letter', 'receipt', 'photograph', 'bill of sale', 'exhibition label')",
  "dimensions": "physical dimensions if visible or mentioned, otherwise null",
  "description": "detailed description (3-5 sentences) explaining what this document shows and its provenance significance. Include ALL text visible in the image that is relevant.",
  "language": "primary language(s) of the document text (e.g., 'English', 'German', 'Chinese', 'French, German')",
  "entities": [
    {
      "name": "full name of person, object, or institution EXACTLY as written in the document",
      "type": "person OR object OR institution",
      "role": "role in this document (e.g., 'seller', 'buyer', 'previous owner', 'auction house', 'artist', 'artwork', 'dealer', 'museum', 'consignor')",
      "context": "one sentence explaining how this entity relates to the document"
    }
  ],
  "transactions": [
    {
      "seller": "seller name or null",
      "buyer": "buyer name or null",
      "date": "transaction date YYYY-MM-DD or null",
      "price": "numeric price as a number (no currency symbols) or null",
      "currency": "3-letter currency code: USD, GBP, DEM, FRF, CHF, EUR, JPY, etc., or null",
      "auction_house": "auction house name if this is an auction sale, otherwise null",
      "lot_number": "lot number as string if auction sale, otherwise null",
      "location": "city/country where the transaction occurred or null",
      "notes": "any additional details about the transaction or null"
    }
  ],
  "transcription": "faithful word-for-word transcription of ALL visible text in the document, preserving original spelling, punctuation, and line breaks. Non-English text (Chinese, German, Hebrew, French, etc.) must be reproduced exactly as written — do not translate. Use [illegible] for unreadable words. Use [image] for non-text elements. If the document has no readable text, set this to null.",
  "tags": ["relevant", "keyword", "tags", "describing", "this", "document"]
}

Critical rules:
1. Preserve ALL non-English text (Chinese, German, Hebrew, French, etc.) EXACTLY as written — do not translate
2. Extract EVERY person, institution, and artwork mentioned, even if mentioned briefly
3. If multiple transactions are described, list each as a separate object in the transactions array
4. tags should include: era/decade, document type, geographic region, transaction type, and any distinctive features
5. If a field has no applicable information, use null (not empty string)
6. Return ONLY valid JSON — no markdown, no explanation, no code fences"""


# ── Multi-page extraction prompt ─────────────────────────────────────────────

MULTI_PAGE_EXTRACTION_PROMPT = """You are an expert museum archivist and provenance researcher. You are examining a {n}-page document — each image is one page in sequence. Treat all pages as a single unified document and extract combined provenance information.

Return ONLY a valid JSON object with this exact structure (no other text before or after):
{{
  "title": "a short descriptive title for this document",
  "date_depicted": "specific date shown or discussed in document, ISO format YYYY-MM-DD if clearly indicated; if no date is clearly present in the document use the string \"date unknown\"",
  "date_range_start": "earliest date this document could relate to, YYYY-MM-DD or null",
  "date_range_end": "latest date this document could relate to, YYYY-MM-DD or null",
  "location": "primary location mentioned or associated with this document",
  "medium": "type of document (e.g., 'letter', 'auction catalog', 'receipt')",
  "dimensions": "physical dimensions if visible or mentioned, otherwise null",
  "description": "detailed description (3-5 sentences) explaining what this document shows across all pages and its provenance significance",
  "language": "primary language(s) of the document text",
  "transcription": "faithful word-for-word transcription of ALL visible text across all pages in sequence, preserving original spelling, punctuation, and line breaks. Mark page breaks with [Page N]. Non-English text must be reproduced exactly — do not translate. Use [illegible] for unreadable words.",
  "entities": [
    {{
      "name": "full name of person, object, or institution EXACTLY as written",
      "type": "person OR object OR institution",
      "role": "role in this document",
      "context": "one sentence explaining how this entity relates to the document"
    }}
  ],
  "transactions": [
    {{
      "seller": "seller name or null",
      "buyer": "buyer name or null",
      "date": "transaction date YYYY-MM-DD or null",
      "price": "numeric price as a number or null",
      "currency": "3-letter currency code or null",
      "auction_house": "auction house name or null",
      "lot_number": "lot number as string or null",
      "location": "city/country or null",
      "notes": "any additional details or null"
    }}
  ],
  "tags": ["relevant", "keyword", "tags"]
}}

Critical rules:
1. Preserve ALL non-English text EXACTLY as written — do not translate
2. Extract EVERY person, institution, and artwork mentioned across all pages
3. The transcription must cover all pages in order, separated by [Page N] markers
4. Return ONLY valid JSON — no markdown, no explanation, no code fences"""


# ── Main extraction function ──────────────────────────────────────────────────

def extract_from_image(image_path: Path, api_key: str) -> dict:
    """
    Send an image to Claude Vision and return the extracted provenance data.

    Args:
        image_path: Path to the image file.
        api_key:    Anthropic API key.

    Returns:
        Parsed dict from Claude's JSON response, or error dict on failure.

    Raises:
        ValueError: If the API returns non-JSON content after retries.
    """
    client = anthropic.Anthropic(api_key=api_key)

    # Read and resize if needed; _prepare_image re-encodes as JPEG when resizing
    original_bytes = image_path.read_bytes()
    image_bytes    = _prepare_image(image_path, original_bytes)
    image_b64      = base64.standard_b64encode(image_bytes).decode("utf-8")
    media_type     = "image/jpeg" if image_bytes is not original_bytes else _get_media_type(image_path)

    last_error = None
    for attempt in range(3):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type":       "image",
                                "source": {
                                    "type":       "base64",
                                    "media_type": media_type,
                                    "data":       image_b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": EXTRACTION_PROMPT,
                            },
                        ],
                    }
                ],
            )

            text = response.content[0].text.strip()

            # Strip markdown code fences if Claude added them
            if text.startswith("```"):
                text = text.split("```", 2)[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.rsplit("```", 1)[0].strip()

            data = json.loads(text)
            return data

        except json.JSONDecodeError as exc:
            logger.warning("Attempt %d: JSON parse error for %s: %s", attempt + 1, image_path.name, exc)
            last_error = exc
            time.sleep(2 ** attempt)

        except anthropic.APIError as exc:
            logger.warning("Attempt %d: API error for %s: %s", attempt + 1, image_path.name, exc)
            last_error = exc
            time.sleep(2 ** attempt)

    # All retries exhausted — return a minimal error record
    logger.error("Extraction failed for %s after 3 attempts: %s", image_path.name, last_error)
    return {
        "title":             image_path.stem,
        "description":       f"Extraction failed: {last_error}",
        "entities":          [],
        "transactions":      [],
        "tags":              ["extraction-failed"],
    }


def extract_from_images(image_paths: list[Path], api_key: str) -> dict:
    """
    Send multiple page images to Claude Vision in a single API call and return
    combined provenance extraction.  image_paths must be in desired page order.
    """
    client = anthropic.Anthropic(api_key=api_key)

    content = []
    for path in image_paths:
        raw = path.read_bytes()
        image_bytes = _prepare_image(path, raw)
        media_type  = "image/jpeg" if image_bytes is not raw else _get_media_type(path)
        content.append({
            "type": "image",
            "source": {
                "type":       "base64",
                "media_type": media_type,
                "data":       base64.standard_b64encode(image_bytes).decode("utf-8"),
            },
        })

    content.append({
        "type": "text",
        "text": MULTI_PAGE_EXTRACTION_PROMPT.format(n=len(image_paths)),
    })

    last_error = None
    for attempt in range(3):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                messages=[{"role": "user", "content": content}],
            )
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("```", 2)[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.rsplit("```", 1)[0].strip()
            return json.loads(text)

        except json.JSONDecodeError as exc:
            logger.warning("Attempt %d: JSON parse error (multi-page): %s", attempt + 1, exc)
            last_error = exc
            time.sleep(2 ** attempt)
        except anthropic.APIError as exc:
            logger.warning("Attempt %d: API error (multi-page): %s", attempt + 1, exc)
            last_error = exc
            time.sleep(2 ** attempt)

    logger.error("Multi-page extraction failed after 3 attempts: %s", last_error)
    return {
        "title":       f"Multi-page document ({len(image_paths)} pages)",
        "description": f"Extraction failed: {last_error}",
        "entities":    [],
        "transactions":[],
        "tags":        ["extraction-failed"],
    }


_MAX_BYTES = 3_932_160   # 3.75 MB — leaves headroom under Claude's 5 MB base64 limit
_MAX_DIM   = 8000        # Claude's max dimension in either direction



def _prepare_image(path: Path, raw: bytes) -> bytes:
    """
    Return image bytes ready for the API.
    If the image exceeds Claude's limits, resize it down and re-encode as JPEG.
    Otherwise return the same `raw` bytes object unchanged (caller uses identity
    check to determine the correct media type).
    """
    if len(raw) <= _MAX_BYTES:
        with Image.open(path) as img:
            if img.width <= _MAX_DIM and img.height <= _MAX_DIM:
                return raw  # already within limits

    with Image.open(path) as img:
        # Convert palette/RGBA modes so JPEG encoding works
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Downscale if either dimension exceeds the max
        if img.width > _MAX_DIM or img.height > _MAX_DIM:
            img.thumbnail((_MAX_DIM, _MAX_DIM), Image.LANCZOS)

        # Iteratively lower JPEG quality until the file fits
        quality = 92
        while quality >= 40:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= _MAX_BYTES:
                logger.info(
                    "Resized %s: %d bytes → %d bytes (quality=%d, size=%dx%d)",
                    path.name, len(raw), len(data), quality, img.width, img.height,
                )
                return data
            quality -= 10

        # Last resort: halve the resolution and try again
        img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75)
        data = buf.getvalue()
        logger.warning(
            "Aggressively resized %s to %dx%d (%d bytes)",
            path.name, img.width, img.height, len(data),
        )
        return data


def _get_media_type(path: Path) -> str:
    """Return MIME type string for the given image path."""
    ext = path.suffix.lower()
    return {
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png":  "image/png",
        ".gif":  "image/gif",
        ".webp": "image/webp",
        ".tif":  "image/tiff",
        ".tiff": "image/tiff",
    }.get(ext, "image/jpeg")


def generate_text_embedding(text: str, api_key: str) -> list[float] | None:
    """
    Generate a simple embedding vector for semantic search.

    Since Anthropic does not yet expose a standalone embeddings endpoint,
    we use a TF-IDF-inspired bag-of-words approach with 512-dim hashing.
    This gives useful semantic similarity without external dependencies.

    Returns a list of 512 floats, or None on error.
    """
    import hashlib, math
    if not text:
        return None
    try:
        # Tokenise (works for both Latin and CJK scripts)
        tokens = _tokenize(text)
        if not tokens:
            return None

        dim = 512
        vec = [0.0] * dim

        freq: dict[str, int] = {}
        for tok in tokens:
            freq[tok] = freq.get(tok, 0) + 1

        for tok, cnt in freq.items():
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx  = h % dim
            sign = 1 if (h >> 9) & 1 else -1
            tf   = 1 + math.log(cnt)
            vec[idx] += sign * tf

        # L2-normalise
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]
    except Exception as exc:
        logger.warning("Embedding generation failed: %s", exc)
        return None


def _tokenize(text: str) -> list[str]:
    """
    Very simple tokeniser:
    - For CJK: split into individual characters
    - For Latin/other: split on whitespace + punctuation, lowercase
    """
    import re
    tokens = []
    # CJK Unified Ideographs range
    cjk_re = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
    word_re = re.compile(r"[a-zA-Z0-9\u00C0-\u024F]+")

    for char in cjk_re.findall(text):
        tokens.append(char)

    for word in word_re.findall(text):
        tokens.append(word.lower())

    return tokens
