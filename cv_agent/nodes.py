"""nodes.py — the CV agent's node functions (spec 029, FR-017).

Each function below is a LangGraph node: it reads from ``CVAgentState``, does its
job, and writes a **partial** state dict back (LangGraph merges it, honouring the
``revision_count`` reducer). Node naming matches the fixed pipeline in
``contracts/node-io-contract.md``:

    resolve_reference → refresh_context → extract_requirements → analyze_and_plan
      → analysis_gate ─(abort)─► mark_skipped ─► END
      → tailor_cv → draft_cover_letter → draft_recruiter_message → self_critique
      → render → publish → approval_gate ─(approve)─► file_and_record ─► END

"det" nodes make no LLM call; "llm" nodes make exactly one ``llm.call``; the two
"gate" nodes pause the graph via ``langgraph.types.interrupt()`` (the CLI resumes
them with ``Command(resume=...)``). This file is deliberately comment-heavy: it is
the project's first real LangGraph agent and the reusable foundation for future
human-validated agents.
"""

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from langgraph.types import interrupt

import llm
from models import JobPosting
from paths import DB_PATH
from scorer import extract_job_fields, evaluate_for_profile
from storage import JobStorage

from cv_agent.prompts import (
    ANALYZE_AND_PLAN_PROMPT,
    DRAFT_COVER_LETTER_PROMPT,
    DRAFT_RECRUITER_MESSAGE_PROMPT,
    EXTRACT_REQUIREMENTS_PROMPT,
    JOB_CONTEXT,
    MASTER_ANCHOR,
    SELF_CRITIQUE_PROMPT,
    TAILOR_CV_PROMPT,
)
from cv_agent.nextcloud_publish import publish_application
from cv_agent.renderer import default_output_root, load_master, render_documents

# Reconstruct a JobPosting from a DB row (job_actions' round-trip helper).
from job_actions import _dict_to_posting

REVISION_LIMIT = 3  # hard integer bound on the revise loop (FR-010 / FR-012)


# ── small helpers ───────────────────────────────────────────────────────────

def _progress(msg: str) -> None:
    """Human-facing progress line to stderr (keeps stdout's result line clean).

    The slow LLM/render steps are otherwise silent, which reads as a hang — this
    tells the user the agent is still working, not stuck.
    """
    print(f"cv_agent: {msg}", file=sys.stderr, flush=True)


def _slugify(text: str) -> str:
    """ASCII slug for a company/title, e.g. "Ceffu" "Senior PM" → "ceffu-senior-pm"."""
    s = unicodedata.normalize("NFKD", text or "")
    s = s.encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80] or "job"


def _parse_json(raw: str) -> dict:
    """Parse an LLM JSON payload defensively (strip fences, tolerate prose)."""
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}


def _job_context_str(job: dict, description_text: str) -> str:
    """Assemble the job context block fed to every LLM node's user message."""
    parts = []
    for label, key in (
        ("Title", "title"), ("Company", "company"), ("Location", "location"),
        ("Work mode", "work_mode"), ("Geo zone", "geo_zone"),
        ("Company country", "company_country"), ("Industry", "industry_sector"),
    ):
        if job.get(key):
            parts.append(f"{label}: {job[key]}")
    desc = (description_text or job.get("description") or job.get("summary") or "").strip()
    if desc:
        parts.append(f"Description:\n{desc[:4000]}")
    return "\n".join(parts)


def _load_profile(db, profile_id: str):
    """Reconstruct a SearchProfile for profile_id, mirroring job_actions.score_one.

    Reads from the DB first (onboarding-saved profiles), then the code seed, then
    falls back to the active profile. ``load_active_profile`` seeds the active
    profile on first run; here we only need the object, never to mutate it.
    """
    from profiles import ALL_PROFILES, SearchProfile, load_active_profile

    row = db.get_profile(profile_id)
    if row:
        return SearchProfile.from_criteria(row["id"], row["name"], row["criteria"])
    if profile_id in ALL_PROFILES:
        # Seed the code profile into search_profiles BEFORE scoring: job_scores has a
        # FK (profile_id → search_profiles.id), and save_scored will fail on a fresh
        # DB unless the profile row exists. load_active_profile does the same seeding
        # for the active profile; we only upsert here (never flip active_profile_id).
        seed = ALL_PROFILES[profile_id]
        db.upsert_profile(seed)
        return seed
    return load_active_profile(db)


