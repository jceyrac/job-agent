# Feature Specification: Location Independence (roadmap step 1a)

**Feature Branch**: `032-location-independence`

**Created**: 2026-10-03

**Status**: Draft — ready for `/speckit.clarify` (no open question expected)

**Input**: Roadmap `docs/roadmap-api.md`, step 1a. "Before moving the domain into `core/` (step 1c), remove every place where the code depends on *where its files are* or *where it is launched from*, so that the move can only fail loudly (import error at startup), never silently. No file is moved in this step. The live tracker must behave exactly as today."

---

## Context (verified against source at `61de958`, 2026-10-03)

### Silent risk 1 — the data directory is derived from a file's location

- `paths.py`: `_ROOT = os.path.dirname(os.path.abspath(__file__))`; `DATA_DIR = os.environ.get("JOB_AGENT_DATA_DIR") or os.path.join(_ROOT, "data")`; it then calls `os.makedirs(DATA_DIR, exist_ok=True)`.
- `docker-compose.yml` does **not** set `JOB_AGENT_DATA_DIR` for `tracker`, `agent` or `email-monitor` (only `env_file: .env` + volume `job_data:/app/data`). Prod therefore works only because `paths.py` happens to sit at `/app/paths.py`.
- Consequence if `paths.py` moved to `core/`: the app would open `/app/core/data/jobs.db`, **silently create an empty DB**, show onboarding, and the nightly cron would write into it.

### Other data/output locations computed outside `paths.py`

| File | Line | Current code | Kind |
|---|---|---|---|
| `export_jobs.py` | 26–27 | `DB_PATH = Path(__file__).parent / "data" / "jobs.db"`; `OUTPUT_DIR = Path(__file__).parent / "data"` | `__file__`-relative; consumed by `tracker_views/reports.py` (lines 9, 88, 98) |
| `tracker_views/preferences.py` | 114 | `JobStorage("data/jobs.db")` | CWD-relative |
| `tracker_views/shared.py` | 95 | `os.path.join(os.path.dirname(__file__), "..", ".env")` | `__file__`-relative |
| `notifier.py` | 106, 210 | `os.path.join(os.path.dirname(__file__), "outputs")` | `__file__`-relative |
| `score.py` | 568 | same `outputs` pattern | `__file__`-relative |
| `prepare.py` | 568 | `.../outputs/applications` | `__file__`-relative |
| `seed.py` | 18–28 | `open("data/companies.json")` | CWD-relative (deploy.sh copies the file to `/app/data/companies.json`) |
| `export_seed.py` | 45 | `output_path = "data/companies.json"` | CWD-relative |
| `scripts/scraper_toggle.py` | 14 | `JobStorage("/app/data/jobs.db")` | hard-coded absolute |

Note: `outputs/` is git- and docker-ignored and not on the volume — it is ephemeral in prod today. This spec does not change that; it only makes its location explicit.

### Silent risk 2 — scripts launched by file name

Every launch below uses `[sys.executable, "<name>.py", ...]`, which depends on the CWD and on the script staying at the root. In the tracker these run in a background thread: a failure only shows in a log, the button seems to do nothing.

| File | Lines | Commands |
|---|---|---|
| `tracker_views/jobs.py` | 119, 123, 135 | `scrape.py`; `score.py --profile`; `score.py --extract` |
| `tracker_views/settings.py` | 200, 215, 230, 824 | `scrape.py`; `scrape.py --monitored-only`; `score.py --profile`; `score.py --extract` |
| `main.py` (nightly cron path) | 77, 89, 100, 113 | `scrape.py --monitored-only`; `scrape.py`; `score.py --extract`; `score.py --profile` |
| `scrape.py` | 318–319 | `score.py --extract`; `score.py --profile` |
| `monitoring_agent.py` (dev tool) | 83 | `export_seed.py` (with `cwd=ROOT`) |

### Not in scope of 1a (handled later)

