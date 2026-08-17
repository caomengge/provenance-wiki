"""
representations.py – Retrieval representations for the production index.

Every retrieval unit (a standalone document or a multi-page document group;
grouped pages are never retrieved individually) yields two kinds of
retrievable representations, kept methodologically distinct:

  representation_type = 'transcription'
      Chunks of the primary-source transcription, produced by a
      deterministic, documented chunking algorithm (CHUNKING_VERSION below).

  representation_type = 'generated'
      One deterministic text composed from AI-extracted structured fields:
      title, date, location, description, entities (+role/context),
      transactions, tags, source archive. Researcher annotations are
      deliberately EXCLUDED (they would leak human interpretation into
      retrieval). This mirrors the experiment's build_modelled_text().

The two are never merged invisibly: representation_type is stored on every
chunk row and preserved through retrieval, RAG context, and citations, so
retrieval over primary sources vs. AI-generated representations can be
compared experimentally.

Chunking strategy — CHUNKING_VERSION 'v1'
-----------------------------------------
Config: CHUNK_MAX_CHARS (default 1500), CHUNK_OVERLAP_CHARS (default 200).

1. Normalise line endings; split the transcription on blank lines into
   paragraphs (natural units in correspondence and archival documents).
2. Greedily pack consecutive paragraphs into a chunk of at most
   CHUNK_MAX_CHARS characters.
3. A single paragraph longer than the cap is split at sentence boundaries
   (. ! ? and CJK 。！？), never mid-word; a sentence longer than the cap
   is hard-wrapped at the last space before the cap.
4. Each chunk after the first is prefixed with the last CHUNK_OVERLAP_CHARS
   characters of the previous chunk ("… " marker) so clauses spanning a
   boundary stay retrievable.
5. Deterministic: identical text + identical constants → identical chunks.
   The constants are part of the chunking contract; changing them (or this
   algorithm) requires bumping CHUNKING_VERSION / INDEX_SCHEMA_VERSION,
   which the incremental indexer detects and re-embeds.
"""

import hashlib
import logging
import re

logger = logging.getLogger(__name__)

CHUNKING_VERSION = "v1"

TRANSCRIPTION = "transcription"
GENERATED = "generated"

_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")


