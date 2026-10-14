# Feature Specification: Platform Ingestion Scope — title judgement moves to scoring (roadmap step 4 — Phase B)

**Feature Branch**: `040-platform-ingestion-scope`

**Created**: 2026-10-14

**Status**: Clarified 2026-10-14 — ready for `/speckit.plan`

**Input**: Roadmap `docs/roadmap-api.md`, step 4; Constitution principles I (wide net, single filtering point; ingestion scope is a platform parameter, never derived from a profile), VIII (company-keyed sources may apply a coarse family filter) and X (no user judgement stored on a catalogue entity). "What the platform collects must not depend on any user's profile. A user's title preferences decide what *that user* sees, at scoring time. Day one: same jobs ingested, same feed."

---

## Clarifications

### Session 2026-10-14

- Q1: Where is the platform scope edited? → A: **CLI only for now** (`scripts/ingestion_scope.py`, FR-004), plus a short read-only note next to the profile's job-titles field in Settings: "Ingestion uses the platform scope — edit with `python -m scripts.ingestion_scope`". One-line UI addition, no mockup needed. A real editor comes with the new front (`web/`).
- Q2: Initial value of the scope? → A: **Copy the active profile's current** `job_titles`, `title_exclude`, `scrape_remote_or_hybrid`, so day one is identical (parity). Widening is a later, separate, measured decision. Fresh install with no profile: the default profile's values from `core/profiles.py`.

---

## Why

- Today the active profile decides what enters the shared catalogue. With a second user (or a changed profile), jobs relevant to someone else would never be ingested.
- The title-gate outcome for monitored companies is written on the job itself (`jobs.filtered_non_product`): one profile's judgement stored on a catalogue entity, visible to everyone.
- Spec 039 created the user space; this step makes ingestion and scoring respect it. It closes Phase B.

---

## Context (verified against source at `cd14148`, 2026-10-14)

### Where the profile shapes ingestion today

| Where | What | Effect |
|---|---|---|
| `core/scrape.py:331–336` `_run_broad_scrape` | `JobFilter(titles=profile.job_titles, exclude=profile.title_exclude, remote_or_hybrid=profile.scrape_remote_or_hybrid, date_from=…)` | The filter passed to every board scraper's `fetch()` — used as **search query terms** by query-driven boards — and to `JobFilterEngine.apply` (title include, exclude words, date) |
| `core/scrape.py:379` | `title_matches_profile(job.title, profile.job_titles)` before `save_unscored` | A second, profile-based title gate drops jobs before they reach the catalogue |
| `core/scrapers/greenhouse.py:168` | `title_matches_profile(..., profile_titles)` on each board's full job list | Company-keyed source filtered by the profile's titles instead of a platform "product family" filter (Constitution I/VIII) |
| `core/score.py:443–446` | For monitored-company jobs: `title_matches_profile(title, profile.job_titles)` → `db.set_job_filtered_non_product(job_id)` | Writes `jobs.filtered_non_product = TRUE` — a profile judgement on a catalogue row (Constitution X violation) |
| `core/storage.py:1717, 2737` | Feed queries select `j.filtered_non_product` | The tracker hides / labels jobs from that catalogue flag |
| `core/scrape.py` `log_run(profile_id=profile.id, …)` | Scrape runs carry a profile id | Non-goal here (see below) |

### Tier 0 today (`core/scorer.py:757–833`, `evaluate_for_profile`)

Deterministic, profile-based, stored in `job_scores` (user space through the profile): denylisted company → 1; language → 1; sector → 1; contract type → 1; work mode → 1; per-mode geography → 2. **No title rule**: titles are filtered earlier — at ingestion (the scrape gate) *and* by the scoring SQL pre-filter (`title_contains` / `exclude_title_contains` in `effective_pre_filter`, spec 017), neither of which records a score.

### The profile fields involved (`core/profiles.py`)

`job_titles` (include list), `title_exclude` (exclude words), `scrape_remote_or_hybrid` (scraping option). Edited in Settings (`tracker_views/settings.py:549` and around).

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Ingestion is driven by a platform scope, not a profile (Priority: P1)

As the operator, I want a single platform-level ingestion scope (title families, exclude words, remote/hybrid option) stored in `config`, used by every scraper and by the ingestion filter, so that what enters the catalogue never depends on a user's profile.

**Independent Test**: With the scope initialised from today's profile values, a broad scrape and a monitored scrape on staging ingest exactly the same jobs as the current code (same counts per source, same job ids on a fixed fixture); changing the profile's `job_titles` no longer changes what is ingested.

**Acceptance Scenarios**:

