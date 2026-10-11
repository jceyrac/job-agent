# Feature Specification: Single DB Access Path + Single LLM Provider (roadmap step 2a + 2c)

**Feature Branch**: `037-single-db-access-path`

**Created**: 2026-10-10

**Status**: Clarified 2026-10-10 — ready for `/speckit.plan`

**Input**: Roadmap `docs/roadmap-api.md`, step 2 (2a + 2c). "Before the data-model change of step 3 (adding `user_id` to the user-space tables), every read and write of the database must go through `JobStorage`, so that step 3 changes one file, not twenty. At the same time, finish the move to a single LLM provider: only `core/llm.py` knows which provider is used. No behaviour change; release through the standard flow."

---

## Clarifications

### Session 2026-10-10

- Q: `core/email_monitor.py` — delete, or port to `llm.call` and keep dormant (FR-010)? → A: **Delete** (option A). The module and the `email-monitor` compose service are removed and recorded in `docs/history.md`. Its purpose (following up applications from emails) will come back as an agent using the API (roadmap step 6b).

---

## Why this comes before Phase B (step 3)

Step 3 adds `user_id` to `job_tracking`, `status_history`, `job_applications`, `interactions`, `contacts`, and moves the relationship fields of `companies` into `user_companies`. Today, raw SQL outside `core/storage.py` reads or writes exactly these tables (inventory below). If step 3 started now, each of these sites would have to be found and migrated by hand, and any one missed would silently read or write without the user filter. Routing them through named `JobStorage` methods first makes step 3 a change to one module, covered by its unit tests.

---

## Context (verified against source at `8ccfc83`, 2026-10-10)

### Raw SQL in the tracker (`db._conn()` used from the UI) — 16 sites in 7 files

| File | Lines | Kind | Tables |
|---|---|---|---|
| `tracker_views/dashboard.py` | 115–122 | read (overview counts) | `jobs`, `job_scores`, `companies`, `contacts` |
| `tracker_views/settings.py` | 780–789 | read (overview counts — duplicate of the dashboard block) | `jobs`, `job_scores`, `companies`, `contacts`, `interactions` |
| `tracker_views/settings.py` | 798–799 | read (contacts by role) | `contacts` |
| `tracker_views/settings.py` | 488–489, 509–510 | write | to inventory in plan |
| `tracker_views/job_detail.py` | 67–68, 340–341 | read | to inventory in plan |
| `tracker_views/contact_detail.py` | 77, 86, 97, 134 | write ×4 | `contacts` / `interactions` |
| `tracker_views/contacts.py` | 81–83 | write | `contacts` |
| `tracker_views/company_detail.py` | 280–281 | write | `companies` |
| `tracker_views/forms.py` | 52–53 | write | to inventory in plan |

Tables touched from the UI (counts of SQL references): `contacts` 11, `companies` 6, `job_scores` 4, `interactions` 2, `jobs` 2, `job_tracking` 1.

### Raw SQL in `core/` outside `storage.py` — 7 sites in 6 modules

| Module | Sites | Tables |
|---|---|---|
| `core/context_tuner.py` | 1 | `companies`, `job_scores`, `job_tracking`, `jobs` |
| `core/score.py` | 2 | `job_scores`, `jobs` |
| `core/export_seed.py` | 1 | `companies` |
| `core/preference_report.py` | 1 | `companies`, `job_applications`, `job_scores`, `job_tracking`, `jobs` |
| `core/backfill_descriptions.py` | 1 | `jobs` |
| `core/prepare.py` | 1 | `job_scores` |

The constitution already forbids this ("Tout accès à la base MUST passer par `JobStorage`. Pas de `sqlite3` direct hors de `storage.py`") and lists the tracker sites as a known violation to resolve at this step. No test enforces it today.

### Groq residue (Constitution VII: only `core/llm.py` names a provider)

| Where | What | Effect |
|---|---|---|
| `core/email_monitor.py:29, 50, 196–203, 466` | `from groq import Groq`, `GROQ_API_KEY`, Groq client calls | **Broken**: `groq` is not in `requirements.txt`, so the module cannot even be imported. The `email-monitor` compose service is documented as "not in use". |
| `tracker_views/onboarding.py:435–453` | First-run wizard requires `GROQ_API_KEY` and links to console.groq.com | **Broken for a new user**: the app uses DeepSeek through `llm.call`; a Groq key is useless and a DeepSeek key is never asked. |
| `.env.example:4–5` | Documents `GROQ_API_KEY` as the scorer LLM | Misleading setup doc. |
| `core/scorer.py:707` | Comment "authority over Groq inference" | Wording only. |

