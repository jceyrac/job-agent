# Claude Code Implementation Prompts — Companies & Contacts CRM

Working repo: `/Users/jeanclaudevd/AI-Suite/job_agent`

## Locked decisions (recap)

1. **Company-level fields move off `jobs`** onto `companies`: `company_country`, `industry_sector`, `company_size`.
2. **Job-level fields stay on `jobs`**: `work_mode`, `geo_zone`, `language_required`, `contract_type`.
3. **Company status enum** (8 values):
   `prospect` · `watching` · `active_outreach` · `engaged` · `dormant` · `passed_by_me` · `declined_by_them` · `blacklisted`
   With a `company_status_history` audit-trail table (append-only, mirrors the existing `status_history` pattern for jobs).
4. **Backfill is in scope** — when companies are created from existing jobs, enrich country/sector/size by reading the current `jobs` table values (these will move from `jobs` to `companies` in PR2).
5. **Contacts live on companies**, not on jobs. Per-ad provenance handled by an `interactions` table (`type='discovered_on_posting'`), not by a `job_contacts` link table.
6. **`unified_jc` is the only active profile.** Don't modify `web3_remote` or `ch_hybrid` scoring logic.

---

## PR 1 — Companies table + blacklist migration

Paste the following into a fresh Claude Code session at the repo root.

---

