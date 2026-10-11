# Raw SQL Inventory — spec 037 (single DB access path)

Every raw SQL statement outside `core/storage.py`, catalogued for the refactor
that replaces each with a named `JobStorage` method. Verified at HEAD
(`8ccfc83`); all line numbers below were re-confirmed against the working tree.

Two access patterns appear:

- **Tracker/core modules using `db._conn()`** — connection context manager, but
  still issues raw `conn.execute(...)` statements that must move into `JobStorage`.
- **CLI/dev scripts using `sqlite3.connect(...)`** — full raw access to `DB_PATH`.

---

## Tracker raw-SQL sites

### tracker_views/dashboard.py:115

- **File:line**: `tracker_views/dashboard.py:115` — confirmed (block spans 115–123).
- **Read vs write**: Read.
- **Exact SQL statements** (six, in one `with db._conn() as conn:` block):

  ```sql
  SELECT COUNT(*) FROM jobs
  SELECT COUNT(*) FROM job_scores
  SELECT COUNT(DISTINCT job_id) FROM job_scores
  SELECT COUNT(*) FROM companies
  SELECT COUNT(*) FROM contacts
  SELECT COUNT(*) FROM contacts WHERE is_unverified = 1
  ```

- **Tables touched**: `jobs`, `job_scores`, `companies`, `contacts` (read-only).
- **Transaction structure**: single read-only block; no writes, no `commit()`.
  `unscored` is computed in Python (`total_jobs - distinct scored`).
- **Inputs/bindings**: none — no parameters.
- **Existing JobStorage coverage**: **NONE fully** — this "Database stats" tile
  is not produced by any single method. `get_stats(profile_id=None)` returns
  `total/scored/hot/solid/by_status` (jobs + scores only, no companies/contacts),
  and `get_dashboard_data()` returns `unverified_contacts_count` among its keys.
  `total_companies`, `total_contacts` have no existing getter. Closest:
  `get_stats` + `get_dashboard_data` (partial overlap).

### tracker_views/settings.py:488

- **File:line**: `tracker_views/settings.py:488` — confirmed (statement at 489–492).
- **Read vs write**: Write.
- **Exact SQL statement**:

  ```sql
  UPDATE companies SET monitored = 0 WHERE id = ?
  ```

- **Tables touched**: `companies` (`monitored`).
- **Transaction structure**: single statement; no explicit `commit()` (the
  `db._conn()` context handles it).
- **Inputs/bindings**: `comp.get("id")` — company id from a dict built earlier in
  `_render_company_monitoring` (data from `db.get_all_monitorable_companies()`).
- **Existing JobStorage coverage**: **EXACT — fully covered** by
  `set_company_monitored(company_id: int, monitored: bool) -> None` (storage.py:2485).

### tracker_views/settings.py:509

- **File:line**: `tracker_views/settings.py:509` — confirmed (statement at 510–513).
- **Read vs write**: Write.
- **Exact SQL statement**:

  ```sql
  UPDATE companies SET monitored = 1 WHERE id = ?
  ```

- **Tables touched**: `companies` (`monitored`).
- **Transaction structure**: single statement; no explicit `commit()`.
- **Inputs/bindings**: `comp.get("id")`.
- **Existing JobStorage coverage**: **EXACT — fully covered** by
  `set_company_monitored(company_id, monitored)`.

### tracker_views/settings.py:780

- **File:line**: `tracker_views/settings.py:780` — confirmed (block spans 780–789).
- **Read vs write**: Read.
- **Exact SQL statements** (seven, in one block):

  ```sql
  SELECT COUNT(*) FROM jobs
  SELECT COUNT(*) FROM job_scores
  SELECT COUNT(DISTINCT job_id) FROM job_scores
  SELECT COUNT(*) FROM companies
  SELECT COUNT(*) FROM contacts
  SELECT COUNT(*) FROM contacts WHERE is_unverified = 1
  SELECT COUNT(*) FROM interactions
  ```

