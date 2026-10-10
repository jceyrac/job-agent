# Quickstart: Remove Dead Files (spec 035)

Read-only validation that the deletion is complete and nothing broke. Run on the dev Mac
first, then the release checks on verva.

## Prerequisites

- venv with `pip install -e ".[dev]"`
- on branch `035-remove-dead-files`

## Local checks (before staging)

1. Root contains only `tracker.py` (SC-001):

   ```bash
   ls *.py
   # → tracker.py  (and nothing else)
   ```

2. Root-module guard green and still catches a stray root `.py` (SC-001):

   ```bash
   python -m pytest tests/test_installable_project.py -q
   # negative fixture: touch a scratch root module, re-run, confirm the guard fails,
   # then delete the scratch module
   ```

3. Full suite green (SC-002):

   ```bash
   python -m pytest tests/ -q
   ```

4. A removed file is retrievable from history (SC-003):

   ```bash
   # <sha> and <file> come from docs/history.md
   git show <sha>:tracker_legacy.py | head -5
   ```

## Release checks (verva)

Per `docs/migration-checklist.md`:

- `scripts/staging.sh up <exact-sha>` → staging tracker on :8502 serves the backup copy;
  health OK; parity fingerprint identical (candidate vs prod image, same backup copy,
  same `--as-of`); quick look at the feed renders.
- `scripts/staging.sh down` (no staging container/image/volume/worktree left).
- ff-only merge of the SHA onto `main` (`git merge --ff-only <exact-sha>`).
- `./scripts/deploy.sh` on verva → `git -C /opt/job-agent rev-parse HEAD` = merged SHA,
  health OK.
- next scheduled cron run: latest `runs` row (`run_type='full'`) has `status='success'`.