> **Task: introduce a `companies` table, wire scraping to populate it, migrate company-level blacklist out of profiles into company status.**
>
> Read first:
> - `storage.py` (full file — schema lives at top, migrations pattern is the `_init_db` method)
> - `models.py` (the `JobPosting` dataclass)
> - `scrape.py` (`main()` calls `db.save_unscored(job)` per job — the upsert hook)
> - `profiles.py` (look for `denylisted_companies` on the `SearchProfile` dataclass and the `UNIFIED_JC` instance)
> - `score.py` lines ~250-260 (where denylist is currently logged) and wherever `denylisted_companies` is actually applied in `scorer.py`
>
> ### What to build
>
> **1. New `companies` table** (added to the `SCHEMA` string in `storage.py`, applied via the existing migration pattern):
>
> ```sql
> CREATE TABLE IF NOT EXISTS companies (
>     id              INTEGER PRIMARY KEY AUTOINCREMENT,
>     name            TEXT NOT NULL,              -- canonical display name
>     name_normalized TEXT NOT NULL UNIQUE,       -- dedup key (see normalization rules)
>     website         TEXT,
>     careers_url     TEXT,
>     status          TEXT NOT NULL DEFAULT 'prospect',
>     notes           TEXT,
>     first_seen_at   TEXT NOT NULL,
>     last_seen_at    TEXT NOT NULL,
>     created_at      TEXT NOT NULL
> );
> CREATE INDEX IF NOT EXISTS idx_companies_status ON companies (status);
> CREATE INDEX IF NOT EXISTS idx_companies_name_norm ON companies (name_normalized);
> ```
>
> Status enum values (no CHECK constraint — enforce in Python):
> `prospect`, `watching`, `active_outreach`, `engaged`, `dormant`, `passed_by_me`, `declined_by_them`, `blacklisted`.
>
> Define them as a module-level frozenset `COMPANY_STATUSES` near the top of `storage.py`.
>
> **2. New `company_status_history` table** (append-only):
>
> ```sql
> CREATE TABLE IF NOT EXISTS company_status_history (
>     id          INTEGER PRIMARY KEY AUTOINCREMENT,
>     company_id  INTEGER NOT NULL,
>     status      TEXT NOT NULL,
>     note        TEXT,
>     changed_at  TEXT NOT NULL,
>     FOREIGN KEY (company_id) REFERENCES companies(id)
> );
> CREATE INDEX IF NOT EXISTS idx_company_status_history ON company_status_history (company_id, changed_at DESC);
> ```
>
> Any time the `companies.status` column is written, insert a row here in the same transaction.
>
> **3. `company_id` FK on `jobs`**: add as nullable for now (we don't have a backfill story for jobs whose company can't be resolved yet — but in practice every existing job has a `company` string so they'll all resolve). Add the column via the existing migration block in `_init_db`.
>
> **4. Name normalization function** (`_normalize_company_name(name: str) -> str`):
> - Lowercase, strip whitespace
> - Strip legal suffixes (case-insensitive): `ag`, `gmbh`, `sa`, `sarl`, `inc`, `inc.`, `ltd`, `ltd.`, `llc`, `llp`, `plc`, `bv`, `nv`, `oy`, `ab`, `as`, `corp`, `corporation`, `co`, `co.`, `holding`, `holdings`, `group`, `the`
> - Strip generic suffixes: `labs`, `lab`, `software`, `technologies`, `technology`, `tech`, `solutions`, `systems`, `services`
> - Strip all punctuation, collapse internal whitespace to single space
> - Return empty string if input is empty/None
>
> Examples to satisfy with unit tests:
> - `"ConsenSys Software Inc."` → `"consensys"`
> - `"Consensys"` → `"consensys"`
> - `"FELFEL AG"` → `"felfel"`
> - `"Aave Companies LLC"` → `"aave"`
> - `"UBS Group"` → `"ubs"`
>
> **5. `JobStorage.upsert_company(name, *, website=None, careers_url=None) -> int`** (returns `company_id`):
> - Compute `name_normalized`
> - If empty after normalization, raise `ValueError`
> - SELECT by `name_normalized`. If exists: update `last_seen_at`, optionally fill `website`/`careers_url` if currently NULL, return existing id.
> - If not exists: INSERT with default status `prospect`, return new id. Do NOT insert into `company_status_history` for the initial default — only on transitions.
>
> **6. Wire `scrape.py`** to upsert a company per job and resolve `company_id`:
> - In the loop where `db.save_unscored(job)` is called, first call `db.upsert_company(job.company)` and pass the resulting id through to `save_unscored`.
> - Modify `save_unscored` to set `jobs.company_id` when present.
>
> **7. Migration of existing data** (run once, inside `_init_db` after the schema applies, gated by a check that companies table is empty):
> - For every distinct `(company, source)` pair in `jobs`, upsert a company. Use `MAX(last_seen)` from `jobs` for that company as `last_seen_at`, `MIN(first_seen)` as `first_seen_at`.
> - Then UPDATE `jobs` to set `company_id` for every row matched by `name_normalized`.
> - Log counts: "Migration: created N companies, linked M jobs."
>
> **8. Blacklist migration**:
> - For each profile in `profiles.py` (`UNIFIED_JC`, `WEB3_REMOTE`, `CH_HYBRID`) that has a `denylisted_companies` list, upsert each name into `companies` and set `status='blacklisted'` (write the transition row to `company_status_history` with `note='migrated from profiles.py denylist'`).
> - **Do NOT delete the `denylisted_companies` field from profiles.py.** Keep it as-is for now — we'll remove it in a follow-up after one or two scrape cycles confirm the SQL filter is equivalent.
>
> **9. SQL pre-filter for blacklisted companies**:
> - In `storage.py`, find `get_jobs_for_scoring` (and `get_jobs_for_extraction` if it exists). Add a JOIN to `companies` and a `WHERE c.status != 'blacklisted'` clause.
> - Print the count of jobs filtered by the blacklist in the `score.py` output, similar to the existing pre-filter stats.
>
> ### Constraints
>
> - Don't change `JobPosting` (models.py) in this PR — `company_id` is a storage-only concern for now.
> - Don't move `company_country`/`industry_sector`/`company_size` yet — that's PR2.
> - Keep `denylisted_companies` on profiles working alongside the new SQL filter (belt-and-suspenders during transition).
> - All schema changes go through the existing `_init_db` migration pattern (ALTER TABLE … ADD COLUMN inside a `if "X" not in cols` block).
>
> ### Tests to add (`tests/` directory)
>
> - `test_normalize_company_name` covering the examples above plus edge cases (empty string, single-word names, unicode like "Société Générale").
> - `test_upsert_company_dedupes` — calling upsert twice with `"ConsenSys"` and `"ConsenSys Software Inc."` returns the same id.
> - `test_blacklist_filter_excludes_jobs` — set a company to blacklisted, verify `get_jobs_for_scoring` drops its jobs.
> - `test_status_history_logs_transitions` — change a company status, verify a row appears in `company_status_history`.
>
> ### Verification
>
> 1. Run the full test suite (`pytest tests/`).
> 2. Run `python -c "from storage import JobStorage; db = JobStorage('data/jobs.db'); print(db.get_stats('unified_jc'))"` — should not error, migration should be idempotent.
> 3. Connect to the DB and count: `SELECT COUNT(*), status FROM companies GROUP BY status;` — confirm `blacklisted` count matches the union of denylists across profiles.
> 4. `SELECT COUNT(*) FROM jobs WHERE company_id IS NULL;` — should be 0 or very close.
> 5. Run `python score.py --profile unified_jc --limit 10` and confirm output includes "X jobs excluded by company blacklist".
>
> ### Commit message
>
> ```
> Add companies table with status enum and blacklist migration
>
> - companies + company_status_history tables
> - jobs.company_id FK (nullable, backfilled)
> - upsert_company() with name normalization (strips legal/generic suffixes)
> - scrape.py wires every job to a company
> - Blacklisted companies filtered out at SQL level in get_jobs_for_scoring
> - Migration backfills denylisted_companies from profiles.py
> - Keeps profile-level denylist active for one transition cycle
> ```

---

## PR 2 — Move company-level fields off jobs

Run **after PR1 has merged** and you've verified the companies table is populated and queries work.

---

> **Task: move `company_country`, `industry_sector`, `company_size` from the `jobs` table to the `companies` table. Backfill from existing job data. Update extraction to write to companies.**
>
> Read first:
> - The PR1 diff (whatever just landed for companies)
> - `storage.py` (schema + the Phase 1e migration block — these three fields were added there)
> - `models.py` (`JobPosting` dataclass — these three fields are on it)
> - `scorer.py` — find `extract_job_fields` and the `_parse_extraction_result` function
> - `score.py` — find `_run_extraction` and the in-loop extraction in `main()` (around the "Phase 1: extraction of unextracted survivors" block)
>
> ### What to build
>
> **1. Add columns to `companies`** (via the migration pattern):
> ```sql
> ALTER TABLE companies ADD COLUMN company_country   TEXT;
> ALTER TABLE companies ADD COLUMN industry_sector   TEXT;
> ALTER TABLE companies ADD COLUMN company_size      TEXT;
> ALTER TABLE companies ADD COLUMN enriched_at       TEXT;  -- NULL until extraction has run for this company
> ALTER TABLE companies ADD COLUMN enriched_by       TEXT;  -- model that filled the fields
> ```
>
> **2. Backfill from existing `jobs` rows**:
> - For each company, pick the most recent non-null `(company_country, industry_sector, company_size)` from its jobs (use `extracted_at` to break ties).
> - Copy those values onto the company row. Set `enriched_at = extracted_at` of the source job, `enriched_by` = the source job's `extracted_by`.
> - If a company has no enriched jobs, leave its fields NULL.
> - Log: "Backfilled N companies with country/sector/size from existing job data."
>
> **3. Update extraction flow** (in `score.py` `_run_extraction` and the in-`main()` extraction loop):
> - After `extract_job_fields` returns a result, write `work_mode`, `geo_zone`, `language_required`, `contract_type`, `summary` to `jobs` (as today).
> - Write `company_country`, `industry_sector`, `company_size` to the **company row** (the job's `company_id`). Only update fields that are currently NULL on the company — never overwrite. Update `enriched_at` only if at least one field was written.
> - `db.update_job_extraction` should be split or supplemented with a new `db.update_company_enrichment(company_id, fields_dict)` call.
>
> **4. Drop the columns from `jobs`** after backfill confirms parity:
> - SQLite doesn't support `DROP COLUMN` cleanly on older versions, but recent SQLite (3.35+) does. Use `ALTER TABLE jobs DROP COLUMN company_country;` etc. inside a try/except — if the SQLite version is too old, leave the columns in place but stop writing to them (they become dead weight, can clean up manually later).
> - Either way, remove the fields from `JobPosting` (models.py) and from the `_dict_to_posting` reconstruction in `score.py`.
>
> **5. Update all readers**:
> - `score.py` digest assembly (around line 376 of current score.py) reads `company_country` and `industry_sector` from the job dict to apply post-scoring filters. Change these reads to join through the company:
>   ```python
>   # Either join in get_digest, or look up company_country via a helper.
>   company_country  = job_dict.get("company_country", "unknown")   # now comes from a join
>   industry_sector  = job_dict.get("industry_sector", "other")
>   ```
> - `get_digest`, `get_jobs_for_scoring`, `get_jobs_for_extraction` and any other JOIN-emitting reader should select `c.company_country AS company_country`, etc., so the calling code keeps working.
> - The tracker UI (`tracker.py`) may also reference these fields — check and update.
>
> **6. Update `extract_job_fields` prompt** in `scorer.py`:
> - The extraction prompt currently extracts company_country/sector/size every time. Keep that — but in `score.py`, suppress the write if the company is already enriched (saves Groq calls? No, the call still happens. Actually: skip extraction entirely for jobs whose company is already enriched AND whose job-level fields are already filled). That's a meaningful Groq quota saving once a company is enriched.
> - Pre-extraction check: `SELECT enriched_at FROM companies WHERE id = ?` — if not NULL, only extract the job-level fields by passing a flag to `extract_job_fields` that switches to a shorter prompt. Implement this as a second prompt `EXTRACTION_PROMPT_JOB_ONLY` in `scorer.py`.
>
> ### Constraints
>
> - The extraction prompt change is optional in this PR if it adds risk — note it as TODO and ship the simpler version.
> - Don't break the tracker UI — verify it still loads and shows country/sector for jobs.
> - The backfill must be idempotent (gated on whether companies have already been enriched).
>
> ### Tests
>
> - `test_company_enrichment_write_once` — calling enrich twice doesn't overwrite existing values.
> - `test_backfill_pulls_latest_extracted` — backfill picks the most recent non-null values across a company's jobs.
> - `test_digest_joins_company_fields` — get_digest returns rows with company_country/sector populated from the join.
>
> ### Verification
>
> 1. Test suite passes.
> 2. `SELECT COUNT(*) FROM companies WHERE company_country IS NOT NULL;` — should be a substantial fraction of the table after backfill (rough sanity check, not exact).
> 3. Run `python score.py --profile unified_jc --limit 5` end-to-end and confirm the digest output looks normal.
> 4. Confirm `tracker.py` (Streamlit) loads.

---

## PR 3 — Contacts + interactions (full prompt)

Locked decisions for PR3:
- Extraction: regex during `--extract` pass; gated LLM behind a feature flag (OFF by default)
- `role_family` values: `recruiter`, `hiring_manager`, `founder`, `leadership`, `product`, `peer`, `other`
- `relationship_status`: **derived** from interactions, no stored column
- Interaction types: 11-value list (below)
- Auto-attribution: contacts auto-created by extraction get `is_unverified=1`
- `job_tracking` status transitions to `applied` / `interviewing` / decision-terminal states auto-log an interaction
- Hunter.io email enrichment: out of scope for this PR

Run **after PR1 and PR2 have merged** and you've verified companies and contacts FKs work end-to-end.

---

> **Task: Add `contacts` and `interactions` tables. Wire deterministic regex-based contact discovery into the existing `--extract` pass. Add gated LLM contact extraction behind a feature flag (OFF by default). Auto-log interactions when `job_tracking` status transitions.**
>
> Read first:
> - The diffs from PR1 (companies + status migration) and PR2 (company-level fields moved off jobs)
> - `storage.py` — full file; pay attention to the SCHEMA block, the migration pattern in `_init_db`, and any function that updates `job_tracking.status`
> - `scorer.py` — `extract_job_fields`, `_parse_extraction_result`, and the Groq fallback chain
> - `score.py` — `_run_extraction` and the in-`main()` extraction loop
> - `tracker.py` — find every call site that updates `job_tracking.status`
>
> ### What to build
>
> **1. New `contacts` table** (added to SCHEMA, applied via the existing migration pattern):
>
> ```sql
> CREATE TABLE IF NOT EXISTS contacts (
>     id                INTEGER PRIMARY KEY AUTOINCREMENT,
>     company_id        INTEGER NOT NULL,
>     first_name        TEXT,
>     last_name         TEXT,
>     full_name         TEXT,
>     role_title        TEXT,
>     role_family       TEXT,                     -- enum
>     seniority         TEXT,                     -- enum
>     email             TEXT,
>     email_status      TEXT DEFAULT 'unknown',   -- enum
>     email_confidence  REAL,                     -- 0.0–1.0 or NULL
>     linkedin_url      TEXT,
>     x_handle          TEXT,
>     telegram_handle   TEXT,
>     github_handle     TEXT,
>     phone             TEXT,
>     is_current        INTEGER NOT NULL DEFAULT 1,
>     is_unverified     INTEGER NOT NULL DEFAULT 0,
>     last_verified_at  TEXT,
>     notes             TEXT,
>     first_seen_at     TEXT NOT NULL,
>     last_seen_at      TEXT NOT NULL,
>     created_at        TEXT NOT NULL,
>     FOREIGN KEY (company_id) REFERENCES companies(id)
> );
> CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_linkedin
>     ON contacts (linkedin_url) WHERE linkedin_url IS NOT NULL;
> CREATE INDEX IF NOT EXISTS idx_contacts_company    ON contacts (company_id);
> CREATE INDEX IF NOT EXISTS idx_contacts_email      ON contacts (email) WHERE email IS NOT NULL;
> CREATE INDEX IF NOT EXISTS idx_contacts_unverified ON contacts (is_unverified) WHERE is_unverified = 1;
> ```
>
> Module-level frozensets near the top of `storage.py`:
>
> ```python
> CONTACT_ROLE_FAMILIES = frozenset({
>     "recruiter", "hiring_manager", "founder", "leadership",
>     "product", "peer", "other",
> })
> CONTACT_SENIORITIES = frozenset({
>     "IC", "manager", "director", "vp", "cxo", "unknown",
> })
> CONTACT_EMAIL_STATUSES = frozenset({
>     "unknown", "pattern_guessed", "verified", "bounced", "role_account",
> })
> ```
>
> **2. New `interactions` table:**
>
> ```sql
> CREATE TABLE IF NOT EXISTS interactions (
>     id                INTEGER PRIMARY KEY AUTOINCREMENT,
>     company_id        INTEGER NOT NULL,
>     contact_id        INTEGER,
>     job_id            TEXT,
>     type              TEXT NOT NULL,                  -- enum
>     direction         TEXT NOT NULL DEFAULT 'none',   -- enum
>     outcome           TEXT,                           -- 'positive' | 'negative' | NULL
>     subject           TEXT,
>     body_excerpt      TEXT,
>     occurred_at       TEXT NOT NULL,
>     follow_up_due_at  TEXT,
>     created_at        TEXT NOT NULL,
>     FOREIGN KEY (company_id) REFERENCES companies(id),
>     FOREIGN KEY (contact_id) REFERENCES contacts(id),
>     FOREIGN KEY (job_id)     REFERENCES jobs(id)
> );
> CREATE INDEX IF NOT EXISTS idx_interactions_company  ON interactions (company_id, occurred_at DESC);
> CREATE INDEX IF NOT EXISTS idx_interactions_contact  ON interactions (contact_id, occurred_at DESC);
> CREATE INDEX IF NOT EXISTS idx_interactions_job      ON interactions (job_id);
> CREATE INDEX IF NOT EXISTS idx_interactions_followup ON interactions (follow_up_due_at)
>     WHERE follow_up_due_at IS NOT NULL;
> ```
>
> Module-level frozensets:
>
> ```python
> INTERACTION_TYPES = frozenset({
>     "discovered_on_posting", "discovered_manual",
>     "outreach_sent", "reply_received", "intro_received",
>     "call", "meeting",
>     "application_submitted", "interview", "decision_received",
>     "note",
> })
> INTERACTION_DIRECTIONS = frozenset({"inbound", "outbound", "none"})
> INTERACTION_OUTCOMES   = frozenset({"positive", "negative"})  # NULL is also valid
> ```
>
> The `outcome` column exists specifically so the derived `relationship_status` (item 5 below) can distinguish positive vs. negative `decision_received` events without parsing free text.
>
> **3. `db.upsert_contact()` API:**
>
> ```python
> def upsert_contact(
>     self,
>     *,
>     company_id: int,
>     linkedin_url: str | None = None,
>     email: str | None = None,
>     first_name: str | None = None,
>     last_name: str | None = None,
>     full_name: str | None = None,
>     role_title: str | None = None,
>     role_family: str | None = None,
>     seniority: str | None = None,
>     email_status: str = "unknown",
>     email_confidence: float | None = None,
>     x_handle: str | None = None,
>     telegram_handle: str | None = None,
>     github_handle: str | None = None,
>     phone: str | None = None,
>     is_unverified: bool = False,
>     notes: str | None = None,
> ) -> int:
>     ...
> ```
>
> Resolution order (stop at first match):
> 1. `linkedin_url` (exact match, normalized form)
> 2. `email` (exact match, lowercased)
> 3. `(company_id, normalized_name)` — name normalization: lowercase, strip punctuation, sort tokens alphabetically so "Sarah Müller" matches "Müller, Sarah"
>
> On match: update `last_seen_at`, set `is_current=1`, fill any currently-NULL fields from the incoming data, **never overwrite non-NULL values**. Return existing id.
>
> On no match: insert with `first_seen_at=now`, `created_at=now`. Return new id.
>
> Validation: `role_family` (if provided) must be in `CONTACT_ROLE_FAMILIES`; `email_status` must be in `CONTACT_EMAIL_STATUSES`; `seniority` (if provided) must be in `CONTACT_SENIORITIES`. Raise `ValueError` otherwise.
>
> **4. `db.log_interaction()` API:**
>
> ```python
> def log_interaction(
>     self,
>     *,
>     company_id: int,
>     type: str,
>     contact_id: int | None = None,
>     job_id: str | None = None,
>     direction: str = "none",
>     outcome: str | None = None,
>     subject: str | None = None,
>     body_excerpt: str | None = None,
>     occurred_at: str | None = None,        # ISO timestamp; defaults to now
>     follow_up_due_at: str | None = None,
> ) -> int:
>     ...
> ```
>
> Validate `type` against `INTERACTION_TYPES`, `direction` against `INTERACTION_DIRECTIONS`, `outcome` against `INTERACTION_OUTCOMES | {None}`. Defaults `occurred_at` and `created_at` to `datetime.now(timezone.utc).isoformat()`.
>
> **5. Derived relationship status:**
>
> ```python
> def get_contact_relationship_status(self, contact_id: int) -> str:
>     ...
> ```
>
> Logic, evaluated against the contact's interaction history in DESC order by `occurred_at`:
> - If most recent terminal event is `decision_received` with `outcome='negative'` → `'declined'`
> - Else if most recent terminal event is `decision_received` with `outcome='positive'` → `'offer'`
> - Else if any `interview` exists → `'interviewing'`
> - Else if any `application_submitted` exists → `'applied'`
> - Else if any `reply_received` exists → `'replied'`
> - Else if any `outreach_sent` exists → `'cold_contacted'`
> - Else if any non-discovery interaction exists → `'engaged'`
> - Else → `'none'`
>
> This is a Python helper, not a stored column. Add a parallel `get_company_relationship_summary(company_id)` that aggregates: total contacts, count by relationship status, last interaction date.
>
> **6. Additional read APIs:**
>
> - `db.get_company_contacts(company_id: int, include_unverified: bool = True) -> list[dict]`
> - `db.get_company_interactions(company_id: int, limit: int = 50) -> list[dict]`
> - `db.get_contact(contact_id: int) -> dict | None`
>
> **7. Regex-based contact discovery** — new module `scrapers/contact_extract.py`:
>
> ```python
> def extract_contact_references(description: str) -> list[dict]:
>     """Returns list of {email, linkedin_url, role_account, source_pattern}.
>     One entry per unique contact reference found."""
> ```
>
> - Email regex: standard RFC-light pattern, case-insensitive
> - LinkedIn regex: `linkedin\.com/in/[\w\-]+`, case-insensitive. Normalize the output to `https://www.linkedin.com/in/<slug>`.
> - Role-account emails (`careers@`, `jobs@`, `hr@`, `recruiting@`, `talent@`, `apply@`, `info@`, `contact@`) are returned with `role_account=True` so the caller can set `email_status='role_account'`. Do not filter them out — they're still actionable.
> - Dedupe within a single description (don't return the same email twice).
> - Returns `[]` for empty or None input.
>
> **8. Wire regex discovery into `--extract` pass** (in `score.py`):
>
> After `extract_job_fields` succeeds for a job and the job-level fields are written:
>
> ```python
> from scrapers.contact_extract import extract_contact_references
>
> refs = extract_contact_references(job.description or "")
> for ref in refs:
>     contact_id = db.upsert_contact(
>         company_id=job.company_id,
>         email=ref.get("email"),
>         linkedin_url=ref.get("linkedin_url"),
>         email_status="role_account" if ref.get("role_account") else "unknown",
>         is_unverified=True,
>     )
>     db.log_interaction(
>         company_id=job.company_id,
>         contact_id=contact_id,
>         job_id=job.id,
>         type="discovered_on_posting",
>         direction="none",
>         body_excerpt=(job.description or "")[:200],
>     )
> ```
>
> Per-job console summary: `Contacts: 2 found via regex (1 email, 1 linkedin)`.
>
> Wrap the whole block in try/except — contact extraction must never break job extraction. Log a warning on failure and continue.
>
> **9. Gated LLM contact extraction** (OFF by default):
>
> - New env var `JOB_AGENT_LLM_CONTACTS=1` to enable.
> - Pre-gate check: only call the LLM if the description contains any of `@`, `linkedin.com`, or the keywords `recruiter`, `hiring manager`, `talent`, `please contact`, `apply directly`, `for questions`. (Case-insensitive.) This keeps the LLM off for the vast majority of descriptions where it would yield nothing.
> - New prompt `CONTACT_EXTRACTION_PROMPT` in `scorer.py`. Should return JSON:
>   ```json
>   {
>     "contacts": [
>       {"name": "...", "role_title": "...", "email": null, "linkedin_url": null, "x_handle": null}
>     ]
>   }
>   ```
> - Use the existing Groq fallback chain (`_call_groq_fallback_chain`).
> - For each result, upsert + log interaction the same way as regex.
> - If both regex AND LLM run for the same job, dedupe by (linkedin_url, email) — regex takes priority.
> - Print `LLM contact extraction: skipped (env disabled)` or `LLM contact extraction: enabled, found N contacts` per job.
>
> **10. job_tracking auto-interaction hooks:**
>
> Identify every code path that updates `job_tracking.status`. After the status is written, insert a corresponding interaction:
>
> | new status | interaction type | direction | outcome |
> |---|---|---|---|
> | `applied` | `application_submitted` | `outbound` | NULL |
> | `interviewing` (if exists) | `interview` | `none` | NULL |
> | `rejected` (if exists) | `decision_received` | `inbound` | `negative` |
> | `offer` (if exists) | `decision_received` | `inbound` | `positive` |
>
> List the actual values in the current `job_tracking.status` state machine first and map only what exists. Other transitions don't auto-log (no harm done; user can `discovered_manual` later).
>
> Resolution: `company_id` comes from `jobs.company_id`; `contact_id=None` (we don't know which contact); `job_id=<the job>`; `occurred_at=now`.
>
> Make this a small helper `_auto_log_status_interaction(job_id, new_status)` so it's reusable from every call site.
>
> ### Constraints
>
> - **All automated-extraction contacts get `is_unverified=1`.** Never set `is_unverified=0` from regex or LLM paths. The tracker UI (future PR) flips it.
> - **Upsert never overwrites non-NULL fields.** Existing data is sacred.
> - **LLM contact extraction is OFF by default.** `JOB_AGENT_LLM_CONTACTS` env var must be set explicitly.
> - **Contact extraction failures do not break job extraction.** Try/except around the whole block, warn-and-continue.
> - **No tracker.py UI changes in this PR.** Just storage APIs that the future UI will consume.
> - `is_current` is set to 1 on every upsert. There is no automated path to set it to 0 yet — leave a `# TODO: detect company changes from LinkedIn refresh` comment in `upsert_contact`.
> - Don't backfill historical contacts from existing jobs in this PR — the regex pass will catch them naturally on the next `--extract` run if `extracted_at` is reset, but that's the user's call. Add a note in the PR description: "to backfill historical contacts, clear `extracted_at` on existing jobs and re-run `--extract`."
>
> ### Tests (add to `tests/`)
>
> - `test_email_regex_extracts_basic` — catches `name@domain.com` in a paragraph
> - `test_email_regex_flags_role_accounts` — `careers@x.com` returned with `role_account=True`
> - `test_linkedin_regex_normalizes_url` — extracts and normalizes various LinkedIn URL forms (trailing slash, query string, http vs https)
> - `test_extract_contact_references_dedupes` — same email twice in description = one result
> - `test_upsert_contact_dedupes_by_linkedin` — same LinkedIn URL twice across companies = one row (LinkedIn is globally unique)
> - `test_upsert_contact_dedupes_by_email_within_company` — same email at same company_id = one row
> - `test_upsert_contact_separate_at_different_companies` — same email at two different company_ids = two rows (people change jobs)
> - `test_upsert_contact_never_overwrites_non_null` — pre-existing role_title not overwritten by upsert with different value
> - `test_upsert_contact_validates_role_family` — invalid role_family raises ValueError
> - `test_log_interaction_validates_type` — invalid type raises ValueError
> - `test_log_interaction_validates_outcome` — invalid outcome raises ValueError; NULL is fine
> - `test_derived_relationship_status_none` — fresh contact, only `discovered_on_posting` → `'none'`
> - `test_derived_relationship_status_replied` — contact with `outreach_sent` then `reply_received` → `'replied'`
> - `test_derived_relationship_status_declined` — contact with `decision_received` outcome=`negative` → `'declined'`
> - `test_extract_pass_discovers_contacts_via_regex` — end-to-end on a fixture job description with one email and one LinkedIn URL, in a real DB
> - `test_job_tracking_applied_logs_interaction` — transitioning a job to `applied` creates an `application_submitted` interaction
> - `test_llm_contacts_off_by_default` — env var unset means `_call_groq_fallback_chain` is not invoked for contacts (mock and assert not called)
>
> ### Verification
>
> 1. `pytest tests/` — all tests pass.
> 2. `python score.py --profile unified_jc --extract --limit 20` with `JOB_AGENT_LLM_CONTACTS` unset. Verify console reports contacts found via regex on at least a few jobs.
> 3. SQL spot-check:
>    ```sql
>    SELECT COUNT(*) FROM contacts;
>    SELECT COUNT(*) FROM contacts WHERE is_unverified = 1;
>    SELECT COUNT(*) FROM interactions WHERE type = 'discovered_on_posting';
>    SELECT type, COUNT(*) FROM interactions GROUP BY type;
>    ```
>    Auto-discovered counts should all be > 0 and `is_unverified` count should equal total contacts (since none have been manually verified yet).
> 4. Python REPL spot-check:
>    ```python
>    from storage import JobStorage
>    db = JobStorage("data/jobs.db")
>    cid = db.upsert_contact(company_id=<some_id>, email="test@example.com", is_unverified=False)
>    db.log_interaction(company_id=<some_id>, contact_id=cid, type="outreach_sent", direction="outbound")
>    print(db.get_contact_relationship_status(cid))   # should print 'cold_contacted'
>    ```
> 5. Manually update a `job_tracking.status` to `applied` via `db.update_job_tracking_status(...)` (or whatever the existing function is called) and confirm an `application_submitted` interaction row exists for that job.
> 6. Repeat step 2 with `JOB_AGENT_LLM_CONTACTS=1` set, on a fresh batch (clear `extracted_at` on a few jobs). Verify LLM contacts surface, are flagged `is_unverified=1`, and don't duplicate the regex ones (run dedup query on `(email, linkedin_url)`).
>
> ### Commit message
>
> ```
> Add contacts and interactions tables with discovery pipeline
>
> - contacts table (FK to companies, role_family + email_status enums, is_unverified flag)
> - interactions table with outcome column for positive/negative decisions
> - Regex-based email and LinkedIn URL extraction wired into --extract pass
> - Gated LLM contact extraction (OFF by default; JOB_AGENT_LLM_CONTACTS=1 to enable)
> - Upsert resolution: linkedin_url > email > (company_id, normalized_name)
> - Auto-log interactions on job_tracking status transitions
> - Derived relationship_status from interaction history (no stored column)
> - All automated extraction marks contacts as is_unverified=1
> ```

---

## PR 4 — Tracker UI rebuild (multi-page Streamlit, six views)

Locked decisions for PR4:
- Five top-level nav items via `st.navigation` / `st.Page`: **Dashboard** (landing), **Jobs**, **Companies**, **Contacts**, **Settings**
- Detail views within Jobs / Companies / Contacts pages, routed via query params (`?id=<entity_id>`) — bookmarkable
- Manual-add and log-interaction flows as `st.dialog` modals
- Existing `tracker.py` is preserved as `tracker_legacy.py` for one transition cycle
- Streamlit pinned to `>= 1.36` (needed for `st.navigation`, `st.Page`, `st.dialog`, `st.dataframe(selection_mode=...)`)

Run **after PR3 has merged** and you've spent a few days with the regex-discovered contact data. Tweak this prompt based on what you actually find yourself wanting to check daily before you launch the session.

---

> **Task: Replace the single-page tracker with a five-tab multi-page Streamlit app. Add list and detail views for jobs, companies, and contacts. Add three `st.dialog` forms (add company, add contact, log interaction). Preserve the existing filter and badge logic. Keep the old tracker around as `tracker_legacy.py` for one cycle.**
>
> Read first:
> - The diff from PR3 (contacts + interactions tables and APIs)
> - `tracker.py` — preserve the `apply_filters`, `score_badge`, `render_job_card`, and `_load_jobs` functions; they're solid and battle-tested
> - `storage.py` — confirm PR3 landed: `upsert_contact`, `log_interaction`, `get_company_contacts`, `get_company_interactions`, `get_contact_relationship_status`, `get_company_relationship_summary`, plus the existing `get_companies` and `update_company_status` (these latter two may need to be added if PR1 didn't expose them)
> - `profiles.py` — for the profile dropdown in Settings
> - Streamlit docs for `st.navigation`, `st.Page`, `st.dialog`, `st.query_params`, `st.switch_page` — APIs have evolved fast, verify signatures against installed version
>
> ### What to build
>
> **1. File structure:**
>
> ```
> tracker.py                       # NEW: entry point with st.navigation
> tracker_legacy.py                # the existing tracker.py, renamed (preserved for one cycle)
> tracker_views/
>     __init__.py                  # empty
>     dashboard.py                 # render() for Dashboard
>     jobs.py                      # render() — list + detail (switches on ?id=)
>     companies.py                 # render() — list + detail
>     contacts.py                  # render() — list + detail
>     settings.py                  # render() — ported from existing tracker
>     forms.py                     # @st.dialog functions
>     shared.py                    # data loaders, badges, formatters, nav helpers
> ```
>
> First step: `git mv tracker.py tracker_legacy.py`. Do not modify `tracker_legacy.py` content. Confirm `streamlit run tracker_legacy.py` still works.
>
> **2. Top-level navigation (`tracker.py`):**
>
> ```python
> import streamlit as st
> from tracker_views import dashboard, jobs, companies, contacts, settings
>
> st.set_page_config(page_title="Job Tracker", layout="wide")
>
> pages = [
>     st.Page(dashboard.render, title="Dashboard", icon="🏠", default=True),
>     st.Page(jobs.render,      title="Jobs",      icon="💼"),
>     st.Page(companies.render, title="Companies", icon="🏢"),
>     st.Page(contacts.render,  title="Contacts",  icon="👤"),
>     st.Page(settings.render,  title="Settings",  icon="⚙️"),
> ]
> st.navigation(pages).run()
> ```
>
> Each `render()` function takes no arguments. Within Jobs/Companies/Contacts, inspect `st.query_params.get("id")` at the top and route to `_render_list()` or `_render_detail(entity_id)`.
>
> **3. Dashboard page (`tracker_views/dashboard.py`):**
>
> Five widgets, each in its own `st.container(border=True)`. Two-column layout for widgets 1-5, with the high-score jobs widget below taking full width.
>
> | Widget | Query | Empty state |
> |---|---|---|
> | Follow-ups due today | interactions where `follow_up_due_at <= today` AND no later interaction with same `(contact_id, job_id)` | "No follow-ups due. Nice." |
> | Recent inbound (7d) | interactions where `type IN ('reply_received', 'intro_received')` AND `occurred_at >= now - 7d` | "No replies yet this week." |
> | Unverified contacts | `COUNT(*)` from contacts where `is_unverified = 1` | "All contacts verified." |
> | Stale active outreach | companies where `status = 'active_outreach'` AND no interaction in 14d | "All outreach is fresh." |
> | Hot jobs feed | reuse `_load_jobs` from legacy tracker; show top 10 unscored-by-user or top 10 by score not in archived state | "No new high-score jobs." |
>
> Each widget renders rows as clickable items that link to the corresponding detail view (Contacts → contact detail, Companies → company detail, Jobs → job detail). Use the `navigate_to` helper from `shared.py`.
>
> Add a "Refresh" button at top-right that calls `st.cache_data.clear()` and reruns.
>
> **4. Jobs page (`tracker_views/jobs.py`):**
>
> ```python
> def render():
>     job_id = st.query_params.get("id")
>     if job_id:
>         _render_detail(job_id)
>     else:
>         _render_list()
> ```
>
> List view:
> - Sidebar filters (port from `tracker_legacy.apply_filters`): profile, score range, work_mode, country, tracking status, exclude archived, exclude blacklisted-company jobs (default ON)
> - Use `st.dataframe` with `selection_mode='single-row'` and `on_select='rerun'`. When a row is selected, set `st.query_params["id"]` to the job id and `st.rerun()`.
> - Columns: Score (with emoji badge), Title, Company (also a link to the company detail), Country, Posted Date, Tracking Status, Source
> - Default sort: Score DESC, then Posted Date DESC
>
> Detail view:
> - Header: title, company (as link to `?id=<company_id>` on Companies page), score badge, score reason
> - Info chips: work_mode, contract_type, geo_zone, language_required, posted_date, source
> - Full description in an expander (default expanded)
> - Tracking status editable dropdown — change triggers PR3's auto-log hook
> - "Contacts discovered from this ad" section: query contacts where any interaction has `type='discovered_on_posting'` AND `job_id = this job`. Show name, role, channels, link to contact detail.
> - "Log interaction" button → opens `log_interaction_dialog` with `company_id` and `job_id` prefilled
> - Back button: `st.query_params.clear(); st.rerun()`
> - Original LinkedIn URL prominently as a link
>
> **5. Companies page (`tracker_views/companies.py`):**
>
> List view:
> - Sidebar filters: status (multiselect, default exclude `blacklisted`), country, sector, size, name search
> - "+ Add company" button → `add_company_dialog`
> - Table: Name, Status (colored badge), Country, Sector, Size, #Jobs, #Contacts, Last Interaction
> - Sortable by all columns. Default: Last Interaction DESC.
> - Bulk actions: use `st.data_editor` with a checkbox column. After selection, show "Change status to..." dropdown + "Apply" button. Apply iterates selected rows and calls `update_company_status`, which logs each to `company_status_history`.
>
> Detail view:
> - Header: name (large), website (clickable), status (editable dropdown — change logs to history), country/sector/size as chips, last interaction date
> - Four tabs (`st.tabs`):
>   - 💼 **Jobs at this company** — table of jobs (score, title, posted_date, tracking_status, link to job detail)
>   - 👤 **Contacts** — table of contacts with name, role, role_family, channels, relationship_status (derived), unverified flag; "+ Add contact" button at top
>   - 📅 **Interactions** — chronological feed (DESC) of all interactions for this company; "+ Log interaction" button at top
>   - 📝 **Notes** — `st.text_area` bound to `companies.notes`, save button
> - Back link to Companies list
>
> **6. Contacts page (`tracker_views/contacts.py`):**
>
> List view:
> - Sidebar filters: company (autocomplete or multiselect), role_family, is_unverified, relationship_status (derived; this needs a SQL subquery or a Python post-filter — pick whichever is cleaner), search-by-name
> - Default filter: exclude contacts at blacklisted companies
> - "+ Add contact" button → `add_contact_dialog`
> - Table: Name, Role, Role Family, Company (link), Email, LinkedIn (icon link if present), Relationship Status (badge), Unverified (badge if true), Last Seen
> - Bulk actions: select rows → "Mark verified" button (flips `is_unverified` to 0 for all selected)
>
> Detail view:
> - Header: full name, role title @ company (company as link), derived relationship_status as a prominent badge
> - Channels section: Email (mailto:), LinkedIn (link), X (link), Telegram (link if username, otherwise as text), GitHub (link), Phone (tel:) — only render channels with values
> - Two toggle buttons:
>   - "Mark verified" / "Mark unverified" (single button, label flips based on state)
>   - "No longer at this company" — confirmation prompt, then `is_current=0`
> - Interactions timeline: chronological feed (DESC) of all interactions where `contact_id = this`
> - "+ Log interaction" button → opens `log_interaction_dialog` with `company_id` and `contact_id` prefilled
> - Notes section (editable, save button)
>
> **7. Settings page (`tracker_views/settings.py`):**
>
> Port the existing settings tab from `tracker_legacy.py`. Add new sections:
> - **Stats**: total jobs, scored, unscored, by status; total companies by status; total contacts (split: verified vs. unverified, by role_family)
> - **Actions**:
>   - "Re-extract job fields" button — runs `python score.py --extract` as a subprocess; displays output
>   - "Clear cache" button — calls `st.cache_data.clear()`
> - **Profile management**: preserve the existing active-profile selector
>
> **8. Forms (`tracker_views/forms.py`):**
>
> Three `@st.dialog`-decorated functions:
>
> `add_company_dialog()`:
> - Fields: name (required), website, country (selectbox from a fixed list + "other"), industry_sector (selectbox), company_size (selectbox), status (selectbox, default `watching`), notes
> - On submit: validate name not empty, call `upsert_company` then `update_company_status` if not default, `st.cache_data.clear()`, `st.rerun()`
>
> `add_contact_dialog(company_id: int | None = None)`:
> - Fields: company (autocomplete/selectbox of existing companies, prefilled if `company_id` passed), first_name, last_name, role_title, role_family (selectbox from CONTACT_ROLE_FAMILIES), seniority (selectbox), email, linkedin_url, x_handle, telegram_handle, github_handle, phone, notes
> - On submit: call `upsert_contact` with `is_unverified=False` (manual adds start verified), `st.cache_data.clear()`, `st.rerun()`
>
> `log_interaction_dialog(company_id: int, contact_id: int | None = None, job_id: str | None = None)`:
> - Fields: contact (selectbox of contacts at this company + "none"; prefilled if `contact_id` passed), job (selectbox of jobs at this company + "none"; prefilled), type (selectbox from INTERACTION_TYPES), direction (selectbox), outcome (only shown when type=`decision_received`; selectbox positive/negative/none), subject, body_excerpt (text_area), occurred_at (date_input, default today), follow_up_due_at (date_input, optional)
> - On submit: call `log_interaction`, `st.cache_data.clear()`, `st.rerun()`
>
> **9. Shared helpers (`tracker_views/shared.py`):**
>
> ```python
> # Cached data loaders
> @st.cache_data(ttl=60)
> def load_jobs(profile_id, filters): ...
>
> @st.cache_data(ttl=60)
> def load_companies(filters): ...
>
> @st.cache_data(ttl=60)
> def load_contacts(filters): ...
>
> # Badges (return Markdown strings with emoji + color)
> def score_badge(score: int) -> str: ...               # 🔥/⭐/👀
> def company_status_badge(status: str) -> str: ...      # color by status
> def relationship_badge(status: str) -> str: ...        # color by relationship
> def unverified_badge() -> str: ...                     # ⚠️
>
> # Channel renderers
> def email_link(email: str) -> str: ...                 # mailto:
> def linkedin_link(url: str) -> str: ...                # styled "in" icon
> def x_link(handle: str) -> str: ...
>
> # Navigation
> def navigate_to_company(company_id: int): ...          # sets ?id= and switches page
> def navigate_to_contact(contact_id: int): ...
> def navigate_to_job(job_id: str): ...
> ```
>
> Implementation note: Streamlit's `st.switch_page` works with `st.Page` objects since 1.36 — pass the Page object directly. For setting query params alongside a switch, set `st.query_params["id"] = ...` before calling `st.switch_page`.
>
> **10. Update `requirements.txt`:**
>
> Pin `streamlit >= 1.36`. If `requirements.txt` is currently empty (it was last we checked), populate it with the actual installed packages from the venv (`pip freeze | grep -v "^pkg_resources"` is a reasonable starting point) — but be conservative on minimum versions; only pin where APIs we use require it.
>
> ### Constraints
>
> - **Don't break the legacy tracker.** `streamlit run tracker_legacy.py` must still work. Don't change its imports.
> - **All writes go through PR3 APIs** (`upsert_contact`, `log_interaction`, `update_company_status`). No raw SQL for state changes.
> - **Bulk status changes log to history.** Iterate, don't batch — each company status change is one `company_status_history` row.
> - **Use `@st.cache_data(ttl=60)`** on all data loaders. Provide a "Refresh" button that calls `st.cache_data.clear()` (Dashboard and Settings both have one).
> - **Default filters exclude blacklisted companies** on the Jobs list and the Contacts list. User can toggle them on.
> - **Manual adds start verified.** Forms call `upsert_contact(is_unverified=False)`. The `is_unverified=True` path is only for automated extraction (PR3).
> - **Empty states are friendly.** Every list and section that can be empty shows a one-line helpful message instead of a blank table.
> - **No bulk delete.** Deletes are dangerous; defer to a later "data hygiene" PR.
> - The existing `tracker_legacy.py` filter/badge logic is the source of truth for Jobs list behavior — port, don't reinvent.
>
> ### Tests
>
> Streamlit apps are notoriously hard to unit-test. Pragmatic targets:
>
> - `test_score_badge_thresholds` — 8 → 🔥, 5 → ⭐, 3 → 👀
> - `test_company_status_badge_all_values_render` — each value in `COMPANY_STATUSES` produces a non-empty string
> - `test_relationship_badge_all_values_render` — same for derived relationship statuses
> - `test_load_companies_excludes_blacklisted_by_default` — without UI, just verify the data-loader respects the default filter
> - `test_load_contacts_filters_by_role_family` — basic filter sanity
> - **Smoke import test**: `python -c "from tracker_views import dashboard, jobs, companies, contacts, settings, forms, shared"` succeeds with no import errors
>
> Don't try to write end-to-end UI tests — manual checklist below is the verification surface.
>
> ### Verification (manual checklist)
>
> 1. `streamlit run tracker_legacy.py` launches without errors (confirms the rename didn't break anything).
> 2. `streamlit run tracker.py` launches without errors; the five top-level nav items are visible.
> 3. **Dashboard**: all five widgets render. If a widget has no data, the empty state shows. Click an item in each widget — navigation lands on the right detail page.
> 4. **Jobs list**: filters work (test score range, work_mode, country, exclude archived). Clicking a row navigates to Job detail.
> 5. **Job detail**: tracking status change updates DB AND auto-logs an interaction (verify with `SELECT * FROM interactions WHERE job_id = '...' ORDER BY occurred_at DESC LIMIT 5;`).
> 6. **Companies list**: status filter excludes blacklisted by default; toggle reveals them. "+ Add company" dialog opens, submits, new row appears after a Refresh.
> 7. **Companies bulk action**: select 2 rows, change status to "passed_by_me", apply. Verify both rows updated AND two new rows in `company_status_history`.
> 8. **Company detail**: status dropdown change logs to history. Each of the four tabs renders. "+ Add contact" dialog opens and submits.
> 9. **Contacts list**: `is_unverified` filter shows unverified contacts. "Mark verified" bulk action flips the flag.
> 10. **Contact detail**: derived relationship_status displays correctly (test with a contact that has `outreach_sent` → should show `cold_contacted`; add a `reply_received` interaction → should update to `replied`). Channels render only when populated.
> 11. **Settings**: stats display correctly. "Re-extract job fields" button kicks off the subprocess and shows output.
> 12. **Deep links work**: open `http://localhost:8501/?id=<some_company_id>` while on the Companies page → Company detail renders directly.
> 13. **Cache refresh**: edit a contact's email via Python REPL → click Refresh on Dashboard → updated email appears in the Contacts list.
> 14. **Old tracker survives one cycle**: leave `tracker_legacy.py` alone, use the new tracker for ~1 week, then delete legacy in a follow-up commit.
>
> ### Commit message
>
> ```
> Rebuild tracker as multi-page Streamlit app with CRM views
>
> - 5 top-level pages via st.navigation: Dashboard, Jobs, Companies, Contacts, Settings
> - Detail views via query-param sub-routes (?id=X) within Jobs/Companies/Contacts
> - Add/log forms as st.dialog modals (add_company, add_contact, log_interaction)
> - Dashboard widgets: follow-ups due, recent inbound, unverified contacts,
>   stale active outreach, hot jobs feed
> - Bulk actions: company status change, contact verify
> - Port filter and badge logic from tracker_legacy.py
> - Preserves tracker_legacy.py for one transition cycle
> - Pins streamlit >= 1.36 (for st.navigation, st.dialog, st.dataframe selection)
> ```

---

## PR 5 — LinkedIn hiring-team widget scraper (defer)

This unlocks the highest-yield contact source but it's also the most fragile (LinkedIn anti-scraping, rate limits, per-job re-fetch). Defer until PR3 and PR4 have shipped and the contacts pipeline + UI exist end-to-end — that way you can dogfood widget-discovered contacts directly in the new UI as they land.

Outline:

- Standalone module `scrapers/linkedin_hiring_team.py` that takes a LinkedIn job URL and returns `{name, headline, profile_url}` or `None`.
- Targets the `.jobs-poster` block on the public job page (selector may have changed — verify before building). Use `httpx` with a real User-Agent.
- Aggressive rate limiting (one request per 6-8 seconds, jittered) and graceful failure — if LinkedIn blocks or returns a login wall, return `None` and log the URL for manual review.
- Hook: after `JobSpyScraper._dataframe_to_postings` finishes for LinkedIn rows, iterate and call the widget scraper for each. Surface contacts via `db.upsert_contact()` with `is_unverified=False` (widget-sourced contacts are higher confidence than regex/LLM) and log a `discovered_on_posting` interaction.
- Optional: only refetch for jobs above a score threshold to avoid scraping the entire LinkedIn pool — saves rate-limit budget.

Full prompt to be written after PR4 lands.

---

## Suggested workflow

1. Paste **PR1** prompt into Claude Code on a feature branch `companies-foundation`. Review diff, verify, merge.
2. Wait one scrape cycle or run `scrape.py` once. Confirm `jobs.company_id` is populated and the blacklist filter excludes what you expect.
3. Paste **PR2** prompt on branch `move-company-fields`. Verify, merge.
4. Paste **PR3** prompt on branch `contacts-and-interactions`. Verify, merge.
5. **Smoke-test interval after PR3**: clear `extracted_at` on your top ~50 jobs (`UPDATE jobs SET extracted_at = NULL WHERE id IN (SELECT job_id FROM job_scores WHERE score >= 6);`) and re-run `python score.py --extract`. Inspect the contacts and interactions that land in the DB. Spend a few days noticing what you'd want to see in a UI.
6. Tweak the **PR4** prompt based on what you learned in step 5, then paste on branch `tracker-ui-rebuild`. Verify, merge.
7. Use the new tracker for one week. If nothing breaks, delete `tracker_legacy.py` in a follow-up commit.
8. **PR5** (LinkedIn widget) last — write its full prompt once you've used the new UI for a week and know what you want widget-sourced contacts to look like in it.

If anything in the prompts above feels wrong before you launch a session, edit this doc — the prompts are designed to be edited, not pasted verbatim.