- **Tables touched**: `jobs`, `job_scores`, `companies`, `contacts`, `interactions`.
- **Transaction structure**: single read-only block; no writes/commit.
- **Inputs/bindings**: none.
- **Existing JobStorage coverage**: **NONE fully** — same gap as dashboard.py:115,
  plus `total_interactions` which also has no getter. Closest: `get_stats` +
  `get_dashboard_data`.

### tracker_views/settings.py:798

- **File:line**: `tracker_views/settings.py:798` — confirmed (statement at 799–801).
- **Read vs write**: Read.
- **Exact SQL statement**:

  ```sql
  SELECT role_family, COUNT(*) FROM contacts GROUP BY role_family ORDER BY COUNT(*) DESC
  ```

- **Tables touched**: `contacts` (`role_family`).
- **Transaction structure**: single read-only statement; no commit.
- **Inputs/bindings**: none.
- **Existing JobStorage coverage**: **NONE** — no contacts-by-role aggregation method.

### tracker_views/job_detail.py:67

- **File:line**: `tracker_views/job_detail.py:67` — confirmed (function
  `_current_status`, statement at 68–70).
- **Read vs write**: Read.
- **Exact SQL statement**:

  ```sql
  SELECT status FROM job_tracking WHERE job_id = ?
  ```

- **Tables touched**: `job_tracking` (`status`).
- **Transaction structure**: single read; returns `"new"` when no row.
- **Inputs/bindings**: `job_id` — function argument.
- **Existing JobStorage coverage**: **NONE exact** — no public "get tracking
  status" getter. `set_status(job_id, status, notes)` (1693) is the write
  counterpart and reads the same row internally; `get_job_for_prepare` /
  `get_all_for_tracker` return `status` only as part of a wider row.

### tracker_views/job_detail.py:340

- **File:line**: `tracker_views/job_detail.py:340` — confirmed (statement at 341–347).
- **Read vs write**: Read.
- **Exact SQL statement**:

  ```sql
  SELECT DISTINCT ct.* FROM contacts ct
  JOIN interactions i ON i.contact_id = ct.id
  WHERE i.job_id = ? AND i.type = 'discovered_on_posting'
  ORDER BY ct.last_seen_at DESC
  ```

- **Tables touched**: `contacts`, `interactions`.
- **Transaction structure**: single read-only statement.
- **Inputs/bindings**: `job_id`.
- **Existing JobStorage coverage**: **NONE** — no "contacts discovered from a
  posting" query. Closest `get_company_contacts(company_id)` and
  `get_contact_interactions(contact_id)` operate on different keys.

### tracker_views/contact_detail.py:77

- **File:line**: `tracker_views/contact_detail.py:77` — confirmed (statement 78–81).
- **Read vs write**: Write.
- **Exact SQL statement**:

  ```sql
  UPDATE contacts SET is_unverified = 0, last_verified_at = ? WHERE id = ?
  ```

- **Tables touched**: `contacts` (`is_unverified`, `last_verified_at`).
- **Transaction structure**: single statement; no explicit `commit()`.
- **Inputs/bindings**: `now` (UTC ISO timestamp computed inline via
  `__import__("datetime")...`), `contact_id` (function arg).
- **Existing JobStorage coverage**: **NONE** — no "mark verified" method on contacts.

### tracker_views/contact_detail.py:86

- **File:line**: `tracker_views/contact_detail.py:86` — confirmed (statement 87–90).
- **Read vs write**: Write.
- **Exact SQL statement**:

  ```sql
  UPDATE contacts SET is_unverified = 1 WHERE id = ?
  ```

- **Tables touched**: `contacts` (`is_unverified`).
- **Transaction structure**: single statement; no explicit `commit()`.
- **Inputs/bindings**: `contact_id`.
- **Existing JobStorage coverage**: **NONE** — no "mark unverified" method.

