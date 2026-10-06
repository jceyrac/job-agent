# Feature Specification: Installable Project + Staging Environment (roadmap step 1b)

**Feature Branch**: `033-installable-project`

**Created**: 2026-10-03

**Status**: Draft — ready for `/speckit.clarify`

**Input**: Roadmap `docs/roadmap-api.md`, step 1b. "Make the project an installable Python project so imports work the same way from anywhere, remove every `sys.path` hack, and give me a staging environment on verva to validate a new image before it replaces production — required for the `core/` move (step 1c). Also restrict the production tracker to Tailscale only. No file is moved in this step. The live tracker must behave exactly as today."

---

## Context (verified against source at `9f3b1df`, 2026-10-03)

### Prerequisite

Spec 032 (location independence) is committed (`9f3b1df`): `paths.py` is the single source of locations, the prod data dir is pinned and guarded, all 14 script launches use `python -m <module>`.

### How imports work today

- No `pyproject.toml`, `setup.cfg` or `conftest.py`. Imports work only because the CWD is the repo root (`WORKDIR /app` in the image; repo root in dev) — `python -m x` and `streamlit run tracker.py` put the CWD/script dir on `sys.path`.
- Packages: `scrapers/` (+ `ats/`, `boards/`, `company_sites/`), `tracker_views/`, `cv_agent/` have `__init__.py`. `scripts/` and `tests/` do not.
- 33 Python modules at the root. Runtime modules vs. one-offs to be archived in 1d: `migrate_expired_status.py`, `migrate_profile_independent_tracking.py`, `migrate_single_status.py`, `tracker_legacy.py`, `test_wellfound.py`.

### `sys.path` hacks (13 files)

| File | Form |
|---|---|
| `scripts/filter_funnel.py:20` | `sys.path.insert(0, ".")` (CWD-relative) |
| `scripts/fingerprint.py:24`, `dedupe_title_company.py:18`, `dedupe_jobs.py:22`, `scraper_toggle.py:2`, `health_check.py:24`, `re_extract_sample.py:11` | `dirname(dirname(abspath(__file__)))` |
| `tests/test_storage.py:18`, `scraper_checks.py:19`, `test_scorer_parsing.py:18`, `run_all.py:17`, `test_prepare.py:18`, `test_migration_safety_net.py:14` | `join(dirname(__file__), "..")` |

### CWD-relative DB defaults in diagnostic scripts (deferred from spec 032)

`scripts/filter_funnel.py:97`, `scripts/audit_provenance.py:18`, `scripts/diag_freelance.py:13`, `scripts/audit_work_mode.py:18` default to `"data/jobs.db"`.

### Dependencies and image

- `requirements.txt` (14 deps, incl. `langgraph`, `langgraph-checkpoint-sqlite`) is installed in the `Dockerfile` before `COPY . .` (layer cache). No dev/test dependency list (pytest is installed ad hoc).
- Script invocations documented as file paths: `docs/migration-checklist.md:41–42` (`python scripts/fingerprint.py`, `python scripts/compare_fingerprints.py`), health check `python /app/scripts/health_check.py`, restore procedure, spec 031/032 quickstarts.

### Special case: `scripts/backup_db.py`

`deploy.sh` copies it into the **currently running (old) image** via `docker cp` to `/tmp` and runs it as a file, before the rebuild. It is stdlib-only and imports no project module. It MUST stay a standalone file runnable by path, independent of the installed project.

### No staging environment

The spec-031 drill (`docs/restore-procedure.md`) runs the **live image** on port 8502. `deploy.sh` rebuilds images under the same names and restarts production, so a new image cannot be exercised before it replaces the live one. Step 1c (the `core/` move) requires that ability.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — One way to import, from anywhere (Priority: P1)

As the maintainer, I want the project installed in editable mode in dev and in the image, so every module, script and test imports the same way regardless of the current directory, and no file manipulates `sys.path`.

