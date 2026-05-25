"""
relationships.py – Batch backfill of LLM-inferred person↔person relationships.

For each pair of person entities that co-occur in at least `min_cooccurrence`
documents or groups, Claude is asked to summarise their relationship as a
short verb phrase (e.g. "reports to", "purchases from"). Results land in
`entity_relationships` (directional, multiple verbs per pair allowed).

A single global `_RUN_STATE` dict tracks the most recent / currently-running
backfill so the UI can poll progress.
"""

import json
import logging
import threading
import time
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────

CONFIDENCE_FLOOR    = 0.4    # drop verbs Claude isn't reasonably sure about
MIN_COOCCURRENCE    = 2      # only run on pairs sharing ≥ N docs/groups
MAX_EVIDENCE_DOCS   = 8      # cap docs/groups fed to Claude per pair
MAX_EVIDENCE_CHARS  = 1200   # truncate each evidence description
MODEL               = "claude-sonnet-4-6"
MAX_RETRIES         = 3

# ── In-process status (single-process Flask) ─────────────────────────────────
# Tracks the currently-running or most-recently-completed refresh so the UI
# can poll progress without hitting the DB on every tick.

_RUN_LOCK  = threading.Lock()
_RUN_STATE: dict = {
    "running":       False,
    "run_id":        None,
    "total":         0,
    "processed":     0,
    "created":       0,
    "errors":        0,
    "started_at":    None,
    "finished_at":   None,
    "error_message": None,
}


def get_status() -> dict:
    """Snapshot of the current/last refresh run for the status endpoint."""
    with _RUN_LOCK:
        return dict(_RUN_STATE)


def is_running() -> bool:
    with _RUN_LOCK:
        return _RUN_STATE["running"]


# ── Public entry point ───────────────────────────────────────────────────────

def refresh_relationships(min_cooccurrence: int = MIN_COOCCURRENCE) -> dict:
    """Synchronously rebuild entity_relationships.

    Wipes the table and regenerates from scratch. Returns a summary dict
    matching the GET /api/relationships/status payload.
    """
    from config import ANTHROPIC_API_KEY
    from modules.db import get_db

    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    with _RUN_LOCK:
        if _RUN_STATE["running"]:
            raise RuntimeError("A relationship refresh is already running")
        _RUN_STATE.update({
            "running": True,
            "total": 0, "processed": 0, "created": 0, "errors": 0,
            "started_at": _now_iso(), "finished_at": None,
            "error_message": None,
        })

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    run_id = None

    try:
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO relationship_runs (status) VALUES ('running')"
            )
            run_id = cur.lastrowid
        with _RUN_LOCK:
            _RUN_STATE["run_id"] = run_id

        pairs = _collect_pairs(min_cooccurrence)
        with _RUN_LOCK:
            _RUN_STATE["total"] = len(pairs)
        logger.info("Relationship refresh: %d pairs to process", len(pairs))

        # Wipe prior results so removed/changed pairs don't linger.
        with get_db() as conn:
            conn.execute("DELETE FROM entity_relationships")

        for pair in pairs:
            try:
                created = _process_pair(client, pair)
                with _RUN_LOCK:
                    _RUN_STATE["processed"] += 1
                    _RUN_STATE["created"]   += created
            except Exception as exc:
                logger.exception("Pair %s/%s failed: %s",
                                 pair["a_id"], pair["b_id"], exc)
                with _RUN_LOCK:
                    _RUN_STATE["processed"] += 1
                    _RUN_STATE["errors"]    += 1

        with get_db() as conn:
            state = get_status()
            conn.execute(
                """UPDATE relationship_runs
                   SET finished_at = datetime('now'),
                       total = ?, processed = ?, errors = ?, status = 'done'
                   WHERE id = ?""",
                (state["total"], state["processed"], state["errors"], run_id),
            )

        with _RUN_LOCK:
            _RUN_STATE["running"]     = False
            _RUN_STATE["finished_at"] = _now_iso()

    except Exception as exc:
        logger.exception("Relationship refresh crashed: %s", exc)
        with _RUN_LOCK:
            _RUN_STATE["running"]       = False
            _RUN_STATE["finished_at"]   = _now_iso()
            _RUN_STATE["error_message"] = str(exc)
        if run_id is not None:
            try:
                from modules.db import get_db
                with get_db() as conn:
                    conn.execute(
                        """UPDATE relationship_runs
                           SET finished_at = datetime('now'),
                               status = 'crashed', error_message = ?
                           WHERE id = ?""",
                        (str(exc), run_id),
                    )
            except Exception:
                pass
        raise

    return get_status()


def refresh_in_background(min_cooccurrence: int = MIN_COOCCURRENCE) -> dict:
    """Kick off refresh on a daemon thread; return current status immediately."""
    if is_running():
        return get_status()

    def _worker():
        try:
            refresh_relationships(min_cooccurrence)
        except Exception:
            pass  # status already records the error

    t = threading.Thread(target=_worker, daemon=True, name="relationships-refresh")
    t.start()
    # brief pause so the started_at/run_id fields are visible to the first poll
    time.sleep(0.05)
    return get_status()


