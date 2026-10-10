"""review.py — write the compact review bundle (spec 036, US4).

At **every** stop at ``approval_gate`` the agent writes ``review.json`` to
``output_paths.local_dir``, so a reviewer (human, or a later ``--resume``) has
everything needed to approve/reject without re-reading logs. Target ≤ 20 KB.

This module imports no ``llm`` and no network — it is a deterministic dump of the
in-memory state plus the rendered data file (Constitution §IV).
"""

import json
import os


def write_review(state: dict) -> str:
    """Build + write ``review.json`` to ``output_paths.local_dir``; return its path.

    The bundle is shaped per ``data-model.md`` §4: ``job`` narrowed to its
    id/title/company/url/entry_kind; ``requirements`` and ``fit_analysis`` narrowed
    to the spec-listed fields; ``cv`` read from the **rendered** ``output_paths.json``
    minus ``photo``/``interests`` (so the reviewer sees exactly what was produced,
    not ``cv_content``); plus critique/lint/lint_ok/revision_count/refine_notes/
    output_paths/thread_id. Returns ``""`` when there is no local dir to write to.
    """
    output_paths = state.get("output_paths") or {}
    local_dir = output_paths.get("local_dir")
    if not local_dir:
        return ""
    json_path = output_paths.get("json")

    cv = {}
    if json_path and os.path.isfile(json_path):
        try:
            with open(json_path, encoding="utf-8") as f:
                cv = json.load(f)
        except Exception:
            cv = {}
    cv = {k: v for k, v in cv.items() if k not in ("photo", "interests")}

    job = state.get("job") or {}
    requirements = state.get("requirements") or {}
    fit_analysis = state.get("fit_analysis") or {}

    bundle = {
        "job": {
            "id": state.get("job_id"),
            "title": job.get("title"),
            "company": job.get("company"),
            "url": job.get("url"),
            "entry_kind": state.get("entry_kind"),
        },
        "requirements": {
            "critical_requirements": requirements.get("critical_requirements", []),
            "ats_keywords": requirements.get("ats_keywords", []),
        },
        "fit_analysis": {
            "fit_recap": fit_analysis.get("fit_recap", ""),
            "angle": fit_analysis.get("angle", ""),
            "gaps": fit_analysis.get("gaps", []),
            "proposed_profile": state.get("proposed_profile"),
        },
        "cv": cv,
        "critique": state.get("critique") or {},
        "lint": state.get("lint") or [],
        "lint_ok": bool(state.get("lint_ok")),
        "revision_count": state.get("revision_count", 0),
        "refine_notes": state.get("refine_notes") or [],
        "output_paths": output_paths,
        "thread_id": state.get("thread_id"),
    }

    path = os.path.join(local_dir, "review.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)
    return path
