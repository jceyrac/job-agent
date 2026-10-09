# Feature Specification: Domain Package `core/` (roadmap step 1c)

**Feature Branch**: `034-core-package`

**Created**: 2026-10-08

**Status**: Clarified 2026-10-08 — ready for `/speckit.plan`

**Input**: Roadmap `docs/roadmap-api.md`, step 1c. "Move the domain code into a `core/` package so that `api/`, `web/` and the tracker are clients of one clearly bounded domain. First remove the last hidden location dependencies and make every string-based module reference verifiable, so the move can only fail loudly. Then move in one mechanical commit, no shims. The live tracker and the nightly cron must keep working; release through the staging flow."

---

## Context (verified against source at `7a44b92`, 2026-10-08)

### Prerequisites (done)

- Spec 031: backup/restore, parity fingerprint, health check.
- Spec 032: `paths.py` single source of data/output locations; prod data dir pinned + guarded; script launches via `python -m`.
- Spec 033: editable install (`pyproject.toml`, 28 `py-modules` + 7 packages), no `sys.path` hacks, `scripts/staging.sh`, release flow (branch → staging on exact SHA → ff-only merge → deploy → SHA check).

### Hidden location dependencies still present (missed by the spec-032 guard)

| File | Line | Code | Effect after a move into `core/` |
|---|---|---|---|
| `cv_agent/renderer.py` | 31–35 | `_REPO_ROOT = dirname(dirname(abspath(__file__)))`; `CV_PIPELINE_DIR` default = `dirname(_REPO_ROOT)/.cv_pipeline` | **Silent**: would look for `job_agent/.cv_pipeline` instead of `AI-Suite/.cv_pipeline` → master CV not found by the CV agent |
| `monitoring_agent.py` | 38 | `ROOT = Path(__file__).resolve().parent` | Dev tool writes specs/stubs under `core/` instead of the repo root |
| `scrape.py` | 21 | `scrapers_dir = join(dirname(__file__), "scrapers")` | Discovery path tied to file location (moves with the package, but is the last `__file__` use in runtime code besides `paths.py`) |

The spec-032 guard only matched `data/`/`outputs`/`.env` patterns, hence the miss.

### String-based module references (invisible to an import rewriter)

| Kind | Where | Examples |
|---|---|---|
| `python -m <module>` launches | `main.py` (4), `scrape.py` (2), `tracker_views/jobs.py` (3), `tracker_views/settings.py` (4), `monitoring_agent.py` (1) | `"-m", "scrape"`, `"-m", "score"`, `"-m", "export_seed"` |
| `importlib.import_module` | `scrape.py:32, 48, 224, 228`; `storage.py:1081` | `f"scrapers.{subpkg}.{module_name}"`, `"scrapers.greenhouse"` |
| `mock.patch` / `monkeypatch.setattr` targets | `tests/` (≈15) | `"llm.call"`, `"cv_agent.nodes.interrupt"`, `"scrapers.boards.joinup.time.sleep"` |
| Container commands | `docker-compose.yml` | `python main.py` (agent — the nightly cron runs `docker compose run --rm agent`), `python email_monitor.py` |
| Deploy | `scripts/deploy.sh:59` | `docker exec job-tracker python seed.py` |

### Layout today

- 33 root `.py` files: 27 domain modules, `tracker.py` (Streamlit entry), 5 one-offs (archived in 1d).
- Packages: `scrapers/` (+ `ats/`, `boards/`, `company_sites/`), `cv_agent/`, `tracker_views/`, `scripts/`. No non-Python assets inside `scrapers/` or `cv_agent/`.
- No relative imports in `cv_agent/` or `scrapers/` (all absolute).
- Streamlit: 11 `st.Page(...)` + 5 `st.switch_page(...)`, all paths under `tracker_views/` (unchanged by this spec).
- LangGraph checkpointer (`SqliteSaver`, `data/cv_agent_checkpoints.sqlite`): absent on verva; may exist on the dev Mac.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — No location or string reference can break silently (Priority: P1)

As the maintainer, before moving anything, I want the last `__file__`-derived locations removed and every string-based module reference checked by a test, so that any wrong path or module name fails the test suite instead of failing at runtime.

**Why this priority**: Turns the move (US2) into an operation that can only fail loudly.

