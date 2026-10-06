# Tasks: Installable Project + Staging Environment (spec 033)

**Input**: Design documents from `/specs/033-installable-project/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Guard tests are **required** (FR-007). No other new tests beyond the guard
tests; the existing suite is the regression net.

**Organization**: Grouped by user story (US1, US3, US2, US4 — priority order).

## Format: `[ID] [P?] [Story] Description`

- **[P]** = parallelizable (different files, no dependency on another task)
- **[Story]** = US1 / US2 / US3 / US4

---

## Phase 1: Setup

**Purpose**: Record the green baseline before any change.

- [X] T001 Run `python -m pytest tests/` and confirm the full suite is green (253 passed) before any change; note the count for the final validation in T018.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The installable project declaration — everything else depends on it.

**⚠️ CRITICAL**: No user-story work can begin until this phase is complete.

- [X] T002 Create `pyproject.toml` at the repo root declaring the project (FR-001 + FR-002): `[build-system] requires=["setuptools>=68"]` / `build-backend="setuptools.build_meta"`; `[project] name="job-agent"`, `version="0.1.0"`, `requires-python=">=3.11"`, `dynamic=["dependencies"]`; `[tool.setuptools.dynamic] dependencies={file=["requirements.txt"]}`; `[tool.setuptools]` `py-modules` = the 28 runtime modules (`ats_detection, backfill_descriptions, company_researcher, context_tuner, create_profile, cv_extract, email_monitor, export_jobs, export_seed, filters, job_actions, llm, main, models, monitoring_agent, notifier, paths, preference_report, prepare, profile_generator, profiles, score, scorer, scrape, seed, storage, title_gate, tracker`) and `packages` = the 7 packages (`scrapers, scrapers.ats, scrapers.boards, scrapers.company_sites, tracker_views, cv_agent, scripts`). Exclude the 5 one-offs and `fill_orp_pdf.py` (see data-model.md §1).
- [X] T003 Create `scripts/__init__.py` (empty package marker) so `scripts` is a real package (FR-003) and `python -m scripts.<name>` resolves.

**Checkpoint**: `pip install -e .` must succeed and expose the 28 modules + 7 packages.

---

## Phase 3: User Story 1 — One way to import, from anywhere (Priority: P1) 🎯 MVP

**Goal**: `pip install -e .` (dev) and `pip install --no-deps -e .` (image) make every
module/script/test import the same way from any CWD; zero `sys.path` hacks remain.

**Independent Test**: In a fresh venv, `pip install -e .` then from `/tmp`:
`python -m score --help`, `python -m scripts.health_check --help`, and
`pytest <repo>/tests` all succeed; `grep -r "sys.path" --include=*.py` finds nothing
outside `scripts/backup_db.py` and `specs/`.

### Implementation

- [X] T004 [P] [US1] Remove the `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` line (and its now-unused `os`/`sys` imports) from `scripts/fingerprint.py`, `scripts/dedupe_title_company.py`, `scripts/dedupe_jobs.py`, `scripts/scraper_toggle.py`, `scripts/health_check.py`, `scripts/re_extract_sample.py` (FR-005).
- [X] T005 [P] [US1] In `scripts/filter_funnel.py`, remove the `sys.path.insert(0, ".")` line and re-point the `--db` default from `"data/jobs.db"` to `paths.DB_PATH` (add `from paths import DB_PATH`) (FR-005 + FR-006).
- [X] T006 [P] [US1] Remove the `sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))` line from `tests/test_storage.py`, `tests/scraper_checks.py`, `tests/test_scorer_parsing.py`, `tests/run_all.py`, `tests/test_prepare.py`, `tests/test_migration_safety_net.py` (FR-005).
- [X] T007 [P] [US1] Re-point the DB defaults from `"data/jobs.db"` to `paths.DB_PATH` (add `from paths import DB_PATH`) in `scripts/audit_provenance.py` (positional `nargs="?"` default), `scripts/diag_freelance.py` (positional `sys.argv[1]` fallback), `scripts/audit_work_mode.py` (positional `sys.argv[1]` fallback) (FR-006).
- [X] T008 [P] [US1] Add `RUN pip install --no-deps -e .` to `Dockerfile` immediately after the existing `COPY . .` line, keeping the `requirements.txt` layer first (FR-004).
- [X] T009 [US1] Create `tests/test_installable_project.py` with the two guard tests (FR-007): (a) no `sys.path` manipulation in any runtime `*.py` outside the allow-list `{scripts/backup_db.py}` + `specs/` (scan via AST/`os.walk`, skipping `.venv`, `.git`, `__pycache__`, `tests`, `specs`); (b) every git-tracked root `*.py` module appears in `pyproject.toml` `[tool.setuptools] py-modules` (parse the TOML), allow-listing the 5 one-offs (`migrate_expired_status`, `migrate_profile_independent_tracking`, `migrate_single_status`, `test_wellfound`, `tracker_legacy`) and `fill_orp_pdf.py`.
- [X] T010 [US1] In `tests/test_location_independence.py`, remove `scripts/filter_funnel.py`, `scripts/audit_work_mode.py`, `scripts/audit_provenance.py`, `scripts/diag_freelance.py` from `B_ALLOW` (they no longer carry CWD `data/` literals after T005/T007); keep `test_no_cwd_data_literals` failing on any reintroduction.
- [X] T011 [P] [US1] Update `CLAUDE.md` **Commands** section to module form (`python -m main`, `python -m scrape`, `python -m score …`, `python -m score --extract`) and add `pip install -e ".[dev]"` for the dev install (FR-008).
- [X] T012 [P] [US1] Update file-path invocations to module form in `docs/migration-checklist.md` (health check → `python -m scripts.health_check`, fingerprint → `python -m scripts.fingerprint`), `docs/restore-procedure.md` (health check line), and the spec 031/032 `quickstart.md` files; keep `backup_db.py`'s file-path invocation in `scripts/deploy.sh` unchanged (FR-008).

**Checkpoint**: US1 is functional and independently testable via its Independent Test above.

---

## Phase 4: User Story 3 — Staging tracker on verva (Priority: P1)

**Goal**: Run a candidate commit as a disposable tracker on port 8502 against a copy of
the latest backup, without touching production.

**Independent Test**: `scripts/staging.sh up <sha>` serves the backup on 8502; production
container/image/volume are byte-identical before and after; `down` removes every staging
artifact. (Deploy-time; run via SSH on verva.)

### Implementation

- [X] T013 [US3] Create `scripts/staging.sh` implementing `up <ref>`, `run <command…>`, `down` exactly per `contracts/staging-cli.md`: separate worktree `/opt/job-agent-staging`, image `job-agent:staging`, volume `job_agent_staging_data`, container `job-agent-staging`, loopback port `127.0.0.1:8502:8501`, `JOB_AGENT_DATA_DIR=/app/data` + `JOB_AGENT_REQUIRE_DB=1`, latest-backup copy, `tailscale serve --bg --tcp=8502 tcp://127.0.0.1:8502`, `--server.address 127.0.0.1` (never `0.0.0.0`), fail-fast when no backup, and `run` warning for `main`/`scrape`/`score` (FR-009, FR-010, FR-014).
- [X] T014 [US3] Add a pre-deploy section to `docs/migration-checklist.md` (FR-011): "staging up on the candidate commit → click every background-launch button → open `/job_detail?id=<id>` → parity fingerprint on staging vs baseline → staging down".

