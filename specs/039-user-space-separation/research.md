# Research — Shared Catalogue vs User Space (spec 039)

Resolves the `NEEDS CLARIFICATION` items from the plan's Technical Context. Each
decision is grounded in a read of the current source (`core/storage.py` at 4429
lines, `core/profiles.py`, `core/models.py`, `core/seed.py`, `core/monitoring_agent.py`,
`core/company_researcher.py`, `scripts/fingerprint.py`, `tests/`) against spec 039
and Constitution v2.0.2.

---

## D1 — Default user identity

- **Decision**: a `users` table with `id TEXT PRIMARY KEY`, `display_name TEXT`,
  `active_profile_id TEXT`, `onboarding_completed_at TEXT`, `created_at TEXT NOT NULL`.
  The default user id is an opaque UUID (`uuid.uuid4().hex`), generated once and
  stored in `config.default_user_id`. `display_name` is the human label (`"Jean Claude"`).
- **Rationale**: Q3 resolved to "opaque UUID" — no personal data in keys, stable under
  rename. `display_name` is human-facing only and never used as a key. The single-user
  app has exactly one row; multi-user is out of scope (non-goal).
- **Alternatives rejected**: readable slug (`jc`) — personal data in a key, breaks on
  rename, no benefit at one row.

## D2 — The `user_id` seam on `JobStorage`

- **Decision**: `JobStorage.__init__(self, db_path=DB_PATH, user_id=None)`. When
  `user_id is None` (all current callers), the instance binds to the default user —
  resolved by reading `config.default_user_id` (and creating the default user + key if
  absent, which the migration does anyway). `self.user_id` is set on every user-space
  read (added `WHERE … user_id = ?`) and write (added `user_id` column in INSERT/UPDATE).
  Catalogue methods (`jobs`, extraction, `runs`, `job_scores`, company facts) ignore
  `self.user_id`.
- **Rationale**: FR-008/FR-009. "No caller changes" for single-user behaviour: every
  existing `JobStorage(db_path)` call site is unchanged because `None` → default user.
- **Alternatives rejected**: requiring `user_id` on every call site (churn + breaks the
  single-user guarantee); a global/thread-local "current user" (hidden state, breaks
  the in-memory two-user isolation test).

## D3 — Migration transactionality & idempotency

- **Decision**: one named migration `user_space_separation`, guarded by
  `_migration_applied` / `_record_migration` (the existing `migrations` table pattern),
  run inside a **single transaction** (`BEGIN` … `COMMIT`, `ROLLBACK` on any failure) in
  `_init_db` after the existing migrations. On a fresh DB the same code path creates the
  `users` table via `SCHEMA` and then creates the default user (no backfill to do).
- **Rationale**: FR-007. The existing `_init_db` runs `conn.executescript(SCHEMA)` first
  (idempotent `CREATE TABLE IF NOT EXISTS`), then migrations. The new tables are added to
  `SCHEMA` (for fresh installs) *and* the migration handles existing DBs (for upgrades).
  The migration must be one transaction so a mid-migration crash leaves the DB exactly
  as before (US1 acceptance scenario 4).
- **Alternatives rejected**: many small recorded migrations — they'd each need their own
  rollback reasoning and would risk a partially-migrated DB between steps.

## D4 — Rebuild of `job_tracking` and `job_applications`

- **Decision**: rekey both tables to `(user_id, job_id)` in-place via the SQLite
  table-rebuild procedure, inside the D3 transaction:
  1. `CREATE TABLE job_tracking_new (user_id TEXT NOT NULL, job_id TEXT NOT NULL, status …,
     notes …, changed_at …, PRIMARY KEY (user_id, job_id))` (mirror all current columns).
  2. `INSERT INTO job_tracking_new (user_id, job_id, status, notes, changed_at)
     SELECT :default_user, job_id, status, notes, changed_at FROM job_tracking`.
  3. `DROP TABLE job_tracking`; `ALTER TABLE job_tracking_new RENAME TO job_tracking`.
  4. Recreate `idx_tracking_status` and any other indexes.
  Same for `job_applications` (keep all columns incl. `profile_id`, `cv_bullets_selected`, …).
  `status_history` keeps its `id` PK and just gains a `user_id` column (no rebuild — its PK
  is an autoincrement id, not `job_id`).
- **Rationale**: Q1 resolved to "rebuild now". SQLite can't alter a PK in place; doing it
  now, with one user's data, is cheap and reversible (backup + staging + rehearsed
  rollback per US3). Deferring leaves a broken seam (second user can't track a job the
  first already tracks) until step 10.
- **Alternatives rejected**: add `user_id` + unique index, keep `job_id` PK (the "defer"
  option) — rejected by Q1.

## D5 — `user_companies` and "no row = default relationship"