**Why this priority**: Removes the last location dependency of imports; step 1c then only changes the package declaration, not how code is run.

**Independent Test**: In a fresh venv, `pip install -e .` then from `/tmp`: `python -m score --help`, `python -m scripts.health_check --help` and `pytest <repo>/tests` all work. `grep -r "sys.path" --include=*.py` finds nothing outside the allow-list.

**Acceptance Scenarios**:

1. **Given** `pyproject.toml` at the root, **When** I run `pip install -e .`, **Then** all runtime root modules and the packages `scrapers*`, `tracker_views`, `cv_agent`, `scripts` are importable from any CWD.
2. **Given** the image is rebuilt, **When** the tracker, the agent (cron) and the tracker's background launches run, **Then** they behave as today (same parity fingerprint, same logs).
3. **Given** the 13 files listed in Context, **When** the change is applied, **Then** none contains `sys.path` manipulation and all still run (scripts via `python -m scripts.<name>`).
4. **Given** a new runtime module is added at the root but not declared in `pyproject.toml`, **When** the test suite runs, **Then** a guard test fails naming the undeclared module.

---

### User Story 2 — Dependencies declared once (Priority: P2)

As the maintainer, I want runtime dependencies declared in one place and test tooling declared explicitly, so a fresh environment is reproducible.

**Independent Test**: A fresh venv with `pip install -e ".[dev]"` runs the full test suite green; the Docker image build uses the same runtime dependency list.

**Acceptance Scenarios**:

1. **Given** `pyproject.toml`, **Then** runtime dependencies are read from `requirements.txt` (single source, `dynamic = ["dependencies"]`), preserving the Dockerfile's dependency layer cache.
2. **Given** `pyproject.toml`, **Then** a `dev` extra declares the test tooling (`pytest`), and `[tool.pytest.ini_options]` sets `testpaths = ["tests"]`.

---

### User Story 3 — Staging tracker on verva (Priority: P1)

As the operator, I want to run a candidate commit on verva as a separate tracker on port 8502, with a copy of the latest backup, without touching the production containers, images or volume — then tear it down.

**Why this priority**: Mandatory validation environment for step 1c; also used to validate this spec's own image before deploy.

**Independent Test**: Run the staging command for the commit of this spec: a staging tracker serves the backup copy on 8502; production on 8501 is untouched (same container ID, same image ID, same volume). Teardown removes every staging artifact.

**Acceptance Scenarios**:

1. **Given** a commit SHA or branch, **When** I run `scripts/staging.sh up <ref>`, **Then** it checks the ref out in a separate worktree (e.g. `/opt/job-agent-staging`), builds an image tagged `job-agent:staging`, copies the latest backup from `/app/data/backups/` into a dedicated staging volume, and starts a tracker on port 8502 with `JOB_AGENT_DATA_DIR`/`JOB_AGENT_REQUIRE_DB=1`.
2. **Given** staging is up, **When** I run `scripts/staging.sh run <command...>`, **Then** the command runs in a one-shot staging container on the staging volume (e.g. `python -m main`, `python -m scrape --source <name>`, `python -m scripts.fingerprint ...`).
3. **Given** staging is up, **When** I run `scripts/staging.sh down`, **Then** the staging container, image, volume and worktree are removed.
4. **Given** any staging command, **Then** production containers, images, the `job_data` volume and `/opt/job-agent` are never modified (no `docker compose` call on the production project).
5. **Given** staging, **Then** the port is bound to the Tailscale interface only, never `0.0.0.0`.

---

### User Story 4 — Production tracker reachable through Tailscale only (Priority: P2)

As the operator, I want the production tracker (no authentication) to be unreachable from my local network or any non-Tailscale interface, while keeping the exact URL I use today (`http://100.74.139.28:8501`, incl. the `/job_detail?id=` retrieval flow), and surviving a reboot of verva.

