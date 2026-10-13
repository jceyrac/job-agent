# Tasks: Shared Catalogue vs User Space (spec 039)

**Input**: `specs/039-user-space-separation/` — spec.md, plan.md, research.md, data-model.md, contracts/storage-user-binding.md, quickstart.md

**Tests**: REQUIRED (FR-012, FR-013 explicitly mandate the isolation suite and schema-test updates).

**Organization**: One feature, three P1 user stories, all in `core/storage.py` (+ tests, `core/profiles.py`, `core/seed.py`, one new `scripts/` diagnostic). `[P]` marks only cross-file tasks that are safe to run in parallel; everything touching `core/storage.py` is sequential (single file).

---

## Phase 1: Setup

**Purpose**: Get on the spec branch; no new dependencies.

- [X] T001 Create feature branch `039-user-space-separation` from `main` (verify `.specify/feature.json` points at `specs/039-user-space-separation`).

---

## Phase 2: Foundational (schema + migration + seam)

**Purpose**: The blocking prerequisites — schema, the one-shot migration, and the `user_id` seam. Nothing in US1–US3 is testable until this lands.

- [X] T002 Add `users` and `user_companies` tables and the final `user_id` shape to the `SCHEMA` constant in `core/storage.py` (fresh installs: `job_tracking`/`job_applications` declare `PRIMARY KEY (user_id, job_id)`; `search_profiles`/`status_history`/`contacts`/`interactions`/`company_status_history` declare `user_id TEXT NOT NULL`). Mirror the column lists in [data-model.md](data-model.md).
- [X] T003 Add the constructor seam in `core/storage.py`: `JobStorage.__init__(self, db_path=DB_PATH, user_id=None)`; resolve `None` → `config.default_user_id` (create default user + key if absent); store `self.user_id`.
- [X] T004 Implement `_migrate_user_space_separation(conn)` in `core/storage.py` as a single transaction guarded by `_migration_applied`/`_record_migration`: create `users`/`user_companies`; create default user (UUID, `display_name='Jean Claude'`, `active_profile_id` from `config`, `onboarding_completed_at` from `config.onboarding_complete`); write `config.default_user_id`; rekey `job_tracking` and `job_applications` (create→copy→drop→rename, recreate `idx_tracking_status`); add+backfill `user_id` on `search_profiles`/`status_history`/`contacts`/`interactions`/`company_status_history`; backfill `user_companies` (non-default relationships, `monitored = 1 OR monitoring_status='watching'`); re-value `companies.monitoring_status` `watching`→`watch_ready`; rebuild per-user unique indexes. Details in [research.md](research.md) D1–D7 and [plan.md](plan.md) §1b.
- [X] T005 Create the default user on fresh init (empty DB) in `_init_db` in `core/storage.py` so onboarding works unchanged (D3, D10).
- [X] T006 Add accessors `get_active_profile_id`/`set_active_profile_id`/`get_onboarding_completed`/`set_onboarding_completed` to `JobStorage` in `core/storage.py` (read/write the bound user's row).
- [X] T007 Repoint `load_active_profile` in `core/profiles.py` and every `get_config("active_profile_id")`/`get_config("onboarding_complete")` call site (grep for the ~11 in `tracker_views/*` and `core/`) to the T006 accessors. Leave the old `config` keys in place (D13).

**Checkpoint**: migration runs once, idempotently, on any open; `users` has one row; `config.default_user_id` set; fresh init self-seeds.

---

## Phase 3: User Story 1 — Every user-space row is owned by a user (P1) 🎯 MVP

**Goal**: After migration on a copy of the live DB, row counts are unchanged, every user-space row carries `user_id`, `user_companies` holds exactly the non-default relationships, and the fingerprint is identical.

**Independent Test**: `python -m scripts.migration_report --db <copy>` before/after a `JobStorage` open shows per-table row counts unchanged, 0 user-space rows without `user_id`, `user_companies` count = non-default relationships, `users` = 1 row.

- [X] T008 [US1] Update `tests/test_storage.py` for the new schema (rekeyed PK, `user_id` column lists) — adjust assertions, never delete coverage (FR-013); run `python -m pytest tests/test_storage.py -q`.
- [X] T009 [US1] Add migration tests in `tests/test_migration.py` (new, in-memory DB): idempotency (second open is a no-op, recorded in `migrations`), row counts unchanged after migration, every user-space row has non-null `user_id` = default user, `user_companies` backfill = exactly the non-default companies, single-transaction rollback on an induced mid-migration failure (US1 scenario 4).

**Checkpoint**: US1 acceptance scenarios 1–4 green.

---

## Phase 4: User Story 2 — `JobStorage` is bound to a user and never leaks (P1)

**Goal**: `JobStorage` filters every user-space read on `self.user_id`, sets `self.user_id` on every user-space write, catalogue methods ignore the user, and a two-user isolation suite proves no leakage.

**Independent Test**: `python -m pytest tests/test_isolation.py -v` — two users in `:memory:`, every public user-space method driven as A, every public read as B returns none of A's data, and vice versa.

- [X] T010 [US2] Rewire company-relationship writers to `user_companies` in `core/storage.py`: `set_company_status` (status + `company_status_history.user_id`), `set_company_monitored`, `set_monitoring_status` (user-request path → `watch_requested_at`), `update_company_fields` on `notes`. Old `companies.status`/`notes`/`monitored` left unused (D5/D6).
- [X] T011 [US2] Rewrite monitoring readers to the split predicate in `core/storage.py`: `get_monitored_companies`, `get_watching_companies_by_method`, `get_companies_by_ats_provider` → `user_companies.monitored = 1 AND companies.monitoring_status = 'watch_ready'`; `get_watch_pending_companies` → `monitored = 1 AND monitoring_status = 'watch_pending'` (union across users, FR-010/Constitution VIII).
- [X] T012 [US2] Read `status`/`notes`/`monitored` through `LEFT JOIN user_companies … COALESCE` in `core/storage.py`: `get_companies`, `get_company_by_id`, `get_dashboard_data` (stale-outreach), `count_blacklisted_jobs`; blacklist filter becomes `COALESCE(uc.status,'prospect') != 'blacklisted'` scoped to the bound user.
- [X] T013 [US2] Add `user_id` read filters to every `job_tracking`/`job_applications` reader in `core/storage.py` per [plan.md](plan.md) §3b: `get_digest`, `get_stats` (both branches), `get_job_for_prepare`, `get_jobs_for_company`, `get_all_jobs_best_score`, `get_all_for_tracker`, `get_cohort_jobs`, `get_classified_jobs`, `find_jobs_by_company`, `get_jobs_for_scoring`, `get_queued_jobs_unprepared`, `get_queued_jobs`, `get_calibration_signals`, `get_calibration_deltas`, `get_archived_notes`, `get_few_shot_anchors`, `get_action_item_candidates`, `get_application`, `get_all_applications`, `get_applications_count`.
- [X] T014 [US2] Set `user_id` on every `job_tracking`/`job_applications`/`status_history` writer in `core/storage.py` (plan §3b/§3c): `set_status`, `record_lifecycle_event`, `reconcile_lifecycle_status`, `save_application`, `save_prepared_application`, `_auto_log_status_interaction` (D9).
- [X] T015 [US2] Apply `user_id` to `search_profiles` (plan §3a), `contacts` + `_lookup_contact` (plan §3d), `interactions` (plan §3e), and the aggregate readers `get_dashboard_data`/`get_database_stats` (plan §3f) in `core/storage.py`; widen `idx_contacts_linkedin` → `(user_id, linkedin_url)` and `idx_interactions_source_ref` → `(user_id, job_id, type, source_ref)` (D7).
- [X] T016 [P] [US2] Drop `notes` from the git seed round-trip in `core/storage.py` `get_export_seed_companies` and `core/seed.py` (capability fields stay) — D14.
- [X] T017 [US2] Preserve the documented cross-user exceptions unfiltered in `core/storage.py` (plan §5): `purge_stale_jobs`/`count_purgeable_jobs`, `get_engaged_job_keys`, and the monitoring-union readers (T011).
- [X] T018 [US2] Add `tests/test_isolation.py` (new): two-user in-memory isolation suite driving every public user-space method as A and asserting B sees none of A's data; module-level `USER_SPACE_METHODS` list asserted to cover the full set (FR-012).

**Checkpoint**: US2 acceptance scenarios 1–6 green; `python -m pytest tests/ -q` green.

---

## Phase 5: User Story 3 — Released through the standard flow, no visible change (P1)

**Goal**: The migration report command exists, the schema section of CLAUDE.md reflects the new model, and the full suite + parity + tracker smoke pass.

**Independent Test**: `python -m scripts.migration_report --db <path>` prints row counts per table, rows-without-`user_id` per user-space table, `users` and `user_companies` counts.

- [X] T019 [US3] Add `scripts/migration_report.py` (new, read-only `sqlite3`, `scripts/` is outside the raw-SQL guard) printing row counts per table, rows-without-`user_id` per user-space table, `users` and `user_companies` counts (FR-014, D11).
- [X] T020 [US3] Update the `## DB schema (key tables)` section of `CLAUDE.md` for `users`/`user_companies` and the `user_id` additions (FR-016); update `docs/roadmap-api.md` step-3 progress.
- [X] T021 [US3] Run the full validation from [quickstart.md](quickstart.md): `python -m pytest tests/ -q`, `python -m scripts.migration_report` on a DB copy, fingerprint parity, `core.score --extract`, `core.scrape --monitored-only --no-score`, tracker smoke checklist.

**Checkpoint**: quickstart steps 1–6 green on the dev Mac.

---

## Phase 6: Polish & Cross-Cutting

- [X] T022 [P] Record the release/rollback plan (staging rehearsal + parity + SHA check) in `docs/migration-checklist.md` or the spec's release section, per the standard release process in CLAUDE.md.

---

## Dependencies & Execution Order

- **Phase 1 → 2 → 3 → 4 → 5 → 6**, strictly sequential.
- Within Phase 2, T002–T006 are all `core/storage.py` and run in order; T007 (`core/profiles.py` + call sites) depends on T006.
- Phase 3 tests (T008, T009) need Phase 2 complete; T008 may run before T009.
- Phase 4 is one contiguous edit to `core/storage.py` (T010–T015, T017 in order), with T016 (`core/seed.py`) and T018 (tests) parallel-safe against the storage edits only after the corresponding methods exist.
- T019 (`scripts/migration_report.py`) is independent of Phase 4's storage edits and could be written earlier.

### Parallel Opportunities

- T016 (seed.py) and T019 (migration_report.py) touch different files and can run while storage edits proceed.
- T022 is independent documentation.

### MVP Scope

User Story 1 alone (Phases 1–3) is the shippable MVP: the schema and migration are in place and verified, with the app behaving identically for the single user. US2 (the seam + isolation) and US3 (release) build on it without changing that guarantee.