- `scrape.py:21` `scrapers_dir = os.path.join(os.path.dirname(__file__), "scrapers")` — package-relative, moves together with `scrapers/`; fails loudly. Handled in 1c.
- `sys.path` manipulation in 13 scripts/tests and CWD-relative `--db` defaults of diagnostic scripts (`filter_funnel.py`, `audit_provenance.py`, `audit_work_mode.py`, `diag_freelance.py`) — step 1b.
- `monitoring_agent.py` `ROOT`, `scripts/duplicate_report.py` `ROOT` — dev tools, repo-root by design; revisited in 1c.
- `st.Page("tracker_views/...")` / `st.switch_page(...)` paths and `url_path="job_detail"` — only change when files move; step 1c.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Prod data location is explicit and guarded (Priority: P1)

As the operator, I want the live app to find its data only through explicit configuration, and to refuse to start rather than silently create an empty DB.

**Why this priority**: Removes the only failure mode in the roadmap that could split live data across two DB files without any error.

**Independent Test**: Start a tracker container with `JOB_AGENT_DATA_DIR` pointing to an empty directory and the guard enabled → it fails at startup with an explicit message. Start it normally → identical behaviour to today.

**Acceptance Scenarios**:

1. **Given** `docker-compose.yml` sets `JOB_AGENT_DATA_DIR=/app/data` and `JOB_AGENT_REQUIRE_DB=1` on `tracker`, `agent`, `email-monitor`, **When** the stack is deployed, **Then** the tracker serves the same DB as before (same job count, parity fingerprint identical).
2. **Given** `JOB_AGENT_REQUIRE_DB=1` and no `jobs.db` in `DATA_DIR`, **When** any entry point imports `paths`, **Then** it raises an explicit error naming the expected path — no directory or DB file is created.
3. **Given** `JOB_AGENT_REQUIRE_DB` is unset (dev, tests, new-user testing with `data_test`), **When** the app starts on an empty directory, **Then** behaviour is unchanged (dir created, onboarding shown).

---

### User Story 2 — One file knows where things are (Priority: P1)

As the maintainer, I want every data, output and config location to come from `paths.py`, so that moving files later changes exactly one place.

**Why this priority**: Makes step 1c a mechanical move; prevents new location dependencies from creeping back.

**Independent Test**: A test scans the repo and fails if any runtime module (outside `paths.py`, `tests/`, `specs/`, dev-only tools listed in Context) derives a data/output/`.env` location from `__file__` or a CWD-relative `"data/..."` literal.

**Acceptance Scenarios**:

1. **Given** the change, **When** the Reports page exports CSV, **Then** it reads the DB from `paths.DB_PATH` and writes where it wrote before (`DATA_DIR`).
2. **Given** the change, **When** the Preferences page action at `preferences.py:114` runs, **Then** it opens `paths.DB_PATH`.
3. **Given** the change, **When** `deploy.sh` runs `seed.py` in the container, **Then** it reads `paths.data_path("companies.json")` and seeds as before.
4. **Given** the change, **When** notifier/score/prepare write outputs, **Then** they write under `paths.OUTPUT_DIR` (default: `<repo root>/outputs`, i.e. the same place as today).

---

### User Story 3 — Scripts launched as modules (Priority: P1)

As the user, I want the tracker buttons and the nightly pipeline to keep launching scrape/score exactly as today, through a mechanism that does not depend on file location.

**Why this priority**: The background-thread launches are the second silent failure mode of the move.

**Independent Test**: On the drill tracker (port 8502, restored backup), click every button that launches a background process (Jobs: fetch, score, extract; Settings: scrape, monitored-only, score, extract) → each completes and its log shows normal output. Run `main.py` in the drill container → the four stages run.

**Acceptance Scenarios**:

1. **Given** the change, **When** any of the 14 launch sites runs, **Then** the command is `[sys.executable, ("-u",) "-m", "<module>", ...args]` with identical arguments and the same `-u` flag as today.
2. **Given** a test, **When** it scans runtime code, **Then** no `subprocess` call passes a `"<name>.py"` script path (dev-only `monitoring_agent.py` included in the conversion).

---

### Edge Cases