**Why this priority**: Constitution VII (least privilege, no public exposure). Today `"8501:8501"` publishes the tracker on every interface of verva. Must be settled before the API (step 5) reuses the same pattern.

**Independent Test**: From a LAN device not on the tailnet, `http://<verva LAN IP>:8501` is refused; from a tailnet device, `http://100.74.139.28:8501` and `/job_detail?id=<id>` work; after a reboot of verva, the tailnet URL works again without manual action.

**Acceptance Scenarios**:

1. **Given** the change, **When** I scan verva's LAN IP from a non-tailnet device, **Then** port 8501 is closed.
2. **Given** the change, **When** I open `http://100.74.139.28:8501/job_detail?id=<id>` from a tailnet device, **Then** the page loads as today.
3. **Given** verva reboots, **When** boot completes, **Then** the tracker is reachable on the tailnet URL without manual intervention.
4. **Given** the in-container health check (`localhost:8501`), **Then** it keeps working unchanged.

**Design decision**: publish the port on loopback only (`"127.0.0.1:8501:8501"`) and expose it on the tailnet with `tailscale serve --bg --tcp=8501 tcp://127.0.0.1:8501` (persistent in tailscaled's config).
Rejected alternatives:
- Binding directly to the Tailscale IP (`"100.74.139.28:8501:8501"`): boot-order race — if Docker starts the container before tailscaled has assigned the IP, the bind fails and the tracker stays down after a reboot (same class of issue as the CIFS-before-smbd race already met on verva).
- Keeping `0.0.0.0` and filtering with ufw: Docker-published ports bypass ufw rules (iptables `DOCKER` chain), so the filter would silently not apply.

---

### Edge Cases

- Editable install inside the image: the code is copied to `/app` then `pip install --no-deps -e .` — the dependency layer stays cached.
- `streamlit run tracker.py` keeps working unchanged (the installed project makes `tracker_views` importable regardless of how Streamlit sets `sys.path`).
- `scripts/backup_db.py` stays a standalone file; the guard tests allow-list it.
- `tests/` stays a plain directory (no package); pytest discovers it via `testpaths`.
- Staging with no backup present → `staging.sh up` fails with an explicit message and creates nothing.
- Staging and production running the nightly cron: staging never schedules anything; jobs only run on explicit `staging.sh run`.
- Staging scraping hits real sources and spends LLM credits if scoring is run — `staging.sh run` prints a warning for `main`/`scrape`/`score`.
- Excluded modules (the five one-offs listed in Context) stay runnable from the repo root until archived in 1d, and are not declared in `pyproject.toml`.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A `pyproject.toml` MUST declare the project (`name = "job-agent"`, `requires-python = ">=3.11"`), runtime dependencies dynamically from `requirements.txt`, a `dev` extra with `pytest`, and `[tool.pytest.ini_options] testpaths = ["tests"]`.
- **FR-002**: `pyproject.toml` MUST declare explicitly (setuptools) the runtime root modules as `py-modules` and the packages `scrapers`, `scrapers.ats`, `scrapers.boards`, `scrapers.company_sites`, `tracker_views`, `cv_agent`, `scripts`. The five one-offs listed in Context are excluded.
- **FR-003**: `scripts/` MUST become a package (`__init__.py`); its Python tools MUST run as `python -m scripts.<name>`.
- **FR-004**: The `Dockerfile` MUST install the project in editable mode after `COPY . .` (`pip install --no-deps -e .`), keeping the requirements layer first.
- **FR-005**: All 13 `sys.path` manipulations listed in Context MUST be removed.
- **FR-006**: The 4 diagnostic scripts' DB defaults MUST come from `paths.DB_PATH`.
- **FR-007**: Guard tests MUST fail on (a) any `sys.path` manipulation in Python files outside the allow-list (`scripts/backup_db.py` if ever needed, `specs/`), (b) any root runtime module not declared in `pyproject.toml` (allow-list: the five one-offs).
- **FR-008**: Every documented invocation of a project Python script MUST use the module form: `docs/migration-checklist.md`, `docs/restore-procedure.md`, `CLAUDE.md` (commands section, incl. `pip install -e ".[dev]"`), spec 031/032 quickstarts. `backup_db.py` keeps its file-path invocation in `deploy.sh`.
- **FR-009**: `scripts/staging.sh` MUST implement `up <ref>`, `run <command...>`, `down` as described in US3, using its own container/image/volume names (`job-agent-staging*`), a separate worktree, the latest backup as data, and a Tailscale-bound port 8502.
- **FR-010**: `scripts/staging.sh` MUST NOT call `docker compose` on the production project nor touch `job_data`, production images, or `/opt/job-agent`'s working tree.
- **FR-011**: `docs/migration-checklist.md` MUST add a pre-deploy section: "staging up on the candidate commit → click every background-launch button → open `/job_detail?id=<id>` → parity fingerprint on staging vs baseline → staging down".
- **FR-012**: `docker-compose.yml` MUST publish the tracker as `"127.0.0.1:8501:8501"`.
- **FR-013**: The tailnet exposure MUST be configured with `tailscale serve --bg --tcp=8501 tcp://127.0.0.1:8501` on verva, and documented (command, how to check with `tailscale serve status`, how to undo) in `docs/infrastructure.md` (new) and referenced from `CLAUDE.md` Infrastructure. The switch MUST be done in this order to avoid downtime on the tailnet URL: deploy the compose change → immediately run the `tailscale serve` command → verify.
- **FR-014**: The staging tracker (US3) MUST follow the same pattern or bind to the Tailscale IP (manual use only, no boot race); never `0.0.0.0`.

### Non-goals

- No file moved or renamed (step 1c). No `core/` package.
- No change to runtime behaviour, `storage.py`, `models.py`, `profiles.py`, `scorer.py`, `llm.py`, scrapers, or the DB schema.
- No change to the API or any other service exposure beyond the tracker (the API does not exist yet).
- No CI pipeline.
- No dependency upgrades or pinning changes in `requirements.txt`.

### Key Entities

- **Project declaration**: `pyproject.toml` — the single statement of what is installed.
- **Staging environment**: an isolated, disposable copy of production (worktree + image + volume + container) for a given commit.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From `/tmp`, after `pip install -e ".[dev]"` in a fresh venv: `python -m score --help`, `python -m scripts.health_check --help` and `pytest <repo>` all succeed.
- **SC-002**: Zero `sys.path` manipulations outside the allow-list; guard tests pass, and fail when one is reintroduced or a root module is left undeclared.
- **SC-003**: This spec's commit is validated **on staging before deploy**: tracker on 8502 serves the backup copy, every background-launch button completes, `/job_detail?id=<id>` opens, parity fingerprint equals the baseline; production untouched during the whole session (same container and image IDs before/after).
- **SC-004**: After deploy: health check OK, next nightly cron OK, parity identical (migration checklist).
- **SC-005**: `staging.sh down` leaves no staging container, image, volume or worktree.
- **SC-006**: Full test suite green.
- **SC-007**: Port 8501 closed from a non-tailnet LAN device; tailnet URL and `/job_detail?id=<id>` work; after one reboot of verva (or at minimum a `systemctl restart docker tailscaled`), the tailnet URL works without manual action.

---

## Assumptions

- verva has enough disk for a second image and a copy of one backup (~170 MB).
- The Tailscale IP of verva (`100.74.139.28`) is stable; `staging.sh` reads it from an env var or `tailscale ip -4`.
- Claude Code uses its SSH access to verva only to run `staging.sh` and deploy commands (dev/live separation: no development on verva).
- Tailscale on verva supports `tailscale serve` with TCP forwarding (to check: `tailscale version`; the feature is available in current releases).
- A device on the LAN but outside the tailnet is available for the closed-port check (e.g. phone with Tailscale disabled).
- Deploy avoids the month-end status batch.
