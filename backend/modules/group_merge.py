"""
group_merge.py – Synthesize a group record from already-extracted child pages.

Avoids a second Claude vision call when grouping documents whose pages have
already been extracted at ingest time. Returns a dict in the same shape as
extractor.extract_from_images so callers can treat the two paths identically.
"""

from collections import Counter
from pathlib import Path


def merge_pages_data(conn, page_ids: list[int]) -> dict:
    """
    Build a synthesised group record from the already-extracted data of its
    child documents. Output mirrors the JSON Claude returns from a vision
    extraction so create_group / re_extract_group can consume it the same way.
    """
    if not page_ids:
        return {}

    placeholders = ",".join("?" * len(page_ids))

    pages = [dict(r) for r in conn.execute(
        f"""SELECT id, filename, title, date_depicted, date_range_start,
                   date_range_end, location, medium, dimensions, description,
                   language, transcription
            FROM documents WHERE id IN ({placeholders})
            ORDER BY filename""",
        page_ids,
    ).fetchall()]

    def first_non_null(field):
        return next((p[field] for p in pages if p.get(field)), None)

    def most_common(field):
        vals = [p[field] for p in pages if p.get(field)]
        return Counter(vals).most_common(1)[0][0] if vals else None

    def min_value(*fields):
        vals = []
        for p in pages:
            for f in fields:
                if p.get(f):
                    vals.append(p[f])
                    break
        return min(vals) if vals else None

    def max_value(*fields):
        vals = []
        for p in pages:
            for f in fields:
                if p.get(f):
                    vals.append(p[f])
                    break
        return max(vals) if vals else None

    # Concatenate transcriptions with [Page N] markers.
    transcription_parts = []
    for i, p in enumerate(pages, 1):
        t = p.get("transcription")
        if not t:
            continue
        if "[Page " in t:
            # Page already self-marked (e.g., from a prior multi-page extraction).
            transcription_parts.append(t)
        else:
            transcription_parts.append(f"[Page {i}]\n{t}")
    transcription = "\n\n".join(transcription_parts) or None

    # Entities: union across child docs, deduped by (entity_id, role).
    ent_rows = conn.execute(
        f"""SELECT DISTINCT de.entity_id, e.name, e.type, de.role, de.context
            FROM document_entities de
            JOIN entities e ON e.id = de.entity_id
            WHERE de.document_id IN ({placeholders})""",
        page_ids,
    ).fetchall()

    seen = {}
    for row in ent_rows:
        key = (row["entity_id"], row["role"])
        if key not in seen:
            seen[key] = {
                "name":    row["name"],
                "type":    row["type"],
                "role":    row["role"],
                "context": row["context"],
            }

    # Transactions: union across child docs (no dedup — user can prune).
    txn_rows = conn.execute(
        f"""SELECT seller, buyer, date, price, currency, auction_house,
                   lot_number, location, notes
            FROM transactions WHERE document_id IN ({placeholders})""",
        page_ids,
    ).fetchall()

    # Tags: distinct names.
    tag_rows = conn.execute(
        f"""SELECT DISTINCT t.name FROM document_tags dt
            JOIN tags t ON t.id = dt.tag_id
            WHERE dt.document_id IN ({placeholders})""",
        page_ids,
    ).fetchall()

    fallback_title = pages[0].get("title") or Path(pages[0]["filename"]).stem
    fallback_description = pages[0].get("description") or ""

    return {
        "title":            fallback_title,
        "description":      fallback_description,
        "date_depicted":    first_non_null("date_depicted"),
        "date_range_start": min_value("date_range_start", "date_depicted"),
        "date_range_end":   max_value("date_range_end",   "date_depicted"),
        "location":         most_common("location"),
        "medium":           most_common("medium"),
        "dimensions":       first_non_null("dimensions"),
        "language":         most_common("language"),
        "transcription":    transcription,
        "entities":         list(seen.values()),
        "transactions":     [dict(r) for r in txn_rows],
        "tags":             [r["name"] for r in tag_rows],
        "_pages_summary":   [
            {
                "page":        i,
                "title":       p.get("title"),
                "description": p.get("description"),
                "date":        p.get("date_depicted") or p.get("date_range_start"),
                "location":    p.get("location"),
                "medium":      p.get("medium"),
            }
            for i, p in enumerate(pages, 1)
        ],
    }
