# Research — Installable Project + Staging Environment (spec 033)

Phase 0 output. Each decision resolves a "NEEDS CLARIFICATION" from the Technical
Context. Verified against the repo at `9f3b1df` (2026-10-03).

---

## 1. Flat-layout `pyproject.toml` declaration

**Decision**: Use a flat-layout `pyproject.toml` with **explicit** `py-modules`
and **explicit** `packages`, `dynamic = ["dependencies"]` reading
`requirements.txt`, and `dev` extra = `pytest`.

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "job-agent"
version = "0.1.0"
requires-python = ">=3.11"
dynamic = ["dependencies"]

[project.optional-dependencies]
dev = ["pytest"]

[tool.setuptools.dynamic]
dependencies = {file = ["requirements.txt"]}

[tool.setuptools]
py-modules = [
  # 28 runtime root modules — exact list in data-model.md
]

[tool.setuptools.packages]
# explicit: scrapers, scrapers.ats, scrapers.boards, scrapers.company_sites,
# tracker_views, cv_agent, scripts
```

**Rationale**:
- Explicit `py-modules` + explicit `packages` avoids setuptools flat-layout
  **auto-discovery** ever walking `.venv/`, `specs/`, `tests/` or any stray
  `__init__.py` and mis-packaging it. The package set is fixed and small, so the
  explicit list is more deterministic than `packages.find`.
- `dynamic = ["dependencies"]` + `[tool.setuptools.dynamic] dependencies = {file = ["requirements.txt"]}` is the setuptools-native way to keep `requirements.txt`
  the single source of runtime deps (FR-001, US2). `requirements.txt` entries are
  valid PEP 508 specifiers (`streamlit>=1.40.0`, `langgraph>=1.2,<2`, …), so they
  are parsed verbatim.
- `version = "0.1.0"` is a static placeholder — the project is not published to an
  index, and nothing reads the version (no CI, no `__version__`). No dynamic
  versioning machinery is warranted (non-goal: no CI, no dep changes).

**Alternatives considered**:
- `[tool.setuptools.packages.find] include=["scrapers*", …] exclude=["tests*", "specs*"]`
  — works, but the exclude list must be kept in lockstep with every future
  directory; explicit `packages` is the smaller, self-documenting surface.
- Hatchling/PDM/uv backends — introduce a new toolchain for no benefit here;
  setuptools is the default and already satisfiable by the Python image.

---

## 2. Editable install in the Docker image

**Decision**: `Dockerfile` keeps `COPY requirements.txt` + `RUN pip install -r
requirements.txt` first (layer cache preserved), then after `COPY . .`:

```dockerfile
RUN pip install --no-deps -e .
```

**Rationale**:
- `-e .` makes all `py-modules` and packages importable from **any** CWD inside the
  container (via an editable finder pointing at `/app`), so `python -m score`,
  `python -m scripts.health_check`, `streamlit run tracker.py`, and the tracker's
  `-m` background launches all resolve regardless of how the process sets `sys.path`.
- `--no-deps` skips re-installing the 14 runtime deps (already present from the
  first `RUN`), so the dependency layer stays cached — the requirement behind US1's
  "behave as today" and US2's "same runtime dependency list".
- The **dev** extra is *not* installed in the image (no pytest in production).

**Trade-off noted (not a blocker)**: `pip install -e .` runs under build isolation,
so pip fetches `setuptools` into an isolated build env on each image build — a small
network fetch with no layer cache. Acceptable (verva has internet; it already pulls
`requirements.txt`). Mitigation (pre-install setuptools) is rejected because it
would add an install line and touches nothing in `requirements.txt` (non-goal).

---

## 3. Removing the 13 `sys.path` hacks

**Decision**: Delete each `sys.path` manipulation verbatim (the file keeps its other
imports). No replacement is needed once the project is installed: the editable
install puts the root on `sys.path`, and `python -m <module>` puts the CWD on
`sys.path` for the CLI entry points.

- The `scripts/*.py` tools (FR-003) run as `python -m scripts.<name>`, which makes
  `scripts` importable as a package and its sibling root modules importable because
  `-m` adds the CWD (repo root) to `sys.path`.
- `tests/*.py` lose their `join(dirname(__file__), "..")` inserts; the guard
  `python -m pytest tests/` and the fresh-venv `pytest <repo>` both resolve imports
  via the editable install.
- `tests/` stays a non-package directory (edge case in spec) — pytest inserts the
  `tests/` dir itself via rootdir conftest discovery, but the *project modules*
  resolve through the installed distribution, not through a test-side path hack.

---

## 4. `tailscale serve` for TCP exposure (US4, FR-013/014)

**Decision**: Bind the compose port to loopback and expose on the tailnet with
`tailscale serve` TCP proxy, exactly as the spec's design decision states:

```bash
# production (once, on verva — persists in tailscaled's state)
tailscale serve --bg --tcp=8501 tcp://127.0.0.1:8501
tailscale serve status          # verify the 8501 forward is listed
tailscale serve --bg --tcp=8501 off   # undo
```

**Rationale**:
- `--bg` + tailscaled's persisted config means the forward survives a reboot of
  verva (tailscaled is a systemd service) — satisfying SC-007's "works after reboot
  without manual action".
- Loopback bind (`127.0.0.1:8501:8501`) avoids the boot-order race of binding
  directly to the Tailscale IP (Docker may start before tailscaled assigns
  `100.74.139.28`), and avoids Docker-published ports bypassing ufw.
- The in-container health check targets `localhost:8501`, which the loopback bind
  still serves — unchanged (US4 acceptance 4).

**Staging port 8502** (FR-014): use the same pattern
(`tailscale serve --bg --tcp=8502 tcp://127.0.0.1:8502`) OR bind the staging
container directly to the Tailscale IP. Since staging is a *manual* `up` (no boot
race), either is allowed; the spec prefers the same pattern for consistency. The
container itself must never bind `0.0.0.0` (`streamlit --server.address 127.0.0.1`).

---

## 5. `staging.sh` design (US3, FR-009/010)

**Decision**: `scripts/staging.sh up|run|down` around a **separate worktree** at
`/opt/job-agent-staging`, a dedicated image `job-agent:staging`, a dedicated volume
`job_agent_staging_data`, and a container `job-agent-staging` — none of which share
names with the production Compose project.

- **`up <ref>`**: `git worktree add` (or fetch+checkout the SHA) into
  `/opt/job-agent-staging` → `docker build -t job-agent:staging .` → copy the latest
  backup out of the live volume (`docker cp job-tracker:/app/data/backups/<newest>`)
  into the staging volume → `docker run -d --name job-agent-staging -p
  127.0.0.1:8502:8501 -e JOB_AGENT_DATA_DIR=/app/data -e JOB_AGENT_REQUIRE_DB=1
  -v job_agent_staging_data:/app/data job-agent:staging streamlit run tracker.py
  --server.port 8501 --server.address 127.0.0.1 --server.fileWatcherType none`.
  **Fail fast** if no backup exists (edge case) — explicit message, create nothing.
- **`run <command…>`**: `docker run --rm -v job_agent_staging_data:/app/data
  -e JOB_AGENT_DATA_DIR=/app/data -e JOB_AGENT_REQUIRE_DB=1 job-agent:staging
  <command…>` (e.g. `python -m main`, `python -m scrape --source <name>`,
  `python -m scripts.fingerprint …`). Prints a warning when the command is
  `main`/`scrape`/`score` (real sources + LLM credits, edge case).
- **`down`**: `docker rm -f job-agent-staging` → `docker rmi job-agent:staging` →
  `docker volume rm job_agent_staging_data` → `git worktree remove
  /opt/job-agent-staging --force`.

**FR-010 guarantee**: `staging.sh` never invokes `docker compose` on the production
project, never names `job_data`/`job-agent-tracker`/`job-agent-agent`, and never
writes under `/opt/job-agent`'s working tree (the worktree lives beside it, at
`/opt/job-agent-staging`). All staging artifacts are prefixed `job-agent-staging*`.

**Tailscale IP source**: read `TAILSCALE_IP` from the environment, else
`tailscale ip -4` (spec assumption).

---

## 6. Guard tests (FR-007) — extension of the spec-032 repo scan

**Decision**: Add to `tests/test_installable_project.py` (new) two repo-scan checks
mirroring `test_location_independence.py`, plus a declared-module check:

- **(a) no `sys.path` manipulation** outside the allow-list
  `{scripts/backup_db.py}` + everything under `specs/`. Scan all runtime `*.py`
  (same `SCAN_SKIP_DIRS`) for the token `sys.path` in a non-`import`/non-comment
  position (an AST check on `ast.Attribute` with `value.id == "sys"` and
  `attr == "path"` under an `ast.Call` to `insert`/`append`, or the simpler textual
  `"sys.path."` match).
- **(b) every root runtime module is declared** — parse `[tool.setuptools]
  py-modules` from `pyproject.toml`, walk root `*.py` files, and fail on any
  git-tracked root module missing from the list. Allow-list: the five one-offs
  (`migrate_expired_status`, `migrate_profile_independent_tracking`,
  `migrate_single_status`, `test_wellfound`, `tracker_legacy`) **and**
  `fill_orp_pdf.py` (gitignored — personal data, never declared).

**Interaction with the spec-032 test**: spec 032's `B_ALLOW` currently allow-lists
`scripts/filter_funnel.py`, `audit_work_mode.py`, `audit_provenance.py`,
`diag_freelance.py` for their CWD `data/` literals. FR-006 re-points those four at
`paths.DB_PATH`, so **this spec shrinks `B_ALLOW` back to empty** (or removes the
allow-list) once those defaults are converted — a task to track.

---

## 7. `python -m scripts.<name>` — argparse surface

**Decision**: The four diagnostic tools gain `from paths import DB_PATH` and use it
as their default (FR-006). `scripts.health_check` already has argparse (`--url`,
`--db`, `--max-age-hours`) so `python -m scripts.health_check --help` works.
`scripts.export_seed` has **no** argparse — document its module form as a dry import
(`python -c "import scripts.export_seed"`) rather than `--help`, exactly as spec 032
did for the root module. `scripts/backup_db.py` keeps its file-path invocation in
`deploy.sh` (`docker cp … /tmp/backup_db.py` + `docker exec … python
/tmp/backup_db.py`) — it is stdlib-only and imports no project module.

---

## 8. Documentation updates (FR-008, FR-011, FR-013)

**Decision**: Replace file-path invocations with module form in
`docs/migration-checklist.md` (health check → `python -m scripts.health_check`,
fingerprint → `python -m scripts.fingerprint`), `docs/restore-procedure.md`
(health check line), `CLAUDE.md` (commands section: add `pip install -e ".[dev]"`,
switch `python scripts/…` to `python -m scripts.…`), and the spec 031/032
quickstarts. Add a **pre-deploy staging section** to `docs/migration-checklist.md`
(FR-011). Create `docs/infrastructure.md` (FR-013) documenting the
`tailscale serve` command, `tailscale serve status`, and the undo command, and link
it from `CLAUDE.md` Infrastructure.