### tracker_views/contact_detail.py:97

- **File:line**: `tracker_views/contact_detail.py:97` — confirmed (statement 98–101).
- **Read vs write**: Write.
- **Exact SQL statement**:

  ```sql
  UPDATE contacts SET is_current = 0 WHERE id = ?
  ```

- **Tables touched**: `contacts` (`is_current`).
- **Transaction structure**: single statement; no explicit `commit()`.
- **Inputs/bindings**: `contact_id`.
- **Existing JobStorage coverage**: **NONE**.

### tracker_views/contact_detail.py:134

- **File:line**: `tracker_views/contact_detail.py:134` — confirmed (statement 135–138).
- **Read vs write**: Write.
- **Exact SQL statement**:

  ```sql
  UPDATE contacts SET notes = ? WHERE id = ?
  ```

- **Tables touched**: `contacts` (`notes`).
- **Transaction structure**: single statement; no explicit `commit()`.
- **Inputs/bindings**: `new_notes` (`st.text_area` value), `contact_id`.
- **Existing JobStorage coverage**: **NONE** — `upsert_contact(..., notes=...)`
  handles notes on insert/update but is not a standalone notes setter and
  re-resolves the contact; no dedicated `update_contact_*` method exists.

### tracker_views/contacts.py:81

- **File:line**: `tracker_views/contacts.py:81` — confirmed (block 81–87).
- **Read vs write**: Write (bulk).
- **Exact SQL statement** (executed once per selected contact inside one block):

  ```sql
  UPDATE contacts SET is_unverified = 0, last_verified_at = ? WHERE id = ?
  ```

- **Tables touched**: `contacts` (`is_unverified`, `last_verified_at`).
- **Transaction structure**: **multiple statements in one `with db._conn() as conn:`
  block** — a `for ct in selected_for_verify` loop; the whole bulk-verify set is
  one transaction (no explicit `commit()`, context-managed).
- **Inputs/bindings**: `now` (UTC ISO), `ct["id"]` from `selected_for_verify`
  (a list of `load_contacts` dicts from the filter, i.e. derived from
  `db.get_all_contacts`).
- **Existing JobStorage coverage**: **NONE** — no bulk-verify method.

### tracker_views/company_detail.py:280

- **File:line**: `tracker_views/company_detail.py:280` — confirmed (statement 281–284).
- **Read vs write**: Write.
- **Exact SQL statement**:

  ```sql
  UPDATE companies SET notes = ? WHERE id = ?
  ```

- **Tables touched**: `companies` (`notes`).
- **Transaction structure**: single statement; no explicit `commit()`.
- **Inputs/bindings**: `new_notes` (`st.text_area` value), `company_id` (function arg).
- **Existing JobStorage coverage**: **NONE exact** — `update_company_fields(
  company_id, fields)` is the generic company updater but `"notes"` is NOT in
  `_ALLOWED_COMPANY_FIELDS` (storage.py:2650–2654), so it would raise
  `ValueError`. Closest: `update_company_fields` (would need `"notes"` added to
  the allowlist) or a new dedicated method.

### tracker_views/forms.py:52

- **File:line**: `tracker_views/forms.py:52` — confirmed (statement 53–56), inside
  the `add_company_dialog` `@st.dialog`.
- **Read vs write**: Write.
- **Exact SQL statement**:

  ```sql
  UPDATE companies SET notes = ? WHERE id = ?
  ```

- **Tables touched**: `companies` (`notes`).
- **Transaction structure**: single statement; no explicit `commit()`.
- **Inputs/bindings**: `notes.strip()` (form text area), `cid` (returned by
  `db.upsert_company(...)` just above).
- **Existing JobStorage coverage**: **NONE exact** — same `notes`-not-allowed
  caveat as company_detail.py:280.

---

## Core raw-SQL sites

### core/context_tuner.py:41

