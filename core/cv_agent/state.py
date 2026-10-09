"""CVAgentState — the LangGraph state schema for the CV agent (spec 029).

This is the agent's *own* data model (see ``specs/029-.../data-model.md`` §1);
it does **not** change the SQLite schema — every persistent write reuses the
existing tables via ``JobStorage`` (``jobs``, ``job_scores``,
``job_applications``, ``job_tracking``).

One field uses a reducer: ``revision_count`` is ``Annotated[int, operator.add]``
so each re-entry into ``tailor_cv`` *accumulates* the count (the node returns
``{"revision_count": 1}`` and LangGraph folds it into the running total). Every
other field is last-writer-wins. This is the pedagogical "state reducer" the
spec calls out (FR-017).
"""

import operator
from typing import Annotated, TypedDict


class CVAgentState(TypedDict, total=False):
    """Mutable state threaded through the graph.

    ``total=False`` makes every key optional, so the CLI may seed only a handful
    of input fields and let each node fill in the rest as the pipeline runs.
    """

    # ── Inputs (seeded by the CLI) ─────────────────────────────────────────
    reference: str          # raw job_id | url | pasted posting text
    profile_id: str         # active profile id (fallback DEFAULT_PROFILE_ID)
    force_letter: bool      # --letter: draft a letter even if the posting doesn't ask

    # ── resolve_reference / refresh_context ────────────────────────────────
    entry_kind: str         # "id" | "url" | "paste" (recorded for audit)
    job: dict               # JobPosting.to_json()-shaped dict
    job_id: str             # 20-char lowercase hex id
    description_text: str   # effective description used downstream
    score: int              # read-only downstream — the scorer is the sole judge
    score_reason: str

    # ── extract_requirements ───────────────────────────────────────────────
    requirements: dict      # {critical_requirements, ats_keywords, letter_required, ...}
    letter_required: bool
    recruiter_contact: dict  # {name, email, linkedin_url} | None

    # ── analyze_and_plan ───────────────────────────────────────────────────
    fit_analysis: dict      # {fit_recap, angle, proposed_profile, profile_confidence, gaps}
    proposed_profile: str   # "swiss" | "french" (overridable at analysis_gate)
    profile_confidence: float  # 0–1

    # ── analysis_gate ──────────────────────────────────────────────────────
    decision: str           # "proceed" | "abort"
    user_directives: str    # free-text directives merged into tailor/draft prompts
    title_override: str     # CV header title (subtitle); wins over the LLM-adapted and master title when set

    # ── tailor_cv ──────────────────────────────────────────────────────────
    slug: str               # "company-title" ASCII slug; names cv_data_<slug>.json
    cv_content: dict        # re-angled content fields (render-harness shape)

    # ── drafts ─────────────────────────────────────────────────────────────
    cover_letter: str       # None when skipped
    recruiter_message: str  # None when no contact

    # ── self_critique ──────────────────────────────────────────────────────
    critique: dict
    needs_revision: bool

    # ── reducer: hard-bound ≤ 3 revise loop (FR-010 / FR-012) ──────────────
    revision_count: Annotated[int, operator.add]

    # ── reducer: accumulated human refine feedback (FR-012) ─────────────────
    refine_notes: Annotated[list, operator.add]

    # ── approval_gate ──────────────────────────────────────────────────────
    approved: bool
    approval_notes: str

    # ── render ─────────────────────────────────────────────────────────────
    output_paths: dict      # {json, docx, pdf, local_dir}

    # ── publish (optional WebDAV step; None when CV_NC_* unset) ────────────
    nextcloud_web_url: str  # browser Files-app URL of the published folder
    pdf_url: str            # WebDAV URL of the published .pdf
