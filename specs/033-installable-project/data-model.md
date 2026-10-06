# Data Model — Installable Project + Staging Environment (spec 033)

This spec changes **no database schema**. The "entities" below are the declarative
surfaces it introduces: the project declaration, the staging environment lifecycle,
and the guard allow-lists.

---

## 1. Project declaration (`pyproject.toml`)

The single statement of what is installed (spec Key Entity). No DB counterpart.

| Field | Value | Notes |
|-------|-------|-------|
| `build-system.requires` | `["setuptools>=68"]` | build-time backend only |
| `project.name` | `job-agent` | FR-001 |
| `project.version` | `0.1.0` | static placeholder; never read |
| `project.requires-python` | `>=3.11` | FR-001 |
| `project.dynamic` | `["dependencies"]` | resolved from `requirements.txt` |
| `project.optional-dependencies.dev` | `["pytest"]` | FR-002 (US2) |
| `tool.setuptools.py-modules` | 28 names (below) | FR-002 |
| `tool.setuptools.packages` | 7 names (below) | FR-002 |
| `tool.pytest.ini_options.testpaths` | `["tests"]` | FR-002 |

### The 28 runtime `py-modules`

```text
ats_detection, backfill_descriptions, company_researcher, context_tuner,
create_profile, cv_extract, email_monitor, export_jobs, export_seed, filters,
job_actions, llm, main, models, monitoring_agent, notifier, paths,
preference_report, prepare, profile_generator, profiles, score, scorer, scrape,
seed, storage, title_gate, tracker
```

### The 7 packages

```text
scrapers, scrapers.ats, scrapers.boards, scrapers.company_sites,
tracker_views, cv_agent, scripts
```

### Exclusion rule (FR-002, edge case)

Not declared, still runnable from the repo root until archived in 1d:
`migrate_expired_status`, `migrate_profile_independent_tracking`,
`migrate_single_status`, `test_wellfound`, `tracker_legacy`.
`fill_orp_pdf.py` is **gitignored** (personal data) and never declared.
`scripts/backup_db.py` is a standalone file (never a module) — run by path only.

**Validation**: `scripts/` gains `__init__.py` (FR-003); `tests/` remains a plain
directory (no `__init__.py`) — it must NOT be auto-discovered as a package.

---

## 2. Staging environment (spec Key Entity)

An isolated, disposable copy of production for a given commit. Lifecycle:
`absent → up → running → down → absent`.

| Field | Value / naming | Notes |
|-------|----------------|-------|
| worktree path | `/opt/job-agent-staging` | separate `git worktree`, beside prod, not inside it |
| image tag | `job-agent:staging` | built from the worktree's Dockerfile |
| volume name | `job_agent_staging_data` | seeded with the latest backup copy |
| container name | `job-agent-staging` | tracker on port 8502 |
| port bind | `127.0.0.1:8502:8501` | loopback only; tailnet via `tailscale serve` |
| data env | `JOB_AGENT_DATA_DIR=/app/data`, `JOB_AGENT_REQUIRE_DB=1` | spec 032 guard |
| ref | commit SHA or branch argument to `up` | checkout target |

**State transitions / invariants**:

- `up <ref>` → `running` **only if** the latest backup is present in
  `/app/data/backups/`; otherwise it fails with an explicit message and creates
  nothing (edge case).
- `run <command…>` → transient one-shot container on the **same** staging volume
  (`--rm`), no persistent container; prints a warning for `main`/`scrape`/`score`.
- `down` → `absent`: removes container, image, volume, and worktree — no residue
  (SC-005).
- **FR-010 invariant**: no command may touch `job_data`, production images, the
  `docker compose` production project, or `/opt/job-agent`'s working tree.

---

## 3. Guard allow-lists (FR-007)

| List | Contents | Purpose |
|------|----------|---------|
| `sys.path` allow-list | `scripts/backup_db.py` (if ever needed), everything under `specs/` | FR-007(a) — no `sys.path` manipulation elsewhere |
| undeclared-module allow-list | 5 one-offs + `fill_orp_pdf.py` | FR-007(b) — every git-tracked root module is in `py-modules` |

These lists are **test fixtures**, not runtime data. They live in
`tests/test_installable_project.py`.
