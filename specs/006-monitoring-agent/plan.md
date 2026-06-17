# Plan 006 — Monitoring Agent

## Phase 0 — Seed file prerequisite (export_seed.py + seed.py)

Read `storage.py` to understand the companies table schema.

### `export_seed.py`
Reads all rows from the `companies` table in the Dev DB.
Excludes `id` and `researched_at` (env-specific).
Writes `data/companies.json` (pretty-printed, sorted by name).

### `seed.py`
Reads `data/companies.json`.
For each entry, calls `db.upsert_company_from_seed(row)` — a new storage method
that matches on `name` (case-insensitive) and upserts all config fields.
Never touches `jobs`, `scores`, `run_logs` tables.
Prints: "N companies upserted, M already up to date."

### `storage.py` addition
Add `upsert_company_from_seed(row: dict)` — upsert on `LOWER(name)`, update all
seed fields, preserve `id` and `researched_at`.

### `deploy.sh`
Add `python seed.py` after `git pull`, before service restart.

### `.gitignore`
Confirm `data/jobs.db` is ignored. Add `data/companies.json` to tracked files
(remove from gitignore if present).

Test: run `export_seed.py` on Dev → `data/companies.json` created with correct
content. Run `seed.py` on same Dev DB → "N already up to date." Run `seed.py`
after manually deleting one company row → that row is re-inserted.

## Phase 1 — Read existing scraper config structure

Before writing `monitoring_agent.py`, read:
- `scrapers/greenhouse.py` — exact location of GREENHOUSE_BOARDS list
- `scrapers/ats/lever.py` — exact location of slug config
- `scrapers/ats/workable.py` — exact location of slug config
- `specs/` directory — current highest spec number

This phase produces no code — only knowledge for Phase 2.

## Phase 2 — Core routing + Actions A/B/E/F

Create `monitoring_agent.py`:
1. `_load_pending_researched(db)` — `watch_pending` companies with `researched_at` set
2. `_decide_action(company) -> str` — switch on `scraping_method`
3. `_action_a_greenhouse(company, db, dry_run)` — edit config + git commit
4. `_action_b_ats(company, provider, db, dry_run)` — edit config + git commit
5. `_action_e_jobspy(company, db, dry_run)` — set `watch_ready`
6. `_action_f_manual(company, db, dry_run)` — log note, stay `watch_pending`
7. `_run_export_seed(dry_run)` — subprocess call to `export_seed.py`
8. CLI: `--dry-run`, `--apply-simple`, `--apply-all`, `--company NAME`
9. Formatted output table + summary line

Test:
- `--dry-run` on Tier A — correct action per company, no side effects
- `--apply-simple` on Impossible Cloud → lever commit to main, `watch_ready` in DB
- `--apply-simple` on Arrakis Finance → action F, stays `watch_pending`
- `data/companies.json` updated after apply run

## Phase 3 — Actions C/D (spec + branch generation)

1. `_next_spec_number() -> str` — scan `specs/`, return next zero-padded NNN
2. `_write_ats_spec(company, result, spec_num)` — write spec/plan/tasks from template
3. `_write_custom_spec(company, result, spec_num)` — same for custom HTML
4. `_write_scraper_stub(ats_provider, path)` — BaseScraper stub with TODO markers
5. `_action_c_new_ats(company, result, db, dry_run)`:
   - Check existing spec for this ATS
   - Generate spec files
   - `git checkout -b scraper/{ats}-{slug}`
   - Write stub
   - Commit on branch
6. `_action_d_custom(company, result, db, dry_run)`:
   - Generate spec files
   - `git checkout -b scraper/custom-{slug}`
   - Write stub
   - Commit on branch

Test:
- `--dry-run` on hypothetical Ashby company → prints branch name, no files written
- `--apply-all` → branch created, spec files present, stub file present
- `git log --oneline` shows commit on branch

## Phase 4 — End-to-end

1. `python monitoring_agent.py --dry-run` on full watch_pending list — no crash
2. `--apply-simple` → git log shows A/B commits on main, `data/companies.json` updated
3. `--apply-all` → branches exist for C/D companies
4. DB: correct status for all companies
5. `python scrape.py --monitored-only` — no regressions after config edits
6. Simulate deploy: run `python seed.py` on a fresh DB → companies correctly seeded
