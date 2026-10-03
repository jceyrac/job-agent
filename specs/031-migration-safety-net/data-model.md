# Data Model: Migration Safety Net

**No database schema change** (non-goal). This spec introduces file-based artifacts on top of the
existing `jobs.db` / `cv_agent_checkpoints.sqlite`.

## Existing tables touched (read-only)

- Read via `JobStorage` public methods (fingerprint app-level sections):
  `jobs`, `job_scores`, `job_tracking`, `interactions`, `contacts`, `companies`, `runs`.
- Read via raw `mode=ro` (FR-014 diagnostic exception): per-table row counts only.

## New artifacts

### 1. Backup

A self-contained SQLite file (plus an optional checkpoint DB) with a JSON manifest, stored in
`/app/data/backups/`.

File naming (UTC):

- `jobs_<YYYYMMDD_HHMMSS>.db`
- `cv_agent_checkpoints_<YYYYMMDD_HHMMSS>.sqlite` (only when present)
- `manifest_<YYYYMMDD_HHMMSS>.json`

Manifest fields (FR-003):

| Field | Type | Meaning |
|-------|------|---------|
| `timestamp` | string (ISO-8601 UTC) | when the backup was taken |
| `git_ref` | string | short HEAD the code was built from |
| `files.jobs` | object | `{ path, size_bytes, integrity }` |
| `files.checkpoints` | object \| null | same shape, or `null` when absent (FR-004) |
| `counts` | object | `{ jobs, job_scores, job_tracking, interactions, contacts, companies }` |

### 2. Fingerprint

Deterministic, key-sorted JSON describing app-visible state at an `--as-of` date (FR-012/013).

Sections:

| Section | Source | Content |
|---------|--------|---------|
| `meta` | args | `{ schema, as_of, git_ref, db_path }` |
| `counts` | raw `mode=ro` | per-table row counts |
| `feed` | `get_all_for_tracker` | feed size per status and per score band (active profile) |
| `scores` | `get_stats` / scores read | score distribution |
| `dashboard` | `get_dashboard_data` | dashboard figures |
| `applications` | `get_all_applications` | application count/figures |
| `companies` | `get_companies` | company count/figures |
| `contacts` | `get_all_contacts` | contact count/figures |
| `regression_cases` | `regression_cases.json` | per case: `{ presence, score }` or `"absent"` |

### 3. Regression case (in `scripts/regression_cases.json`, versioned)

| Field | Type | Meaning |
|-------|------|---------|
| `id` | string | stable label (e.g. `felfel`, `lausanne_usa`) |
| `match` | object | `{ title }` or `{ title, company }` pattern |
| `expected_presence` | bool | whether the job must appear in the feed |
| `expected_score_band` | string | e.g. `">=8"`, `"5-7"`, `"any"` |

A job not found in a snapshot is recorded as `"absent"` (not an error), so old snapshots remain
usable (acceptance scenario 4).