def sha256_text(text: str) -> str:
    """Stable content hash of the exact text that will be embedded."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ── Chunking ──────────────────────────────────────────────────────────────────

def _split_long_paragraph(par: str, max_chars: int) -> list[str]:
    """Split an oversized paragraph at sentence boundaries, then spaces."""
    sentences = _SENTENCE_END.split(par)
    pieces, buf = [], ""
    for s in sentences:
        while len(s) > max_chars:  # pathological single sentence
            cut = s.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            head, s = s[:cut].rstrip(), s[cut:].lstrip()
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.append(head)
        if not buf:
            buf = s
        elif len(buf) + 1 + len(s) <= max_chars:
            buf = f"{buf} {s}"
        else:
            pieces.append(buf)
            buf = s
    if buf:
        pieces.append(buf)
    return [p for p in pieces if p.strip()]


def chunk_transcription(text: str,
                        max_chars: int | None = None,
                        overlap_chars: int | None = None) -> list[str]:
    """
    Deterministically chunk a transcription (CHUNKING_VERSION 'v1').
    Returns a list of chunk texts (possibly empty when there is no text).
    """
    from config import CHUNK_MAX_CHARS, CHUNK_OVERLAP_CHARS
    max_chars = max_chars or CHUNK_MAX_CHARS
    overlap_chars = overlap_chars if overlap_chars is not None else CHUNK_OVERLAP_CHARS

    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    # Pack paragraphs greedily.
    units: list[str] = []
    for par in paragraphs:
        if len(par) > max_chars:
            units.extend(_split_long_paragraph(par, max_chars))
        else:
            units.append(par)

    chunks, buf = [], ""
    for unit in units:
        if not buf:
            buf = unit
        elif len(buf) + 2 + len(unit) <= max_chars:
            buf = f"{buf}\n\n{unit}"
        else:
            chunks.append(buf)
            buf = unit
    if buf:
        chunks.append(buf)

    # Prefix overlap from the previous chunk (after packing, so packing is
    # independent of the overlap setting).
    if overlap_chars > 0 and len(chunks) > 1:
        out = [chunks[0]]
        for prev, cur in zip(chunks, chunks[1:]):
            tail = prev[-overlap_chars:]
            cut = tail.find(" ")
            if 0 < cut < len(tail) - 1:
                tail = tail[cut + 1:]          # start overlap on a word boundary
            out.append(f"… {tail}\n\n{cur}")
        chunks = out

    return chunks


def chunk_generated(text: str, max_chars: int | None = None) -> list[str]:
    """
    Split an oversized generated representation line-wise (each line is a
    self-contained field: Title/Entity/Transaction/Tags…), packing
    consecutive lines up to max_chars. Deterministic; no overlap — lines do
    not continue across boundaries. The header lines (everything before the
    first Entity/Transaction line) are repeated on every chunk so each chunk
    stays self-describing.
    """
    from config import GENERATED_MAX_CHARS
    max_chars = max_chars or GENERATED_MAX_CHARS

    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    lines = text.split("\n")
    header, body = [], []
    for i, ln in enumerate(lines):
        if ln.startswith(("Entity:", "Transaction:", "Tags:")):
            body = lines[i:]
            break
        header.append(ln)
    else:
        body = []
    head_txt = "\n".join(header).strip()

    chunks, buf = [], head_txt
    for ln in body:
        if len(buf) + 1 + len(ln) <= max_chars or not buf:
            buf = f"{buf}\n{ln}" if buf else ln
        else:
            chunks.append(buf)
            buf = f"{head_txt}\n{ln}" if head_txt else ln
    if buf:
        chunks.append(buf)
    return chunks


# ── Retrieval units ───────────────────────────────────────────────────────────

def get_retrieval_units(conn) -> list[dict]:
    """
    Every retrieval unit with metadata and its primary-source transcription.

    Units = standalone documents (group_id IS NULL, not trashed)
          + document groups (not trashed).
    Group transcription = group.transcription if present, otherwise page
    transcriptions concatenated in page order.

    Each dict: {unit_id, record_type, title, source_archive, date_display,
                transcription}
    """
    units = []

    docs = conn.execute(
        """SELECT id, title, source_archive, transcription,
                  date_depicted, date_range_start
           FROM documents
           WHERE group_id IS NULL AND is_trashed = 0
           ORDER BY id"""
    ).fetchall()
    for d in docs:
        units.append({
            "unit_id": d["id"],
            "record_type": "document",
            "title": d["title"],
            "source_archive": d["source_archive"],
            "date_display": d["date_depicted"] or d["date_range_start"],
            "transcription": (d["transcription"] or "").strip(),
        })

    groups = conn.execute(
        """SELECT id, title, source_archive, transcription,
                  date_depicted, date_range_start
           FROM document_groups
           WHERE is_trashed = 0
           ORDER BY id"""
    ).fetchall()
    for g in groups:
        units.append({
            "unit_id": g["id"],
            "record_type": "group",
            "title": g["title"],
            "source_archive": g["source_archive"],
            "date_display": g["date_depicted"] or g["date_range_start"],
            "transcription": get_unit_transcription(conn, g["id"], "group"),
        })

    return units


def get_unit_transcription(conn, unit_id: int, record_type: str) -> str:
    """Primary-source transcription of one unit (group pages concatenated)."""
    if record_type == "document":
        row = conn.execute("SELECT transcription FROM documents WHERE id=?",
                           (unit_id,)).fetchone()
        return ((row["transcription"] if row else "") or "").strip()

    row = conn.execute("SELECT transcription FROM document_groups WHERE id=?",
                       (unit_id,)).fetchone()
    text = ((row["transcription"] if row else "") or "").strip()
    if not text:
        pages = conn.execute(
            """SELECT transcription FROM documents
               WHERE group_id = ? AND is_trashed = 0
               ORDER BY COALESCE(page_number, id)""",
            (unit_id,),
        ).fetchall()
        text = "\n\n".join(
            (p["transcription"] or "").strip()
            for p in pages if (p["transcription"] or "").strip()
        )
    return text


# ── Generated representation ──────────────────────────────────────────────────

def build_generated_text(conn, unit_id: int, record_type: str) -> str:
    """
    Deterministic AI-generated representation of a unit, composed from
    machine-extracted structured fields. Fixed field order; entities, tags
    and transactions ordered by stable sort keys, so the text is exactly
    reproducible from the database. Annotations are excluded by design.
    """
    if record_type == "document":
        row = conn.execute("SELECT * FROM documents WHERE id=?", (unit_id,)).fetchone()
        ent_sql = """SELECT e.name, e.type, de.role, de.context
                     FROM document_entities de JOIN entities e ON e.id = de.entity_id
                     WHERE de.document_id=? ORDER BY e.name, de.role"""
        txn_sql = """SELECT seller, buyer, date, price, currency, auction_house,
                            lot_number, location, notes
                     FROM transactions WHERE document_id=? ORDER BY date, id"""
        tag_sql = """SELECT t.name FROM document_tags dt JOIN tags t ON t.id = dt.tag_id
                     WHERE dt.document_id=? ORDER BY t.name"""
    elif record_type == "group":
        row = conn.execute("SELECT * FROM document_groups WHERE id=?", (unit_id,)).fetchone()
        ent_sql = """SELECT e.name, e.type, ge.role, ge.context
                     FROM group_entities ge JOIN entities e ON e.id = ge.entity_id
                     WHERE ge.group_id=? ORDER BY e.name, ge.role"""
        txn_sql = """SELECT seller, buyer, date, price, currency, auction_house,
                            lot_number, location, notes
                     FROM group_transactions WHERE group_id=? ORDER BY date, id"""
        tag_sql = """SELECT t.name FROM group_tags gt JOIN tags t ON t.id = gt.tag_id
                     WHERE gt.group_id=? ORDER BY t.name"""
    else:
        raise ValueError(f"Unknown record_type: {record_type}")

    if row is None:
        raise ValueError(f"No {record_type} with id {unit_id}")

    parts = []
    if row["title"]:
        parts.append(f"Title: {row['title']}")
    if row["date_depicted"]:
        parts.append(f"Date: {row['date_depicted']}")
    if row["location"]:
        parts.append(f"Location: {row['location']}")
    if row["source_archive"]:
        parts.append(f"Source archive: {row['source_archive']}")
    if row["description"]:
        parts.append(f"Description: {row['description']}")

    for e in conn.execute(ent_sql, (unit_id,)).fetchall():
        line = f"Entity: {e['name']} ({e['type']})"
        if e["role"]:
            line += f", role: {e['role']}"
        if e["context"]:
            line += f", context: {e['context']}"
        parts.append(line)

    for t in conn.execute(txn_sql, (unit_id,)).fetchall():
        bits = ["Transaction:"]
        for field in ("seller", "buyer", "date", "price", "currency",
                      "auction_house", "lot_number", "location", "notes"):
            if t[field]:
                bits.append(f"{field}={t[field]}")
        if len(bits) > 1:
            parts.append(" ".join(bits))

    tags = [r["name"] for r in conn.execute(tag_sql, (unit_id,)).fetchall()]
    if tags:
        parts.append("Tags: " + ", ".join(tags))

    return "\n".join(parts)


def build_representations(conn, unit_id: int, record_type: str,
                          title=None, source_archive=None, date_display=None) -> list[dict]:
    """
    Build every retrievable representation for one unit.

    Returns a list of dicts ready for the indexer:
      {representation_type, chunk_index, chunk_count, text, content_sha256,
       title, source_archive, date_display}
    Metadata (title/archive/date) is looked up when not supplied.
    """
    if title is None or source_archive is None or date_display is None:
        table = "documents" if record_type == "document" else "document_groups"
        row = conn.execute(
            f"""SELECT title, source_archive, date_depicted, date_range_start
                FROM {table} WHERE id=?""", (unit_id,)).fetchone()
        if row is not None:
            title = title if title is not None else row["title"]
            source_archive = source_archive if source_archive is not None else row["source_archive"]
            date_display = date_display if date_display is not None else (
                row["date_depicted"] or row["date_range_start"])

    reps = []

    t_chunks = chunk_transcription(get_unit_transcription(conn, unit_id, record_type))
    for i, chunk in enumerate(t_chunks):
        reps.append({
            "representation_type": TRANSCRIPTION,
            "chunk_index": i,
            "chunk_count": len(t_chunks),
            "text": chunk,
            "content_sha256": sha256_text(chunk),
        })

    g_chunks = chunk_generated(build_generated_text(conn, unit_id, record_type))
    for i, chunk in enumerate(g_chunks):
        reps.append({
            "representation_type": GENERATED,
            "chunk_index": i,
            "chunk_count": len(g_chunks),
            "text": chunk,
            "content_sha256": sha256_text(chunk),
        })

    for r in reps:
        r.update({"title": title, "source_archive": source_archive,
                  "date_display": date_display})
    return reps