# ── Pair discovery ───────────────────────────────────────────────────────────

def _collect_pairs(min_cooccurrence: int) -> list[dict]:
    """Find every person↔person pair sharing >= N documents OR groups.

    Returns a list of {a_id, b_id, a_name, b_name, doc_ids, group_ids}.
    Ordered alphabetically by id (a_id < b_id) so we don't double-count.
    """
    from modules.db import get_db

    pairs: dict[tuple[int, int], dict] = {}

    with get_db() as conn:
        # Document-level co-occurrence
        rows = conn.execute("""
            SELECT a.entity_id AS ea, b.entity_id AS eb,
                   ea_e.name   AS ea_name, eb_e.name AS eb_name,
                   a.document_id AS doc_id
            FROM document_entities a
            JOIN document_entities b
              ON a.document_id = b.document_id AND a.entity_id < b.entity_id
            JOIN entities ea_e ON ea_e.id = a.entity_id
            JOIN entities eb_e ON eb_e.id = b.entity_id
            JOIN documents d   ON d.id    = a.document_id
            WHERE ea_e.type = 'person' AND eb_e.type = 'person'
              AND d.is_trashed = 0
        """).fetchall()
        for r in rows:
            key = (r["ea"], r["eb"])
            p = pairs.setdefault(key, {
                "a_id": r["ea"], "b_id": r["eb"],
                "a_name": r["ea_name"], "b_name": r["eb_name"],
                "doc_ids": [], "group_ids": [],
            })
            p["doc_ids"].append(r["doc_id"])

        # Group-level co-occurrence
        rows = conn.execute("""
            SELECT a.entity_id AS ea, b.entity_id AS eb,
                   ea_e.name   AS ea_name, eb_e.name AS eb_name,
                   a.group_id  AS group_id
            FROM group_entities a
            JOIN group_entities b
              ON a.group_id = b.group_id AND a.entity_id < b.entity_id
            JOIN entities ea_e ON ea_e.id = a.entity_id
            JOIN entities eb_e ON eb_e.id = b.entity_id
            JOIN document_groups g ON g.id = a.group_id
            WHERE ea_e.type = 'person' AND eb_e.type = 'person'
              AND g.is_trashed = 0
        """).fetchall()
        for r in rows:
            key = (r["ea"], r["eb"])
            p = pairs.setdefault(key, {
                "a_id": r["ea"], "b_id": r["eb"],
                "a_name": r["ea_name"], "b_name": r["eb_name"],
                "doc_ids": [], "group_ids": [],
            })
            p["group_ids"].append(r["group_id"])

    return [
        p for p in pairs.values()
        if len(p["doc_ids"]) + len(p["group_ids"]) >= min_cooccurrence
    ]


# ── Per-pair processing ──────────────────────────────────────────────────────