def _detect_entry_kind(reference: str) -> str:
    """Classify the CLI's ``reference`` as an id / url / paste entry.

    A 20-char lowercase-hex string is *always* an id — whether or not it exists
    locally. If it isn't found, ``resolve_reference`` raises a clear error
    instead of silently treating the hex as pasted posting text (a footgun when
    the job only exists on the Live server's DB, not the dev DB).
    """
    if reference.startswith(("http://", "https://")):
        return "url"
    if re.fullmatch(r"[0-9a-f]{20}", reference):
        return "id"
    return "paste"


def _fetch_url_job(url: str) -> JobPosting:
    """Best-effort fetch + HTML→text for a live URL (deterministic, no LLM).

    Quality is best-effort per the spec ("no per-URL scraping reliability is
    promised"); structured fields are filled later by the scorer's extractor.
    """
    resp = httpx.get(url, follow_redirects=True, timeout=25.0)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = (og_title["content"] or "").strip() or title

    company = ""
    og_site = soup.find("meta", property="og:site_name")
    if og_site and og_site.get("content"):
        company = og_site["content"].strip()
    if not company:
        company = urlparse(url).netloc

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()

    # Fail-loud on closed boards / login walls: warn, don't block — the human can
    # correct Company/Title at Gate 1 or switch to --paste (research: closed boards).
    host = (urlparse(url).netloc or "").lower()
    closed_host = any(h in host for h in ("linkedin.com", "indeed.", "glassdoor."))
    lowered = text.lower()
    login_wall = len(text) < 200 or "sign in" in lowered or "join now" in lowered
    if closed_host or login_wall:
        print(
            "cv_agent:   warning: closed board — fetched text is likely a login "
            "wall; prefer --paste, or correct Company/Title at Gate 1.",
            file=sys.stderr, flush=True,
        )

    return JobPosting(
        source="manual", title=title or "Untitled role", company=company or "",
        location="", url=url, description=text[:3000],
    )


def _persist_and_populate(job: JobPosting, db, profile_id: str, kind: str) -> dict:
    """Run extraction + scoring **through the scorer** (the sole fit judge) and
    persist the deduped job, exactly like ``job_actions.score_one`` — no scoring
    logic is duplicated here (FR-002 / FR-003, Constitution §I).
    """
    _progress("Fetching & scoring the posting via the scorer…")
    profile = _load_profile(db, profile_id)

    extracted = extract_job_fields(job)   # LLM; mutates + returns the job (or None)
    if extracted is not None:
        job = extracted
    result = evaluate_for_profile(job, profile)  # LLM; dict | None

    if result is not None:
        db.save_scored(job, result, profile.id)
        score, score_reason = result.get("score"), result.get("reason")
    else:
        db.save_unscored(job)
        score, score_reason = None, None

    return {
        "entry_kind": kind,
        "job": job.to_json(),
        "job_id": job.id,
        "description_text": job.description or "",
        "score": score,
        "score_reason": score_reason,
    }


# ── node 1: resolve_reference ──────────────────────────────────────────────

def resolve_reference(state: dict) -> dict:
    """Resolve ``reference`` (job_id | url | paste) into a JobPosting-shaped state.

    Reads: ``reference``, ``profile_id``.
    Writes: ``entry_kind``, ``job``, ``job_id``, ``description_text``,
            ``score``, ``score_reason``.
    """
    reference = state["reference"]
    profile_id = state.get("profile_id")
    db = JobStorage(DB_PATH)
    kind = _detect_entry_kind(reference)

    if kind == "id":
        row = db.get_job_for_prepare(reference)
        if row is None:
            raise ValueError(
                f"job_id {reference!r} not found in the local DB ({DB_PATH}). "
                "If this job only exists on the Live server, pass its URL or "
                "use --paste instead."
            )
        job = _dict_to_posting(row)
        score_result = db.get_score_result(job.id, profile_id)
        return {
            "entry_kind": "id",
            "job": job.to_json(),
            "job_id": job.id,
            "description_text": (row.get("description") or "") or (row.get("summary") or ""),
            "score": score_result["score"] if score_result else None,
            "score_reason": score_result["reason"] if score_result else None,
        }

    if kind == "url":
        return _persist_and_populate(_fetch_url_job(reference), db, profile_id, "url")

    # paste: the reference *is* the posting text (from stdin).
    job = JobPosting(source="paste", title="", company="", location="", url="",
                     description=reference[:3000])
    return _persist_and_populate(job, db, profile_id, "paste")