1. **Given** the migration, **Then** `config` holds `ingestion_scope` (JSON: `title_families`, `exclude`, `remote_or_hybrid`), initialised **once** from the active profile's `job_titles`, `title_exclude`, `scrape_remote_or_hybrid` (see Q2), recorded in `migrations`.
2. **Given** `_run_broad_scrape`, **Then** `JobFilter` is built from `ingestion_scope` + freshness window; no profile field is read.
3. **Given** `_run_broad_scrape`, **Then** the second title gate before `save_unscored` (scrape.py:379) is removed — the `JobFilterEngine` title/exclude filter on the platform scope is the only ingestion title filter.
4. **Given** the Greenhouse scraper (and any company-keyed source applying a title filter), **Then** it filters on `ingestion_scope.title_families` (the coarse family filter allowed by Constitution I), not on a profile.
5. **Given** a fixture where the profile's `job_titles` differs from the scope, **Then** ingestion follows the scope (test).

---

### User Story 2 — Title judgement is a Tier 0 rule, stored in the user space (Priority: P1)

As a user, I want jobs whose title does not match **my** profile to be filtered for **me**, at scoring, recorded in my scores — not flagged on the job for everyone.

**Independent Test**: A job ingested under the platform scope but outside the profile's titles gets a `job_scores` row for that profile with `scored_by = 'tier_0'` and a "filtered: title" reason, without any LLM call; `jobs.filtered_non_product` is no longer written; the feed hides it exactly as before.

**Acceptance Scenarios**:

1. **Given** `evaluate_for_profile`, **Then** a new first Tier 0 rule applies `title_matches_profile(job.title, profile.job_titles)` and the profile's `title_exclude` words (same matching semantics as today: substring include, word-boundary exclude) → score 1, reason `filtered: title not in profile` / `filtered: excluded title word (<word>)`, `scored_by = tier_0`, for **every** job (not only monitored-company ones).
2. **Given** `core/score.py`, **Then** the monitored-only title gate and `set_job_filtered_non_product` are removed; scoring goes through `evaluate_for_profile` for all jobs.
3. **Given** existing jobs with `filtered_non_product = TRUE` (no score), **Then** no migration backfills them; the next `core.score --profile` run scores them via the new Tier 0 title rule — score 1, `scored_by = tier_0`, no LLM; the `jobs.filtered_non_product` column is kept unused until step 10 (expand/contract).
4. **Given** the feed (`get_all_for_tracker`, dashboard, stats), **Then** title-filtered visibility is derived from the profile's Tier 0 row, not from `jobs.filtered_non_product`. The flag is write-only in the UI today: those jobs render as *unscored*, not hidden; after the scoring run they render as score 1 (filtered title). Parity is byte-identical **before** any scoring run.
5. **Given** a guard test, **Then** no runtime code writes `jobs.filtered_non_product` (the only writer is removed) and no ingestion path (`core/scrape.py`, `core/scrapers/**`) reads a profile's `job_titles`, `title_exclude` or `scrape_remote_or_hybrid`.

---

### User Story 3 — Released through the standard flow, no visible change (Priority: P1)

**Acceptance Scenarios**:

1. **Given** staging on the exact SHA, **Then** parity (candidate vs current prod image, same backup copy, same `--as-of`) is identical — in particular the feed by status and the regression cases.
2. **Given** staging, **Then** `core.scrape --source <one cheap board>` and `core.scrape --monitored-only --no-score` ingest the same jobs as the current prod image on the same backup copy (compare `runs` counts and new job ids); `core.score --profile <active>` produces N Tier 0 title rows (SC-001) with no existing score changed, instead of writing `filtered_non_product`.
3. **Given** the deploy, **Then** standard checks + the next two scheduled runs: `runs.jobs_scraped` and the number of extracted jobs stay within the usual range (no ingestion-volume jump — the scope equals the old profile values on day one).

---

### Edge Cases

- **Empty `job_titles` in a profile** → `title_matches_profile` is open (every title passes) — unchanged semantics, now at scoring.
- **Empty `ingestion_scope.title_families`** → open ingestion (wide net). Allowed but logged as a warning, because board scrapers then have no query terms; the plan checks each query-driven board's behaviour with empty terms.
- **Profile edited after day one** (titles widened) → no new jobs are ingested until the platform scope is widened too: this is the intended separation, and must be explained where titles are edited (see Q1).
- **Jobs already scored for the profile** and flagged `filtered_non_product` → existing score kept; no duplicate row.
- **Flagged jobs outside the freshness window** → not re-scored by the next run (the freshness filter excludes them); they stay unscored until `--rescore` or a re-scrape. Acceptable — the flag was already inert (unscored) today.
- **cv_agent / `--paste` jobs** → not scraped, not affected; their scoring goes through the same Tier 0.
- **Extraction cost**: extraction (LLM) runs on every ingested catalogue job regardless of users; Tier 0 title filtering happens after extraction. Volume is unchanged on day one; widening the scope later increases extraction cost — to be watched in `runs`.

---

## Requirements *(mandatory)*

### Functional Requirements

**Platform scope (US1)**

