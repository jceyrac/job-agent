# CLI Contracts: Monitored Companies

**Feature**: 001-monitored-companies
**Date**: 2026-06-15

## New Flag: `--monitored-only`

Added to `scrape.py`.

### Contract

```bash
python scrape.py --monitored-only
```

**Behavior**:
- Iterates over all companies where `monitored = true` in the `companies` table
- For each monitored company, dispatches to the appropriate scraper by `ats_provider` or `scraper_id`
- Writes all returned openings to the `jobs` table (no filtering at scrape time)
- Sets `monitored_company_id` on each job to the company's `id`
- Skips broad aggregation boards entirely (LinkedIn, Indeed, RemoteOK, etc.)
- Deduplicates by URL (existing mechanism) — jobs already in the DB are skipped
- Rate-limits per provider (polite delays between companies sharing the same ATS)

**Exit codes**:
- `0`: Run completed (even if 0 monitored companies — "nothing to monitor" message)
- `non-zero`: Fatal error (network failure after retries, DB corruption)

**Output**: Standard pipeline logging. Each monitored company contacted is logged. New jobs found are counted.

### Interaction with existing flags

- `--monitored-only` is mutually exclusive with normal broad scrape (default mode)
- `--source <name>` can further filter within monitored-only mode (e.g., `--source greenhouse` to only scrape Greenhouse-monitored companies)
- `--profile`, `--rescore`, `--limit` remain on `score.py` and are unaffected

## ATS Detection (Settings UI)

### Contract: Add Company Form

**Input**: Careers URL (text field)

**Processing**:
1. Parse hostname → match against known patterns (`boards.greenhouse.io`, `jobs.lever.co`, `*.ashbyhq.com`, `*.myworkdayjobs.com`, `careers.smartrecruiters.com`, `apply.workable.com`)
2. If match: extract identifier from URL path, set `ats_provider` and `ats_identifier`
3. If no match: fetch the page HTML, search for known ATS board URLs or API endpoints
4. If still no match: return "no scrapable source detected", offer manual provider + identifier input
5. If resolved: create/update company row, enable monitoring toggle

**Output**:
- Success: `ats_provider` + `ats_identifier` set, toggle enabled
- Failure: error message with manual fallback option

### Contract: Toggle Monitoring

**Input**: Company ID + boolean `monitored`

**Behavior**:
- If `monitored = true`: company will be included in `--monitored-only` runs
- If `monitored = false`: company is excluded (pause) — scraping config preserved
- Toggle availability gated on `ats_provider IS NOT NULL OR scraper_id IS NOT NULL`

**Output**: Immediate UI feedback (toggle state, no page reload needed)

## Title Gate

### Contract

Runs in `score.py` before the LLM extraction/evaluation call, for every unscored job.

**Input**: Job title string

**Processing**: Case-insensitive substring match against inclusion list:
- `product manager`, `senior product manager`, `staff product manager`, `principal product manager`, `lead product manager`, `group product manager`
- `director of product`, `head of product`, `vp product`, `chief product officer`
- `product owner`, `technical product owner`

**Output**:
- Match: job proceeds to scoring pipeline (unchanged flow)
- No match: `filtered_non_product = TRUE` set on the job row. Job is skipped for this scoring run. No Groq call made.