**Independent Test**: With no file moved, the suite is green; deliberately breaking one `-m` string, one `importlib` string, one `mock.patch` target, or reintroducing a `__file__` derivation makes a guard test fail.

**Acceptance Scenarios**:

1. **Given** the change, **When** I import `cv_agent.renderer` on the dev Mac, **Then** `CV_PIPELINE_DIR` resolves to exactly the same path as before (`AI-Suite/.cv_pipeline`), now computed in `paths.py` (`CV_PIPELINE_DIR` env override kept).
2. **Given** the change, **Then** `monitoring_agent.py` uses `paths.PROJECT_ROOT`, and scraper discovery in `scrape.py` iterates the scrapers package through the import system (package `__path__` / `pkgutil`), not `__file__`.
3. **Given** a guard test, **When** the suite runs, **Then** it fails on any `__file__` use in runtime code (the 27 domain modules, `scrapers/`, `cv_agent/`, `tracker.py`, `tracker_views/`, `scripts/`) outside two allow-listed *patterns*: `paths.py`'s `PROJECT_ROOT` derivation, and the AST-matched `is_active_page(__file__)` call (call to `is_active_page` whose sole argument is `__file__`). `tracker_views/` is covered, not exempt.
4. **Given** a guard test, **When** the suite runs, **Then** every module name used as a string — `-m` arguments in `subprocess` calls, literal (or f-string prefix) arguments of `importlib.import_module`, `mock.patch`/`monkeypatch.setattr` targets in `tests/` — resolves to an importable module/attribute (`importlib.util.find_spec` / import + `getattr`), and the test fails naming the file and line otherwise.

---

### User Story 2 — Domain moved into `core/` in one mechanical commit (Priority: P1)

As the maintainer, I want the 27 domain modules, `scrapers/` and `cv_agent/` under a `core/` package, imported everywhere as `core.<module>`, with no compatibility shims, so there is exactly one way to import the domain.

**Why this priority**: The structural goal of step 1c; prerequisite for `api/` (step 5).

**Independent Test**: After the move: full suite green, guard tests green, `pip install -e .` from a fresh venv exposes `core`, `core.scrapers.*`, `core.cv_agent`, `tracker_views`, `scripts`, `tracker`; no root module named like a domain module remains.

**Acceptance Scenarios**:

1. **Given** the move, **Then** it is done with `git mv` (history preserved) and all imports are rewritten by a script committed under `scripts/` (not by hand), in a single commit separate from US1.
2. **Given** the move, **Then** imports use absolute `core.` paths everywhere, including inside `core/` (`from core.storage import JobStorage`, `from core import llm`), and no root-level alias module exists.
3. **Given** the move, **Then** every string reference listed in Context is updated (`-m core.scrape`, `importlib.import_module(f"core.scrapers...")`, `mock.patch("core.llm.call")`, compose `python -m core.main` / `python -m core.email_monitor`, deploy.sh `python -m core.seed`), and the US1 guard proves it.
4. **Given** the move, **Then** `paths.PROJECT_ROOT` is updated to the repo root (one level up from `core/paths.py`) — the only location change in the codebase — and `DATA_DIR`, `DB_PATH`, `OUTPUT_DIR`, `ENV_PATH`, `CV_PIPELINE_DIR` resolve to the same values as before on the dev Mac and in the container.
5. **Given** the move, **Then** `pyproject.toml` declares packages `core`, `core.scrapers`, `core.scrapers.ats`, `core.scrapers.boards`, `core.scrapers.company_sites`, `core.cv_agent`, `tracker_views`, `scripts`, and `py-modules = ["tracker"]`; the "root modules declared" guard is updated accordingly (root may only contain `tracker.py` + the five one-offs until 1d).

---

### User Story 3 — Released through the staging flow with no behaviour change (Priority: P1)

As the user, I want the moved code validated on staging on the exact SHA before it reaches production, and the live tracker, the nightly cron and the CV agent to behave exactly as before.

**Independent Test**: Staging on the candidate SHA passes all checks below; after ff-only merge and deploy, health check, parity and next nightly cron are green.

**Acceptance Scenarios**:

1. **Given** `staging.sh up <sha>`, **Then** the tracker serves the backup copy on 8502, every background-launch button (Jobs: fetch, score, extract; Settings: scrape, monitored-only, score, extract) runs `python -m core.*` and logs normally, and `/job_detail?id=<id>` opens.
2. **Given** staging, **When** I run `staging.sh run python -m core.scrape --source <one cheap source>` and `staging.sh run python -m core.score --extract`, **Then** both complete normally.
3. **Given** staging, **Then** the parity fingerprint (candidate image vs current prod image, same backup copy, same `--as-of`) is identical.
4. **Given** the dev Mac, **Then** `python -m core.cv_agent.cli --help` works and `CV_PIPELINE_DIR`/`MASTER_CV_PATH` print the same values as before the move.
5. **Given** the deploy, **Then** the deployed SHA equals the staged SHA, `deploy.sh` runs `python -m core.seed` successfully, the health check is OK, and the next nightly `docker compose run --rm agent` run (`python -m core.main`) succeeds.

---

### Edge Cases

- **In-progress CV agent sessions on the dev Mac**: the LangGraph serializer may store module paths of objects in state. A session started before the move may fail to resume after it. Before implementing US2, check `data/cv_agent_checkpoints.sqlite` on the Mac for unfinished threads; finish them or accept that they restart from scratch. verva has none.
- **`streamlit run tracker.py`** keeps working: `tracker.py` and `tracker_views/` stay at the root in this spec; only their imports change.
- **Old image during deploy**: `deploy.sh` runs `backup_db.py` inside the *old* image before rebuild (file path, stdlib only) — unaffected.
- **`deploy.sh` re-exec after `git pull`** guarantees the new `python -m core.seed` line is the one executed.
- **f-string module names** (`f"scrapers.{subpkg}.{name}"`): the guard checks the static prefix (`core.scrapers`) resolves as a package; discovery itself is covered by the scraper discovery test.
- **`tests/` imports**: rewritten by the same script; test files themselves do not move.
- **Docs and prompts**: `prompts/` is historical and not rewritten; `CLAUDE.md`, `docs/*.md` and the constitution's Stable-core list are updated to `core/` paths.

---

## Requirements *(mandatory)*

### Functional Requirements

**Phase A — hardening, no move (US1), its own commit**

- **FR-001**: `paths.py` MUST expose `CV_PIPELINE_DIR` (env `CV_PIPELINE_DIR`, default `dirname(PROJECT_ROOT)/.cv_pipeline`); `cv_agent/renderer.py` MUST use it and keep `MASTER_CV_PATH` derived from it.
- **FR-002**: `monitoring_agent.py` MUST use `paths.PROJECT_ROOT` for `ROOT` (instead of its own `Path(__file__)`), and MUST derive every source-tree scraper path it references (`scrapers/greenhouse.py`, `scrapers/ats/{provider}.py`, `scrapers/company_sites/{slug}.py`) from a single `SCRAPERS_SRC_DIR` constant in `paths.py` derived from `PROJECT_ROOT` (pre-move `scrapers/`, post-move `core/scrapers/`) — no hardcoded `"scrapers/…"` filesystem path remains.
- **FR-003**: Scraper discovery in `scrape.py` MUST enumerate modules through the package (`pkgutil.iter_modules(scrapers.<subpkg>.__path__)` or equivalent), with no `__file__`.
- **FR-004**: A guard test MUST fail on any `__file__` use in runtime code (the 27 domain modules, `scrapers/`, `cv_agent/`, `tracker.py`, `tracker_views/`, `scripts/`), with a minimal explicit allow-list matched by **pattern, not by directory**: (a) `paths.py`, where `PROJECT_ROOT` is the single allowed `__file__`-derived location; (b) the exact call pattern `is_active_page(__file__)` — a call to `is_active_page` whose sole argument is the `__file__` name, matched via AST — since it is a filename identity check that survives any move. `tracker_views/` is **not** exempt as a directory. Phase A fixes the two real violations this guard would otherwise flag: `scripts/duplicate_report.py` (→ `paths.DB_PATH`) and `scripts/fingerprint.py` (→ `importlib.resources.files("scripts") / "regression_cases.json"`).
- **FR-005**: A guard test MUST resolve every string module reference (`-m` args of `subprocess` calls, `importlib.import_module` literals and f-string static prefixes, `mock.patch`/`monkeypatch.setattr` string targets in `tests/`) and fail with file:line on any unresolvable one.
- **FR-014**: A guard test MUST assert the source-tree base directories referenced by `SCRAPERS_SRC_DIR` exist — `SCRAPERS_SRC_DIR` itself and its `ats/`, `boards/`, `company_sites/` subdirectories — so a wrong constant fails the suite.

