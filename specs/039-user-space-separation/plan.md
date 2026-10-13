# Implementation Plan — Shared Catalogue vs User Space (spec 039)

**Branch**: `039-user-space-separation` · **Status**: ready for `/speckit.tasks`

The single file that changes is `core/storage.py` (plus tests and two `scripts/`
diagnostics). No UI, no scraper, no model change. See
[research.md](research.md) (D1–D14), [data-model.md](data-model.md),
[contracts/storage-user-binding.md](contracts/storage-user-binding.md).

---

## Technical Context

| Item | Value |
|---|---|
| Language / runtime | Python 3.11, stdlib `sqlite3` |
| Storage | `core/storage.py` (WAL SQLite), `JobStorage` class |
| Tests | `pytest`, in-memory `:memory:` DB; new `tests/test_isolation.py` |
| New dependency | none (`uuid` is stdlib) |
| Files touched | `core/storage.py`, `core/profiles.py`, `core/seed.py`, `tests/test_storage.py`, `tests/test_isolation.py` (new), `scripts/migration_report.py` (new) |
| Never touched | `core/models.py`, `core/llm.py`, `core/scrape.py`, `core/scorer.py`, `core/main.py`, scrapers, all `tracker_views/*`, `tracker.py` |

All `NEEDS CLARIFICATION` items were resolved in [research.md](research.md).

---

## Constitution Check (v2.0.x)

| Principle | Status |
|---|---|
| III — `user_id` seam, profile belongs to user | ✅ `users` + `user_id` on all user-space rows; profile gains `user_id` |
| V — surgical edits | ✅ one module + tests; no refactors |
| X — shared catalogue / user space | ✅ `jobs`/`job_scores`/`runs`/company facts stay catalogue; decisions move to `users`/`user_companies` |
| XI — progressive migration, expand/contract, parity | ✅ expand (`user_companies`) + contract (old columns kept unused until step 10); parity via fingerprint |
| VIII — monitoring = union of users' watched companies | ✅ `user_companies.monitored=1` unioned across users |
| I — ingestion scope = platform, never profile-derived | ✅ untouched |

No gate violations. FR-016 (Sync Impact Report) is handled at release.

---

## Phase 1 — Schema + migration (`core/storage.py`)

### 1a. `SCHEMA` (fresh installs)

Add to the `SCHEMA` constant:

```sql
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    active_profile_id TEXT,
    onboarding_completed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_companies (
    user_id TEXT NOT NULL,
    company_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'prospect',
    notes TEXT,
    monitored INTEGER NOT NULL DEFAULT 0,
    watch_requested_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, company_id)
);
```

And add `user_id TEXT NOT NULL` to the `SCHEMA` DDL of `search_profiles`,
`status_history`, `contacts`, `interactions`, `company_status_history` (fresh
DBs get the final shape directly). `job_tracking` / `job_applications` in
`SCHEMA` declare `PRIMARY KEY (user_id, job_id)`.

### 1b. The `user_space_separation` migration (existing DBs)

One named migration, `_migrate_user_space_separation(conn)`, guarded by
`_migration_applied` / `_record_migration`, wrapped in a single
`BEGIN … COMMIT` (rollback on any exception). Steps, in order:

1. `CREATE TABLE users …; CREATE TABLE user_companies …` (idempotent).
2. Create the default user: `id = uuid.uuid4().hex`, `display_name = 'Jean Claude'`,
   `active_profile_id = COALESCE(config.active_profile_id, 'unified_jc')`,
   `onboarding_completed_at` from `config.onboarding_complete`.
   `INSERT INTO config (key, value) VALUES ('default_user_id', :id)`.
3. `job_tracking` rekey (create→copy→drop→rename):
   `CREATE TABLE job_tracking_new (user_id TEXT NOT NULL, job_id TEXT NOT NULL, status …,
    notes …, changed_at …, PRIMARY KEY (user_id, job_id))`; copy
   `SELECT :default_user, job_id, status, notes, changed_at FROM job_tracking`;
   drop + rename; recreate `idx_tracking_status`.
4. `job_applications` rekey — same procedure, copying all current columns
   (`profile_id`, `analysis`, `cover_letter`, `cv_bullets_selected`,
   `company_research`, `screening_answers`, `language`, `prepared_by`,
   `prepared_at`, `created_at`).
5. `ALTER TABLE` `user_id` onto `search_profiles`, `status_history`, `contacts`,
   `interactions`, `company_status_history` (add column + backfill to default
   user + `NOT NULL`). `status_history` / `company_status_history` keep their
   autoincrement `id` PK.
6. Backfill `user_companies` (non-default relationships only) from `companies`
   — see D5. The `monitored` backfill also treats
   `monitoring_status = 'watching'` as a user request (D6).
7. Re-value `companies.monitoring_status`: `watching` → `watch_ready`
   (capability-only vocabulary); all other values unchanged.
