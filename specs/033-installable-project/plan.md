# Implementation Plan: Installable Project + Staging Environment (roadmap step 1b)

**Branch**: `033-installable-project` | **Date**: 2026-10-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/033-installable-project/spec.md`

## Summary

Make `job_agent` an installable Python project (editable install in dev and in the
Docker image) so that every module, script and test imports the same way from any
current directory, and remove all 13 `sys.path` hacks. Add a disposable staging
environment on verva (`scripts/staging.sh`) that runs a candidate commit as a
separate tracker on port 8502 against a copy of the latest backup, without touching
production — the validation sandbox required before the `core/` move (step 1c).
Finally, restrict the production tracker to Tailscale only (loopback bind +
`tailscale serve`), keeping the exact tailnet URL working today.

No file is moved or renamed; runtime behaviour is unchanged; the live tracker must
behave exactly as today. Technical approach (from research): a flat-layout
`pyproject.toml` with explicit `py-modules` + `packages`, `dynamic` dependencies
read from `requirements.txt`, `pip install --no-deps -e .` in the image, and
`tailscale serve --bg --tcp=…` for the tailnet exposure.

## Technical Context

**Language/Version**: Python 3.11 (repo-wide `X | Y` unions, f-strings).

**Primary Dependencies**: `setuptools>=68` as the **build-time** backend only (no
new runtime dependency). Runtime dependencies are unchanged and read from
`requirements.txt` (14 deps) via `dynamic = ["dependencies"]`. `pytest` becomes an
explicit `dev` extra.

**Storage**: SQLite (`data/jobs.db` + `cv_agent_checkpoints.sqlite`) — unchanged.
`paths.DB_PATH` remains the single source of truth (spec 032).

**Testing**: `pytest` via `[tool.pytest.ini_options] testpaths = ["tests"]`;
`python -m pytest tests/` (repo root, `-m` puts the root on `sys.path`) and
`pytest <repo>/tests` after `pip install -e ".[dev]"` both run green. Guard tests
in `tests/` extend the spec-032 repo-scan.

**Target Platform**: Linux server (verva, Ubuntu + Docker Compose) for staging and
Tailscale exposure; macOS dev Mac for the editable install.

**Project Type**: Python application (Streamlit tracker + cron agent + CLI scripts) —
this step changes only **packaging and deployment surface**, not runtime behaviour.

**Performance Goals**: N/A — no runtime code path changes; parity fingerprint must
stay byte-identical (Principle XI).

**Constraints**: No file moved/renamed (step 1c). `storage.py`, `models.py`,
`profiles.py`, `scrape.py`, `scorer.py`, `main.py`, `llm.py`, `scrapers/`,
`tracker_views/shared.py`, `tracker_views/onboarding.py`, migrations — untouched.
`scripts/backup_db.py` stays a standalone file runnable by path (used by
`deploy.sh`). `fill_orp_pdf.py` is gitignored (personal data) — never declared in
`pyproject.toml`.

**Scale/Scope**: 28 runtime root modules (`py-modules`) + 7 packages; 13 `sys.path`
hacks to remove; 4 diagnostic DB defaults to re-point at `paths.DB_PATH`; 1 staging
script; 1 compose port change; 1 tailnet exposure + 1 doc (`docs/infrastructure.md`).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Applies | Assessment |
|-----------|:-------:|------------|
| I. Filet large, point de filtrage unique | No | No ingestion/filtering change. |
| II. Deux chemins d'amélioration | Yes | Code path (git → deploy); staging is code-path tooling, never exposed by the API. |
| III. Profil unifié / `user_id` | No | No data-model or profile change. |
| IV. Structure déterministe | No | No LLM or control-flow change. |
| V. Modification chirurgicale | **Yes** | Minimal diffs; explicit non-goals; no opportunistic refactor. |
| VI. Validation empirique | **Yes** | Staging validation + parity before deploy (SC-003/SC-004); known-regression cases untouched. |
| VII. Sécurité d'abord | **Yes** | Tracker becomes Tailscale-only (US4); staging never touches prod; no secrets. |
| VIII. Scrapers par modèle | No | Scrapers unchanged; staging `run` only invokes them as-is. |
| IX. Scoring optionnel | No | No scoring-path change. |
| X. Catalogue / espace utilisateur | No | No schema or data placement change. |
| XI. Migration progressive | **Yes** | Roadmap step; app stays operational; staging is the pre-deploy gate. |

**Architecture Constraints**:
- **Stable core untouched** — spec non-goals explicitly exclude every stable-core
  file; the editable install and `sys.path` removal touch *callers* only.
- **Dependencies** — `setuptools` is introduced as a *build-time* backend (the
  standard mechanism an "installable project" requires), not a runtime dependency.
  This is the explicit justification; no runtime footprint, no `requirements.txt`
  change.

**Verdict: PASS.** No violations requiring Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/033-installable-project/
├── plan.md              # This file
├── research.md          # Phase 0 output — packaging/tailscale/staging decisions
├── data-model.md        # Phase 1 output — project declaration + staging lifecycle
├── quickstart.md        # Phase 1 output — validation guide
├── contracts/           # Phase 1 output — staging CLI, package surface, network
│   ├── staging-cli.md
│   ├── package.md
│   └── network.md
└── tasks.md             # Phase 2 output (NOT created by /speckit-plan)
```

### Source Code (repository root — flat layout, unchanged)

```text
job_agent/
├── pyproject.toml             # NEW — installable project declaration
├── requirements.txt           # unchanged (single source of runtime deps)
├── Dockerfile                 # + `pip install --no-deps -e .`
├── docker-compose.yml         # tracker port → "127.0.0.1:8501:8501"
├── scripts/
│   ├── __init__.py            # NEW — scripts becomes a package
│   ├── staging.sh             # NEW — up / run / down
│   ├── backup_db.py           # unchanged (standalone, allow-listed)
│   └── … (12 diagnostic tools)
├── docs/
│   └── infrastructure.md      # NEW — Tailscale exposure how-to
├── <28 runtime modules>.py    # declared as py-modules
├── scrapers/{,ats,boards,company_sites}/   # packages
├── tracker_views/             # package
├── cv_agent/                  # package
└── tests/                     # plain dir (no package), guard tests added
```

**Structure Decision**: Keep the flat layout (no file moves — non-goal). Declare the
28 runtime root modules explicitly as `py-modules` and the 7 packages explicitly via
`[tool.setuptools.packages]`. `scripts/` gains `__init__.py` (FR-003) to become the
`scripts` package. `tests/` stays a non-package directory discovered via `testpaths`.

## Complexity Tracking

> No constitution violations — this table is intentionally empty.
