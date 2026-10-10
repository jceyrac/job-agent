"""lint.py — deterministic, zero-LLM lint of the rendered CV (spec 036, US3).

Pure Python: no LLM, no network, no subprocess except reading the PDF page
count with ``pypdf``. Each check is a pure function returning
``{"id", "status", "detail"}``; :func:`run_lint` applies all nine and returns
``(results, all_pass)``. :func:`retryable_failures` selects the checks that
route back to ``tailor_cv`` (US5).

The lint reads the **rendered** ``cv_data_<slug>.json`` (exactly what the
reviewer sees) plus the master (the factual source of truth). Personal
allowlists are passed in — never imported from the repo (clarify Q2).
"""

import json
import re

from pypdf import PdfReader

from core.cv_agent.lint_config import (
    BANNED_PHRASES,
    CH_LOCATIONS,
    FR_LOCATIONS,
    IN_PROGRESS_COURSES,
    RETRYABLE_LINT_IDS,
)

EM_DASH = "—"  # U+2014 — banned in checked fields (en-dash – is allowed)

# A digit run with optional thousands/decimal separators ("30", "1,000", "3.5").
_NUM_RE = re.compile(r"\d[\d,.]*")


def _result(check_id: str, ok: bool, detail: str = "") -> dict:
    return {"id": check_id, "status": "pass" if ok else "fail", "detail": detail or ""}


# ── text collectors ─────────────────────────────────────────────────────────

def _iter_text(data: dict):
    """Yield (field, text) for every checked text field: title, profile,
    competencies, role title/dates/sub/bullets, education. Excludes the fixed
    ``_note``/``photo``/``filename`` and derived ``contact`` (never linted)."""
    if data.get("title"):
        yield ("title", data["title"])
    if data.get("profile"):
        yield ("profile", data["profile"])
    for label, text in data.get("competencies") or []:
        if label:
            yield ("competencies", str(label))
        if text:
            yield ("competencies", str(text))
    for role in data.get("roles") or []:
        for key in ("title", "dates", "sub"):
            if role.get(key):
                yield (f"role.{key}", str(role[key]))
        for bullet in role.get("bullets") or []:
            if bullet:
                yield ("role.bullet", str(bullet))
    for label, text in data.get("education") or []:
        if label:
            yield ("education", str(label))
        if text:
            yield ("education", str(text))


def _iter_fact_text(data: dict):
    """Yield the factual-figure fields for ``facts_numbers``: profile,
    competencies and role bullets (never titles/subs/education/dates)."""
    if data.get("profile"):
        yield data["profile"]
    for _label, text in data.get("competencies") or []:
        if text:
            yield str(text)
    for role in data.get("roles") or []:
        for bullet in role.get("bullets") or []:
            if bullet:
                yield str(bullet)


# ── the nine checks ─────────────────────────────────────────────────────────

def _check_pages(pdf_path: str) -> dict:
    """PDF has ≤ 2 pages (page count read with pypdf)."""
    try:
        n = len(PdfReader(pdf_path).pages)
    except Exception as exc:  # unreadable PDF → fail loudly, don't pass silently
        return _result("pages", False, f"cannot read PDF: {exc}")
    return _result("pages", n <= 2, f"{n} pages")


def _check_em_dash(data: dict) -> dict:
    """No U+2014 em-dash in any checked text field (en-dash in dates is fine)."""
    offenders = [text.strip()[:60] for _field, text in _iter_text(data) if EM_DASH in text]
    return _result("em_dash", not offenders, "; ".join(offenders))


def _number_allowed(token: str, master_text: str, allowed_numbers: set) -> bool:
    """A numeric token passes if it appears in the master or the allowlist,
    tolerating thousands/decimal separator variation (1,000 vs 1000)."""
    if token in allowed_numbers or token in master_text:
        return True
    return any(
        variant in master_text or variant in allowed_numbers
        for variant in (token.replace(",", ""), token.replace(".", ""))
    )


def _check_facts_numbers(data: dict, master_text: str, allowlist: dict) -> dict:
    """Every numeric token in profile/competencies/bullets appears in the
    master or the personal allowlist (catches LLM-invented figures)."""
    allowed_numbers = set(allowlist.get("numbers") or [])
    offenders = [
        token
        for text in _iter_fact_text(data)
        for token in _NUM_RE.findall(text)
        if not _number_allowed(token, master_text, allowed_numbers)
    ]
    return _result("facts_numbers", not offenders, ", ".join(offenders))