- **File:line**: `core/context_tuner.py:41` — confirmed (block spans 41–129),
  inside `propose_context_update(profile_id, db, *, dry_run)`.
- **Read vs write**: Read.
- **Exact SQL statements** (all in one `with db._conn() as conn:` block; note
  `conn.row_factory = None` at line 42):

  ```sql
  SELECT j.title, j.company, s.score, t.notes
    FROM jobs j
    JOIN job_tracking t ON j.id = t.job_id
    JOIN job_scores s   ON j.id = s.job_id
   WHERE t.status = 'archived' AND s.score >= 8
     AND s.profile_id = ?
     AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)
   ORDER BY s.score DESC
   LIMIT 10
  ```

  ```sql
  SELECT j.title, j.company, s.score, t.status
    FROM jobs j
    JOIN job_tracking t ON j.id = t.job_id
    JOIN job_scores s   ON j.id = s.job_id
   WHERE t.status IN ('applied', 'interviewing', 'offer', 'rejected', 'withdrawn') AND s.score <= 6
     AND s.profile_id = ?
   ORDER BY s.score ASC
   LIMIT 10
  ```

  Bucket loop (f-string, `{cond}` ∈ `"s.score >= 9"`, `"s.score BETWEEN 7 AND 8"`,
  `"s.score BETWEEN 5 AND 6"`, `"s.score <= 4"`):

  ```sql
  SELECT COUNT(*) FROM jobs j
   JOIN job_tracking t ON j.id = t.job_id
   JOIN job_scores s ON j.id = s.job_id
  WHERE t.status IN ('applied', 'interviewing', 'offer', 'rejected', 'withdrawn')
    AND s.profile_id = ?
    AND {cond}
    AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)
  ```

  ```sql
  SELECT COUNT(*) FROM jobs j
   JOIN job_tracking t ON j.id = t.job_id
   JOIN job_scores s ON j.id = s.job_id
  WHERE t.status = 'archived'
    AND s.profile_id = ?
    AND {cond}
    AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)
  ```

  ```sql
  SELECT COALESCE(c.industry_sector, 'other'), COUNT(*) AS n
    FROM jobs j
    JOIN job_tracking t ON j.id = t.job_id
    JOIN job_scores s ON j.id = s.job_id
    LEFT JOIN companies c ON j.company_id = c.id
   WHERE t.status = 'archived' AND s.score >= 8
     AND s.profile_id = ?
     AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)
   GROUP BY 1 ORDER BY n DESC LIMIT 5
  ```

  ```sql
  SELECT COALESCE(j.company_country, 'unknown'), COUNT(*) AS n
    FROM jobs j
    JOIN job_tracking t ON j.id = t.job_id
    JOIN job_scores s ON j.id = s.job_id
   WHERE t.status = 'archived' AND s.score >= 8
     AND s.profile_id = ?
     AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)
   GROUP BY 1 ORDER BY n DESC LIMIT 5
  ```

- **Tables touched**: `jobs`, `job_tracking`, `job_scores`, `companies`.
- **Transaction structure**: single read-only block; no writes/commit. Results
  fed to the LLM prompt (tuple rows).
- **Inputs/bindings**: `profile_id` — function argument.
- **Existing JobStorage coverage**: **NONE** — these are calibration-signal
  analytics queries (over/under-scored cohorts, score-bucket distribution,
  sector/country patterns); no existing method returns them.

### core/prepare.py:552

- **File:line**: `core/prepare.py:552` — confirmed (function `_auto_pick_profile`,
  statement at 553–558).
- **Read vs write**: Read.
- **Exact SQL statement**:

  ```sql
  SELECT profile_id FROM job_scores
   WHERE job_id = ? AND score IS NOT NULL
   ORDER BY score DESC LIMIT 1
  ```