def _process_pair(client, pair: dict) -> int:
    """Build evidence, query Claude, persist any high-confidence verbs.
    Returns the number of relationship rows inserted for this pair."""
    from modules.db import get_db

    evidence = _gather_evidence(pair)
    if not evidence:
        return 0

    parsed = _ask_claude(client, pair, evidence)
    rels   = parsed.get("relationships", []) if parsed else []

    inserted = 0
    name_to_id = {
        pair["a_name"].lower().strip(): pair["a_id"],
        pair["b_name"].lower().strip(): pair["b_id"],
    }

    with get_db() as conn:
        for rel in rels:
            verb = (rel.get("verb") or "").strip().lower()
            conf = float(rel.get("confidence") or 0)
            subj = (rel.get("subject") or "").lower().strip()
            obj  = (rel.get("object")  or "").lower().strip()
            ev_ids = rel.get("evidence_doc_ids") or []

            if not verb or conf < CONFIDENCE_FLOOR:
                continue
            src_id = name_to_id.get(subj)
            tgt_id = name_to_id.get(obj)
            # Fall back to a partial-match if Claude paraphrased the name
            if src_id is None:
                src_id = _fuzzy_match(subj, name_to_id)
            if tgt_id is None:
                tgt_id = _fuzzy_match(obj, name_to_id)
            if not src_id or not tgt_id or src_id == tgt_id:
                continue

            try:
                conn.execute(
                    """INSERT OR REPLACE INTO entity_relationships
                       (source_entity_id, target_entity_id, verb,
                        confidence, evidence_doc_ids, generated_at)
                       VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                    (src_id, tgt_id, verb, conf, json.dumps(ev_ids)),
                )
                inserted += 1
            except Exception as exc:
                logger.warning("Insert failed for %s→%s '%s': %s",
                               src_id, tgt_id, verb, exc)

    return inserted


def _fuzzy_match(name: str, name_to_id: dict) -> Optional[int]:
    """If Claude returned a partial name, try a containment match."""
    if not name:
        return None
    for known, eid in name_to_id.items():
        if name in known or known in name:
            return eid
    return None


def _gather_evidence(pair: dict) -> list[dict]:
    """Pull a compact list of {kind, id, title, date, description, role_a, role_b}
    for every document / group the pair appears in together."""
    from modules.db import get_db

    docs = []
    with get_db() as conn:
        if pair["doc_ids"]:
            ids = list({d for d in pair["doc_ids"]})[:MAX_EVIDENCE_DOCS]
            ph = ",".join("?" * len(ids))
            rows = conn.execute(
                f"""SELECT d.id, d.title, d.date_depicted, d.description,
                           (SELECT role FROM document_entities
                            WHERE document_id = d.id AND entity_id = ?) AS role_a,
                           (SELECT role FROM document_entities
                            WHERE document_id = d.id AND entity_id = ?) AS role_b
                    FROM documents d WHERE d.id IN ({ph})""",
                [pair["a_id"], pair["b_id"]] + ids,
            ).fetchall()
            for r in rows:
                docs.append({
                    "kind": "document",
                    "id":   r["id"],
                    "title": r["title"] or f"Document #{r['id']}",
                    "date":  r["date_depicted"] or "",
                    "description": (r["description"] or "")[:MAX_EVIDENCE_CHARS],
                    "role_a": r["role_a"] or "",
                    "role_b": r["role_b"] or "",
                })

        if pair["group_ids"] and len(docs) < MAX_EVIDENCE_DOCS:
            remaining = MAX_EVIDENCE_DOCS - len(docs)
            ids = list({g for g in pair["group_ids"]})[:remaining]
            ph = ",".join("?" * len(ids))
            rows = conn.execute(
                f"""SELECT g.id, g.title, g.date_depicted, g.description,
                           (SELECT role FROM group_entities
                            WHERE group_id = g.id AND entity_id = ?) AS role_a,
                           (SELECT role FROM group_entities
                            WHERE group_id = g.id AND entity_id = ?) AS role_b
                    FROM document_groups g WHERE g.id IN ({ph})""",
                [pair["a_id"], pair["b_id"]] + ids,
            ).fetchall()
            for r in rows:
                docs.append({
                    "kind": "group",
                    "id":   r["id"],
                    "title": r["title"] or f"Group #{r['id']}",
                    "date":  r["date_depicted"] or "",
                    "description": (r["description"] or "")[:MAX_EVIDENCE_CHARS],
                    "role_a": r["role_a"] or "",
                    "role_b": r["role_b"] or "",
                })

    return docs


# ── Claude call ──────────────────────────────────────────────────────────────

PROMPT_TEMPLATE = """You are analysing historical archive documents to identify direct interpersonal relationships between two people.

PERSON A: {a_name}
PERSON B: {b_name}

These are the documents where both people appear:

{evidence}

Based ONLY on what the documents actually state or directly imply, identify any direct relationships between Person A and Person B. Express each as a short verb phrase from one person's point of view toward the other.

Examples of good verbs: "reports to", "purchases from", "sells to", "represents", "delegates to", "gifts to", "writes to", "consigns to", "employs", "advises".

Rules:
- A relationship MUST be supported by the evidence. Do not infer beyond it.
- Use simple, present-tense verb phrases (2–4 words).
- Direction matters: subject is the actor, object is the recipient. "A reports to B" ≠ "B reports to A".
- If A and B clearly act on each other differently, emit two separate relationships.
- Confidence is 0.0–1.0. Use ≥0.7 only when the evidence is explicit.
- evidence_doc_ids should be a subset of the {{document/group}} ids shown above that support this specific verb.
- If no clear relationship is supported, return {{"relationships": []}}.

Return ONLY valid JSON with this exact shape (no prose, no markdown fences):
{{
  "relationships": [
    {{
      "subject": "exact name of Person A or Person B",
      "verb":    "short verb phrase",
      "object":  "exact name of the other person",
      "confidence": 0.0,
      "evidence_doc_ids": [1, 2]
    }}
  ]
}}"""


def _ask_claude(client, pair: dict, evidence: list[dict]) -> Optional[dict]:
    evidence_text = "\n\n".join(
        f"[{e['kind']} #{e['id']}] {e['title']}"
        f"{(' — ' + e['date']) if e['date'] else ''}\n"
        f"  Role of {pair['a_name']}: {e['role_a'] or 'unspecified'}\n"
        f"  Role of {pair['b_name']}: {e['role_b'] or 'unspecified'}\n"
        f"  {e['description']}"
        for e in evidence
    )
    prompt = PROMPT_TEMPLATE.format(
        a_name=pair["a_name"], b_name=pair["b_name"], evidence=evidence_text
    )

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("```", 2)[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.rsplit("```", 1)[0].strip()
            return json.loads(text)
        except json.JSONDecodeError as exc:
            last_error = exc
            time.sleep(1.5 ** attempt)
        except anthropic.RateLimitError as exc:
            last_error = exc
            time.sleep(5 * (2 ** attempt))
        except anthropic.APIError as exc:
            last_error = exc
            time.sleep(2 ** attempt)

    logger.warning("Claude call failed for pair %s/%s: %s",
                   pair["a_id"], pair["b_id"], last_error)
    return None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat(timespec="seconds")
