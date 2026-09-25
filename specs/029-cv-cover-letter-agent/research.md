# Phase 0 — Research: CV + Cover Letter Agent (029)

Findings resolve every "NEEDS CLARIFICATION" and "Open question" in `spec.md`.
Format: Decision / Rationale / Alternatives considered.

---

## 1. LangGraph is NOT installed — it is a new dependency

**Decision**: Add `langgraph` + `langgraph-checkpoint-sqlite` to
`requirements.txt` and install into `.venv/`.

**Rationale**: `import langgraph` currently raises `ModuleNotFoundError`. The
spec is explicit that this is the project's "first real LangGraph agent" and a
learning vehicle; the checkpointer→interrupt→resume topology is the stated
reusable foundation (FR-015, FR-017). This is the single new dependency, and the
justification lives in the spec itself (Constitution §V "no new dependency
without explicit justification" — the justification is explicit).

**Alternatives considered**:
- Hand-rolled state machine + pickle persistence. Rejected: duplicates exactly
  what LangGraph provides (checkpointer, `interrupt()`, resume via `invoke(None)`),
  loses the pedagogical goal, and is not the "reusable foundation" the spec wants.

**Versions** (verified against PyPI, Python 3.11.15):
- `langgraph` — latest `1.2.11`. In the 1.x line the SQLite saver moved to a
  separate package.
- `langgraph-checkpoint-sqlite` — latest `3.1.1`. Provides
  `from langgraph.checkpoint.sqlite import SqliteSaver`.
- Pin at implementation time: `langgraph>=1.2,<2` and
  `langgraph-checkpoint-sqlite>=3.0,<4`. `from langgraph.types import interrupt`
  and `StateGraph`, `interrupt_before` are all in `langgraph` core.

**Constraint**: no other dependency is needed — `httpx` (0.28.1) and
`beautifulsoup4`/`lxml` (for the URL-entry HTML→text parse) are already present.

---

## 2. Single-job read + insert: reuse existing methods (no new storage code)

**Decision**: The agent reuses `JobStorage` methods that already exist. **No new
read or insert method is required.**

**Rationale**: The spec left "the exact `storage` single-job read + insert method
names" open. Confirmed against `storage.py`:

| Need | Reused method | Signature |
|------|---------------|-----------|
| Single-job read (all fields) | `get_job_for_prepare(job_id)` | `-> dict \| None` |
| Existing score for a job+profile | `get_score_result(job_id, profile_id)` | `-> dict \| None` |
| Reconstruct `JobPosting` from a row | `job_actions._dict_to_posting(d)` | `-> JobPosting` |
| Persist a scored job (URL/paste entry) | `save_scored(job, score_result, profile_id)` | upsert job + `job_scores` + extraction fields |
| Persist an unscored job | `save_unscored(job)` | upsert job only |
| Record the application | `save_application(job_id, analysis, cover_letter)` | writes `job_applications` + sets status `ready` |
| Update tracking status | `set_status(job_id, status, notes=None)` | writes `job_tracking` |
| Active profile | `load_active_profile(db)` (profiles.py) | seeds/reads `config` |

`get_job_for_prepare` returns `id, title, company, company_id, url, source,
location, base_location, posted_date, description, summary, work_mode, geo_zone,
company_size, contract_type, company_country, industry_sector,
language_required, country_code, status` — everything `resolve_reference` and
`analyze_and_plan` need. `job_actions._dict_to_posting` already knows how to
round-trip a DB row back into a `JobPosting` (including the company-level fields
that live in the `companies` table after Phase 5).

**Alternatives considered**:
- Add a thin `get_job(job_id)` alias. Rejected: `get_job_for_prepare` is the
  single-job read and is stable; an alias is a non-functional diff (§V surgical).
- New `job_applications` column/table for output paths. Rejected — see §6.

---

## 3. `set_status` signature correction (spec "Context" is stale)

**Decision**: Use `set_status(job_id, status, notes=None)`. The spec's "Known
methods" line `set_status(job_id, profile_id, status, notes=…)` is **wrong** —
the profile is not a parameter; status is profile-independent (`job_tracking` is
keyed by `job_id` only).

**Rationale**: Verified at `storage.py:1583`. `VALID_STATUSES =
{"new", "queued", "ready", "applied", "rejected", "archived", "expired"}`.

**Alternatives considered**: none — the actual signature governs.

---

## 4. Render harness CLI convention (spec "Context" is stale)

**Decision**: The render node shells out to
`node render_cv.js <data.json> <output_dir>` — **not** `node render_cv.js <slug>`
as FR-011 guessed.

**Rationale**: Verified at `render_cv.js:2,20,146`. The script reads a full data
JSON path as `argv[2]`, an output dir as `argv[3]`, and names the `.docx` from
`D.filename` inside the data file (`OUT + "/" + D.filename + ".docx"`). The
interests fallback reads `cv_data_master.json` via `__dirname`, so `cwd` is
irrelevant but the data path should be absolute. PDF is produced by the agent,
not the harness, via LibreOffice:
`/Applications/LibreOffice.app/Contents/MacOS/soffice --headless --convert-to pdf --outdir <dir> <docx>`
(confirmed present on the dev Mac; on prod Ubuntu use `soffice`).

`cv_data_master.json` keys (verified): `_note, contact, relocation, filename,
photo, title, profile, competencies, roles, education, languages, interests`.
`filename` example: `Jerome_Ceyrac_CV_Ceffu_Senior_PM`.

**Implication for design**: `tailor_cv` (LLM) emits only the *content* fields
(`title`, `profile`, `competencies`, `roles`, `education`, `languages`,
`interests`). `renderer.py` (deterministic, no LLM) merges in the fixed fields —
`photo` and `relocation` copied from the master, `contact` = `CH`/`FR` derived
from `proposed_profile`, `filename` derived from the title-only slug — then
writes `cv_data_<slug>.json` and shells out. This keeps `filename`/`contact`
deterministic (§IV).

**Alternatives considered**:
- Have `tailor_cv` emit the full JSON including `filename`/`contact`. Rejected:
  those fields drive deterministic output naming and the CH/FR header, so they
  must not be LLM-controlled (§IV).

---

## 5. `job.id` type

**Decision**: `job.id` is a **TEXT** 20-char lowercase hex (`sha256(key)[:20]`),
not an integer (`models.py:JobPosting.id`). `thread_id` (FR-015) and every
`job_id` parameter carry this string.

**Rationale**: Confirmed in `JobPosting.id` and `_upsert_job_raw` (which inserts
`job.id` verbatim). No numeric FK is involved for jobs.

**Alternatives considered**: none.

---

## 6. Application↔job link: reuse `job_applications` + `set_status` (no schema change)

**Decision**: `file_and_record` writes the application via the existing
`save_application(job_id, analysis, cover_letter)` and marks the job via
`set_status(job_id, "ready", notes=…)`. **No new column or table.**

**Rationale**: `job_applications` already exists, is keyed by `job_id`, and has a
`cover_letter` TEXT column (`storage.py:231`). `save_application` stores
`analysis` + `cover_letter` and flips status to `ready` (the "prepared" state).
Mapping:
- `cover_letter` ← the tailored cover-letter text (or `""` when none).
- `analysis` ← a compact JSON string `{fit_recap, output_paths, slug,
  recruiter_message, generated_at}` so the tracker later shows the full history.
- `set_status(job_id, "ready", notes=f"cv_agent: <pdf filename>")` for a readable
  tracker note + output path.

**Alternatives considered**:
- New `cv_outputs` table or a `cv_path` column on `job_applications`. Rejected:
  §V (surgical, no schema change) and the spec's explicit preference to "reuse
  existing status/notes". A JSON blob in the already-present `analysis` column
  carries the paths with zero migration.

---

## 7. `abort` / "skipped" status mapping

**Decision**: `abort` at `analysis_gate` → `set_status(job_id, "archived",
notes="cv_agent: skipped at analysis_gate")`.

**Rationale**: There is no `skipped` status (`VALID_STATUSES` above). `archived`
is defined as "candidate decision (not relevant), requires a note" — the closest
semantic match for "I reviewed it and am not pursuing", and it carries the
required note. It also keeps the job out of future scoring/digest queries, which
already exclude `archived` (`get_jobs_for_scoring`).

**Alternatives considered**: `rejected` (recruiter rejection — wrong actor),
`expired` (external decision — wrong actor). Both rejected on semantics.

---

## 8. URL/paste entry: route through scorer exactly like `job_actions.score_one`

**Decision**: The URL/paste branch builds a `JobPosting`, then runs
`extract_job_fields(job)` → `evaluate_for_profile(job, profile)` →
`save_scored(job, result, profile.id)` (or `save_unscored(job)` if the score
call returns `None`). This mirrors `job_actions.score_one` (verified), so no
scoring logic is duplicated (FR-002, FR-003).

**Rationale**: `extract_job_fields` mutates the in-memory `JobPosting` (fills
structured fields) and returns it (or `None` on failure); it does **not**
persist. `evaluate_for_profile` returns a `save_scored`-compatible dict (or
`None`). `save_scored` upserts the job (dedup by `canonical_url`, then
`source+norm_company+norm_title`) and writes the score + extraction fields. This
satisfies FR-003's "dedupe by url; reuse the record" because `_upsert_job_raw`
is idempotent.

**Deterministic `resolve_reference` for the three entry kinds**:
- `job_id` → `db.get_job_for_prepare(id)` + `_dict_to_posting` (+
  `db.get_score_result(id, profile_id)` for the stored score).
- `url` → `httpx` GET, `beautifulsoup4` HTML→text; title from `<title>`/`og:title`,
  company from `og:site_name` or URL host, `description` = stripped text,
  `source="manual"`. (No LLM — deterministic; quality is best-effort per the
  spec's "No per-URL scraping reliability is promised".) **Closed boards
  (LinkedIn/Indeed/Glassdoor)** hit a login wall, so title/company/text are
  unreliable: the URL branch warns (fail-loud, stderr) and the human corrects
  Company/Title at Gate 1, or uses `--paste` instead.
- `paste` → text from stdin; title/company left minimal (or minimal heuristic),
  `description` = pasted text, `source="paste"`. Extraction fills the rest.

**Note (title/company guard at Gate 1, not an extraction fix)**: `extract_job_fields`
deliberately does **not** fill `title`/`company` — in the main pipeline the scrapers
supply them upstream, so the extractor only back-fills `summary`/`work_mode`/
`geo_zone`/`company_country` and the like. A `--paste` entry therefore starts with
both blank and would otherwise render into `Company - Role` / `Jerome_Ceyrac_CV_Job`.
Rather than change `scorer.py` (shared with the main pipeline, out of scope), the
`analysis_gate` payload now surfaces `job_title`/`job_company`, and `cli.py` forces
non-blank values for both before `proceed` — in the direct `[1] proceed` branch as
well as `[2] adjust`. The corrected values are patched back onto the job downstream
(the existing closed-board company/title override), so the slug, render folder and
LLM context all see them.

**Alternatives considered**:
- Re-implement extraction/scoring in the agent. Rejected: violates "scorer is the
  sole fit judge" (Constitution I) and FR-002.

---

## 9. Nextcloud publication is host-agnostic via WebDAV (no desktop client)

**Decision**: FR-013's "copy to the Nextcloud job-applications folder" is
implemented as an explicit WebDAV publication step, not a desktop-sync
dependency. The `render` node writes all three deliverables
(`.json`/`.docx`/`.pdf`) into a **neutral local working dir**
(`<data>/cv_outputs/<Company> - <Title>` by default, overridable via
`CV_OUTPUT_DIR` env → config `cv.output_dir`). A separate `publish` node then
pushes them to `Documents/01 Job/Job applications/<Company> - <Title>/` on the
user's Nextcloud over WebDAV — `MKCOL` each folder level (201/405/301 all mean
"OK, exists"), then `PUT` each file. Secrets come from
`CV_NC_BASE_URL`/`CV_NC_USER`/`CV_NC_APP_PASSWORD` env vars, never hardcoded;
the whole step is a clean no-op (returns `None`) when they are unset.

**Rationale**: The agent runs on the Mac today but is hosted on the headless verva
server in prod, where the Nextcloud desktop client does not exist. Depending on
`~/Nextcloud` (or any local mount) would silently fail on the server; explicit
WebDAV publication works on any host with the env vars set, while keeping render
100% local. `httpx` is already a dependency, so no new dependency is introduced.
The `job-application-docs` skill never existed (`grep -rl` returns only the spec);
the folder convention is captured here instead.

**Alternatives considered**:
- Desktop sync (`~/Nextcloud` folder, or `nextcloudcmd`). Rejected: no desktop
  client on the server; would reintroduce a host-specific path.
- Writing directly into Nextcloud's managed data dir + `occ files:scan`.
  Rejected: out of scope and fragile (bypasses Nextcloud's own file cache).
- A `job-application-docs` skill. Deferred: a documentation concern orthogonal to
  V1's CLI code; the convention can be promoted to a skill later.

---

## 10. LLM path confirmation

**Decision**: All model calls go through `llm.call(messages, *, json_mode=True,
max_tokens, temperature, sleep_after, max_retries, retry_base_delay) -> str`,
reading `llm.MODEL` / `llm.is_configured()`. Default provider is now DeepSeek
(spec 013b) — the CLAUDE.md "Groq primary" note is stale but out of scope.

**Rationale**: Verified at `llm.py`. `scorer.py` itself uses `llm.call`, so the
agent's reuse of `extract_job_fields`/`evaluate_for_profile` transitively keeps
every LLM call on the unified path (FR-014). The agent's own nodes
(`extract_requirements`, `analyze_and_plan`, `tailor_cv`, `draft_*`,
`self_critique`) call `llm.call` directly.

**Alternatives considered**: LangChain `ChatOpenAI` (used by a past standalone
exercise) — explicitly rejected by the spec.

---

## 11. Hardening source: the Jobgether analytics job (2026-09-25)

**Decision**: `tailor_cv` and `self_critique` prompts were hardened after a
real run exposed three defects on the Jobgether "Senior Product Manager,
Analytics Features" posting.

**Rationale** (observed failures, each now a rule):
1. **Header/body incoherence** — the header subtitle was `Senior Product
   Manager | AI | Web3` on a non-Web3 analytics role, even though the Web3
   line had been trimmed from the body. Cause: the title was deterministic
   (master default only), so it could not track the re-angled body. Fix: the
   LLM now emits an adapted `title`; `renderer.py` resolves it as
   `title_override > cv_content.title > master default` (still never the
   posting's title verbatim, still human-overridable at the gate).
2. **Bullets untouched** — only `profile`/`competencies` were re-angled; role
   bullets were copied verbatim, so announced skills ("dashboards",
   "data-quality") had no supporting bullet. Fix: `TAILOR_CV_PROMPT` now
   re-angles bullet vocabulary/emphasis with the integrity guardrails (only
   material a role really contained; figures/dates/locations immutable; no
   JD keyword injected without material).
3. **Role order bug** — ascending dates (ADEO 2021 → Powens 2022 → Vaudoise
   2024) with the current role buried 3rd. Fix: anti-chronological default,
   the "Present" role always first, deviation only for a strong relevance gain.

`SELF_CRITIQUE_PROMPT` gained four blocking controls from the same case —
SKILLS-SANS-PREUVE, MOT-CLÉ NON ANCRÉ, ÉTIREMENT DE DOMAINE, COHÉRENCE
EN-TÊTE/CORPS — each forcing `needs_revision` with the offender named. The
master `_note` TITLE and role-order rules were reconciled to match.