- **Tables touched**: `job_scores` (`profile_id`, `score`).
- **Transaction structure**: single read-only statement.
- **Inputs/bindings**: `job_id` — function argument.
- **Existing JobStorage coverage**: **NONE exact** — `get_scores_for_job(job_id)`
  returns all scores ordered by `scored_at DESC` (full rows), not the top-score
  profile. Closest: `get_scores_for_job`; a dedicated "best-score profile for
  job" is needed.

### core/score.py:175

- **File:line**: `core/score.py:175` — confirmed (function
  `_delete_scores_for_profile(db_path, profile_id)`, lines 173–180). Uses
  `sqlite3.connect` (module-level `import sqlite3` at score.py:4).
- **Read vs write**: Write (delete) + read `changes()`.
- **Exact SQL statements**:

  ```sql
  DELETE FROM job_scores WHERE profile_id = ?
  SELECT changes()
  ```

- **Tables touched**: `job_scores`.
- **Transaction structure**: two statements with an **explicit
  `conn.commit()`** then `conn.close()` — delete + row-count must stay atomic.
- **Inputs/bindings**: `profile_id`, `db_path` — both function arguments
  (`db_path` is `DB_PATH` from `core.paths`; called from the `--rescore` flow).
- **Existing JobStorage coverage**: **NONE exact** — `delete_profile(profile_id)`
  (3617) deletes the same `job_scores` rows **and** the `search_profiles` row
  (returns `(scores_deleted, profile_deleted)`), so it is not usable for a
  rescore that must keep the profile. A "delete scores only" method is needed.

### core/score.py:317

- **File:line**: `core/score.py:317` — confirmed (function `_get_jobs_to_score`,
  lines 314–333). Uses `sqlite3.connect`.
- **Read vs write**: Read.
- **Exact SQL statements** (two variants):

  rescore branch:

  ```sql
  SELECT j.* FROM jobs j
  LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
  WHERE s.status IS NULL OR s.status != 'rejected'
  ```

  normal branch:

  ```sql
  SELECT j.* FROM jobs j
  LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
  WHERE (s.job_id IS NULL OR s.score IS NULL)
    AND (s.status IS NULL OR s.status != 'rejected')
  ```

- **Tables touched**: `jobs`, `job_scores`.
- **Transaction structure**: single read-only statement; `conn.row_factory =
  sqlite3.Row`; no commit.
- **Inputs/bindings**: `profile_id`, `rescore` (bool), `db_path` — function args.
- **Existing JobStorage coverage**: **NONE exact, but close** —
  `get_jobs_for_scoring(profile_id, pre_filter=None, rescore=False)` (1408)
  already selects "jobs that need scoring" with the same rescore/non-rescore
  split plus company joins, blacklist exclusion and a freshness window. The raw
  query is narrower (no freshness/pre-filter/company join). Closest:
  `get_jobs_for_scoring` — likely reusable but with behavioural differences to
  confirm.

### core/export_seed.py:20

- **File:line**: `core/export_seed.py:20` — confirmed (function `main`, lines 15–50).
  Uses `sqlite3.connect` (module-level `import sqlite3` at export_seed.py:9).
- **Read vs write**: Read.
- **Exact SQL statement**:

  ```sql
  SELECT name, website, careers_url, ats_provider, ats_identifier,
         scraping_method, monitoring_status,
         research_notes, research_confidence, x_handle,
         industry_sector, company_country, notes
  FROM companies
  WHERE monitoring_status != 'unmonitored'
     OR ats_provider IS NOT NULL
     OR scraper_id IS NOT NULL
  ORDER BY name
  ```

- **Tables touched**: `companies` (wide column set, includes `scraper_id` in the
  WHERE).
- **Transaction structure**: single read-only statement; no commit.
- **Inputs/bindings**: none.
- **Existing JobStorage coverage**: **NONE exact** — `get_companies(...)` (3303)
  is the general filtered company lister but does not return this exact
  monitoring/seed column set or the `monitoring_status != 'unmonitored'` filter.
  Closest: `get_companies`.