### Allowed raw access (already justified, kept)

- `scripts/backup_db.py` — stdlib online backup run inside the old image before rebuild (spec 031).
- `scripts/fingerprint.py` — read-only `mode=ro` table counts (spec 031 FR-014 diagnostic exception).
- `tests/` — fixtures.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Every database access goes through `JobStorage` (Priority: P1)

As the maintainer, I want the tracker and the domain modules to read and write the database only through named `JobStorage` methods, so that the next schema change (step 3) is made in one module, and a test prevents raw SQL from coming back.

**Why this priority**: Direct prerequisite of step 3; removes a known constitution violation.

**Independent Test**: The guard test fails on any `_conn()` / `sqlite3` use outside `core/storage.py` and the allow-list; all pages and pipeline steps behave exactly as before (parity identical, staging checks pass).

**Acceptance Scenarios**:

1. **Given** the change, **Then** none of the 16 tracker sites and 7 core sites listed in Context contains SQL; each calls a named, unit-tested `JobStorage` method.
2. **Given** the dashboard and the settings page, **Then** both overview-count blocks call the **same** `JobStorage` method (the duplicated SQL block is removed, not copied).
3. **Given** a write that was done with raw SQL (contacts, interactions, companies, …), **Then** the new or existing `JobStorage` method performs exactly the same statement(s) and the same transaction boundaries.
4. **Given** a guard test, **When** anyone adds `db._conn()`, `._conn(` on a `JobStorage`, `sqlite3.connect` or `import sqlite3` in runtime code outside `core/storage.py` and the allow-list, **Then** the suite fails with file:line (AST-based, negative fixtures for each form).
5. **Given** existing `JobStorage` methods, **Then** a new method is only added when no existing one does the job; if an existing one is reused, its behaviour is unchanged.

---

### User Story 2 — Only `core/llm.py` knows the LLM provider (Priority: P2)

As the maintainer and as a future new user, I want the app to configure and check the LLM only through `core/llm.py`, with no trace of Groq left.

**Independent Test**: `grep -ri groq` over runtime code, `.env.example` and compose returns nothing; the onboarding LLM step checks the configuration through `core/llm.py`; a guard test fails if a known provider name appears outside `core/llm.py`.

**Acceptance Scenarios**:

1. **Given** the onboarding wizard on a fresh data dir, **When** the LLM is not configured, **Then** it asks for the variables `core/llm.py` actually uses (`LLM_API_KEY`, and whatever else `llm.py` requires), never a Groq key, and the check is a public function of `core/llm.py` (e.g. `llm.is_configured()`).
2. **Given** `.env.example`, **Then** it documents the `LLM_*` variables and no Groq variable.
3. **Given** `core/email_monitor.py` and the `email-monitor` compose service, **Then** both are deleted (`git rm` + compose edit) and recorded in `docs/history.md` (FR-010).
4. **Given** a guard test, **Then** it fails if `groq`, `openai`, `anthropic`, `deepseek`, `ollama` (case-insensitive, as identifiers, imports or env-var names) appear in runtime code outside `core/llm.py` (allow-list for comments documenting history is not needed: comments are reworded).

---

### User Story 3 — Released through the standard flow, no behaviour change (Priority: P1)

**Independent Test**: Staging on the exact SHA; every touched screen and action checked; parity identical; deploy flow.

**Acceptance Scenarios**:

1. **Given** `staging.sh up <exact SHA>`, **Then** on the staging tracker the following work as before: Dashboard overview counts; Settings overview counts and contacts-by-role; the two Settings actions at the former lines 488/509; Job detail (both former SQL reads); Contact detail edit actions (4); Contacts page action; Company detail action; the form dialog in `forms.py`.
2. **Given** staging, **When** I run `python -m core.score --extract`, a `core.scrape --source <one cheap source>`, and the preference report / context tuner entry points that were touched, **Then** they complete normally.
3. **Given** staging, **Then** parity (candidate vs current prod image, same backup copy, same `--as-of`) is identical.
4. **Given** the deploy, **Then** deployed SHA = staged SHA, health check OK, next scheduled cron run `success`.

---

### Edge Cases