8. Per-user unique indexes (D7): rebuild `idx_contacts_linkedin` →
   `(user_id, linkedin_url) WHERE linkedin_url IS NOT NULL`;
   `idx_interactions_source_ref` → `(user_id, job_id, type, source_ref)
   WHERE source_ref IS NOT NULL`; add plain `user_id` indexes where hot queries
   filter on it.
9. Fresh-init path: after `executescript(SCHEMA)` on an empty DB, create the
   default user (no backfill). `users.active_profile_id` defaults to the
   profile that already exists or `DEFAULT_PROFILE_ID`.

### 1c. Constructor seam (D2)

`JobStorage.__init__(self, db_path=DB_PATH, user_id=None)`; resolve `None` →
`config.default_user_id` (create if absent). Store `self.user_id`.

---

## Phase 2 — `user_companies` split (D5/D6) & monitoring rewrite

- `companies.status`, `notes`, `monitored` become **read-from / write-to
  `user_companies`** everywhere. The old columns remain, unused (drop at step 10).
- Monitoring target rewrite (FR-010, Constitution VIII):
  - `get_monitored_companies` → `SELECT … FROM companies c JOIN user_companies uc
    ON uc.company_id = c.id WHERE uc.monitored = 1
    AND c.monitoring_status = 'watch_ready' ORDER BY c.name` (union across users).
  - `get_watching_companies_by_method` → same join, split by `scraping_method`.
  - `get_companies_by_ats_provider` → `uc.monitored = 1 AND c.ats_provider = ?`.
  - `get_watch_pending_companies` → `uc.monitored = 1 AND
    c.monitoring_status = 'watch_pending'`.
  - `set_company_monitored` / `set_monitoring_status` (user-request path) →
    upsert `user_companies.monitored` / `watch_requested_at`.
  - `set_company_status` → upsert `user_companies.status` + write
    `company_status_history` with `user_id`.
  - `update_company_fields` on `notes` → `user_companies.notes`.
  - `get_companies` / `get_company_by_id` read `status`/`notes`/`monitored` via
    `LEFT JOIN user_companies … COALESCE`; blacklist filter becomes
    `COALESCE(uc.status,'prospect') != 'blacklisted'`.
- Seed/export (D14): `get_export_seed_companies` drops `notes`; `core/seed.py`
  stops writing `notes`. Capability fields stay.

---

## Phase 3 — Method-change inventory (Agent 1 verification pass)

The complete audit of `core/storage.py` (98+ methods) is folded in below; the
line numbers are from the current `main` tree. Grouped by user-space table with
the exact change. The binding contract (filter reads, set `user_id` on writes) is
[contracts/storage-user-binding.md](contracts/storage-user-binding.md); the
explicit cross-user exceptions are listed there and repeated in §5.

### 3a. `search_profiles` — add `user_id`
- `upsert_profile` (1154), `get_profile` (1164), `get_all_profiles` (3609),
  `delete_profile` (3616): write/set `user_id`; filter reads on the bound user.
  `delete_profile` also deletes `job_scores` — scoped so a user deletes only
  their own profile + scores.

### 3b. `job_tracking` / `job_applications` — rekey `(user_id, job_id)`
Every method that addresses these by `job_id` alone gains `user_id` in the
WHERE / ON CONFLICT key:

- `set_status` (1693), `record_lifecycle_event` (1829),
  `reconcile_lifecycle_status` (2096), `save_application` (2364),
  `save_prepared_application` (2381), `get_tracking_status` (3943).
- Readers with a `LEFT JOIN job_tracking t ON j.id = t.job_id`: add
  `AND t.user_id = ?`: `get_digest` (2251), `get_stats` (2284, both branches),
  `get_job_for_prepare` (2418), `get_jobs_for_company` (3460),
  `get_all_jobs_best_score` (3626), `get_all_for_tracker` (3681),
  `get_cohort_jobs` (4118), `get_classified_jobs` (4144),
  `find_jobs_by_company` (3442), `get_jobs_for_scoring` (1408).
- `INNER JOIN job_tracking t` (engagement gating): `get_queued_jobs_unprepared`
  (2457), `get_queued_jobs` (2470), `get_calibration_signals` (3963),
  `get_calibration_deltas` (4199), `get_archived_notes` (4226),
  `get_few_shot_anchors` (4234), `get_action_item_candidates` (4272) — add
  `AND t.user_id = ?`.
- `job_applications`: `get_application` (2349), `get_all_applications` (2357,
  add filter), `get_applications_count` (4139, add filter).

### 3c. `status_history` / `company_status_history` — add `user_id`
- Writes via `set_status`, `record_lifecycle_event`, `reconcile_lifecycle_status`,
  `set_company_status` set `user_id`. No dedicated readers of `status_history`
  beyond those writers and the one-time migration.

### 3d. `contacts` — add `user_id`, per-user uniqueness
- `_lookup_contact` (2996) — scope all three deconfliction branches
  (`linkedin_url`, `email+company_id`, name-within-company) by `user_id`.