- **Decision**: `user_companies (user_id TEXT NOT NULL, company_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'prospect', notes TEXT, monitored INTEGER NOT NULL DEFAULT 0,
  watch_requested_at TEXT, created_at TEXT NOT NULL, PRIMARY KEY (user_id, company_id),
  FOREIGN KEY (company_id) REFERENCES companies(id))`.
  Backfill (one row per company with a **non-default** relationship):
  `INSERT INTO user_companies (user_id, company_id, status, notes, monitored)
   SELECT :default_user, id, status, notes, (monitored IS NOT NULL AND monitored)
   FROM companies
   WHERE status != 'prospect' OR (notes IS NOT NULL AND trim(notes) != '')
         OR (monitored IS NOT NULL AND monitored)`.
  Reads treat "no `user_companies` row" as the default relationship (`status='prospect'`,
  `notes=NULL`, `monitored=0`) — a `LEFT JOIN` + `COALESCE`, so the feed/dashboard are
  byte-identical for companies with only default values.
- **Rationale**: FR-004, US1 scenario 3, Edge Case "default values only → no row". Only
  the user's *decision* data moves; company facts stay on `companies`.
- **Alternatives rejected**: one `user_companies` row per company unconditionally (bloat,
  and a schema that implies a relationship that doesn't exist yet).

## D6 — `monitoring_status` split (request vs capability)

- **Current reality (verified against every writer)**: two overlapping flags.
  - `companies.monitored` (BOOL) = user intent, but **not** the scrape gate.
  - `companies.monitoring_status` (TEXT: `unmonitored` | `watch_pending` | `watching` |
    `watch_ready` | `watch_unsuitable`). The **effective scrape target is
    `monitoring_status = 'watching'`** — read by `get_monitored_companies`
    (`core/main.py:66`, `core/scrape.py:199`) and `get_watching_companies_by_method`
    (the ATS scrapers). `monitoring_agent.py` (dev tool) and `company_researcher.py`
    write only `monitoring_status` (capability transitions: `watch_ready` when
    `scraping_method` is a known ATS, else `watch_pending`; `watch_unsuitable` when
    none/manual/unknown). The Settings page toggles `monitored` **without** syncing
    `monitoring_status` (latent inconsistency — the toggle has no effect on the scrape
    target there).
- **Decision** (final mapping):
  - **User request** → `user_companies.monitored` (bool) + `user_companies.watch_requested_at`.
    Backfilled `monitored = 1` for any company where `monitored = 1`
    **OR** `monitoring_status = 'watching'`.
  - **Capability** → `companies.monitoring_status` re-valued to the capability-only
    vocabulary: `watch_ready` / `watch_unsuitable` / `watch_pending` (awaiting research)
    / `unmonitored`. The `watching` value is dropped: every `watching` row becomes
    `watch_ready` (it implied capable) with the user flag moved to `user_companies`.
  - **Effective scrape target** (new) = `user_companies.monitored = 1
    AND companies.monitoring_status = 'watch_ready'`, computed as the union across users
    (one user today). This is byte-identical to the old `monitoring_status='watching'`
    set — a company that was `watch_ready` but not `monitored` was not scraped before and
    is not scraped now.
  - `get_monitored_companies` / `get_watching_companies_by_method` rewrite to that
    predicate; `get_watch_pending_companies` becomes
    `user_companies.monitored = 1 AND monitoring_status = 'watch_pending'`.
  - The split gives "watched" a **single** source of truth, which also removes the
    Settings-page toggle/sync gap as a side effect (not a separate UI change).
- **Rationale**: FR-005 + Constitution X/VIII. The current column conflates "I asked to
  watch" with "a scraper can watch it"; splitting them is what lets the monitoring union
  (spec VIII) be a pure read of `user_companies` across users.
- **Alternatives rejected**: keeping `monitoring_status` mixed and adding a parallel
  `user_companies` (two sources of truth for the same decision).

## D14 — Seed / export contract (user fields leave the git seed)

- **Decision**: `get_export_seed_companies` (`core/storage.py:4082`) and `core/seed.py`
  currently round-trip `companies.notes` (user decision) and `monitoring_status`
  (capability) into/out of the git-committed `data/companies.json`. After the split,
  **`notes` leaves the seed round-trip** (it is user-space now): `get_export_seed_companies`
  drops `notes`, and `core/seed.py` stops writing `notes`. Capability fields
  (`monitoring_status='watch_pending'`, `ats_provider`, `ats_identifier`,
  `scraping_method`, `research_notes`, `research_confidence`, `x_handle`,
  `industry_sector`, `company_country`) stay in the seed. `status`/`monitored` were
  already default-only in the seed (never set) and remain absent.
- **Rationale**: Constitution X — a user's private `notes` must not be committed to a
  shared seed file. FR-005's "read every writer" includes `core/seed.py` and the
  `core/export_seed.py` path (invoked by `monitoring_agent.py:394`).
- **Alternatives rejected**: keeping `notes` in the export (leaks user data into git);
  moving the whole seed to `user_companies` (facts are catalogue data and must stay in
  `companies`).

## D7 — Per-user unique indexes

- **Decision**:
  - `contacts`: drop `idx_contacts_linkedin` (global `(linkedin_url)`), create
    `(user_id, linkedin_url) WHERE linkedin_url IS NOT NULL`. Keep `(company_id)`,
    `(email)`, `(is_unverified)` but they must not contradict the new per-user uniqueness
    (they don't — they're non-unique lookups). Add `user_id` to `idx_contacts_company` if
    the common query filters on user first (determined in implementation).
  - `interactions`: `idx_interactions_source_ref` currently `(job_id, type, source_ref)
    WHERE source_ref IS NOT NULL` → becomes `(user_id, job_id, type, source_ref)
    WHERE source_ref IS NOT NULL`.
  - Add a plain index on `user_id` for `status_history`, `company_status_history`,
    `job_tracking`, `job_applications`, `interactions`, `contacts` where the hot query
    filters on `user_id`.
- **Rationale**: FR-006 + the spec's "unique index `idx_interactions_source_ref` and
  `idx_contacts_linkedin` must become per-user". Two users may each hold a contact with
  the same `linkedin_url` (US2 scenario 6).
- **Alternatives rejected**: a global unique index plus a `user_id` filter (wrong — it
  would still reject the second user's identical `linkedin_url`).

## D8 — Purge & engagement are cross-user

- **Decision**: `purge_stale_jobs` and `count_purgeable_jobs` must **not** add a
  `user_id` filter to their engagement checks. The existing queries already read
  `job_tracking` / `interactions` / `job_applications` without a user filter, so once
  those tables carry `user_id` the "engaged by any user" semantics fall out naturally:
  a catalogue job is purged only if *no* row (for *any* user) marks it engaged.
- **Rationale**: FR-011 + the spec's "a catalogue job is never purged while any user has
  engaged with it". Same reasoning applies to `get_engaged_job_keys` (scrape dedupe must
  keep jobs any user engaged with).
- **Alternatives rejected**: filtering by the bound user (would delete another user's
  tracked job — wrong on a shared catalogue).

## D9 — `_auto_log_status_interaction` sets `user_id`

- **Decision**: the interaction row it inserts gains the bound `self.user_id`, so a
  status change writes a correctly-owned interaction.
- **Rationale**: spec "must carry the same `user_id`"; FR-009.

## D10 — `active_profile_id` / `onboarding_complete` move to `users`

- **Decision**: add `users.active_profile_id` and `users.onboarding_completed_at`
  (Q2 resolved to "move to `users`"). The migration copies the current values from
  `config` (`active_profile_id` → `users.active_profile_id`;
  `onboarding_complete == 'true'` → `users.onboarding_completed_at = now()`) into the
  default user. Add `JobStorage` accessors — `get_active_profile_id()`,
  `set_active_profile_id(v)`, `get_onboarding_completed()`,
  `set_onboarding_completed(v)` — and repoint the ~12 current `get_config`/`set_config`
  call sites (see plan "Call-site repointing"). The old `config` keys are left as dead
  keys (cleaned at step 10), so a rollback that redeploys the previous SHA still finds
  them.
- **Rationale**: FR-001. Per-user decisions belong on the user, not on the platform
  `config` table (Constitution X).
- **Alternatives rejected**: keep them in `config` (Q2's "keep" option) — rejected.

## D11 — Migration report

- **Decision**: `scripts/migration_report.py`, run as `python -m scripts.migration_report
  --db <path>`, prints (a) row count per table, (b) rows without `user_id` per user-space
  table (must be 0 after migration), (c) `user_companies` count, (d) `users` rows. Uses a
  raw read-only `sqlite3` connection exactly like `scripts/fingerprint.py` `_table_counts`
  — `scripts/` is out of scope of the spec-037 raw-SQL guard, so this is allowed.
- **Rationale**: FR-014 / US3 scenario 1. Read-only diagnostic, never mutates.

## D12 — Isolation test + coverage guard

- **Decision**: a new `tests/test_isolation.py` (in-memory DB) creates two users (A, B),
  drives every public user-space method as A, and asserts every public user-space read as
  B returns none of A's data (and vice versa). A module-level list
  `USER_SPACE_METHODS` in the test is asserted to cover the full set of user-space public
  methods (any new user-space method without an isolation test fails the suite) — FR-012.
  Existing `tests/test_storage.py` keeps passing except where it asserts the old schema
  (e.g. a raw `SELECT * FROM job_tracking` column list, or `job_id`-only PK); those are
  updated, never deleted (FR-013).
- **Rationale**: FR-012/FR-013 + Constitution XI (parity). The in-memory `:memory:` path
  already runs `_init_db` (the seam must work there), so `JobStorage(":memory:",
  user_id=…)` exercises the full migration on every test.

## D13 — `config` keys left in place

- **Decision**: the migration writes `config.default_user_id`; it leaves
  `config.active_profile_id` and `config.onboarding_complete` untouched (dead) rather than
  deleting them.
- **Rationale**: expand/contract (Constitution XI) — deletion is step 10. A rollback
  (redeploy previous SHA) still reads the old keys and behaves correctly.

---

## Open-but-low-impact items (defaulted, not blocking)

- **Default `display_name`**: `"Jean Claude"` (the single real user). Trivial to change;
  never used as a key.
- **UUID format**: `uuid.uuid4().hex` (32 hex, dash-less) for compact, URL-safe keys.
