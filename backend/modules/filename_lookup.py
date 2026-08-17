"""
filename_lookup.py – Deterministic filename lookup for the UI.

This is a METADATA UTILITY, not retrieval. It exists so a researcher who
knows a photograph's file name (`IMG_4683.JPG`, `IMG_4683`, or just `4683`)
can jump straight to the corresponding document record.

Deliberate separation of concerns:
  • It performs plain SQL matching on documents.filename — no embeddings,
    no BM25, no rank fusion, no retrieval index. The chunk index,
    hybrid/semantic ranking, and the A/B/C/D experimental conditions are
    completely untouched and unaware of it.
  • Its results are returned in their own `filename_matches` block, never
    merged into (or reordered within) the relevance-ranked `results`, so a
    filename hit can never inflate or distort a retrieval ranking.
  • Matching is exact-first and fully deterministic — same query, same
    database, same order, every time. No scoring heuristics.

Match precedence (best first), each group ordered by filename then id:
  1. exact      – the query equals the whole filename (case-insensitive)
  2. stem       – the query equals the filename without its extension
  3. number     – an all-digit query equals a complete number in the
                  filename (so `4683` prefers IMG_4683.JPG over IMG_46830.JPG)
  4. prefix     – the filename starts with the query
  5. substring  – the query appears anywhere in the filename
A document is reported once, under its strongest match type.

Trashed documents are excluded. Pages that belong to a multi-page group ARE
included (the researcher is looking for a physical photograph), with
`group_id` exposed so the UI can point at the group the page now lives in.
"""

import logging
import re

logger = logging.getLogger(__name__)

MATCH_TYPES = ("exact", "stem", "number", "prefix", "substring")

DEFAULT_LIMIT = 25

# A query is treated as a filename lookup only when it is a single token
# that plausibly names an image file: an image extension, an IMG_1234-style
# name, or a bare run of digits (photo counters). Ordinary prose queries
# never trigger the utility.
_IMAGE_EXT = r"\.(?:jpe?g|png|tiff?|webp|heic)$"
_FILENAME_QUERY = re.compile(
    rf"^(?:.+{_IMAGE_EXT}|[A-Za-z]{{2,10}}[ _-]?\d{{2,}}|\d{{2,}})$",
    re.IGNORECASE,
)


def looks_like_filename_query(query: str) -> bool:
    """True when the query should also be looked up as a file name."""
    q = (query or "").strip()
    if not q or len(q) > 128 or any(c.isspace() for c in q):
        return False
    return bool(_FILENAME_QUERY.match(q))


def _strip_extension(name: str) -> str:
    return re.sub(_IMAGE_EXT, "", name or "", flags=re.IGNORECASE)


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so a literal % or _ cannot widen the match."""
    return (value.replace("\\", "\\\\")
                 .replace("%", "\\%")
                 .replace("_", "\\_"))


def find_by_filename(conn, query: str, limit: int = DEFAULT_LIMIT,
                     include_trashed: bool = False) -> list[dict]:
    """
    Look up documents whose image file name matches `query`.

    Returns a list of dicts (best match first):
      {id, filename, title, source_archive, date_depicted, group_id,
       page_number, is_trashed, filename_match: 'exact'|'stem'|'prefix'|'substring'}
    """
    q = (query or "").strip()
    if not q:
        return []

    like = _escape_like(q)
    stem = _escape_like(_strip_extension(q))
    trash_sql = "" if include_trashed else " AND is_trashed = 0"

    rows = conn.execute(
        f"""SELECT id, filename, title, source_archive, date_depicted,
                   date_range_start, group_id, page_number, is_trashed
            FROM documents
            WHERE filename LIKE ? ESCAPE '\\'{trash_sql}
            ORDER BY filename COLLATE NOCASE, id""",
        (f"%{like}%",),
    ).fetchall()

    lowered_q, lowered_stem = q.lower(), _strip_extension(q).lower()
    buckets: dict[str, list[dict]] = {t: [] for t in MATCH_TYPES}

    for row in rows:
        name = row["filename"] or ""
        low = name.lower()
        if low == lowered_q:
            kind = "exact"
        elif _strip_extension(low) == lowered_stem:
            kind = "stem"
        elif q.isdigit() and q in re.findall(r"\d+", name):
            kind = "number"
        elif low.startswith(lowered_q):
            kind = "prefix"
        else:
            kind = "substring"

        d = dict(row)
        d["date"] = d.get("date_depicted") or d.get("date_range_start")
        d["filename_match"] = kind
        d["record_type"] = "document"
        buckets[kind].append(d)

    ordered = [d for kind in MATCH_TYPES for d in buckets[kind]]
    return ordered[:limit]


def filename_match_block(conn, query: str, limit: int = DEFAULT_LIMIT) -> dict:
    """
    The `filename_matches` block attached to search responses.

    Always returns the same shape so the frontend can render it
    unconditionally: {applied, query, matches, total}. `applied` is False
    for ordinary prose queries, where no filename lookup is performed.
    """
    if not looks_like_filename_query(query):
        return {"applied": False, "query": query, "matches": [], "total": 0}

    matches = find_by_filename(conn, query, limit=limit)
    return {
        "applied": True,
        "query": query.strip(),
        "matches": matches,
        "total": len(matches),
    }
