# Spec 006 — Monitoring Agent

> ⚠️ Prerequisite: Spec 006-pre (scraper config from DB) must be implemented first.

## Prerequisite — Company seed file (Option 4)

### Problem

Dev and Live have separate SQLite databases. The `companies` table (monitoring
config: `ats_provider`, `ats_board_slug`, `monitoring_status`, etc.) is created
and maintained in Dev but must also exist in Live for the cron scraper to function.
Without a sync mechanism, deploying new monitoring config requires manual DB
intervention on the server.

### Solution — `data/companies.json` versioned in git

The `companies` table is split into two concerns:

- **Config data** (companies, monitoring metadata) — source of truth is Dev,
  versioned in git as `data/companies.json`
- **Runtime data** (jobs, scores, run_logs) — source of truth is Live, gitignored

Two small scripts manage the lifecycle:

#### `export_seed.py` (run on Dev before every commit that changes companies)

```bash
python export_seed.py
# Writes data/companies.json from the Dev DB companies table
# Includes: name, website, careers_url, ats_provider, ats_board_slug,
#           ats_board_url, scraping_method, monitoring_status,
#           research_notes, research_confidence, x_handle, sector,
#           hq_location, notes
# Excludes: id (auto-assigned per env), researched_at (env-specific)
```

#### `seed.py` (run on Live by deploy.sh after git pull)

```bash
python seed.py
# Reads data/companies.json, upserts into DB companies table
# Match key: name (case-insensitive)
# Never deletes existing rows — upsert only
# Never touches jobs, scores, or run_logs tables
# Idempotent — safe to run multiple times
```

#### `deploy.sh` addition

```bash
git pull
python seed.py          # ← add this line before restart
supervisorctl restart job-agent
```

#### `data/companies.json` format

```json
[
  {
    "name": "Impossible Cloud (ICN)",
    "website": "https://impossiblecloud.com",
    "careers_url": "https://jobs.lever.co/impossiblecloud",
    "ats_provider": "lever",
    "ats_board_slug": "impossiblecloud",
    "ats_board_url": "https://jobs.lever.co/impossiblecloud",
    "scraping_method": "lever",
    "monitoring_status": "watch_ready",
    "research_notes": "Detected Lever board from careers page",
    "research_confidence": "high",
    "x_handle": null,
    "sector": "DePIN / Decentralised cloud",
    "hq_location": "Zug",
    "notes": "Fondé par entrepreneurs ayant construit une boîte à 1Md€"
  }
]
```

### Workflow after this prerequisite

```
Dev
  → import CSV → DB Dev companies table
  → researcher → monitoring_agent → config edits
  → python export_seed.py   ← regenerate data/companies.json
  → git add data/companies.json scrapers/ → git commit → git push

Live (deploy.sh)
  → git pull
  → python seed.py          ← upsert companies from JSON into Live DB
  → restart
  → cron: scrape.py --monitored-only uses updated companies table
```

### Files to create for this prerequisite

- **Create** `export_seed.py` at project root (~30 lines)
- **Create** `seed.py` at project root (~30 lines)
- **Create** `data/companies.json` (initially `[]`, populated by first `export_seed.py` run)
- **Modify** `deploy.sh` — add `python seed.py` after `git pull`
- **Modify** `.gitignore` — `data/jobs.db` ignored, `data/companies.json` tracked

`export_seed.py` and `seed.py` must be implemented and tested **before**
`monitoring_agent.py`. The monitoring agent calls `export_seed.py` automatically
at the end of every `--apply-simple` and `--apply-all` run.

---

## Goal

Build `monitoring_agent.py` — a semi-autonomous agent that, given the output of
`company_researcher.py`, takes action on each `watch_pending` company without
human intervention for simple cases, and generates SpecKit specs + git branches
for cases requiring new code.

The agent runs on the **dev machine** (not the server). Its output is either a
direct git commit to `main` (simple config changes) or a new branch + spec (new
scrapers). The human reviews branches before merging and deploying.

---

## Decision model

The agent's routing logic is fully deterministic — no LLM needed for the decision
itself. The `scraping_method` field from `ResearchResult` drives everything:

```
scraping_method = "greenhouse"  → Action A: add board slug to Greenhouse config
scraping_method = "lever"       → Action B: add slug to Lever scraper config
scraping_method = "workable"    → Action B: add slug to Workable scraper config
scraping_method = "ashby"       → Action C: new ATS scraper (Ashby) via SpecKit
scraping_method = "teamtailor"  → Action C: new ATS scraper (Teamtailor) via SpecKit
scraping_method = "custom_html" → Action D: new custom scraper via SpecKit
scraping_method = "jobspy"      → Action E: already covered by broad scrape — mark watch_ready, no new code
scraping_method = "none"        → Action F: mark manual in DB, notify user
scraping_method = "manual"      → Action F: same
```

---

## Actions

### Action A — Add board slug to existing config (Greenhouse)

1. Read `scrapers/greenhouse.py` (or wherever `GREENHOUSE_BOARDS` is defined)
2. Check if slug already present — skip if yes
3. Add slug + company comment to the config list
4. `git add scrapers/greenhouse.py && git commit -m "monitor: add {company} ({slug}) to Greenhouse boards"`
5. Update DB: `monitoring_status = 'watch_ready'`
6. No branch — commit directly to `main`

