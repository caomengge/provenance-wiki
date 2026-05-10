"""
Canonical medium taxonomy.

Documents and groups have a free-text `medium` (the original LLM phrasing,
preserved verbatim) and a canonical `medium_category` from this short enum,
used for the Gallery filter dropdown.

Add new categories here when the existing eight stop being expressive enough.
"""

CATEGORIES = [
    "letter",
    "telegram",
    "memo",
    "transaction record",
    "financial statement",
    "inventory",
    "artifact photograph",
    "other",
]

# Substring rules applied left-to-right; first match wins. Patterns are matched
# against the lower-cased raw medium string. Order matters: more specific
# patterns must come before broader ones (e.g. "letter with invoice" must
# resolve to letter, not to transaction record, so "letter" is checked first).
_RULES = [
    ("letter",              "letter"),
    ("telegram",            "telegram"),
    ("cable",               "telegram"),
    ("memo",                "memo"),
    ("memorandum",          "memo"),
    ("note",                "memo"),
    ("inventory",           "inventory"),
    ("financial statement", "financial statement"),
    ("account statement",   "financial statement"),
    ("expense account",     "financial statement"),
    ("expenditure report",  "financial statement"),
    ("invoice",             "transaction record"),
    ("receipt",             "transaction record"),
    ("bill of sale",        "transaction record"),
    ("purchase record",     "transaction record"),
    ("purchase",            "transaction record"),
    ("acquisition form",    "transaction record"),
    ("photograph",          "artifact photograph"),
    ("photo",               "artifact photograph"),
]


def categorize(raw: str | None) -> str:
    """Map a free-text medium string to one of CATEGORIES. Returns 'other'
    when nothing matches (including for None/empty input)."""
    if not raw:
        return "other"
    s = raw.strip().lower()
    if not s:
        return "other"
    for needle, cat in _RULES:
        if needle in s:
            return cat
    return "other"