# ── node 2: refresh_context (optional, deterministic) ──────────────────────

def refresh_context(state: dict) -> dict:
    """Optionally re-fetch a known URL when the stored description is too thin.

    State-only: never writes the DB. No LLM, and a fetch failure is swallowed so
    the pipeline degrades gracefully (FR-018 / Constitution §IX).
    """
    desc = (state.get("description_text") or "").strip()
    url = (state.get("job") or {}).get("url")
    if len(desc) >= 300 or not url:
        return {}
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=25.0)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
        if text:
            return {"description_text": text[:4000]}
    except Exception:
        pass
    return {}


# ── node 3: extract_requirements (llm) ─────────────────────────────────────

def extract_requirements(state: dict) -> dict:
    """Extract the posting's requirements / keywords / contact (FR-005).

    Reads: ``job``, ``description_text``.
    Writes: ``requirements``, ``letter_required``, ``recruiter_contact``.
    """
    _progress("Extracting requirements…")
    job = state.get("job") or {}
    context = _job_context_str(job, state.get("description_text", ""))
    raw = llm.call(
        [
            {"role": "system", "content": EXTRACT_REQUIREMENTS_PROMPT},
            {"role": "user", "content": JOB_CONTEXT.format(context=context)},
        ],
        json_mode=True, max_tokens=600,
    )
    data = _parse_json(raw)
    requirements = {
        "critical_requirements": data.get("critical_requirements", []),
        "ats_keywords": data.get("ats_keywords", []),
        "letter_required": bool(data.get("letter_required", False)),
        "message_required": bool(data.get("message_required", False)),
        "recruiter_contact": data.get("recruiter_contact"),
    }
    return {
        "requirements": requirements,
        "letter_required": requirements["letter_required"],
        "recruiter_contact": requirements["recruiter_contact"] or None,
    }


# ── node 4: analyze_and_plan (llm) ─────────────────────────────────────────