**Checkpoint**: Staging environment is scripted and documented; usable to validate the spec's own image before deploy.

---

## Phase 5: User Story 2 — Dependencies declared once (Priority: P2)

**Goal**: Runtime deps read from `requirements.txt` (single source); test tooling declared
explicitly; a fresh environment is reproducible.

**Independent Test**: A fresh venv with `pip install -e ".[dev]"` runs the full suite green;
the Docker image build uses the same runtime dependency list (`requirements.txt`, unchanged).

### Implementation

- [X] T015 [US2] Add `[project.optional-dependencies] dev = ["pytest"]` and `[tool.pytest.ini_options] testpaths = ["tests"]` to `pyproject.toml` (FR-001 tail). Confirm the runtime deps remain sourced only from `requirements.txt` via `dynamic = ["dependencies"]` (no duplication).

**Checkpoint**: `pip install -e ".[dev]"` in a clean venv pulls pytest and the 14 runtime deps; `pytest <repo>` discovers `tests/` via `testpaths`.

---

## Phase 6: User Story 4 — Production tracker reachable through Tailscale only (Priority: P2)

**Goal**: The tracker is unreachable from the LAN / non-tailnet interfaces, while the
tailnet URL (incl. `/job_detail?id=`) works unchanged and survives a reboot.