def _check_roles_match_master(data: dict, master_roles: list, allowlist: dict) -> dict:
    """Each role's ``dates`` + ``sub`` (employer + location) must match a master
    role exactly; an allowlisted extra role (by title) is permitted (US3)."""
    extra_roles = set(allowlist.get("extra_roles") or [])
    master_pairs = {(r.get("dates"), r.get("sub")) for r in master_roles}
    offenders = []
    for role in data.get("roles") or []:
        if (role.get("title") or "") in extra_roles:
            continue
        pair = (role.get("dates"), role.get("sub"))
        if pair not in master_pairs:
            offenders.append(f"{role.get('title')} ({role.get('dates')} · {role.get('sub')})")
    return _result("roles_match_master", not offenders, "; ".join(offenders))


def _check_current_role_first(data: dict) -> dict:
    """A role whose dates end in 'Present' must be the first role."""
    for i, role in enumerate(data.get("roles") or []):
        if "present" in (role.get("dates") or "").lower() and i != 0:
            return _result(
                "current_role_first", False,
                f"{role.get('title')} ({role.get('dates')}) is not first",
            )
    return _result("current_role_first", True)


def _check_contact_consistency(data: dict) -> dict:
    """Profile/relocation must not claim a base contradicting the contact
    profile (e.g. 'Based in Lausanne' with contact 'FR')."""
    contact = (data.get("contact") or "CH").upper()
    blob = " ".join(
        filter(None, [data.get("profile") or "", data.get("relocation") or ""])
    ).lower()
    bad = (CH_LOCATIONS if contact == "FR" else FR_LOCATIONS) if contact in ("CH", "FR") else []
    offenders = [loc for loc in bad if loc in blob]
    return _result("contact_consistency", not offenders, ", ".join(offenders))


def _check_in_progress_courses(data: dict) -> dict:
    """Any allowlisted in-progress course must be labelled 'in progress'."""
    offenders = []
    for label, text in data.get("education") or []:
        blob = f"{label} {text}".lower()
        for course in IN_PROGRESS_COURSES:
            if course.lower() in blob and "in progress" not in blob:
                offenders.append(course)
    return _result("in_progress_courses", not offenders, "; ".join(offenders))


def _check_banned_phrases(data: dict) -> dict:
    """None of the configured banned phrases may appear in any text field."""
    offenders = []
    for _field, text in _iter_text(data):
        lowered = text.lower()
        for phrase in BANNED_PHRASES:
            if phrase.lower() in lowered:
                offenders.append(phrase)
    return _result("banned_phrases", not offenders, "; ".join(offenders))


def _check_interests_present(data: dict) -> dict:
    """An interests section must be present (non-empty)."""
    interests = data.get("interests") or []
    ok = bool(interests) and any(any(item for item in pair if item) for pair in interests)
    return _result("interests_present", ok, "" if ok else "interests section missing")


# ── public entry ────────────────────────────────────────────────────────────

def run_lint(rendered_data: dict, pdf_path: str, master: dict, allowlist: dict | None = None):
    """Run all nine checks; return ``(results, all_pass)``.

    ``rendered_data`` is the ``cv_data_<slug>.json`` content; ``pdf_path`` the
    rendered PDF; ``master`` the master CV data (factual source of truth);
    ``allowlist`` the runtime personal allowlist (``{"numbers", "extra_roles", …}``).
    """
    allowlist = allowlist or {}
    master_text = json.dumps(master, ensure_ascii=False)
    master_roles = master.get("roles") or []
    results = [
        _check_pages(pdf_path),
        _check_em_dash(rendered_data),
        _check_facts_numbers(rendered_data, master_text, allowlist),
        _check_roles_match_master(rendered_data, master_roles, allowlist),
        _check_current_role_first(rendered_data),
        _check_contact_consistency(rendered_data),
        _check_in_progress_courses(rendered_data),
        _check_banned_phrases(rendered_data),
        _check_interests_present(rendered_data),
    ]
    return results, all(r["status"] == "pass" for r in results)


def retryable_failures(results: list) -> list:
    """The failing checks that route back to ``tailor_cv`` (US5)."""
    return [r for r in results if r["status"] == "fail" and r["id"] in RETRYABLE_LINT_IDS]
