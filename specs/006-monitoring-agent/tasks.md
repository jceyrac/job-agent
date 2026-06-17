# Tasks 006 — Monitoring Agent

## Phase 0 — Seed file prerequisite

### storage.py
- [x] Read `storage.py` — understand companies table schema
- [x] Add `upsert_company_from_seed(row: dict)` — upsert on LOWER(name),
      update all config fields, preserve id + researched_at

### export_seed.py
- [x] Create `export_seed.py` at project root
- [x] Read all companies from Dev DB
- [x] Exclude `id`, `researched_at` from output
- [x] Write `data/companies.json` (pretty-printed JSON, sorted by name)
- [x] Print: "Exported N companies to data/companies.json"

### seed.py
- [x] Create `seed.py` at project root
- [x] Read `data/companies.json`
- [x] Call `db.upsert_company_from_seed(row)` for each entry
- [x] Print: "N companies upserted, M already up to date"
- [x] Verify: never touches jobs / scores / run_logs tables

### deploy.sh + .gitignore
- [x] Add `python seed.py` to `deploy.sh` after `git pull`
- [x] Confirm `data/jobs.db` is gitignored
- [x] Confirm `data/companies.json` is tracked (not gitignored)

### Tests
- [x] `python export_seed.py` → `data/companies.json` created with correct fields
- [x] `python seed.py` on same DB → "N already up to date" (idempotent)
- [x] Delete one company row, re-run `seed.py` → row re-inserted correctly

## Phase 1 — Read config structure (no code)
- [x] Read `scrapers/greenhouse.py` — note exact GREENHOUSE_BOARDS location + format
- [x] Read `scrapers/ats/lever.py` — note slug config location + format
- [x] Read `scrapers/ats/workable.py` — note slug config location + format
- [x] List `specs/` — note current highest spec number

## Phase 2 — Core routing + Actions A/B/E/F

### Setup
- [x] Create `monitoring_agent.py` at project root
- [x] `_load_pending_researched(db)` — watch_pending + researched_at IS NOT NULL
- [x] `_decide_action(company) -> str`:
  - `greenhouse` → `"A"`
  - `lever` / `workable` → `"B"`
  - `ashby` / `teamtailor` / other known ATS → `"C"`
  - `custom_html` → `"D"`
  - `jobspy` → `"E"`
  - `none` / `manual` / else → `"F"`

### Action A
- [x] `_action_a_greenhouse(company, db, dry_run)`:
  - [x] Read Greenhouse config file
  - [x] Check duplicate slug — skip + warn if present
  - [x] Insert slug + company comment
  - [x] `git add {file} && git commit -m "monitor: add {company} ({slug}) to Greenhouse boards"` if not dry_run
  - [x] `db.set_monitoring_status(id, "watch_ready")` if not dry_run

### Action B
- [x] `_action_b_ats(company, provider, db, dry_run)`:
  - [x] Read lever.py or workable.py config
  - [x] Check duplicate — skip if present
  - [x] Insert slug + comment
  - [x] Git commit to main if not dry_run
  - [x] Set `watch_ready` if not dry_run

### Action E
- [x] `_action_e_jobspy(company, db, dry_run)`:
  - [x] Set `watch_ready`, note "Covered by JobSpy broad scrape"

### Action F
- [x] `_action_f_manual(company, db, dry_run)`:
  - [x] Leave `watch_pending`
  - [x] Append "No automated monitoring available" to research_notes

### Post-run
- [x] `_run_export_seed(dry_run)` — subprocess `python export_seed.py` if not dry_run

### CLI + output
- [x] `argparse`: `--dry-run`, `--apply-simple`, `--apply-all`, `--company NAME`
- [x] Formatted table: Company | Method | Action | Result
- [x] Summary line: "N committed, M branches created, K manual"
- [x] Confirm `data/companies.json` updated line at end

### Tests
- [x] `--dry-run` Tier A — correct action per company, no side effects ✅
- [x] `--apply-simple` Impossible Cloud → lever commit, `watch_ready` in DB ✅
- [x] `--apply-simple` WalletConnect → workable commit, `watch_ready` in DB ✅
- [x] `--apply-simple` Arrakis → action F, stays `watch_pending` ✅
- [x] `data/companies.json` updated after apply ✅

## Phase 3 — Actions C/D

- [x] `_next_spec_number() -> str` — scan specs/, return next NNN zero-padded
- [x] `_write_ats_spec(company, result, spec_num)`:
  - [x] Create `specs/{NNN}-{ats}-scraper/` directory
  - [x] Write `spec.md`, `plan.md`, `tasks.md` from ATS template
- [x] `_write_custom_spec(company, result, spec_num)`:
  - [x] Create `specs/{NNN}-custom-{slug}/` directory
  - [x] Write `spec.md`, `plan.md`, `tasks.md` from custom template
- [x] `_write_scraper_stub(provider, path)`:
  - [x] BaseScraper subclass with SOURCE_NAME, ENABLED, fetch() stub
  - [x] TODO markers for HTTP fetch, HTML parse, JobPosting mapping
  - [x] Reference comment to lever.py pattern
- [x] `_action_c_new_ats(company, result, db, dry_run)`:
  - [x] Check if spec for this ATS already exists — add slug to existing if so
  - [x] Otherwise call `_write_ats_spec()`
  - [x] `git checkout -b scraper/{ats}-{slug}` if not dry_run
  - [x] Write stub to `scrapers/ats/{ats}.py`
  - [x] `git add . && git commit` on branch
  - [x] Print branch name for human review
- [x] `_action_d_custom(company, result, db, dry_run)`:
  - [x] Call `_write_custom_spec()`
  - [x] `git checkout -b scraper/custom-{slug}`
  - [x] Write stub to `scrapers/company_sites/{slug}.py`
  - [x] Commit on branch

### Tests
- [x] `--dry-run` on hypothetical Ashby company → prints branch name, no files ✅
- [x] `--apply-all` → branch `scraper/ashby-{slug}` exists in git ✅
- [x] Spec files in correct `specs/` directory ✅
- [x] Stub in `scrapers/ats/ashby.py` with TODO markers ✅

## Phase 4 — End-to-end
- [x] `--dry-run` full watch_pending list — no crash on any company
- [x] `--apply-simple` → git log shows A/B commits on main
- [x] `--apply-all` → branches for C/D, commits for A/B/E/F
- [x] DB status correct for all actions
- [x] `python scrape.py --monitored-only` — no regression after config edits
- [x] `python seed.py` on fresh DB → companies seeded correctly from JSON