- **FR-001**: `config.ingestion_scope` (JSON: `title_families: list[str]`, `exclude: list[str]`, `remote_or_hybrid: bool`) with typed accessors on `JobStorage` (`get_ingestion_scope()` / `set_ingestion_scope()`), validated on write.
- **FR-002**: One-shot migration initialising it from the active profile (Q2), idempotent, recorded in `migrations`; a fresh install gets a documented default (Q2).
- **FR-003**: `core/scrape.py` and every scraper read titles, exclude words and the remote/hybrid option only from the platform scope; the redundant profile title gate before `save_unscored` is removed.
- **FR-004**: `scripts/ingestion_scope.py` (`--show`, `--set-titles`, `--set-exclude`, `--remote-or-hybrid on|off`) to view and edit the scope — the prose path (Constitution II: config in DB, no deploy).

**Title judgement in scoring (US2)**

- **FR-005**: New first Tier 0 rule in `evaluate_for_profile` for profile titles and exclude words (same matching semantics as today: substring include, word-boundary exclude), applied to every job, no LLM call — now the *only* title judgement (the ingestion gate and the scoring pre-filter title clauses are removed, FR-003/FR-006).
- **FR-006**: `set_job_filtered_non_product` and its caller in `core/score.py` are removed, together with the scoring SQL pre-filter's title clauses (`title_contains` / `exclude_title_contains` in `core/score.py` `effective_pre_filter`), so title judgement happens **only** at Tier 0; feed/dashboard/stats derive title-filtered visibility from the profile's Tier 0 row.
- **FR-007**: No backfill migration. The migration only initialises `config.ingestion_scope`. `jobs.filtered_non_product` is kept unused (dropped at step 10): its runtime writer is removed and its unused `SELECT` columns dropped. Flagged jobs are scored on the next `core.score --profile` run via the Tier 0 title rule (score 1, `tier_0`, no LLM) — they are re-selected once the pre-filter title clause is gone (FR-006).
- **FR-008**: Guard tests: no ingestion code (`core/scrape.py`, `core/scrapers/**`) reads a profile's `job_titles` / `title_exclude` / `scrape_remote_or_hybrid`; no runtime code reads or writes `jobs.filtered_non_product` (allow-list: the migration that created the column).

**Release (US3)**

- **FR-009**: Standard release flow (branch → staging on exact SHA with US3 checks → ff-only merge → deploy → SHA check → next scheduled runs within the usual volume).
- **FR-010**: Constitution I's known violation (profile-based title gate in `scrape.py`) removed from the Sync Impact Report (PATCH); `CLAUDE.md` updated (ingestion scope, where to edit it).

- **FR-011**: Settings shows a read-only note next to the profile's job-titles field: "Ingestion uses the platform scope — edit with `python -m scripts.ingestion_scope`" (Q1). No other UI change.

### Non-goals

- `runs.profile_id` for scrape runs (still logged with the active profile; health check and fingerprint read `get_last_run(profile)` — changing it is a separate, later cleanup).
- Fixing the LLM-extracted `country_code` / `geo_zone` (pre-existing Constitution IV gap).
- Widening the scope (a product decision, made later through FR-004).
- A UI to edit the platform scope (CLI only until `web/`, per Q1).
- Dropping `jobs.filtered_non_product` (step 10).

---

## Success Criteria *(mandatory)*

- **SC-001**: Parity byte-identical on staging (as for step 3): candidate vs current prod image on the same backup copy, same `--as-of`, identical — the migration makes no score change. After `core.score --profile <active>` on staging, exactly N new `job_scores` rows with `scored_by = 'tier_0'` and a title reason exist, where N = unscored jobs within the freshness window whose title fails the profile's `job_titles` / `title_exclude`; no existing score changes.
- **SC-002**: Guard tests green and failing on a reintroduced profile read in ingestion or a write to `jobs.filtered_non_product`.
- **SC-003**: Changing the profile's `job_titles` in a test changes Tier 0 results but not ingestion.
- **SC-004**: Deploy: SHA = staged SHA; health OK; next two scheduled runs `success` with usual volumes.

---

## Open questions for `/speckit.clarify`

- **Q1 — Where the platform scope is edited.** **Recommendation:** CLI only for now (FR-004), plus a short read-only note next to the profile's job-titles field in Settings ("Ingestion uses the platform scope — edit with `python -m scripts.ingestion_scope`"), which is a one-line UI addition; a real editor comes with the new front (`web/`). Alternative: a Settings section to edit the scope now (requires an approved mockup).
- **Q2 — Initial value of the scope.** **Recommendation:** copy the active profile's current `job_titles`, `title_exclude` and `scrape_remote_or_hybrid` so day one is identical (parity), and widen later as a separate, measured decision. For a fresh install with no profile: the default profile's values (`core/profiles.py`, `job_titles` / `title_exclude` defaults). Alternative: start directly from a broader curated product-family list (more jobs, higher extraction cost).
