# Quickstart — Domain Package `core/` (spec 034)

Runnable validation for each phase. Run on the dev Mac first; the release path
(US3) runs on verva via the staging flow. Every check must be green before the
next phase.

---

## Phase A — hardening, no move (US1)

```bash
python -m pytest tests/                     # green; count is the T001 baseline + new guards
python -m pytest tests/test_core_module_refs.py -q    # FR-004 + FR-005 guards green

# FR-001: CV_PIPELINE_DIR unchanged
python -c "from paths import CV_PIPELINE_DIR, MASTER_CV_PATH; print(CV_PIPELINE_DIR)"  # …/AI-Suite/.cv_pipeline
```

Negative check (guard actually fires): temporarily introduce a bad `-m` string /
a `__file__` / a bad `mock.patch` target and confirm the matching guard fails
with `file:line`, then revert.

---

## Phase B — the move (US2)

```bash
python -m scripts.rewrite_core_imports            # rewrite in place
python -m scripts.rewrite_core_imports --check    # exit 0 = no pending (idempotent)

pip install -e ".[dev]"                           # refresh editable install for the new core package
python -c "import core, core.storage, core.scrapers.boards, core.cv_agent; print('ok')"

python -m pytest tests/                           # full suite green (SC-002)

# No domain module left at the root; no root-form import of a moved module
ls core/ | grep -E '^(storage|models|paths|scrape)\.py$'
grep -rnE '^(from|import) (storage|models|scrape|scorer|llm)\b' --include='*.py' . | grep -v 'core\.'   # empty
```

---

## Phase C — release (US3, on verva)

Follow `docs/migration-checklist.md` "Release process":

1. `scripts/staging.sh up <full-sha>` on the candidate SHA.
2. Click every background-launch button (Jobs: fetch/score/extract; Settings:
   scrape/monitored-only/score/extract) — each runs `python -m core.*`.
3. `scripts/staging.sh run python -m core.scrape --source <one cheap source>` and
   `python -m core.score --extract` complete normally.
4. `/job_detail?id=<id>` renders.
5. Parity fingerprint (candidate image vs prod image, same backup copy, same
   `--as-of`) is identical.
6. `scripts/staging.sh down`; ff-only merge of that SHA; `deploy.sh`; SHA check;
   health check; next-night `docker compose run --rm agent` cron success.

Mac-only (SC-004): `python -m core.cv_agent.cli --help` works and prints the same
`CV_PIPELINE_DIR` / `MASTER_CV_PATH` as before the move.
