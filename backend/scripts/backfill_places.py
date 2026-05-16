#!/usr/bin/env python3
"""
backfill_places.py – Create `place` entities from already-extracted data.

Geographical places (cities, provinces, states, countries) became a first-class
entity type. New ingests extract them directly; this script backfills documents
that were ingested earlier — WITHOUT calling the Claude API. It only uses data
already in the database.

Three passes:

  Pass 1 — location fields (high confidence)
      Every document / group has an extracted `location` string. Each distinct
      one becomes a `place` entity, linked with role='location'. These strings
      form a gazetteer: the set of place names known to this archive.

  Pass 2 — gazetteer matches in text
      Scans each document's transcription, description, and tag names for
      whole-word occurrences of any gazetteer place. Matches are linked with
      role='mentioned'. Only known places are matched, so there is no guessing.

  Pass 3 — reclassification candidates (report only)
      Lists existing 'unknown' / 'institution' entities whose name matches a
      gazetteer place. These are NOT changed — review and retype them in the
      Entities UI if appropriate.

By default this is a DRY RUN. Pass --apply to write passes 1 and 2.

Usage (from the project root):
    .venv/bin/python -m backend.scripts.backfill_places          # dry-run
    .venv/bin/python -m backend.scripts.backfill_places --apply  # write

TAKE A BACKUP FIRST:
    cp data/provenance.db data/provenance.db.bak
"""

import argparse
import re
import sys
from pathlib import Path

# Make `import config`, `from modules...` work when run via -m or directly.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # backend/

from modules.db import (  # noqa: E402
    get_db, upsert_entity, normalize_entity_name, record_audit,
)

# A `location` field is messy free text — e.g. "China (Shanghai, Tientsin);
# Kansas City; Cologne" or "Kansas City, Missouri, USA". Each place entity must
# name exactly ONE place, so location strings are split into atomic fragments.
_PLACE_SPLIT_RE = re.compile(r"[,;/()&]|\band\b", re.IGNORECASE)

# Fragments that are not real single places — dropped after splitting.
_PLACE_STOP = {
    "", "usa", "u.s.a.", "u.s.", "us", "united states", "unknown", "n/a", "na",
    "none", "not specified", "various", "region", "area", "etc", "the",
    "city", "east", "west", "north", "south", "northeast", "northwest",
    "southeast", "southwest", "central", "written from", "written", "sent to",
    "sent from", "sent", "from", "to",
}
_QUALIFIER_PREFIX = re.compile(r"^(likely|probably|possibly|near|in|the)\s+", re.IGNORECASE)
_QUALIFIER_SUFFIX = re.compile(r"\s+(region|area)$", re.IGNORECASE)

# Fragments containing these words are institutions, not places.
_INSTITUTION_WORDS = re.compile(
    r"\b(university|museum|gallery|institute|college|library|school|society|"
    r"company|co\.|corp|inc|ltd|department|collection)\b", re.IGNORECASE)


