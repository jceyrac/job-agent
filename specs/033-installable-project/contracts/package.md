# Contract: Installable package surface (US1/US2, FR-001/002/003/004/005)

The installed distribution `job-agent` exposes a fixed import surface, valid from
**any** current directory, after `pip install -e .` (dev) or the image's
`pip install --no-deps -e .` (prod).

## Import surface

**28 root modules** (`py-modules`) — full list in `data-model.md` §1 — importable
as `import <name>` / `python -m <name>`.

**7 packages** — `scrapers`, `scrapers.ats`, `scrapers.boards`,
`scrapers.company_sites`, `tracker_views`, `cv_agent`, `scripts` — importable and
runnable as `python -m <pkg>.<mod>` (e.g. `python -m scripts.health_check`,
`python -m scripts.fingerprint`).

## Entry points / invocation contract

| Invocation | Must work | From |
|-----------|-----------|------|
| `python -m score --help` | ✓ | any CWD (SC-001) |
| `python -m scripts.health_check --help` | ✓ | any CWD |
| `pytest <repo>/tests` | ✓ | any CWD (after `pip install -e ".[dev]"`) |
| `streamlit run tracker.py` | ✓ (unchanged) | repo root / image |
| `python -m main`, `python -m scrape …` | ✓ (unchanged) | repo root / image |

## Dependency contract

- Runtime dependencies: single source = `requirements.txt` (14 deps), pulled into
  `[project]` via `dynamic = ["dependencies"]`. No duplication in `pyproject.toml`.
- Test dependency: `dev` extra = `pytest` only; `[tool.pytest.ini_options]
  testpaths = ["tests"]`.
- `pip install --no-deps -e .` in the image must NOT reinstall runtime deps (layer
  cache preserved).

## Forbidden (guarded by tests, FR-007)

- Any `sys.path` manipulation outside `scripts/backup_db.py` and `specs/`.
- Any git-tracked root module absent from `py-modules` (allow-list: the 5 one-offs
  and gitignored `fill_orp_pdf.py`).
- `scripts/` tools invoked by file path (`python scripts/x.py`); the module form
  `python -m scripts.x` is the contract — **except** `backup_db.py`, which stays a
  standalone file run by path (used by `deploy.sh`).
