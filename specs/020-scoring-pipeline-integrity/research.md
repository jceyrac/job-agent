# Research — Scoring Pipeline Integrity

**Created**: 2026-07-07 | **Plan**: [plan.md](plan.md)

## Investigation 1: `job_helpers.py` per-job scoring

**Question**: Confirm there's no analogous per-job scoring bug in `_render_action_bar`, `_run_score`, `_derive_state` — e.g. a single-job "Score this job" action that bypasses or duplicates the pre-filter logic being removed.

**Finding**: Clean. No pre-filter logic exists in `job_helpers.py`.
- `_run_score()` (line 57-68) calls `score_one(job_id, profile_id)` from `job_actions` — this scores a single job directly, with no batch pre-filter applied. This is by design: when a user manually clicks "Score" on a job, they're explicitly choosing to bypass batch eligibility gates.
- The only gate is the UI state machine: the "Score" button is only enabled when `_derive_state()` returns `"extracted"` (see `ENABLED` dict at line 93).
- There are zero references to `location_contains` or `exclude_location_contains` anywhere in `job_helpers.py`.
- **Decision**: No changes needed. Single-job manual scoring is correct as-is.

## Investigation 2: Extraction/scoring ordering in `score.py`

**Question**: Confirm whether extraction and scoring are decoupled passes that could run out of order, and whether a job reaching Tier-0 unextracted (with `company_country`/`geo_zone` defaulting to `"unknown"`) is handled correctly.

**Finding**: Decoupled passes with an explicit DB re-read between them. Safe.
- **Phase 1** (lines 376-394): Only jobs with `extracted_at IS NULL` get extracted via `extract_one()`.
- **DB re-read** (lines 402-409): After extraction, `get_jobs_for_scoring()` is called AGAIN to pick up freshly extracted fields in the returned rows.
- **Phase 2** (lines 414+): `evaluate_for_profile()` runs against the re-read rows, which have up-to-date `company_country`, `geo_zone`, etc.
- **Edge case — extraction failure**: If `extract_one()` fails (returns None or status=error), extraction fields stay as defaults. In this case, Tier-0 at lines 742-757 handles `company_country="unknown"` safely:
  - For on-site/hybrid with unknown country: passes through (no rejection).
  - For remote with unknown country: falls back to `geo_zone`. If `geo_zone` is also `"unknown"`, passes through.
  - This is the correct behavior — unknown means "cannot determine, let the LLM decide."
- **Decision**: No guard needed. The existing extraction→re-read→evaluation flow is correct.

## Investigation 3: Other consumers of `pre_filter["exclude_location_contains"]` and `pre_filter["location_contains"]`

**Question**: Are there other consumers of these dict keys beyond `get_jobs_for_scoring()`? If so, removal could break something.

**Finding**: Only two consumers beyond `get_jobs_for_scoring()`:

| Consumer | Key | Action |
|----------|-----|--------|
| `score.py:354-358` | `location_contains` | Merges `profile.location_keywords` into `effective_pre_filter["location_contains"]` before passing to `get_jobs_for_scoring()`. Must be removed. |
| `settings.py:642-677` | `exclude_location_contains` | Profile Editor reads/writes this field in the "Advanced scrape inputs" expander. Should be relabeled as "legacy" since nothing reads it from the DB for scoring purposes anymore. |
| `profile_generator.py:138` | `exclude_location_contains` | Standalone profile generation tool. Writes this key when generating profiles with banned countries. No action needed — it's a write-only tool, and the key becomes a no-op gracefully. |
| `profiles.py:208` | `exclude_location_contains` | UNIFIED_JC hardcoded profile definition. Becomes a no-op after Goal 2 — stays for documentation of intent. |

**Decision**:
- Remove `location_keywords` → `location_contains` merge in `score.py` (lines 354-358).
- Relabel the settings UI field as "legacy — no longer used by scoring."
- Keep the DB values (data preservation; no migration needed).
- No scraper-side query builder depends on these keys.

## Summary

All three investigation items confirmed no surprises. The changes in the plan are sufficient and safe.
