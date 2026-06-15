# Quickstart: Monitored Companies

**Feature**: 001-monitored-companies
**Date**: 2026-06-15

Validation guide — not implementation instructions. Run these scenarios to prove the feature works end-to-end.

## Prerequisites

- Working `job_agent` installation (venv, dependencies)
- Groq API key configured (for scoring validation in Phase E)
- At least one known ATS company in the database (e.g., Fireblocks/Greenhouse)

## Phase A — Data Model Validation

### A1: Companies table extended

```bash
# Verify new columns exist
sqlite3 data/jobs.db ".schema companies"
# Expected: careers_url, ats_provider, ats_identifier, scraper_id, monitored, detected_at columns present
```

### A2: Seed companies

```bash
# After migration, verify seed Greenhouse boards
sqlite3 data/jobs.db "SELECT COUNT(*) FROM companies WHERE ats_provider = 'greenhouse'"
# Expected: ~30
```

### A3: Backfill

```bash
# Companies from historical jobs populated
sqlite3 data/jobs.db "SELECT COUNT(*) FROM companies WHERE ats_provider IS NULL"
# Expected: >0 (companies auto-created from scraped jobs, not yet enriched)
```

## Phase B — Scraper Taxonomy + Greenhouse

### B1: Scraper discovery

```bash
python scrape.py --list
# Expected: all boards/* scrapers + ats/* adapters listed
```

### B2: Greenhouse parity

```bash
# Old code (before refactor): jobs from greenhouse
# New code (after refactor, --monitored-only with all 30 boards monitored):
python scrape.py --monitored-only
# Expected: same number of new jobs as a full scrape.py run would produce from greenhouse
```

### B3: Monitored-only skips boards

```bash
python scrape.py --monitored-only 2>&1 | grep -c "linkedin\|indeed\|remoteok"
# Expected: 0 (no broad boards contacted)
```

## Phase C — ATS Adapters

### C1: Lever

```bash
# After adding a Lever company to the DB
python scrape.py --monitored-only --source lever
# Expected: jobs from that company written to DB
```

### C2: Ashby (Kraken)

```bash
# After adding Kraken (ats_provider=ashby, identifier=<board>)
python scrape.py --monitored-only --source ashby
# Expected: Kraken openings in DB
```

### C3: Workday (Lombard Odier)

```bash
# After adding Lombard Odier
python scrape.py --monitored-only --source workday
# Expected: openings written to DB
```

## Phase D — ATS Detection + Company Addition

### D1: Hostname detection

```bash
# In Settings UI: paste https://boards.greenhouse.io/fireblocks
# Expected: ats_provider=greenhouse, ats_identifier=fireblocks resolved
```

### D2: Vanity domain detection

```bash
# In Settings UI: paste https://careers.coinbase.com
# Expected: page fetched, embedded ATS board discovered, method resolved
```

### D3: Non-scrapable URL

```bash
# In Settings UI: paste https://example.com
# Expected: "no scrapable source detected" message, toggle disabled
```

## Phase E — Scoring + Gate + Badge + UI

### E1: Title gate (FELFEL regression)

```bash
# Job titled "Senior Tech Product Owner" → gate PASS
python score.py --extract  # (gate runs pre-LLM within score.py)
sqlite3 data/jobs.db "SELECT COUNT(*) FROM jobs WHERE filtered_non_product = 1 AND title LIKE '%Product Owner%'"
# Expected: 0 (PM titles should not be filtered)
```

### E2: Title gate filters non-PM

```bash
sqlite3 data/jobs.db "SELECT COUNT(*) FROM jobs WHERE filtered_non_product = 1"
# Expected: >0 (engineering/sales jobs from monitored companies filtered)
```

### E3: Provenance badge

```bash
streamlit run tracker.py
# Navigate to Jobs page
# Expected: monitored-company jobs show "🎯 Monitored · {company}" badge
# The badge appears even when no scoring is configured
```

### E4: Monitoring signal in scoring

```bash
# Score a monitored-company job and an identical non-monitored job
python score.py --mock
# Expected: monitored job scores higher, but a clearly irrelevant one scores low
```

### E5: Pause/resume

```bash
# In Settings: pause a monitored company
python scrape.py --monitored-only 2>&1 | grep "<company_name>"
# Expected: paused company not contacted
# In Settings: resume → run again → company IS contacted
```
