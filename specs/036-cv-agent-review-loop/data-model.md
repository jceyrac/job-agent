# Data model — spec 036 (CV agent review loop)

This spec adds **no SQLite schema change** (non-goal). The entities below are
(a) three new fields on the in-memory `CVAgentState`, and (b) the `review.json`
document written to the local filesystem. The relocation of
`_dict_to_posting` → `posting_from_dict` changes no persisted shape.

## 1. `CVAgentState` additions (see `core/cv_agent/state.py`)

| Field | Type | Reducer | Seeded by | Written by |
|-------|------|---------|-----------|------------|
| `thread_id` | `str` | last-writer-wins | CLI (`_thread_id(reference)`, or the `--resume` arg) | — (read by `approval_gate` → `review.json`) |
| `lint` | `list[dict]` | last-writer-wins | — | `lint` node |
| `lint_ok` | `bool` | last-writer-wins | — | `lint` node |

`lint` and `lint_ok` are last-writer-wins (each render→lint pass overwrites the
previous), because only the **final** lint result matters at `approval_gate`.

## 2. Lint check result

Each element of `state["lint"]` is:

```json
{ "id": "facts_numbers", "status": "pass", "detail": "…offending text or ''…" }
```

- `id` ∈ the 9 checks: `pages`, `em_dash`, `facts_numbers`, `roles_match_master`,
  `current_role_first`, `contact_consistency`, `in_progress_courses`,
  `banned_phrases`, `interests_present`.
- `status` ∈ `"pass"` | `"fail"`.
- `detail` = empty string on pass; on fail, the offending text/field (so the
  reviewer sees exactly what failed, and the revise loop gets a usable note).
- `lint_ok` = `all(r["status"] == "pass" for r in lint)` — derived in code (§IV).

**Retryable set** (routes back to `tailor_cv` under `REVISION_LIMIT`, US5):
`facts_numbers`, `roles_match_master`, `em_dash`, `pages`. The other five are
informational only (fixed in config/prompts, not per-run LLM revision).

## 3. Lint config sources (clarify Q2)

- **Tracked** — `core/cv_agent/lint_config.py`: generic style rules only
  (banned phrases, punctuation like the em-dash rule). Public-safe, no personal data.
- **Runtime / untracked** — personal allowlists (allowed numeric tokens, employers,
  extra roles, in-progress courses, claims) derived from `cv_data_master.json`
  (via `renderer.load_master()`) and, where the master lacks a fact, a sidecar
  file next to the master in `.cv_pipeline/`. **Never committed.**

## 4. `review.json` (US4) — the review bundle

Written to `output_paths.local_dir/review.json` on **every** stop at `approval_gate`.
Target ≤ 20 KB (≈ 5k tokens).

```json
{
  "job":          { "id": "<20-hex>", "title": "…", "company": "…", "url": "…", "entry_kind": "id|url|paste" },
  "requirements": { "critical_requirements": ["…"], "ats_keywords": ["…"] },
  "fit_analysis": { "fit_recap": "…", "angle": "…", "gaps": ["…"], "proposed_profile": "swiss|french" },
  "cv":           { /* rendered cv_data_<slug>.json content, minus "photo" and "interests" */ },
  "critique":     { /* self_critique output verbatim */ },
  "lint":         [ { "id": "…", "status": "pass|fail", "detail": "…" }, … ],
  "lint_ok":      true,
  "revision_count": 0,
  "refine_notes": [ "…" ],
  "output_paths": { "json": "…", "docx": "…", "pdf": "…", "local_dir": "…" },
  "thread_id":    "<20-hex>"
}
```

Notes:
- `cv` is read from `output_paths.json` (the **rendered** data file, including the
  derived `contact`/`filename`/fixed fields), with `photo` and `interests` removed —
  not `state["cv_content"]`, so the reviewer sees exactly what was produced.
- `requirements` and `fit_analysis` are narrowed to the fields the spec lists
  (`requirements` drops `letter_required`/`message_required`/`recruiter_contact`;
  `fit_analysis` drops `profile_confidence`) to keep the bundle small.
- `thread_id` enables the `--resume <thread_id>` seam without reading logs.

## 5. `posting_from_dict` (FR-009) — relocated, no shape change

`core.models.posting_from_dict(d: dict) -> JobPosting` is the **verbatim** body of the
old `core.job_actions._dict_to_posting` (field-by-field `d.get(...)`, the `posted_date`
`%Y-%m-%d` and `extracted_at` `%Y-%m-%dT%H:%M:%S` parsing guards, `tags=[]`,
`salary=None`). Same round-trip semantics; only the name and home module change.
