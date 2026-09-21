# Phase 1 — Data Model: CV + Cover Letter Agent (029)

The agent's own data model is the LangGraph `CVAgentState` (a `TypedDict`). It
does **not** change the SQLite schema — every persistent write reuses existing
tables (`jobs`, `job_scores`, `job_applications`, `job_tracking`). Below: the
state shape, the LLM-node output schemas, and the DB write mapping.

---

## 1. `CVAgentState` (TypedDict — `cv_agent/state.py`)

| Field | Type | Producer | Notes |
|-------|------|----------|-------|
| `reference` | `str` | CLI | the raw `job_id` \| `url` \| pasted text |
| `entry_kind` | `"id" \| "url" \| "paste"` | `resolve_reference` | recorded for audit |
| `job_id` | `str` | `resolve_reference` | 20-char hex id (see research §5) |
| `job` | `dict` | `resolve_reference` | `JobPosting.to_json()`-shaped |
| `description_text` | `str` | `resolve_reference` / `refresh_context` | effective description used downstream (full text for url/paste; stored desc for id) |
| `score` | `int \| None` | `resolve_reference` (id) or scorer (url/paste) | **read-only** downstream — the scorer is the sole judge |
| `score_reason` | `str \| None` | same as `score` | |
| `profile_id` | `str` | CLI / `resolve_reference` | active profile id (fallback `DEFAULT_PROFILE_ID`) |
| `requirements` | `dict` | `extract_requirements` | see §2.1 |
| `letter_required` | `bool` | `extract_requirements` | AND with `--letter` flag at the conditional edge |
| `recruiter_contact` | `dict \| None` | `extract_requirements` | `{name,email,linkedin_url}` or `None` |
| `fit_analysis` | `dict` | `analyze_and_plan` | see §2.2 |
| `proposed_profile` | `"swiss" \| "french"` | `analyze_and_plan` | overridable at `analysis_gate` |
| `profile_confidence` | `float` | `analyze_and_plan` | 0–1 |
| `user_directives` | `str` | `analysis_gate` (adjust) | free-text, injected into `tailor_cv` + `draft_*` prompts |
| `title_override` | `str` | `analysis_gate` (adjust) | deterministic CV header title; wins over the master title when set |
| `decision` | `"proceed" \| "abort"` | `analysis_gate` | |
| `slug` | `str` | `tailor_cv` / `render` | title-only ASCII slug; names `cv_data_<slug>.json` (folder stays `<Company> - <Title>`) |
| `cv_content` | `dict` | `tailor_cv` | re-angled content fields (see §2.3) |
| `cover_letter` | `str \| None` | `draft_cover_letter` | `None` when skipped |
| `recruiter_message` | `str \| None` | `draft_recruiter_message` | `None` when no contact |
| `critique` | `dict` | `self_critique` | see §2.4 |
| `needs_revision` | `bool` | `self_critique` | |
| `revision_count` | `Annotated[int, operator.add]` | reducer | hard-bound ≤ 3 (FR-010/FR-012) |
| `refine_notes` | `Annotated[list, operator.add]` | reducer | accumulated human reject feedback (FR-012) |
| `approved` | `bool` | `approval_gate` | |
| `approval_notes` | `str` | `approval_gate` (reject) | injected as directives on revise |
| `output_paths` | `dict` | `render` | `{json, docx, pdf, local_dir}` |
| `nextcloud_web_url` | `str \| None` | `publish` | browser Files-app URL of the published folder (`None` when publish disabled) |
| `pdf_url` | `str \| None` | `publish` | WebDAV URL of the published `.pdf` (`None` when publish disabled) |

**Reducer (pedagogical point, FR-017)**: `revision_count` uses `Annotated[int,
operator.add]` so each re-entry through `tailor_cv` accumulates the count; a
conditional edge clamps at 3. `refine_notes` uses `Annotated[list, operator.add]`
so each reject at `approval_gate` *appends* its note. All other fields are
last-writer-wins (plain `TypedDict` entries).

---

## 2. LLM-node output schemas (the `json_mode=True` contracts)

Each node calls `llm.call(..., json_mode=True)` and parses one of these.

### 2.1 `extract_requirements` → `requirements`
```json
{
  "critical_requirements": ["...", "..."],
  "ats_keywords": ["...", "..."],
  "letter_required": true,
  "message_required": false,
  "recruiter_contact": {"name": "…", "email": "…", "linkedin_url": "…"} | null
}
```

### 2.2 `analyze_and_plan` → `fit_analysis`  (NO score field)
```json
{
  "fit_recap": "…",
  "angle": "…",
  "proposed_profile": "swiss" | "french",
  "profile_confidence": 0.82,
  "gaps": ["…", "…"]
}
```

### 2.3 `tailor_cv` → `cv_content`  (content fields only — see research §4)
```json
{
  "title": "…",
  "profile": "…",
  "competencies": [{"name": "…", "items": ["…"]}],
  "roles": [{"company": "…", "title": "…", "period": "…", "points": ["…"]}],
  "education": [{"institution": "…", "degree": "…", "period": "…"}],
  "languages": [{"name": "…", "level": "…"}],
  "interests": ["…"]
}
```

### 2.4 `self_critique` → `critique`
```json
{
  "needs_revision": false,
  "notes": ["…"],
  "coverage": ["requirement → where addressed"],
  "factuality_violations": ["…"]
}
```
`factuality_violations` MUST be empty for approval; a non-empty list forces a
revise regardless of `needs_revision`.

---

## 3. DB write mapping (no schema change)

| State action | Storage call |
|--------------|--------------|
| url/paste persist (scored) | `save_scored(JobPosting, score_result, profile_id)` |
| url/paste persist (score failed) | `save_unscored(JobPosting)` |
| abort at analysis gate | `set_status(job_id, "archived", notes="cv_agent: skipped at analysis_gate")` |
| approve → record application | `save_application(job_id, analysis=JSON, cover_letter=text)` then `set_status(job_id, "ready", notes="cv_agent: <pdf filename>")` |

`analysis` JSON stored in `job_applications.analysis`:
```json
{
  "fit_recap": "…",
  "angle": "…",
  "slug": "ceffu-senior-product-manager",
  "output_paths": {"json": "…", "docx": "…", "pdf": "…", "local_dir": "…"},
  "nextcloud_web_url": "https://…/index.php/apps/files/?dir=…" | null,
  "pdf_url": "https://…/remote.php/dav/files/…/….pdf" | null,
  "recruiter_message": "…" | null,
  "generated_at": "2026-09-10T…"
}
```

---

## 4. Status transitions touched by the agent

`new/queued → ready` (approved, recorded) · `new/queued/ready → archived`
(abort at analysis gate). The agent never sets `applied` — applying is a later
human action in the tracker.