- `upsert_contact` (3027), `get_company_contacts` (3256), `get_contact` (3295),
  `get_all_contacts` (3505), `update_contact_fields` (3920),
  `bulk_mark_contacts_verified` (3932), `get_contacts_discovered_for_job` (3951),
  `get_contacts_by_role_family` (3908), `get_company_relationship_summary` (3230)
  — filter/set `user_id`. Contact-count subqueries in `get_companies` (3303) /
  `get_company_by_id` (3405) add `WHERE user_id = ?` (or join on `user_id`).

### 3e. `interactions` — add `user_id`, per-user uniqueness
- `record_lifecycle_event` (1829) — the idempotency read and insert gain
  `user_id`; `_auto_log_status_interaction` (3574) sets `user_id` (D9).
- `log_interaction` (3148), `get_lifecycle_events` (1931),
  `update_lifecycle_event` (2043), `delete_lifecycle_event` (2081),
  `get_contact_relationship_status` (3193), `get_company_interactions` (3273),
  `get_contact_interactions` (3284), `build_application_rows` (2158),
  `build_activity_rows` (2189), `get_company_relationship_summary` (3230),
  `_validate_lifecycle_date` (1794), `get_dashboard_data` (3732) — filter/set.
- Interaction-count / last-occurrence subqueries in `get_companies` (3303),
  `get_company_by_id` (3405), `get_all_contacts` (3505) — add `user_id`.

### 3f. `get_dashboard_data` (3732), `get_database_stats` (3877)
Both aggregate across user tables (`contacts`, `interactions`, `job_tracking`,
`company_status`). Add `user_id` filters to every user-space subquery; the
`jobs`/`job_scores` totals stay catalogue-wide.

### 3g. `companies` relationship columns (see §2)
`set_company_status` (1315), `set_company_monitored` (2485),
`update_company_fields` `notes` (2656), `get_companies` (3303),
`get_company_by_id` (3405), `get_dashboard_data` stale-outreach (3732),
`count_blacklisted_jobs` (2335), `get_monitored_company_for_job` (2629).

---

## Phase 4 — Call-site repointing (D10)

`active_profile_id` / `onboarding_complete` move out of `config`. Add `JobStorage`
accessors (`get_active_profile_id`, `set_active_profile_id`,
`get_onboarding_completed`, `set_onboarding_completed`) and repoint every call
site that currently does `get_config("active_profile_id")` /
`get_config("onboarding_complete")` — chiefly `core/profiles.py:load_active_profile`
(163) and the ~11 others in `tracker_views/*` and `core/` (grep-confirmed at
implementation). Old `config` keys stay dead (D13).

---

## Phase 5 — Cross-user exceptions (do NOT filter)

| Method | Why unfiltered |
|---|---|
| `purge_stale_jobs` (2896), `count_purgeable_jobs` (2879) | engagement = any user (FR-011) |
| `get_engaged_job_keys` (1656) | scrape dedupe keeps jobs any user engaged |
| `get_monitored_companies` (2493), `get_watching_companies_by_method` (2542), `get_watch_pending_companies` (2684), `get_companies_by_ats_provider` (2530) | monitoring union across users (FR-010) |

`purge_stale_jobs` still deletes catalogue rows (`jobs`, `job_scores`) only when
no user has engaged (the existing queries already read without a user filter, so
once the tables carry `user_id` the "any user" semantics fall out unchanged —
D8).

---

## Phase 6 — Tests

- `tests/test_isolation.py` (new, D12): two users in `:memory:`, drive every
  public user-space method as A, assert every public read as B returns none of
  A's data (and vice versa). Module-level `USER_SPACE_METHODS` list asserted to
  cover the full set (FR-012).
- `tests/test_storage.py`: update only the assertions that pin the old schema
  (`job_id`-only PK, raw column lists), never delete (FR-013).
- `tests/test_guard_db_access.py`: unchanged (already allows `scripts/`).
- `scripts/migration_report.py` (new, D11): read-only `sqlite3` diagnostics —
  row counts per table, rows-without-`user_id` per user-space table, `users` and
  `user_companies` counts. `scripts/` is out of scope of the raw-SQL guard.

---

## Phase 7 — Release (US3)

Standard flow, plus:

1. `scripts/migration_report.py` **before** on the staging backup copy.
2. `scripts/staging.sh up <exact SHA>` → migration runs on staging copy;
   `migration_report` **after**; parity fingerprint identical; tracker checklist.
3. Rehearse the rollback on staging (restore pre-rebuild backup + previous image),
   and record its measured duration (FR-015).
4. Prod: `git merge --ff-only <sha>` → `scripts/deploy.sh` on verva → confirm
   `git -C /opt/job-agent rev-parse HEAD` = merged SHA; `deploy.sh`'s
   `core.seed` step is the first opener (Edge Case: runs the migration before cron).

## Non-goals (unchanged from spec)

No auth, no second real user, no API, no `job_scores`/`runs.profile_id` change,
no ingestion-scope change, no old-column drops, no UI change.