**Independent Test**: From a non-tailnet LAN device `http://<verva LAN IP>:8501` is refused;
from a tailnet device `http://100.74.139.28:8501` and `/job_detail?id=<id>` work; after a
reboot (or `systemctl restart docker tailscaled`), the tailnet URL works with no manual step.

### Implementation

- [X] T016 [US4] Change `docker-compose.yml` tracker `ports` from `"8501:8501"` to `"127.0.0.1:8501:8501"` (FR-012).
- [X] T017 [US4] Create `docs/infrastructure.md` documenting the tailnet exposure (FR-013): the `tailscale serve --bg --tcp=8501 tcp://127.0.0.1:8501` command, how to verify with `tailscale serve status`, how to undo (`tailscale serve --bg --tcp=8501 off`), and the deploy order (compose change → immediately run `tailscale serve` → verify). Add a reference to it in `CLAUDE.md` **Infrastructure**.

**Checkpoint**: Tracker is loopback-bound in compose; the tailnet exposure is documented and reversible.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation and definition of done.

- [X] T018 Run `python -m pytest tests/` and confirm the full suite is green (the T001 count plus the new guard tests) (SC-006). Then run `quickstart.md` §1–§3: fresh venv `pip install -e ".[dev]"`, `python -m score --help`, `python -m scripts.health_check --help`, `pytest <repo>` from `/tmp` (SC-001), and `grep -rn "sys.path"` clean outside the allow-list (SC-002).

---

## Phase 8: Review fixes (post-review, deploy-gated)

**Purpose**: Corrections from the spec-033 implementation review, applied before any
verva deploy.

- [X] T019 Rework `scripts/staging.sh`: (a) `up <ref>` resolves the ref to a commit on
  `origin` (`git fetch origin`; `git rev-parse --verify origin/<ref>` for a branch, else
  the raw SHA), checks it out with `git worktree add --detach`, and prints the SHA —
  never staging the local `/opt/job-agent` checkout; (b) the tracker runs
  `--server.address 0.0.0.0` inside the container (prod command) with the host bind
  `127.0.0.1:8502:8501` unchanged; (c) the optional checkpoints copy is non-failing
  (`if/then`, not `[ ] && cp`) and a `trap` → `down` cleans up on any `up` failure;
  (d) `--env-file /opt/job-agent/.env` on the tracker and on `run`, with
  `-e GMAIL_APP_PASSWORD= -e NOTIFY_TO= -e JOPLIN_TOKEN=` overrides (verified: empty
  values skip cleanly in `notifier.py`); (e) `down` runs `git worktree prune` and
  verifies no container/image/volume/worktree/`tailscale serve` 8502 entry remains (exit
  non-zero if one does); (f) `tailscale serve` on/off syntax confirmed against verva's
  tailscale and aligned in `staging.sh` + `docs/infrastructure.md`.
- [X] T020 In `tests/test_installable_project.py`, scan `tests/` too (only
  `scripts/backup_db.py` and `specs/` are allow-listed per FR-007); the AST check already
  avoids self-flagging. Do not silently skip files with `SyntaxError` — fail on them.
- [ ] T021 (SC-003) Commit + push. On verva: `scripts/staging.sh up <sha>`, confirm staging
  serves the backup on `8502`, then **STOP** and hand over for the button clicks +
  `/job_detail?id=<id>` check. After the go: fingerprint staging vs baseline,
  `scripts/staging.sh down` (SC-005 verification output). Do **not** deploy before the go.
