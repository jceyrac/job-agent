# Quickstart: Pre-filter exclusion cleanup

**Feature**: Spec 014
**Date**: 2026-07-01

## Prerequisites

- Working DB at `data/jobs.db`
- Python 3.11 venv activated
- `title_gate.py` present (should already exist)

## Validation Scenarios

### Scenario 1: Non-PM job is rejected at scrape time

```bash
# Run a broad scrape
python scrape.py
```

**Expected output**: Lines like `skipped (non-PM title): Senior Software Engineer @ Acme Corp` appear. A summary line at the end shows total skipped: `🔤 N jobs skipped (non-PM title)`.

### Scenario 2: PM job passes the gate

A job with title "Senior Product Manager" or "Product Owner" passes through and is saved to DB as normal. The `save_unscored()` path is unchanged.

### Scenario 3: Monitored company jobs bypass the gate

```bash
# Run monitored-only scrape — NO title gate applied
python scrape.py --monitored-only
```

**Expected**: All jobs from monitored companies are saved, regardless of title. No `skipped (non-PM title)` lines appear (or they appear only for non-monitored paths, but `--monitored-only` skips broad scrape entirely).

### Scenario 4: Regression — no pipeline errors

```bash
# Full pipeline should complete without errors
python main.py
```

**Expected**: Pipeline completes normally. Zero new unscored entries for non-PM titles.

## Acceptance Criteria Trace

| Criteria | Validation |
|----------|------------|
| Zero new unscored non-PM entries after pipeline run | Check `SELECT COUNT(*) FROM jobs j LEFT JOIN job_scores s ON j.id = s.job_id WHERE s.job_id IS NULL` before/after |
| `is_product_management_title` is the sole gate | `grep -r "product.manager\|PM.*title" scrape.py` — only one reference to the function |
| Monitored companies bypass | `grep "is_product_management_title" scrape.py` — NOT present in `_run_monitored_only()` |
| `save_unscored()` still called for LLM failures | No change to `save_unscored()` call site — gate is *before* it |
| No DB schema changes | `git diff main -- storage.py` is empty |