- `paths` imported by a one-off script with an explicit `--db` argument (e.g. `backup_db.py`, `fingerprint.py`) while `JOB_AGENT_REQUIRE_DB=1` is set → the guard checks `DATA_DIR/jobs.db` only; scripts with explicit DB paths that don't import `paths` are unaffected. Verify `backup_db.py` (stdlib, run via `docker cp` in the old image) does not import `paths`.
- The guard MUST NOT create `DATA_DIR` when enabled (the current unconditional `os.makedirs` stays for the unguarded case only).
- `python -m score` requires the CWD to be the repo root (`-m` puts the CWD on `sys.path`) — true today in the container (`WORKDIR /app`) and in dev; this is no worse than the current `"score.py"` form and becomes CWD-independent in 1b (installed package).
- The `.env` path change in `shared.py` must keep loading the same file in dev (repo root) and in the container.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `docker-compose.yml` MUST set `JOB_AGENT_DATA_DIR=/app/data` and `JOB_AGENT_REQUIRE_DB=1` in the `environment` of `tracker`, `agent` and `email-monitor`.
- **FR-002**: `paths.py` MUST, when `JOB_AGENT_REQUIRE_DB=1`, raise a `RuntimeError` at import if `DB_PATH` does not exist, with a message naming the path and the env vars — without creating any directory or file.
- **FR-003**: `paths.py` MUST expose `PROJECT_ROOT`, `DATA_DIR`, `DB_PATH`, `OUTPUT_DIR` (env `JOB_AGENT_OUTPUT_DIR`, default `PROJECT_ROOT/outputs`), `ENV_PATH` (default `PROJECT_ROOT/.env`) and `data_path()`. `PROJECT_ROOT` is the only value derived from `__file__`.
- **FR-004**: All locations listed in the Context table MUST be replaced by the corresponding `paths` value, with no behaviour change in dev or prod.
- **FR-005**: All 14 script launches listed in Context MUST use `-m <module>` with unchanged arguments and flags.
- **FR-006**: A guard test (`tests/test_location_independence.py`) MUST fail on (a) `__file__`-derived data/output/`.env` paths outside `paths.py` in runtime code, (b) CWD-relative `"data/` literals in runtime code, (c) `subprocess` launches of `".py"` files. Allow-list: `tests/`, `specs/`, `scripts/` diagnostic tools deferred to 1b, `scrape.py:21`, `monitoring_agent.py` `ROOT`, `scripts/duplicate_report.py` `ROOT`.
- **FR-007**: `CLAUDE.md` MUST document `JOB_AGENT_DATA_DIR`, `JOB_AGENT_REQUIRE_DB`, `JOB_AGENT_OUTPUT_DIR` and the rule "locations come from `paths.py` only; scripts are launched with `-m`".

### Non-goals

- No file moved or renamed (step 1c). No `pyproject.toml` (step 1b).
- No change to `storage.py`, `models.py`, `profiles.py`, `scorer.py`, `llm.py`, any scraper, or the DB schema.
- No change to the background-task mechanism itself (`subprocess` + thread stays until step 2b) — only the command form.
- No persistence of `outputs/` on the volume (separate decision).
- No change to Streamlit page paths or `url_path` values.

### Key Entities

- **Location configuration**: the set of paths exposed by `paths.py`, overridable by environment.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Parity fingerprint of the live backup is identical before and after the change (same snapshot, same `--as-of`).
- **SC-002**: On the drill tracker (:8502, restored backup) all 7 tracker launch buttons complete normally and `main.py` runs its 4 stages.
- **SC-003**: With `JOB_AGENT_REQUIRE_DB=1` and an empty data dir, startup fails with the explicit error and creates nothing.
- **SC-004**: The guard test passes on the new code and fails if one `"score.py"` launch or one `__file__`-derived data path is reintroduced.
- **SC-005**: After deploy: health check OK, next nightly cron OK, Reports CSV export and Preferences page work on live (migration checklist, Constitution XI).
- **SC-006**: Full test suite green.

---

## Assumptions

- Container `WORKDIR` stays `/app`; the repo root is the CWD for all entry points (dev and prod).
- `.env` on verva does not already set a conflicting `JOB_AGENT_DATA_DIR` (to verify on verva before deploy; `environment:` in compose takes precedence over `env_file` anyway).
- The drill procedure from spec 031 (`docs/restore-procedure.md`) is reused as the staging environment for SC-002.
- Deploy avoids the month-end status batch.
