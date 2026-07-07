# Data Model — Scoring Pipeline Integrity

**Created**: 2026-07-07 | **Plan**: [plan.md](plan.md)

## No Schema Changes

This feature introduces zero schema changes. No new tables, columns, or migrations.

## Affected Tables (no changes)

### `search_profiles`

The `criteria` JSON column stores `SearchProfile` serialized fields including
`pre_filter.exclude_location_contains`. This field continues to be **stored**
(settings UI can still read/write it), but it is no longer **consumed** by
`get_jobs_for_scoring()`. It becomes a legacy record-keeping field.

### `jobs`

No changes. The columns `location`, `base_location`, `company_country`,
`geo_zone` continue to be used by Tier-0 in `evaluate_for_profile()` as before.

### `job_scores`

No changes. Existing score rows are unaffected.

## Method Signature Changes

### `get_jobs_for_scoring()`

**Before**: Recognizes `pre_filter["location_contains"]` and `pre_filter["exclude_location_contains"]` keys, builds SQL LIKE/NOT LIKE clauses from them.

**After**: Silently ignores these keys if present. Only `title_contains` and `exclude_title_contains` keys are still consumed from `pre_filter` (title gate — Spec 014).

The method signature (`profile_id: str, pre_filter: dict | None = None, rescore: bool = False`) is unchanged for backward compatibility.

## Profile Field Status

| Field | Stored | Consumed by Scoring | Notes |
|-------|--------|--------------------|-------|
| `pre_filter.title_contains` | Yes | Yes (SQL pre-filter) | Spec 014 title gate |
| `pre_filter.exclude_title_contains` | Yes | Yes (SQL pre-filter) | Spec 014 title gate |
| `pre_filter.location_contains` | Yes, but ignored | **No** (removed) | Was merged from `location_keywords` in score.py |
| `pre_filter.exclude_location_contains` | Yes | **No** (removed) | Legacy — relabel in Settings UI |
| `work_mode_geography` | Yes | Yes (Tier-0 in scorer.py) | Structured, correct — no change |
| `location_keywords` | Yes | No (merge removed) | Display/record only after this change |