### core/preference_report.py:51

- **File:line**: `core/preference_report.py:51` — confirmed. **Note**: line 51 is
  only the connection factory `_connect()` (`conn = sqlite3.connect(DB_PATH)`,
  sets `row_factory = sqlite3.Row`, `PRAGMA query_only = 1`). This entire module
  is a raw-sqlite3 report generator: every query goes through that one `conn`.
  The following statements/functions all count as raw-SQL sites to be migrated:

  - `_get_cohort_jobs(conn, statuses)` — `SELECT j.id, j.title, j.company,
    j.company_id, j.url, t.status, t.notes, COALESCE(c.industry_sector,'other'),
    COALESCE(j.work_mode,'unknown'), COALESCE(j.geo_zone,'unknown'),
    COALESCE(c.company_country,'unknown'), COALESCE(j.language_required,'unknown'),
    COALESCE(c.company_size,'unknown') FROM jobs j JOIN job_tracking t ON
    j.id = t.job_id LEFT JOIN companies c ON j.company_id = c.id WHERE
    t.status IN (...)` (placeholder list).
  - `_get_applications_count(conn)` — `SELECT COUNT(*) FROM job_applications`.
  - `_get_classified_jobs(conn, profile_id)` — jobs JOIN tracking LEFT JOIN
    companies LEFT JOIN job_scores (per profile) with
    `t.status IN ('applied','interviewing','offer','rejected','withdrawn','archived')`.
  - `_get_company_sector(conn, company_name)` — `SELECT industry_sector FROM
    companies WHERE name = ?`.
  - `_section_header` — `SELECT COUNT(*) FROM jobs`,
    `SELECT COUNT(DISTINCT job_id) FROM job_scores`.
  - `_section_calibration_deltas` — over-scored
    (`t.status='archived' AND s.score>=8`) and under-scored
    (`t.status IN (...) AND s.score<=6`) queries, both joining jobs/tracking/scores.
  - `_section_archive_themes` — `SELECT notes FROM job_tracking WHERE
    status='archived' AND notes IS NOT NULL AND notes != ''`.
  - `export_few_shot_anchors(profile_id)` — positive anchors
    (`t.status IN ('applied','interviewing','offer','rejected','withdrawn','ready','queued')`)
    and negative anchors (`t.status='archived'`), joining jobs/tracking/scores/companies.
  - `generate_action_items(...)` — five further queries (over-scored, under-scored,
    denylist candidates via NOT IN subquery, sector exclusions via NOT IN subquery,
    banned-country candidates via NOT IN subquery, source quality).

- **Read vs write**: Read (entire module is read-only; `PRAGMA query_only = 1`).
- **Tables touched**: `jobs`, `job_tracking`, `job_scores`, `companies`,
  `job_applications`.
- **Transaction structure**: single long-lived `conn` (via `_connect()`) reused
  across many helper calls, closed at the end of `generate_report` /
  `generate_action_items` / `export_few_shot_anchors`. No writes/commit.
- **Inputs/bindings**: `profile_id` (arg or `get_active_profile().id`),
  `statuses` lists, `company_name`.
- **Existing JobStorage coverage**: **NONE** — this is a standalone analytics
  module with no equivalent in `JobStorage`. Closest individual pieces:
  `get_all_jobs_best_score`, `get_scores_for_job`, `get_stats`.

### core/backfill_descriptions.py:74

- **File:line**: `core/backfill_descriptions.py:74` — confirmed (function `main`,
  lines 73–114). Uses `sqlite3.connect` (module-level `import sqlite3` at line 5).
- **Read vs write**: Read then Write.
- **Exact SQL statements**:

  ```sql
  SELECT id, url, source FROM jobs WHERE description IS NULL ORDER BY source, last_seen DESC
  ```

  ```sql
  UPDATE jobs SET description = ? WHERE id = ?
  ```

