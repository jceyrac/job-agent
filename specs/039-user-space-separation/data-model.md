# Data Model — Shared Catalogue vs User Space (spec 039)

Two spaces, per Constitution X. Every table below is classified **catalogue**
(no user) or **user space** (keyed `user_id`). The seam is a single `user_id`
column; no authentication, one user row (non-goal: multi-user is not built here).

## Spaces

- **Catalogue** (unchanged, no `user_id`): `jobs`, `job_scores`, `runs`,
  `migrations`, `config` (platform keys), and the **fact** columns of `companies`.
- **User space** (`user_id` on every row): `search_profiles`, `job_tracking`,
  `status_history`, `job_applications`, `contacts`, `interactions`,
  `company_status_history`, and the new `user_companies`.

---

## New table: `users`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | opaque UUID (`uuid.uuid4().hex`); the `user_id` on every user-space row |
| `display_name` | TEXT | human label (`"Jean Claude"`), never a key |
| `active_profile_id` | TEXT | moved from `config` (Q2) |
| `onboarding_completed_at` | TEXT | moved from `config.onboarding_complete` (Q2) |
| `created_at` | TEXT NOT NULL | |

Migration creates exactly one row (the default user) and records its id in
`config.default_user_id`. Fresh init also creates it (onboarding works unchanged).

---

## New table: `user_companies`

The user's relationship to a company — status, notes, watch decision.

| Column | Type | Notes |
|---|---|---|
| `user_id` | TEXT NOT NULL | FK → `users(id)` |
| `company_id` | INTEGER NOT NULL | FK → `companies(id)` |
| `status` | TEXT NOT NULL DEFAULT `'prospect'` | `COMPANY_STATUSES` vocabulary |
| `notes` | TEXT | user's private notes |
| `monitored` | INTEGER NOT NULL DEFAULT 0 | user's watch decision |
| `watch_requested_at` | TEXT | when the user asked to watch |
| `created_at` | TEXT NOT NULL | |
| **PK** | `(user_id, company_id)` | |

**"No row = default relationship"** invariant: `status='prospect'`, `notes=NULL`,
`monitored=0`. All reads LEFT JOIN + `COALESCE`. Backfill inserts a row only for
companies with a non-default relationship (`status != 'prospect'` OR non-empty
`notes` OR `monitored = 1`).

---

## `companies` — split into facts (catalogue) vs relationship (→ `user_companies`)

**Stays on `companies` (facts / capability):** `id`, `name`, `name_normalized`,
`website`, `careers_url`, `summary`, `company_country`, `industry_sector`,
`company_size`, `enriched_at`, `enriched_by`, `ats_provider`, `ats_identifier`,
`scraper_id`, `detected_at`, `x_handle`, `research_notes`, `research_confidence`,
`researched_at`, `scraping_method`, `first_seen_at`, `last_seen_at`, `created_at`,
and **`monitoring_status` re-valued to capability-only** (`watch_ready` /
`watch_unsuitable` / `watch_pending` / `unmonitored` — the `watching` value is
dropped).

**Moves to `user_companies` (kept unused on `companies` until step 10):**
`status`, `notes`, `monitored`. The user-request meaning of `monitoring_status`
(`watching`) moves to `user_companies.monitored`.

| Column on `companies` | Nature | After |
|---|---|---|
| `status` | user decision | → `user_companies.status` (old column kept, unused) |
| `notes` | user decision | → `user_companies.notes` (old column kept, unused) |
| `monitored` | user decision | → `user_companies.monitored` (old column kept, unused) |
| `monitoring_status` | mixed | re-valued capability-only; `watching` → `watch_ready` + `user_companies.monitored=1` |

**Effective scrape target** = `user_companies.monitored = 1
AND companies.monitoring_status = 'watch_ready'` (union across users).

---

## Changed tables

### `search_profiles` — + `user_id`
`id TEXT PK, name, criteria` → add `user_id TEXT NOT NULL` (FK `users.id`),
backfill to default user. A profile belongs to a user (Constitution III).

### `job_tracking` — rekey `(user_id, job_id)` (Q1 rebuild)
Columns: `user_id TEXT NOT NULL, job_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT
'new', notes TEXT, changed_at TEXT`, **PK `(user_id, job_id)`**. Rebuilt via
create→copy→drop→rename inside the migration transaction. `idx_tracking_status`
recreated.

### `job_applications` — rekey `(user_id, job_id)` (Q1 rebuild)
Columns: `user_id TEXT NOT NULL, job_id TEXT NOT NULL, profile_id TEXT, analysis,
cover_letter, cv_bullets_selected, company_research, screening_answers, language,
prepared_by, prepared_at, created_at`, **PK `(user_id, job_id)`**.

### `status_history` — + `user_id`
`id INTEGER PK, job_id, status, changed_at` → add `user_id TEXT NOT NULL` (keep the
autoincrement `id` PK; no rebuild). Backfill to default user.
`idx_status_history_job (job_id, changed_at DESC)` kept; add a `user_id` index.

### `company_status_history` — + `user_id`
`id INTEGER PK, company_id, status, note, changed_at` → add `user_id TEXT NOT NULL`
(history of the user's relationship status). Backfill to default user.

### `contacts` — + `user_id`, per-user uniqueness
Add `user_id TEXT NOT NULL`. Unique index becomes **`(user_id, linkedin_url)
WHERE linkedin_url IS NOT NULL`** (replaces the global `idx_contacts_linkedin`).
Two users may hold a contact with the same `linkedin_url`.

### `interactions` — + `user_id`, per-user uniqueness
Add `user_id TEXT NOT NULL`. `idx_interactions_source_ref` becomes
**`(user_id, job_id, type, source_ref) WHERE source_ref IS NOT NULL`**.

### `config` — platform keys only
Add `default_user_id` (default user id). `active_profile_id` and
`onboarding_complete` become dead keys (values migrated to `users`; deleted at
step 10).

### `job_scores`, `runs`, `migrations`, `jobs` — unchanged
`job_scores` keeps `PRIMARY KEY (job_id, profile_id)` (a score is the user's view
through their profile); `runs.profile_id` unchanged; no `user_id` on catalogue rows.

---

## Key relationships

```
users 1 ──< search_profiles
users 1 ──< job_tracking      (user_id, job_id)
users 1 ──< job_applications  (user_id, job_id)
users 1 ──< status_history
users 1 ──< contacts
users 1 ──< interactions
users 1 ──< company_status_history
users 1 ──< user_companies ──> companies (facts)
```

`jobs` (catalogue) is referenced by `job_tracking`, `job_applications`,
`status_history`, `interactions` via `job_id` — but the *ownership* of those rows is
the user, never the job.