- [ ] T022 (SC-004/SC-007) After the go: `deploy.sh`, then in the same SSH session the
  `tailscale serve` command for 8501, verify the tailnet URL + `/job_detail` + health
  check. Then hand over for the LAN closed-port check and the reboot test.
- [X] T023 (deploy-gated, unblocks T021) Fix the staging worktree path: `/opt` is
  root-owned on verva, so `git worktree add --detach /opt/job-agent-staging` fails
  with "Permission denied". Move it to `$HOME/job-agent-staging`
  (`WORKTREE="${HOME}/job-agent-staging"` in `scripts/staging.sh`) and align the path
  in `contracts/staging-cli.md`, `research.md`, `data-model.md`, `quickstart.md`,
  `spec.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (P1)**: no deps.
- **Foundational (P2)**: after Setup — **BLOCKS all user stories**.
- **User Stories (P3–P6)**: after Foundational, in priority order **US1 → US3 → US2 → US4**.
- **Polish (P7)**: after all stories.

### User Story Dependencies

- **US1 (P1)**: after Foundational. No story deps. **MVP.**
- **US3 (P1)**: after US1 — `staging.sh` builds the image via the `Dockerfile`
  `pip install -e .` (T008) and needs the `scripts` package (T003).
- **US2 (P2)**: after Foundational. Independent of US1/US3 (touches only `pyproject.toml`
  in an additive way after T002).
- **US4 (P2)**: after Foundational. Independent (compose port + a new doc).

### Within US1

- T004–T008 are parallel (distinct files). T009/T010 (tests) come after T004–T007 so the
  guards pass; T011/T012 (docs) are parallel and independent.

### Parallel Opportunities

- All of T004, T005, T006, T007, T008 touch disjoint files → run together.
- T011 (CLAUDE.md) and T012 (docs) run together.
- Once Foundational lands, US2 (T015) and US4 (T016/T017) can be picked up in parallel with
  US1 (if a second pair of hands existed).

---

## Parallel Example: US1

```text
# Disjoint files — safe to launch together:
Task: "Remove sys.path hack from 6 scripts/*.py (T004)"
Task: "filter_funnel.py sys.path + DB default (T005)"
Task: "Remove sys.path hack from 6 tests/*.py (T006)"
Task: "Re-point 3 diag scripts at paths.DB_PATH (T007)"
Task: "Dockerfile pip install --no-deps -e . (T008)"
```

---

## Implementation Strategy

### MVP First (US1 only)

1. T001 baseline → T002/T003 (Foundational) → T004–T012 (US1).
2. **STOP and VALIDATE**: run US1's Independent Test (fresh venv + `python -m score --help`
   + no `sys.path` grep). This is the whole point of the spec — the installable project.

### Incremental Delivery

1. Foundational → US1 (MVP): imports work from anywhere, hacks gone.
2. + US3: staging env ready — validate **this spec's** image on 8502 before deploy.
3. + US2: `.[dev]` reproducible fresh env.
4. + US4: tracker Tailscale-only.
5. T018: full suite + quickstart validation → then the deploy-time checks (SC-003/004/007)
   in `docs/migration-checklist.md`.

---

## Notes

- `scripts/backup_db.py` is the only `sys.path`-free *and* standalone-file exception: it
  stays runnable by path (`docker cp` + `python /tmp/backup_db.py` in `deploy.sh`) and is
  allow-listed in the guard test.
- The 5 one-offs and gitignored `fill_orp_pdf.py` are never declared in `pyproject.toml`
  and are allow-listed in the FR-007(b) guard.
- Every task touches non-stable-core files only (surgical, Principle V). No change to
  `storage.py`, `models.py`, `profiles.py`, `scrape.py`, `scorer.py`, `main.py`, `llm.py`,
  `scrapers/`, `tracker_views/shared.py`, `tracker_views/onboarding.py`, or migrations.
- Stop at any checkpoint to validate the story independently before proceeding.