- **Tables touched**: `jobs` (`description`, `id`, `url`, `source`, `last_seen`).
- **Transaction structure**: one read at the top, then a loop of single
  `UPDATE` statements each followed by an **explicit `conn.commit()`**
  (line 104) so each fetched description is persisted independently.
- **Inputs/bindings**: `description[:3000]`, `job_id` (from the read rows).
- **Existing JobStorage coverage**: **NONE exact** — `update_job_extraction(
  job_id, fields, extracted_at=None)` (1529) writes a fixed set of extraction
  fields (language_required, work_mode, geo_zone, country_code, contract_type,
  summary, extracted_at, extracted_by) and does **not** include `description`.
  Closest: `update_job_extraction` (not directly reusable for `description`);
  `get_jobs_for_extraction()` (1472) is the read-side "jobs needing extraction"
  candidate but returns full rows, not just `id/url/source`.

---

## Existing JobStorage method surface

Public methods in `core/storage.py` (name + one-line purpose). Sorted in file order.

**Profiles**
- `upsert_profile(profile)` — save/update a SearchProfile (serialised criteria JSON).
- `get_profile(profile_id)` — fetch one serialised profile row.
- `get_all_profiles()` — list all profiles.
- `delete_profile(profile_id)` — delete profile AND its scores; returns `(scores_deleted, profile_deleted)`.

**Scores**
- `get_score_result(job_id, profile_id)` — fetch one score row for job+profile.
- `get_scores_for_job(job_id)` — all scores for a job, newest first.
- `split_new_cached(jobs, profile_id)` — split candidate jobs into new vs already-cached.
- `get_jobs_for_scoring(profile_id, pre_filter, rescore)` — jobs needing scoring (excludes rejected/blacklisted; optional SQL pre-filter + freshness window).
- `get_jobs_for_extraction()` — jobs needing field extraction.
- `save_scored(job, score_result, profile_id, ...)` — persist a score + flags.
- `save_unscored(job, company_id)` — persist a job as unscored for retry.
- `get_all_jobs_best_score(exclude_archived, limit)` — jobs shown once at their best score across profiles.
- `get_all_for_tracker(profile_id, exclude_archived, ...)` — tracker feed rows.

**Companies**
- `upsert_company(name, *, ...)` — insert/update a company.
- `update_company_metadata(company_id, *, summary, website)` — fill NULL summary/website only.
- `set_company_status(company_id, status, note)` — set company status + history row.
- `update_company_enrichment(company_id, fields, ...)` — write company-level extracted fields.
- `set_company_monitored(company_id, monitored)` — toggle the `monitored` flag.
- `get_monitored_companies()` — companies where monitoring_status='watching'.
- `get_all_monitorable_companies()` — companies with ATS provider or dedicated scraper.
- `is_company_scrapable(company_id)` — has ats_provider or scraper_id.
- `get_companies_by_ats_provider(provider)` — monitored companies per ATS provider.
- `get_watching_companies_by_method(scraping_method)` — watching companies by scraping method.
- `set_job_monitored_company(job_id, company_id)` — link a job to a monitored company.
- `set_job_filtered_non_product(job_id)` — mark job as filtered non-product.
- `upsert_company_from_detection(name, careers_url, ...)` — create company from ATS detection.
- `get_monitored_company_for_job(job_id)` — monitored company for a job.
- `update_company_fields(company_id, fields)` — update allowlisted company columns by name.
- `set_monitoring_status(company_id, status)` — set monitoring_status.
- `get_watch_pending_companies()` — companies with monitoring_status='watch_pending'.
- `update_company_research(company_id, result)` — write research fields + status transition.
- `import_companies_from_csv(rows)` — bulk import companies from CSV.
- `upsert_company_from_seed(row)` — upsert a company from a seed row.
- `get_companies(status, search, exclude_blacklisted, ...)` — filtered company listing.
- `get_company_by_id(company_id)` — fetch a single company.
- `find_jobs_by_company(company)` — jobs by company name.
- `get_jobs_for_company(company_id)` — jobs for a company id.
- `count_purgeable_jobs(retention_days)` — count purgeable jobs.
- `purge_stale_jobs(retention_days)` — delete stale jobs.