**Phase B — the move (US2), one commit**

- **FR-006**: The 27 domain modules (`ats_detection, backfill_descriptions, company_researcher, context_tuner, create_profile, cv_extract, email_monitor, export_jobs, export_seed, filters, job_actions, llm, main, models, monitoring_agent, notifier, paths, preference_report, prepare, profile_generator, profiles, score, scorer, scrape, seed, storage, title_gate`), `scrapers/` and `cv_agent/` MUST be moved under `core/` with `git mv`; `core/__init__.py` created.
- **FR-007**: All imports in the repo (runtime, `tracker.py`, `tracker_views/`, `scripts/`, `tests/`) MUST be rewritten to absolute `core.` imports by a committed script (`scripts/rewrite_core_imports.py`), idempotent and re-runnable; no shim modules.
- **FR-008**: All string references (Context table) MUST be updated; FR-005 guard green.
- **FR-009**: `core/paths.py` `PROJECT_ROOT` MUST point to the repo root; resolved locations identical before/after (US2-4).
- **FR-010**: `pyproject.toml` and the module-declaration guard MUST be updated per US2-5.
- **FR-011**: `docker-compose.yml` (`agent`: `python -m core.main`; `email-monitor`: `python -m core.email_monitor`) and `scripts/deploy.sh` (`python -m core.seed`) MUST be updated.
- **FR-012**: `CLAUDE.md` (commands, key files, NEVER-modify list), `docs/*.md` and the constitution's Stable-core paths MUST reference `core/` locations (constitution: PATCH, wording only).
- **FR-015**: Before the move, a grep of runtime code (the 27 domain modules, `scrapers/`, `cv_agent/`, `tracker.py`, `tracker_views/`, `scripts/`) for string literals containing `scrapers/`, `cv_agent/`, or any `<domain module>.py` MUST list every hit; each hit is either derived from a `paths` constant or explicitly justified (e.g. prose in generated SpecKit spec/stub templates).

**Phase C — release (US3)**

- **FR-013**: Release MUST follow the documented flow: branch → `staging.sh up <exact SHA>` → checks of US3-1..3 → `staging.sh down` → ff-only merge of that SHA → `deploy.sh` → SHA check → health check → next-night cron check.

### Non-goals

- No move of `tracker.py` / `tracker_views/` (a separate step, to keep the blast radius of this one bounded). No change to Streamlit page paths or `url_path`.
- No archiving of the one-offs (step 1d).
- No reclassification of modules (e.g. moving `monitoring_agent.py` out of `core/` as a dev tool, splitting entry points from library code) — follow-up decision.
- No behaviour, schema or dependency change.
- No relative imports (absolute `core.` everywhere).

### Key Entities

- **`core` package**: the domain — storage, models, profiles, scoring, scraping, agents — imported by every client (tracker today; `api/`, `web/` later).

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Phase A: suite green; each guard fails when one violation of its kind is reintroduced; `CV_PIPELINE_DIR` unchanged on the Mac.
- **SC-002**: Phase B: suite green; no domain module left at the root; `grep` finds no `import storage`/`from storage` (etc.) outside `core.` form; rewrite script re-run is a no-op.
- **SC-003**: Staging on the exact SHA: all US3-1..3 checks pass; parity identical; prod container/image unchanged during staging.
- **SC-004**: Mac: `python -m core.cv_agent.cli --help` OK; `CV_PIPELINE_DIR`/`MASTER_CV_PATH` identical before/after.
- **SC-005**: Deploy: deployed SHA = staged SHA; `core.seed` OK; health check OK; next nightly cron `success`.

---

## Assumptions

- **Confirmed 2026-10-08**: no unfinished CV agent session on the Mac (user finished his application). Any leftover thread in `data/cv_agent_checkpoints.sqlite` may be discarded.
- The host crontab keeps calling `docker compose run --rm agent` (the command change lives in `docker-compose.yml`).
- Deploy avoids the month-end status batch.
