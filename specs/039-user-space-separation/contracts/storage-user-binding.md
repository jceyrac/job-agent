# Contract — `JobStorage` user binding

The single seam of spec 039. Everything else in the migration and the isolation
test hangs off this contract. `core/storage.py` is the only file that changes in
the codebase; the contract below is the checklist the implementation must meet.

## Constructor

```python
JobStorage(db_path=DB_PATH, user_id=None)
```

- `user_id is None` (every existing call site) → bind to the **default user**:
  resolve `config.default_user_id`; if absent (pre-migration open of a legacy DB,
  the migration creates it), the init path creates the default user and the key.
- `user_id = X` → bind to `X` for the lifetime of the instance. `self.user_id`
  is immutable after `__init__`.

## Read rule

Every method that reads a user-space table — `search_profiles`, `job_tracking`,
`status_history`, `job_applications`, `contacts`, `interactions`,
`company_status_history`, `user_companies`, and the user-relationship columns on
`companies` (`status`, `notes`, `monitored` via `user_companies`) — filters on
`self.user_id`:

- `job_tracking`, `job_applications`: the `job_id`-only WHERE / `ON CONFLICT`
  key gains `user_id`.
- `contacts`, `interactions`, `status_history`, `company_status_history`,
  `search_profiles`: add `WHERE … AND user_id = ?` (and `user_id = ?` in any
  JOIN/ON or unique-index context).
- `companies` relationship columns: read through `LEFT JOIN user_companies uc
  ON uc.company_id = c.id AND uc.user_id = ?` with `COALESCE` to the default
  relationship (`status='prospect'`, `notes=NULL`, `monitored=0`).

Catalogue reads (`jobs`, `job_scores`, `runs`, `migrations`, `config` platform
keys, company **facts**) ignore `self.user_id`.

## Write rule

Every method that writes a user-space table sets `self.user_id`:

- `INSERT` / `UPDATE` / `DELETE` on `job_tracking`, `job_applications`,
  `status_history`, `contacts`, `interactions`, `company_status_history`,
  `search_profiles`, `user_companies` include `user_id` (column on INSERT,
  `WHERE user_id = ?` on UPDATE/DELETE).
- Company relationship writes (`set_company_status`, `set_company_monitored`,
  `set_monitoring_status`'s user-request part, `update_company_fields` on
  `notes`) go to `user_companies`, never to the old `companies.status` /
  `companies.notes` / `companies.monitored` columns (kept, unused).

## Explicit exceptions (documented in the plan)

These read user-space tables **without** a `user_id` filter, deliberately, because
they operate on the shared catalogue, not one user's view:

| Method | Reason |
|---|---|
| `purge_stale_jobs` / `count_purgeable_jobs` | engagement is "any user" (FR-011) |
| `get_engaged_job_keys` | scrape dedupe keeps jobs any user engaged with |
| monitoring target readers (`get_monitored_companies`, `get_watching_companies_by_method`, `get_watch_pending_companies`, `get_companies_by_ats_provider`) | the monitoring set is the **union across users** (FR-010, Constitution VIII) |

## Identity & settings accessors (repointed from `config`)

`load_active_profile` in `core/profiles.py` and ~12 call sites currently read
`config.active_profile_id` / `config.onboarding_complete`. These are replaced by
`JobStorage` accessors that read/write the **bound user's** row:

- `get_active_profile_id() -> str`
- `set_active_profile_id(value)`
- `get_onboarding_completed() -> bool | None`
- `set_onboarding_completed(value)`

## Invariant

For any two instances `A = JobStorage(p, user_id="a")`, `B = JobStorage(p,
user_id="b")` over the same DB: every public user-space read method called on `B`
returns none of the rows `A` wrote, and vice versa. This is what
`tests/test_isolation.py` asserts (FR-012), and a module-level `USER_SPACE_METHODS`
list asserts coverage so a new user-space method without an isolation test fails
the suite.
