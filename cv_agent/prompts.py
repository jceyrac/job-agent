"""Prompts for the CV agent's LLM nodes (spec 029).

Every model call in ``cv_agent/nodes.py`` uses one of these constants via
``llm.call(messages, json_mode=True)`` — the repo's unified LLM path (FR-014).
Only the *system* prompt differs per node; the *user* message carries the job
context. ``TAILOR_CV_PROMPT`` and ``SELF_CRITIQUE_PROMPT`` are paired with the
``MASTER_ANCHOR`` / ``JOB_CONTEXT`` templates whose master-CV anchor is injected
at call time (T004).
"""

# ── extract_requirements (FR-005) ──────────────────────────────────────────
EXTRACT_REQUIREMENTS_PROMPT = """You extract structured requirements from a job posting to drive CV tailoring.

Return STRICT JSON with exactly these keys:
{
  "critical_requirements": ["...", "..."],
  "ats_keywords": ["...", "..."],
  "letter_required": true,
  "message_required": false,
  "recruiter_contact": {"name": "...", "email": "...", "linkedin_url": "..."} | null
}
- critical_requirements: 4–8 must-have requirements, in the posting's own terms.
- ats_keywords: 8–15 concrete keywords/skills an ATS would match (technologies,
  domains, tools, certifications, years of experience).
- letter_required: true only if the posting asks for a cover/motivation letter.
- message_required: reserved — set false.
- recruiter_contact: null unless a specific contact person is named.
Never invent facts absent from the posting."""

# ── analyze_and_plan (FR-006) ──────────────────────────────────────────────
ANALYZE_AND_PLAN_PROMPT = """You are a Senior Product Manager's application strategist.

Given the job posting, its stored score/reason (the SOLE fit judgement — do NOT
re-score it), and the extracted requirements, produce a tailoring plan.

Return STRICT JSON:
{
  "fit_recap": "...",
  "angle": "...",
  "proposed_profile": "swiss" | "french",
  "profile_confidence": 0.82,
  "gaps": ["...", "..."]
}
- fit_recap: 2–3 sentences summarising why this role fits, referencing the stored score/reason.
- angle: the single positioning angle to lead the CV with (e.g. "institutional finance + B2B APIs").
- proposed_profile: "swiss" when the role is in Switzerland / a Swiss employer / CH-viable
  remote; "french" when it targets France. Default "swiss" when uncertain.
- profile_confidence: 0.0–1.0.
- gaps: weaknesses or missing keywords the tailoring should address.
IMPORTANT: do NOT include a score — the scorer already decided it."""

# ── tailor_cv (FR-008) ─────────────────────────────────────────────────────
TAILOR_CV_PROMPT = """You re-angle a master CV for a specific job, preserving FACTUAL INTEGRITY:
reword emphasis and reorder, but NEVER invent a domain, metric, tool, achievement,
or role the master CV does not contain.

Output STRICT JSON matching the master CV's shape exactly:
{
  "title": "...",
  "profile": "...",
  "competencies": [["Name", "items separated by commas"], ...],
  "roles": [{"title": "...", "dates": "...", "sub": "...", "bullets": ["...", "..."]}, ...],
  "education": [["Title", "Detail"], ...],
  "languages": [["Language", "Level"], ...],
  "interests": [["Theme", "items"], ...]
}
Rules:
- title: the header subtitle, ADAPTED to the job's domain (e.g. "Senior Product
  Manager | Data & Analytics", "| Fintech & Payments"). It must be defendable from
  the candidate's real profile (drawn from real strengths), stay coherent with the
  re-angled body (if a domain is trimmed or de-emphasised in the body, do not show
  it here), and NEVER copy the posting's own job title verbatim.
- profile: 3–5 sentence summary angled to the role and its critical requirements.
- competencies: 4–6 competency groups, re-worded/re-ordered to lead with what the
  posting rewards. Keep each group's items as a single comma-separated string.
- roles: title/dates/sub/employer/figures are FACTUAL — never change them. The
  BULLETS are prose: actively re-angle their vocabulary and emphasis (as with
  profile + competencies) so each bullet leads with the existing material that
  answers the posting. Do NOT copy bullets verbatim when a re-angle would surface
  relevant material — shift emphasis and word choice, keep the facts. Integrity
  limits: re-angle only what a role REALLY contained (never attribute a
  domain/tool/result it lacked); never alter figures, dates, locations or employer
  names; if a JD keyword has material in no role, do NOT inject it into a bullet —
  at most assume it as "concept/awareness" in competencies, or drop it.
  ORDER: default anti-chronological, with the CURRENT role (dates ending "Present")
  ALWAYS first. Reorder away from this only for a STRONG relevance gain, never
  burying the current role and never producing a run of ASCENDING dates at the top.
- languages: keep factual (levels may only be adjusted within the master's stated levels).
- interests: always include (from the master)."""

# ── draft_cover_letter (FR-008, conditional) ───────────────────────────────
DRAFT_COVER_LETTER_PROMPT = """You write a tailored cover letter for a Senior Product Manager applying to the job below.

Return STRICT JSON: {"cover_letter": "..."}
The letter: 3–4 short paragraphs, specific to the posting's critical requirements
and the agreed angle. Open by naming the role/company and the single strongest
reason for the fit. Close with a brief, confident call to action.
Do not invent facts about the candidate."""

# ── draft_recruiter_message (FR-008, conditional) ──────────────────────────
DRAFT_RECRUITER_MESSAGE_PROMPT = """You write a short, warm outreach message to the named recruiter/hiring contact about the role below.

Return STRICT JSON: {"recruiter_message": "..."}
2–4 sentences, professional and specific: who the candidate is, why the role is a
fit, and a soft call to action. Do not invent facts about the candidate or contact."""

# ── self_critique (FR-010) ─────────────────────────────────────────────────
SELF_CRITIQUE_PROMPT = """You review a tailored CV (and optional cover letter) against the job posting's requirements and the master CV's factual content.

Return STRICT JSON:
{
  "needs_revision": false,
  "notes": ["...", "..."],
  "coverage": ["requirement -> where addressed", "..."],
  "factuality_violations": ["...", "..."]
}
- factuality_violations MUST be empty for approval: list any claim in the tailored
  CV NOT present in the master CV (an invented role, domain, metric, or credential).
- If any critical requirement is unaddressed, or any claim is invented, set needs_revision true.
Be strict about factuality: re-angling wording is fine; inventing a fact is not.

Each of the following also forces needs_revision true, with notes naming exactly
what is wrong:
- SKILLS-SANS-PREUVE: a competency/skill claimed that no role bullet actually
  demonstrates (re-angle a bullet that has the material, or drop the skill).
- MOT-CLÉ NON ANCRÉ: a JD keyword added to competencies without a role that really
  carried it (e.g. "benchmarking" injected with no supporting experience).
- ÉTIREMENT DE DOMAINE: a domain claim in the profile not supported by the roles
  (e.g. "data science" when the roles are data-infrastructure).
- COHÉRENCE EN-TÊTE/CORPS: the header subtitle showing a domain the body trimmed
  or de-emphasised."""

# ── Templates: master-CV anchor + job context (injected at call time) ──────
MASTER_ANCHOR = "The factual master CV (source of truth — never contradict it):\n{master_json}"
JOB_CONTEXT = "Job posting:\n{context}"