**Jobs / extraction**
- `update_job_extraction(job_id, fields, extracted_at)` — write job-level extraction fields.
- `get_engaged_job_keys()` — job keys with engagement (tracking/applications).
- `touch_many(job_ids)` — bump last_seen on many jobs.
- `get_job_for_prepare(job_id)` — job + company joined row for prepare.
- `get_queued_jobs_unprepared()` — queued jobs without a prepared application.
- `get_queued_jobs()` — queued jobs (id/title/company/url/description).

**Job tracking / status / lifecycle**
- `set_status(job_id, status, notes)` — upsert job_tracking status + status_history.
- `record_lifecycle_event(job_id, *, event, occurred_at, new_status, ...)` — insert lifecycle event.
- `get_lifecycle_events(job_id)` — list lifecycle events.
- `get_lifecycle_summary(job_id)` — derive status/dates from events.
- `update_lifecycle_event(event_id, *, occurred_at, notes, ...)` — edit an event.
- `delete_lifecycle_event(event_id, source)` — delete an event.
- `reconcile_lifecycle_status(job_id)` — recompute status from events.

**Reports / stats / digest**
- `build_application_rows(date_from, date_to, ...)` — applications report rows.
- `build_activity_rows(date_from, date_to)` — activity report rows.
- `application_date_missing()` — applied jobs missing an application date.
- `get_digest(profile_id, min_score, status)` — digest of top jobs.
- `get_stats(profile_id)` — aggregate total/scored/hot/solid/by_status.
- `count_blacklisted_jobs()` — count jobs at blacklisted companies.
- `get_dashboard_data()` — dashboard widget aggregates (follow-ups, inbound, unverified, stale outreach).

**Applications**
- `get_application(job_id)` — fetch a job_applications row.
- `get_all_applications()` — all applications.
- `save_application(job_id, analysis, cover_letter)` — upsert basic application.
- `save_prepared_application(job_id, profile_id, ...)` — upsert full prepared package.

**Contacts / interactions (CRM)**
- `upsert_contact(company_id, *, ...)` — insert/update a contact (returns id).
- `log_interaction(company_id, *, type, contact_id, job_id, ...)` — insert an interaction.
- `get_contact_relationship_status(contact_id)` — derive relationship from interactions.
- `get_company_relationship_summary(company_id)` — contact counts + last interaction.
- `get_company_contacts(company_id, include_unverified)` — list contacts for a company.
- `get_company_interactions(company_id, limit)` — list interactions for a company.
- `get_contact_interactions(contact_id, limit)` — list interactions for a contact.
- `get_contact(contact_id)` — fetch a single contact.
- `get_all_contacts(...)` — filtered contact listing.

**Config / runs**
- `get_config(key, default)` — read a config value.
- `set_config(key, value)` — write a config value.
- `get_freshness_days()` — freshness window in days.
- `log_run(profile_id, jobs_scraped, jobs_scored, ...)` — insert a run row.
- `update_last_run(...)` — update a run row.
- `get_last_run(profile_id)` — fetch the last run.

**Notable gaps observed (no method exists for):** contact field updates
(`is_unverified`, `last_verified_at`, `is_current`, `notes`, bulk-verify),
company `notes` update (notes not in `_ALLOWED_COMPANY_FIELDS`), a single
"database stats" counts block (jobs/scored/companies/contacts/interactions),
contacts-by-role aggregation, "contacts discovered from a posting",
single-job tracking-status read, best-score-profile pick, delete-scores-only
(rescore), backfill `description` write, and the entire analytics surface of
`preference_report.py` / `context_tuner.py` / `export_seed.py`.
