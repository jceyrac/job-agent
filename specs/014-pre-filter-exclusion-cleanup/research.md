# Research: Pre-filter exclusion cleanup

**Feature**: Spec 014 — Don't store non-PM jobs in DB
**Date**: 2026-07-01

## Research Tasks

### 1. Where is the save loop in `_run_broad_scrape()`?

**Decision**: Lines 347–354 in `scrape.py` — the loop that calls `db.save_unscored(job, company_id=company_id)`.

**Rationale**: This is the only path where broad-scraped jobs (boards + ATS discovery) enter the DB. `_run_monitored_only()` (lines 268–280) is the other save path but must NOT get the gate per spec.

**Alternatives considered**: None — the code location is unambiguous.

### 2. Does `is_product_management_title()` handle edge cases?

**Decision**: Yes. Returns `False` for `None`/empty strings (line 34: `if not title: return False`). Case-insensitive. Substring match means "Product Manager" and "Senior Product Manager" both match.

**Rationale**: Already used in `score.py` line 371 for monitored company filtering. No bugs reported.

**Alternatives considered**: Writing a new keyword list — rejected per spec (non-objective: "no new keyword lists introduced").

### 3. Should the gate use `print()` or `logger`?

**Decision**: Use `print()` to match the surrounding code style.

**Rationale**: `scrape.py` uses `print()` throughout (see lines 149–151, 200–201, 243, etc.). Introducing `logger` would be inconsistent.

**Alternatives considered**: `logger.debug()` (shown in spec snippet) — rejected because `scrape.py` doesn't import `logging` and doesn't use loggers anywhere.

### 4. Should we count and report skipped jobs?

**Decision**: Yes — increment a counter and print a summary line at the end, matching existing patterns (e.g., `total_excluded_date` at line 363–364).

**Rationale**: Users need visibility into what the gate is doing. A silent filter is a debugging nightmare.

**Alternatives considered**: Silent skip (no counter) — rejected because it would hide operational issues.
