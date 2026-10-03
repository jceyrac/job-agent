# CLI Contracts

All commands: stdlib only; exit `0` on success, non-zero on failure; deterministic output.

## `scripts/backup_db.py`

```
python scripts/backup_db.py [--db /app/data/jobs.db] [--checkpoints /app/data/cv_agent_checkpoints.sqlite]
                            [--backup-dir /app/data/backups] [--keep 5] [--ref <git-ref>]
```

- Backs up `jobs.db` (and `cv_agent_checkpoints.sqlite` when present) via
  `sqlite3.Connection.backup` — never a raw copy (FR-001).
- Runs `PRAGMA integrity_check` on each backup; writes the manifest JSON (FR-003).
- Prunes to the last `--keep` backups, touching only `--backup-dir` (FR-006).
- Exits `1` on any failure (missing DB, integrity failure, disk space, prune error).

## `scripts/fingerprint.py`

```
python scripts/fingerprint.py --db <path> --as-of <YYYY-MM-DD>
                              [--profile <id>] [--regression-cases scripts/regression_cases.json]
                              [--out <file>]
```

- Copies the DB to a temporary location and opens it via `JobStorage` (never mutates the source,
  FR-012 / edge-case: migrations never alter a backup).
- Pins `datetime.date.today` (and the freshness window) to `--as-of` (R4).
- Computes app-level sections through `JobStorage` public reads (FR-013); table counts via raw
  `mode=ro` (FR-014).
- Writes key-sorted JSON to `--out` (or stdout).

## `scripts/compare_fingerprints.py`

```
python scripts/compare_fingerprints.py <a.json> <b.json>
```

- Diffs two fingerprints section by section; prints a readable report naming every differing
  section/field (FR-016).
- Exits non-zero when any difference exists.

## `scripts/health_check.py`

```
python scripts/health_check.py [--url http://localhost:8501] [--db /app/data/jobs.db] [--max-age-hours 26]
```

- GETs `--url/_stcore/health`; fails unless it returns `ok` (FR-018, R8).
- Reads the `runs` table read-only (`mode=ro`) and reports the latest run of each `run_type`
  for information; gates on the latest **full** run — fails on a missing full run, a
  non-success status, or age > `--max-age-hours`.
- Exits non-zero on any failure.

## Fingerprint JSON schema

```jsonc
{
  "meta": { "schema": 1, "as_of": "2026-10-03", "git_ref": "abc1234", "db_path": "…", "profile": "unified_jc" },
  "counts": { "jobs": 1234, "job_scores": 987, "job_tracking": 1200, "interactions": 345, "contacts": 40, "companies": 210 },
  "feed": { "by_status": { "new": 100, "ready": 20, "…": 0 }, "by_score_band": { ">=8": 50, "5-7": 120, "<5": 60, "unscored": 300 } },
  "scores": { "distribution": { "10": 1, "9": 3, "…": 0 } },
  "stats": { "total": 1234, "scored": 987, "hot": 50, "solid": 120, "by_status": { "new": 100, "…": 0 } },
  "dashboard": { "…": "figures from get_dashboard_data" },
  "last_run": { "ran_at": "…", "status": "ok", "run_type": "full", "…": "…" },
  "applications": { "count": 55 },
  "companies": { "count": 210 },
  "contacts": { "count": 40 },
  "regression_cases": { "felfel": { "presence": true, "matched_jobs": 1, "score": 8 }, "lausanne_present": "absent" }
}
```