### Action B — Add slug to Lever / Workable scraper config

Same pattern as Action A but for `scrapers/ats/lever.py` or `scrapers/ats/workable.py`.
Locate the `SLUGS` or equivalent config list, add slug + comment, commit to `main`.
Update DB: `monitoring_status = 'watch_ready'`

### Action C — New ATS scraper via SpecKit

For an ATS platform not yet implemented (e.g. Ashby, Teamtailor):

1. Check if a spec for this ATS already exists in `specs/` — if so, add the slug
   to that spec's board list instead of creating a new spec
2. Determine next spec number (scan `specs/` directory)
3. Generate `specs/NNN-{ats}-scraper/spec.md`, `plan.md`, `tasks.md` using
   the Greenhouse scraper as a reference template
4. Create git branch: `git checkout -b scraper/{ats}-{slug}`
5. Write a well-commented scraper stub to `scrapers/ats/{ats}.py`
6. Commit spec + stub to branch
7. Update DB: `monitoring_status = 'watch_pending'` (stays until branch merged)
8. Print branch name + spec path for human review

### Action D — Custom HTML scraper via SpecKit

Same as Action C but for a company-specific scraper in `scrapers/company_sites/`.
The spec includes the careers URL, observed HTML structure, and pagination pattern.
Branch name: `scraper/custom-{company_slug}`

### Action E — JobSpy coverage (no new code)

`scraping_method = "jobspy"` means jobs already appear via broad LinkedIn/Indeed scrape.
No new code needed — update DB to `watch_ready`, add a note.

### Action F — No solution found

`scraping_method in ("none", "manual")` — leave `watch_pending`, append to
`research_notes`, print warning for the human.

### All actions — call `export_seed.py` at end of run

After all actions are applied, the agent automatically runs `export_seed.py` to
regenerate `data/companies.json` and stages it for the next commit:

```python
subprocess.run(["python", "export_seed.py"], check=True)
```

---

## LLM usage

| Task | Model |
|---|---|
| Routing decision | No LLM — pure Python switch |
| Spec generation (Actions C, D) | Sonnet 4.6 via Claude Code |
| Scraper stub generation (Actions C, D) | DeepSeek via `./claude-deepseek.sh` |
| Fallback if DeepSeek sloppy | Sonnet 4.6 |
| Opus | Never |

The agent does not call any LLM directly for routing. It delegates code generation
to Claude Code by writing spec files and optionally invoking Claude Code via
subprocess (`--auto-generate` flag).

---

## CLI

```bash
# Dry run — show what would happen, no git changes, no DB writes
python monitoring_agent.py --dry-run

# Apply simple cases (Actions A, B, E, F) only — no new code generation
python monitoring_agent.py --apply-simple

# Full apply — simple cases + generate specs/branches for complex ones
python monitoring_agent.py --apply-all

# Single company
python monitoring_agent.py --company "Impossible Cloud" --apply-simple
```

Output format:

```
Company               Method        Action          Result
─────────────────────────────────────────────────────────────────
Impossible Cloud      lever         B — add slug    ✅ committed to main
WalletConnect         workable      B — add slug    ✅ committed to main
21Shares              greenhouse    A — add slug    ✅ already present — skipped
Dfinity               existing_data —               ✅ already watch_ready
Enzyme Finance        ashby         C — new scraper 📋 branch: scraper/ashby-enzyme
Bitcoin Suisse        custom_html   D — custom      📋 branch: scraper/custom-bitcoin-suisse
Arrakis Finance       none          F — manual      ⚠️  no careers page — marked manual

export_seed.py → data/companies.json updated
```

---

## Git conventions

- Direct commits to `main`: Actions A, B, E, F (config-only, zero risk)
- New branches: Actions C, D (new code, requires human review)
- Commit message format: `monitor: add {company} ({slug}) to {provider} boards`
- Branch name format: `scraper/{provider}-{slug}` or `scraper/custom-{company_slug}`

---

## DB state transitions

| Action | `monitoring_status` after |
|---|---|
| A, B — slug added to config | `watch_ready` |
| C, D — spec + branch created | `watch_pending` (until branch merged + deployed) |
| E — JobSpy coverage | `watch_ready` |
| F — no solution | `watch_pending` (stays, with notes) |

After human merges a C/D branch and deploys, status is set to `watching` manually
via the Streamlit UI (or via `python seed.py` if `companies.json` is updated first).

---

## Files to create

- **Create** `export_seed.py` at project root
- **Create** `seed.py` at project root
- **Create** `data/companies.json` (empty array initially)
- **Create** `monitoring_agent.py` at project root
- **Modify** `deploy.sh` — add `python seed.py` after `git pull`
- **Modify** `.gitignore` — track `data/companies.json`, ignore `data/jobs.db`

## Files read (never modified directly — only via git ops)

- `scrapers/greenhouse.py` — GREENHOUSE_BOARDS location
- `scrapers/ats/lever.py`, `scrapers/ats/workable.py` — slug config location
- `specs/` directory — next spec number

## Non-goals

- No automated merging or deployment (human gate on branches)
- No GitHub PR creation (stretch: add `gh pr create` later)
- No Playwright for JS-rendered scraping
- No Opus usage
- Do not modify `scrape.py`, `main.py`, `storage.py`, or any existing scraper