def analyze_and_plan(state: dict) -> dict:
    """Produce the fit recap + angle + contact-profile plan (FR-006).

    Reads the stored ``score`` / ``score_reason`` only — it **never emits a
    score** (the scorer is the sole fit judge, Constitution §I).
    Writes: ``fit_analysis``, ``proposed_profile``, ``profile_confidence``.
    """
    _progress("Analyzing fit & planning the angle…")
    job = state.get("job") or {}
    context = _job_context_str(job, state.get("description_text", ""))
    user = (
        f"{JOB_CONTEXT.format(context=context)}\n\n"
        f"Score from scorer: {state.get('score')}\n"
        f"Stored score reason: {state.get('score_reason') or ''}\n"
        f"Extracted requirements: {json.dumps(state.get('requirements') or {}, ensure_ascii=False)}\n"
    )
    raw = llm.call(
        [
            {"role": "system", "content": ANALYZE_AND_PLAN_PROMPT},
            {"role": "user", "content": user},
        ],
        json_mode=True, max_tokens=800,
    )
    data = _parse_json(raw)

    proposed = data.get("proposed_profile", "swiss")
    if proposed not in ("swiss", "french"):
        proposed = "swiss"
    confidence = data.get("profile_confidence", 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5

    fit_analysis = {
        "fit_recap": data.get("fit_recap", ""),
        "angle": data.get("angle", ""),
        "proposed_profile": proposed,
        "profile_confidence": confidence,
        "gaps": data.get("gaps", []),
    }
    return {
        "fit_analysis": fit_analysis,
        "proposed_profile": proposed,
        "profile_confidence": confidence,
    }


# ── node 5: analysis_gate (gate) ───────────────────────────────────────────

def analysis_gate(state: dict) -> dict:
    """First human-in-the-loop gate (FR-007). Pauses the graph.

    ``interrupt(payload)`` suspends mid-node; the CLI renders ``payload``, prompts
    the user, and resumes with ``Command(resume=<decision>)`` whose value is
    returned here. ``decision`` ∈ {"proceed", "abort"}; "adjust" is "proceed" plus
    ``user_directives``, an optional ``proposed_profile`` override, and optional
    ``company``/``title`` overrides (closed-board correction).
    """
    payload = {
        "score": state.get("score"),
        "score_reason": state.get("score_reason"),
        "fit_analysis": state.get("fit_analysis"),
        "proposed_profile": state.get("proposed_profile"),
        "profile_confidence": state.get("profile_confidence"),
    }
    decision = interrupt(payload)
    if not isinstance(decision, dict):
        decision = {"decision": decision or "proceed"}

    out = {
        "decision": decision.get("decision", "proceed"),
        "user_directives": decision.get("user_directives", "") or "",
        "title_override": (decision.get("title_override") or "").strip(),
    }
    override = decision.get("proposed_profile")
    if override in ("swiss", "french"):
        out["proposed_profile"] = override
    # Company/Title overrides (closed-board correction): patch the job so the slug,
    # render folder, and the LLM context all see the corrected values downstream.
    patches = {
        k: v.strip() for k, v in (
            ("company", decision.get("company") or ""),
            ("title", decision.get("title") or ""),
        ) if v and v.strip()
    }
    if patches:
        out["job"] = {**(state.get("job") or {}), **patches}
    return out


# ── node 6: mark_skipped (deterministic) ───────────────────────────────────

def mark_skipped(state: dict) -> dict:
    """Abort at analysis_gate → archive the job (FR-007, research §7)."""
    db = JobStorage(DB_PATH)
    db.set_status(state["job_id"], "archived", notes="cv_agent: skipped at analysis_gate")
    return {}


# ── node 7: tailor_cv (llm) ────────────────────────────────────────────────

def tailor_cv(state: dict) -> dict:
    """Re-angle the master CV into ``cv_content`` (FR-008). Increments revision.

    The master anchor is injected at call time (T004); the LLM emits only content
    fields (never filename/contact — those stay deterministic in ``renderer``).
    Returns ``{"revision_count": 1}`` so the reducer accumulates each pass.
    """
    _progress("Tailoring the CV (LLM)…")
    job = state.get("job") or {}
    context = _job_context_str(job, state.get("description_text", ""))
    master = load_master()

    user = (
        f"{JOB_CONTEXT.format(context=context)}\n\n"
        f"Requirements: {json.dumps(state.get('requirements') or {}, ensure_ascii=False)}\n"
        f"Fit plan: {json.dumps(state.get('fit_analysis') or {}, ensure_ascii=False)}\n"
    )
    if state.get("user_directives"):
        user += f"User directives: {state['user_directives']}\n"
    refine_notes = state.get("refine_notes") or []
    if refine_notes:
        notes = "\n".join(f"- {n}" for n in refine_notes)
        user += f"Revision notes (all prior feedback, newest last):\n{notes}\n"
    user += "\n" + MASTER_ANCHOR.format(master_json=json.dumps(master, ensure_ascii=False))

    raw = llm.call(
        [
            {"role": "system", "content": TAILOR_CV_PROMPT},
            {"role": "user", "content": user},
        ],
        json_mode=True, max_tokens=3000,
    )
    content = _parse_json(raw)
    if not content:  # defensive: never render an empty CV — fall back to master
        content = {k: master.get(k) for k in
                   ("title", "profile", "competencies", "roles", "education",
                    "languages", "interests")}

    slug = _slugify(job.get("title") or "job")
    return {"cv_content": content, "slug": slug, "revision_count": 1}


# ── node 8: draft_cover_letter (llm, conditional) ──────────────────────────

def draft_cover_letter(state: dict) -> dict:
    """Draft a cover letter only when required or forced with --letter (FR-008)."""
    if not (state.get("letter_required") or state.get("force_letter")):
        return {"cover_letter": None}
    _progress("Drafting cover letter…")

    job = state.get("job") or {}
    context = _job_context_str(job, state.get("description_text", ""))
    user = (
        f"{JOB_CONTEXT.format(context=context)}\n\n"
        f"Requirements: {json.dumps(state.get('requirements') or {}, ensure_ascii=False)}\n"
        f"Fit plan: {json.dumps(state.get('fit_analysis') or {}, ensure_ascii=False)}\n"
    )
    if state.get("user_directives"):
        user += f"User directives: {state['user_directives']}\n"
    raw = llm.call(
        [
            {"role": "system", "content": DRAFT_COVER_LETTER_PROMPT},
            {"role": "user", "content": user},
        ],
        json_mode=True, max_tokens=1000,
    )
    return {"cover_letter": _parse_json(raw).get("cover_letter", "") or None}


# ── node 9: draft_recruiter_message (llm, conditional) ─────────────────────

def draft_recruiter_message(state: dict) -> dict:
    """Draft an outreach message only when a recruiter contact is named (FR-008)."""
    contact = state.get("recruiter_contact") or {}
    if not any(contact.get(k) for k in ("name", "email", "linkedin_url")):
        return {"recruiter_message": None}
    _progress("Drafting recruiter message…")

    job = state.get("job") or {}
    context = _job_context_str(job, state.get("description_text", ""))
    user = (
        f"{JOB_CONTEXT.format(context=context)}\n\n"
        f"Contact: {json.dumps(contact, ensure_ascii=False)}\n"
        f"Fit plan: {json.dumps(state.get('fit_analysis') or {}, ensure_ascii=False)}\n"
    )
    raw = llm.call(
        [
            {"role": "system", "content": DRAFT_RECRUITER_MESSAGE_PROMPT},
            {"role": "user", "content": user},
        ],
        json_mode=True, max_tokens=600,
    )
    return {"recruiter_message": _parse_json(raw).get("recruiter_message", "") or None}


# ── node 10: self_critique (llm) ───────────────────────────────────────────

def self_critique(state: dict) -> dict:
    """Review coverage/keywords/factuality vs the master (FR-010).

    A non-empty ``factuality_violations`` list forces a revise regardless of the
    model's ``needs_revision`` flag — factual integrity is the hard constraint.
    """
    _progress("Self-critique pass…")
    master = load_master()
    user = (
        f"Requirements: {json.dumps(state.get('requirements') or {}, ensure_ascii=False)}\n"
        f"Tailored CV: {json.dumps(state.get('cv_content') or {}, ensure_ascii=False)}\n"
        f"Cover letter: {state.get('cover_letter') or '(none)'}\n"
        f"Recruiter message: {state.get('recruiter_message') or '(none)'}\n\n"
        + MASTER_ANCHOR.format(master_json=json.dumps(master, ensure_ascii=False))
    )
    raw = llm.call(
        [
            {"role": "system", "content": SELF_CRITIQUE_PROMPT},
            {"role": "user", "content": user},
        ],
        json_mode=True, max_tokens=1200,
    )
    data = _parse_json(raw)
    critique = {
        "needs_revision": bool(data.get("needs_revision", False)),
        "notes": data.get("notes", []),
        "coverage": data.get("coverage", []),
        "factuality_violations": data.get("factuality_violations", []),
    }
    needs_revision = critique["needs_revision"] or bool(critique["factuality_violations"])
    return {"critique": critique, "needs_revision": needs_revision}


# ── node 11: render (deterministic) ────────────────────────────────────────

def render(state: dict) -> dict:
    """Delegate to the deterministic renderer (FR-011)."""
    _progress("Rendering documents (.docx + .pdf)…")
    # Neutral local working dir, overridable per host: CV_OUTPUT_DIR env →
    # config "cv.output_dir" → default. Publication to Nextcloud is the next node.
    db = JobStorage(DB_PATH)
    output_root = (
        os.environ.get("CV_OUTPUT_DIR")
        or db.get_config("cv.output_dir")
        or default_output_root()
    )
    return {
        "output_paths": render_documents(
            state.get("cv_content") or {},
            state.get("proposed_profile", "swiss"),
            state.get("slug") or "job",
            state.get("job") or {},
            output_root,
            title_override=state.get("title_override") or "",
        )
    }


# ── node 12: publish (deterministic, optional) ─────────────────────────────

def publish(state: dict) -> dict:
    """Publish the rendered files to Nextcloud over WebDAV (FR-013).

    Reads ``output_paths.local_dir`` + ``job``; a clean no-op (returns ``{}``)
    when ``CV_NC_*`` is unset, so a pure-local dev run is unaffected. Writes
    ``nextcloud_web_url`` / ``pdf_url`` for the approval gate + record.
    """
    output_paths = state.get("output_paths") or {}
    local_dir = output_paths.get("local_dir")
    if not local_dir or not os.path.isdir(local_dir):
        return {}
    job = state.get("job") or {}
    result = publish_application(
        local_dir, job.get("company") or "Company", job.get("title") or "Role"
    )
    if not result:
        return {}
    return {
        "nextcloud_web_url": result.get("web_url"),
        "pdf_url": result.get("pdf_url"),
    }


# ── node 13: approval_gate (gate) ──────────────────────────────────────────

def approval_gate(state: dict) -> dict:
    """Second human-in-the-loop gate (FR-012). Pauses after render+publish.

    ``interrupt(payload)`` suspends; the CLI resumes with
    ``Command(resume={"approved": bool, "approval_notes": str})``.
    """
    payload = {
        "output_paths": state.get("output_paths"),
        "nextcloud_web_url": state.get("nextcloud_web_url"),
        "reject_count": len(state.get("refine_notes") or []),
    }
    decision = interrupt(payload)
    if not isinstance(decision, dict):
        decision = {"approved": False}
    approved = bool(decision.get("approved", False))
    notes = decision.get("approval_notes", "") or ""
    out = {"approved": approved, "approval_notes": notes}
    if not approved and notes:
        out["refine_notes"] = [notes]  # reducer accumulates across reject passes
    return out


# ── node 14: file_and_record (deterministic) ───────────────────────────────

def file_and_record(state: dict) -> dict:
    """Record the application and mark the job ready (FR-013, research §6).

    The rendered files live in the local working folder (written by ``render``,
    optionally published by ``publish``); this node only records the application
    — including the Nextcloud URLs when published — and flips the job status.
    """
    db = JobStorage(DB_PATH)
    job_id = state["job_id"]
    output_paths = state.get("output_paths") or {}

    analysis = json.dumps({
        "fit_recap": (state.get("fit_analysis") or {}).get("fit_recap", ""),
        "angle": (state.get("fit_analysis") or {}).get("angle", ""),
        "slug": state.get("slug"),
        "output_paths": output_paths,
        "nextcloud_web_url": state.get("nextcloud_web_url"),
        "pdf_url": state.get("pdf_url"),
        "recruiter_message": state.get("recruiter_message"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False)

    db.save_application(job_id, analysis, state.get("cover_letter") or "")
    pdf_name = os.path.basename(output_paths.get("pdf") or "")
    db.set_status(job_id, "ready", notes=f"cv_agent: {pdf_name}")
    return {}


# ── conditional-edge routers (called by graph.py) ──────────────────────────

def route_after_analysis(state: dict) -> str:
    """abort → mark_skipped; anything else (proceed/adjust) → tailor_cv."""
    return "mark_skipped" if state.get("decision") == "abort" else "tailor_cv"


def route_after_critique(state: dict) -> str:
    """Revise while needs_revision and under the cap; otherwise render.

    This is a **conditional edge** — the hard integer bound on revision_count
    is decided here in code, never by the model (Constitution §IV).
    """
    if state.get("needs_revision") and state.get("revision_count", 0) < REVISION_LIMIT:
        return "tailor_cv"
    return "render"


def route_after_approval(state: dict) -> str:
    """approve → file_and_record; reject → tailor_cv (unbounded human loop).

    Unlike ``route_after_critique`` (which keeps the hard ``REVISION_LIMIT`` cap
    on the model-decided auto-revise), a human *reject* always returns to
    ``tailor_cv`` with no cap — each pass is human-validated, so the human, not
    the model, decides the bound (Constitution §IV).
    """
    return "file_and_record" if state.get("approved") else "tailor_cv"