def _split_places(location: str) -> list:
    """Split a free-text location string into atomic single-place names.

    A `location` field is unstructured ("Cambridge, MA (Harvard University)"),
    so this is best-effort: it splits on separators and drops fragments that
    are clearly not a single place (institution names, bare abbreviations,
    qualifier words). It cannot match what the LLM does on a fresh extraction.
    """
    out = []
    for frag in _PLACE_SPLIT_RE.split(location or ""):
        frag = frag.strip().strip(".").strip()
        frag = _QUALIFIER_PREFIX.sub("", frag)
        frag = _QUALIFIER_SUFFIX.sub("", frag).strip()
        norm = normalize_entity_name(frag)
        if len(norm) < 3 or norm in _PLACE_STOP:
            continue                              # too short / known noise
        if frag.isupper() and len(frag) <= 3:
            continue                              # bare abbreviation (e.g. "MA")
        if _INSTITUTION_WORDS.search(frag):
            continue                              # institution, not a place
        out.append(frag)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="write changes (default: dry run)")
    args = parser.parse_args()

    with get_db() as conn:
        # ── Pass 1: location fields → place entities ──────────────────────────
        # gazetteer maps normalized place name -> display name
        # gaz_count maps normalized place name -> how many records used it as a
        # location (a recurring place is far more trustworthy than a one-off).
        gazetteer = {}
        gaz_count = {}
        pass1_links = 0   # (record, place) links created

        # (table, id_col, link_table, link_id_col, rows)
        sources = [
            ("documents", conn.execute(
                "SELECT id, location FROM documents WHERE is_trashed=0"
            ).fetchall(), "document_entities", "document_id", "document"),
            ("document_groups", conn.execute(
                "SELECT id, location FROM document_groups WHERE is_trashed=0"
            ).fetchall(), "group_entities", "group_id", "group"),
        ]

        for _table, rows, link_table, link_col, audit_type in sources:
            for r in rows:
                for place in _split_places(r["location"]):
                    norm = normalize_entity_name(place)
                    gazetteer.setdefault(norm, place)
                    gaz_count[norm] = gaz_count.get(norm, 0) + 1
                    if not args.apply:
                        pass1_links += 1
                        continue
                    place_id = upsert_entity(conn, place, "place")
                    if not place_id:
                        continue
                    cur = conn.execute(
                        f"""INSERT OR IGNORE INTO {link_table}
                            ({link_col}, entity_id, role) VALUES (?,?,?)""",
                        (r["id"], place_id, "location"),
                    )
                    if cur.rowcount > 0:
                        pass1_links += 1
                        record_audit(conn, entity_type=audit_type, entity_id=r["id"],
                                     action="add_entity", actor="system", new=place)

        # ── Pass 2: gazetteer matches in transcription / description / tags ───
        # Only match places that appeared as a location in at least two
        # records. A one-off location is too shaky to fuzzy-match across the
        # whole corpus — this trades some recall for much higher precision.
        # Longest names first so "New York" wins over a bare "York".
        names = sorted((gazetteer[n] for n, c in gaz_count.items() if c >= 2),
                       key=len, reverse=True)
        patterns = [(n, re.compile(r"\b" + re.escape(n) + r"\b", re.IGNORECASE))
                    for n in names]
        pass2_links = 0

        # documents
        doc_rows = conn.execute(
            "SELECT id, transcription, description FROM documents WHERE is_trashed=0"
        ).fetchall()
        for r in doc_rows:
            haystack = " ".join(filter(None, [
                r["transcription"], r["description"],
                " ".join(t["name"] for t in conn.execute(
                    """SELECT t.name FROM document_tags dt
                       JOIN tags t ON t.id = dt.tag_id WHERE dt.document_id=?""",
                    (r["id"],)).fetchall()),
            ]))
            if not haystack:
                continue
            # Entities already linked to this document (any role) — don't relink.
            linked = {row["entity_id"] for row in conn.execute(
                "SELECT entity_id FROM document_entities WHERE document_id=?",
                (r["id"],)).fetchall()}
            for name, pat in patterns:
                if not pat.search(haystack):
                    continue
                if not args.apply:
                    pass2_links += 1
                    continue
                place_id = upsert_entity(conn, name, "place")
                if not place_id or place_id in linked:
                    continue
                cur = conn.execute(
                    """INSERT OR IGNORE INTO document_entities
                       (document_id, entity_id, role) VALUES (?,?,?)""",
                    (r["id"], place_id, "mentioned"),
                )
                if cur.rowcount > 0:
                    pass2_links += 1
                    linked.add(place_id)
                    record_audit(conn, entity_type="document", entity_id=r["id"],
                                 action="add_entity", actor="system", new=name)

        # groups
        grp_rows = conn.execute(
            "SELECT id, transcription, description FROM document_groups WHERE is_trashed=0"
        ).fetchall()
        for r in grp_rows:
            haystack = " ".join(filter(None, [
                r["transcription"], r["description"],
                " ".join(t["name"] for t in conn.execute(
                    """SELECT t.name FROM group_tags gt
                       JOIN tags t ON t.id = gt.tag_id WHERE gt.group_id=?""",
                    (r["id"],)).fetchall()),
            ]))
            if not haystack:
                continue
            linked = {row["entity_id"] for row in conn.execute(
                "SELECT entity_id FROM group_entities WHERE group_id=?",
                (r["id"],)).fetchall()}
            for name, pat in patterns:
                if not pat.search(haystack):
                    continue
                if not args.apply:
                    pass2_links += 1
                    continue
                place_id = upsert_entity(conn, name, "place")
                if not place_id or place_id in linked:
                    continue
                cur = conn.execute(
                    """INSERT OR IGNORE INTO group_entities
                       (group_id, entity_id, role) VALUES (?,?,?)""",
                    (r["id"], place_id, "mentioned"),
                )
                if cur.rowcount > 0:
                    pass2_links += 1
                    linked.add(place_id)
                    record_audit(conn, entity_type="group", entity_id=r["id"],
                                 action="add_entity", actor="system", new=name)

        # ── Pass 3: reclassification candidates (report only) ─────────────────
        gaz_norms = set(gazetteer.keys())
        candidates = [
            row for row in conn.execute(
                "SELECT id, name, type, normalized_name FROM entities "
                "WHERE type IN ('unknown','institution')"
            ).fetchall()
            if row["normalized_name"] in gaz_norms
        ]

        # ── Summary ───────────────────────────────────────────────────────────
        print(f"Gazetteer: {len(gazetteer)} distinct places from location fields")
        mode = "created" if args.apply else "would be created"
        print(f"Pass 1 (location fields): {pass1_links} place links {mode}")
        print(f"Pass 2 (text/tag matches): {pass2_links} place links {mode}")
        print(f"Pass 3 (reclassify candidates): {len(candidates)} existing "
              f"entities look like places — review in the Entities UI:")
        for c in candidates[:30]:
            print(f"  id={c['id']:<6} [{c['type']}] {c['name']}")
        if len(candidates) > 30:
            print(f"  … and {len(candidates) - 30} more")

        if not args.apply:
            print("\nDry-run only. Re-run with --apply to create passes 1 & 2.")


if __name__ == "__main__":
    main()
