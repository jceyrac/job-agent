# Data Model — Domain Package `core/` (spec 034)

This spec changes **no database schema** and **no runtime behaviour**. Its
"entities" are the structural surfaces it introduces: the `core` package, the
rewrite script, and the guard allow-lists.

---

## 1. The `core` package (spec Key Entity)

The domain, imported by every client (tracker today; `api/`, `web/` later).

| Field | Value | Notes |
|-------|-------|-------|
| `core/__init__.py` | empty | package marker |
| modules | 27 `.py` files | the domain modules (FR-006), moved verbatim with `git mv` |
| sub-packages | `core/scrapers/` (+ `ats/`, `boards/`, `company_sites/`), `core/cv_agent/` | moved verbatim |
| stays at root | `tracker.py`, `tracker_views/`, `scripts/`, `tests/`, 5 one-offs | only their imports change |

The 27 modules: `ats_detection, backfill_descriptions, company_researcher,
context_tuner, create_profile, cv_extract, email_monitor, export_jobs,
export_seed, filters, job_actions, llm, main, models, monitoring_agent, notifier,
paths, preference_report, prepare, profile_generator, profiles, score, scorer,
scrape, seed, storage, title_gate`.

**Invariants**:

- No root-level alias module exists after the move (FR-007, US2-2).
- No relative import exists before or after (verified empty).
- `core/paths.py` `PROJECT_ROOT` = repo root; every resolved location identical
  before/after (FR-009).

---

## 2. The rewrite script (`scripts/rewrite_core_imports.py`)

A committed, idempotent, re-runnable tool (FR-007). Interface in
`contracts/rewrite-imports.md`. It is a **migration tool**, not runtime code —
present in the repo, never imported by the app.

| Field | Value |
|-------|-------|
| input | repo root (via `core.paths.PROJECT_ROOT`) |
| scope | every `*.py` under root, `core/`, `tracker_views/`, `scripts/`, `tests/` |
| moved names | 27 modules ∪ `scrapers` ∪ `cv_agent` |
| output | rewritten imports + `-m`/`importlib`/`mock.patch` string references; no-op on second run (source-tree *file* paths are handled by `SCRAPERS_SRC_DIR`, not this script) |

---

## 3. Guard allow-lists (FR-004, FR-005, FR-010, FR-014)

Test fixtures, not runtime data. Live in `tests/test_core_module_refs.py` (new)
plus an update to `tests/test_installable_project.py`.

| List | Contents | Purpose |
|------|----------|---------|
| FR-004 `__file__` allow-list | `core/paths.py` + the `is_active_page(__file__)` call pattern (AST) | every other runtime `__file__` fails; `tracker_views/` is covered, not exempt |
| FR-004 skip dirs | `.venv`, `.git`, `__pycache__`, `tests`, `specs` | out of scope |
| FR-010 root-module allow-list | the 5 one-offs + `fill_orp_pdf.py` | may remain undeclared at root |
| FR-005 stdlib targets | `builtins.*`, `sys.*` | `mock.patch` targets that are not moved names |
| FR-014 source-tree dirs | `SCRAPERS_SRC_DIR` + `ats/`, `boards/`, `company_sites/` | assert each exists (imported from `paths`) |

---

## 4. Non-Python references (FR-011)

| Where | Before | After |
|-------|--------|-------|
| `docker-compose.yml` `agent` | `python main.py` | `python -m core.main` |
| `docker-compose.yml` `email-monitor` | `python email_monitor.py` | `python -m core.email_monitor` |
| `scripts/deploy.sh:59` | `python seed.py` | `python -m core.seed` |
| docs / `CLAUDE.md` / constitution Stable-core | `storage.py`, `scrape.py`, … | `core/storage.py`, `core/scrape.py`, … |
