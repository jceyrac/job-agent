# Quickstart: Location Independence (roadmap step 1a)

Runnable validation scenarios. Paths/env contract: [`contracts/paths.md`](contracts/paths.md).
Entity details: [`data-model.md`](data-model.md).

## Prerequisites

- Repo root as CWD (dev), or the container with `WORKDIR /app`.
- Python 3.11 venv (`.venv/`).

## 1. Guard fails loudly (SC-003)

```bash
# Empty dir, guard on → RuntimeError, nothing created
JOB_AGENT_REQUIRE_DB=1 JOB_AGENT_DATA_DIR=/tmp/empty_032 \
  python -c "import paths"
# expect: RuntimeError naming /tmp/empty_032/jobs.db + both env vars
test ! -e /tmp/empty_032 && echo "OK: nothing created"
```

## 2. Guard passes on real data

```bash
JOB_AGENT_REQUIRE_DB=1 python -c "import paths; print(paths.DB_PATH)"
# expect: <repo>/data/jobs.db (or JOB_AGENT_DATA_DIR if set), no error
```

## 3. Unguarded behaviour unchanged (SC-003 acceptance 3)

```bash
JOB_AGENT_DATA_DIR=/tmp/fresh_032 python -c "import paths, os; print(os.path.isdir(paths.DATA_DIR))"
# expect: True (directory created), no error
```

## 4. Guard test (SC-004)

```bash
python -m pytest tests/test_location_independence.py -q
# expect: pass on converted code; fails if a "score.py" launch or a __file__-derived
#         data path / "data/..." literal is reintroduced
```

## 5. Full suite (SC-006)

```bash
python -m pytest tests/ -q
```

## 6. Local smoke of the launch form

```bash
python -m scrape --help      # module form works from repo root
python -m score --help
python -c "import export_seed"   # export_seed has no argparse; dry import proves `-m export_seed` resolves
```

## 7. Prod validation (SC-001, SC-002, SC-005 — on verva / drill tracker)

- **Parity**: fingerprint a pre-change snapshot and a post-change snapshot with the
  same `--as-of`; `scripts.compare_fingerprints` reports no diff (see spec 031
  `quickstart.md`).
- **Drill tracker** (`:8502`, restored backup): click all 7 launch buttons
  (Jobs: fetch/score/extract; Settings: scrape/monitored-only/score/extract) — each
  completes and its log shows normal output; run `main.py`'s 4 stages.
- **Live**: `scripts.health_check` OK; next nightly `full` run success; Reports CSV export
  and Preferences page work (see `docs/migration-checklist.md`).