- Two UI sites doing the "same" query with slightly different SQL → unify only if results are provably identical; otherwise two methods, and the difference is noted.
- Writes that rely on the UI's transaction scope (several statements in one `with db._conn()`) → the `JobStorage` method keeps them in one transaction.
- `core/score.py` / `core/prepare.py` queries that are performance-sensitive (scoring loop) → the method must not add N+1 queries; keep the same SQL.
- `email-monitor` compose service removed (FR-010): the nightly `agent` and the `tracker` are unaffected. On verva, the deploy must also remove the orphan container if one exists (`docker compose up -d --remove-orphans`, or check that `deploy.sh` already does it), and its image can be pruned.
- The `groq` package is not installed anywhere, so removing its import changes nothing at runtime.

---

## Requirements *(mandatory)*

### Functional Requirements

**DB access (US1)**

- **FR-001**: All raw SQL sites listed in Context (16 in `tracker_views/`, 7 in `core/`) MUST be replaced by calls to named `JobStorage` methods.
- **FR-002**: Each new `JobStorage` method MUST have a unit test in `tests/test_storage.py` (in-memory DB) covering its result or effect.
- **FR-003**: The two identical overview-count blocks (dashboard, settings) MUST use one shared method.
- **FR-004**: Behaviour MUST be identical: same rows returned, same writes, same transaction boundaries.
- **FR-005**: A guard test MUST fail on `_conn` attribute access on a non-`self` object, `sqlite3.connect`, or `import sqlite3` in runtime code outside `core/storage.py`; allow-list: `scripts/backup_db.py`, `scripts/fingerprint.py` (read-only counts), `tests/`. Negative fixtures for each form.
- **FR-006**: `db._conn()` MUST no longer be used outside `core/storage.py` (it stays private).

**LLM provider (US2)**

- **FR-007**: `core/llm.py` MUST expose a public configuration check used by the onboarding wizard; the wizard MUST ask for the `LLM_*` variables, not a Groq key.
- **FR-008**: `.env.example` MUST document `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL` (as used by `llm.py`) and drop `GROQ_*`.
- **FR-009**: A guard test MUST fail if a provider name (`groq`, `openai`, `anthropic`, `deepseek`, `ollama`) appears in runtime code, `.env.example` or `docker-compose.yml` outside `core/llm.py` and the LLM env-var values themselves (allow-list documented in the test).
- **FR-010**: `core/email_monitor.py` MUST be deleted with `git rm`, and the `email-monitor` service removed from `docker-compose.yml`. Any other reference (CLAUDE.md Infrastructure section, `pyproject.toml` module list, guard-test allow-lists, docs) MUST be removed or updated. `docs/history.md` MUST record the module with its purpose, the last commit containing it, and the retrieval command. Rationale: unused, not importable (missing `groq` dependency), and its function belongs to a future API-based agent (roadmap step 6b).

**Release (US3)**

- **FR-011**: Release MUST follow the standard flow (branch → staging on exact SHA with the US3 checklist → ff-only merge → deploy → SHA check → next cron).
- **FR-012**: `CLAUDE.md` and the constitution's Sync Impact Report MUST drop the "raw SQL in tracker_views" and "Groq residue" known violations once resolved (constitution PATCH).

### Non-goals

- No schema change; no `user_id` (step 3); no `user_companies` (step 3).
- No change to the background-task mechanism (`subprocess` + thread) — step 2b, separate spec (number assigned when started; 038 is taken by `038-cv-contact-profile-eu-rule`).
- No UI change (no mockup required): same screens, same actions, same results.
- No change to `core/storage.py` existing methods' behaviour; only additions (and reuse).
- No refactoring of `JobStorage` into repositories.

### Key Entities

- **`JobStorage`**: the single gateway to the database; gains the named methods needed by the former raw-SQL sites.

---

## Success Criteria *(mandatory)*

- **SC-001**: Zero raw SQL outside `core/storage.py` and the allow-list; guard test green, and failing on each reintroduced form.
- **SC-002**: Zero provider name outside `core/llm.py`; guard test green, and failing on a reintroduced `groq` import or `GROQ_API_KEY`.
- **SC-003**: Full suite green, with one new unit test per new `JobStorage` method.
- **SC-004**: Staging: every action of the US3-1 checklist works; touched pipeline entry points run; parity identical.
- **SC-005**: Deploy: SHA = staged SHA; health OK; next scheduled cron `success`; no `email-monitor` container or service left on verva.

---

## Assumptions

- No in-flight work on the touched UI files (spec 036 is merged; the step-2b spec is not started).
- Deploy avoids the cron windows (07:00, 11:00, 16:00) and the month-end status batch.
