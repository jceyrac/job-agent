"""
title_gate.py — Deterministic PM-family title filter for monitored-company jobs.

Gate runs BEFORE any LLM call in score.py, protecting the Groq daily quota
(1000 req/day).  Case-insensitive substring matching against an inclusion list.
Never uses exact equality.  Errs on inclusion: false positive costs one LLM
call, false negative loses a target.

Known regression: "Senior Tech Product Owner" (FELFEL) MUST pass.
"""

# Inclusion list — generous, ordered by interest (C1).
_PM_TITLES = [
    "product manager",
    "senior product manager",
    "staff product manager",
    "principal product manager",
    "lead product manager",
    "group product manager",
    "director of product",
    "head of product",
    "vp product",
    "chief product officer",
    "product owner",
    "technical product owner",
]


def is_product_management_title(title: str) -> bool:
    """Return True if *title* matches the PM family inclusion list.

    Case-insensitive substring match — never exact equality.
    """
    if not title:
        return False
    t = title.lower().strip()
    return any(kw in t for kw in _PM_TITLES)


# ---------------------------------------------------------------------------
# Regression guard — executed at module load so import-time errors are caught
# by any test or script that imports this module.
# ---------------------------------------------------------------------------
assert is_product_management_title("Senior Tech Product Owner"), \
    "FELFEL regression: 'Senior Tech Product Owner' MUST pass the title gate"
