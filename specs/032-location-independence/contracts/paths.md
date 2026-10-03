# Contract: `paths.py` (location configuration)

The single source of truth for every location the runtime depends on. Any runtime
module that needs a data, output, or `.env` location MUST import it from `paths.py`,
never derive it from `__file__` or a CWD-relative literal (enforced by
`tests/test_location_independence.py`).

## Public API

```python
PROJECT_ROOT: str        # absolute repo root — the only __file__-derived value
DATA_DIR: str            # JOB_AGENT_DATA_DIR | <PROJECT_ROOT>/data
DB_PATH: str             # <DATA_DIR>/jobs.db
OUTPUT_DIR: str          # JOB_AGENT_OUTPUT_DIR | <PROJECT_ROOT>/outputs
ENV_PATH: str            # <PROJECT_ROOT>/.env
data_path(*parts) -> str # <DATA_DIR>/<parts...>
```

## Environment variables

| Variable | Default | Semantics |
|----------|---------|-----------|
| `JOB_AGENT_DATA_DIR` | `<PROJECT_ROOT>/data` | Alternate data directory (throwaway dir, mounted volume). |
| `JOB_AGENT_OUTPUT_DIR` | `<PROJECT_ROOT>/outputs` | Alternate ephemeral output directory. |
| `JOB_AGENT_REQUIRE_DB` | unset | `"1"` → refuse to import if `DB_PATH` is missing. |

## Guard behaviour

- **Unset** (dev, tests, new-user testing with `data_test`): `DATA_DIR` is created if
  missing (`os.makedirs(..., exist_ok=True)`); onboarding shows on an empty DB. Unchanged.
- **`"1"`, DB present**: proceed; nothing created.
- **`"1"`, DB absent**: raise `RuntimeError` naming `DB_PATH`, `JOB_AGENT_DATA_DIR` and
  `JOB_AGENT_REQUIRE_DB`. **No directory or file is created.**

The guard checks existence only — not integrity, not row counts. Integrity is
`scripts/health_check.py`'s contract (spec 031). Scripts that never import `paths`
(`backup_db.py`, `fingerprint.py`, which accept an explicit `--db`) are out of scope.

## Launch contract

Background/pipeline launches use the module form (FR-005): `python [-u] -m <module>
[args]`, where `<module>` is the filename minus `.py` (`scrape`, `score`, `export_seed`).
`-u` is preserved exactly where it exists today and must precede `-m`. `-m` requires the
CWD to be the repo root (container `WORKDIR /app`, dev repo root) — becomes
CWD-independent in step 1b.
