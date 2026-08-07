"""
title_gate.py — Deterministic title filter driven by the active profile.

Case-insensitive substring matching against the profile's job_titles.
When no titles are configured (empty/None), the gate is open — every title
passes (wide net, the scorer decides).

Never uses exact equality.  Errs on inclusion: false positive costs one
scoring call, false negative loses a target.
"""


def title_matches_profile(title: str, search_titles: list[str] | None = None) -> bool:
    """Return True if *title* matches any keyword in *search_titles*.

    Case-insensitive substring match — never exact equality.
    An empty or None *search_titles* means "no filter configured" → return True
    (wide net per Constitution principles I & IX).
    """
    if not title:
        return False
    if not search_titles:
        return True
    t = title.lower().strip()
    return any(kw.lower() in t for kw in search_titles)
