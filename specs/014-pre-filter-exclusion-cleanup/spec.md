# Spec 012 — Pre-filter exclusion cleanup (don't store non-PM jobs in DB)

> Spec for Claude Code. Read `scrape.py`, `score.py`, `storage.py`, and `main.py` before starting.
> Do not modify `scorer.py`, `profiles.py`, `models.py`, `llm.py`, or any scraper.

---

## Context

The DB was accumulating non-PM jobs (engineers, marketing managers, data analysts,
etc.) that were excluded by the pre-filter in `score.py` but already persisted in
the DB by `scrape.py`. These jobs sat permanently as "unscored" — never visible to
the user, never purged by spec 009 (because they have no `job_tracking` row and
their `first_seen` refreshes each scrape run).

A one-time cleanup was already run on the Live DB on 2026-06-26, deleting 818 jobs
(Before: 5,527 total / 931 unscored → After: 4,709 total / 113 unscored).

This spec prevents the problem from recurring by applying the PM title gate at
scrape time, before any DB write.

---

## Approach

### What "PM title gate" means at scrape time

The title gate at scrape time is intentionally minimal — a fast, cheap string check,
not the full pre_filter logic (which requires DB access and profile context).

Gate rule: **discard any job whose title does not contain at least one PM keyword**.

Use the existing `is_product_management_title(title)` function from `title_gate.py`,
which is already used in `score.py` for monitored companies. No new logic needed.

### What does NOT change

- The full pre-filter in `score.py` (location, seniority, etc.) stays as-is — it
  operates on jobs already in DB and handles profile-specific filters.
- The `save_unscored()` method in `storage.py` is not removed — it's still used
  for jobs that pass the title gate but fail LLM extraction/scoring.
- No change to the DB schema.

---

## Files to modify

### `scrape.py`

In the section where scraped jobs are saved to DB (the loop that calls
`db.save_unscored()` or equivalent for raw scraped jobs), add the title gate:

```python
from title_gate import is_product_management_title

# Inside the save loop:
if not is_product_management_title(job.title):
    logger.debug(f"[scrape] skipped (non-PM title): {job.title[:60]}")
    continue
```

Location: find the loop in `scrape.py` that iterates over scraped jobs and persists
them. The gate must run **before** any DB write for that job.

The gate must NOT apply to jobs from monitored companies
(`job.monitored_company_id is not None` or equivalent flag). Monitored companies
may post non-PM roles that we want to track for context — they are already filtered
by `is_product_management_title()` in `score.py` at evaluation time.

---

## Non-objectives

- No UI changes in the tracker
- No change to the purge logic (spec 009)
- No filtering on location or seniority at scrape time — title gate only
- No "rejected" or "not relevant" status written to DB for filtered jobs — they are
  simply not inserted
- No one-time cleanup script (already executed on Live DB on 2026-06-26)

---

## Acceptance criteria

- [ ] After deploying, a full pipeline run produces zero new "unscored" entries in
      the DB for non-PM titles
- [ ] `is_product_management_title` is the sole gate — no new keyword lists introduced
- [ ] Jobs from monitored companies bypass the scrape-time gate (scored separately)
- [ ] `save_unscored()` is still called for jobs that pass the gate but fail LLM scoring
- [ ] No change to the DB schema or any storage method
