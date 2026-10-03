# Migration checklist — job_agent

Spec 031, FR-019. This is the Principle-XI definition of done for **every roadmap
step** — tick each item before a step is declared complete. Run on verva (SSH).

---

## Per-step definition of done

- [ ] **Backup taken (and verified) before the deploy**
  `deploy.sh` does this automatically (aborts on failure). Confirm a fresh
  `manifest_<STEM>.json` exists and its `files.jobs.integrity` is `ok`:
  ```bash
  docker exec job-tracker ls -1 /app/data/backups/manifest_*.json
  docker exec job-tracker cat /app/data/backups/manifest_<STEM>.json
  ```

- [ ] **Health check OK** (tracker responds; last run succeeded and is < 26 h)
  ```bash
  docker exec job-tracker python /app/scripts/health_check.py --url http://localhost:8501 --db /app/data/jobs.db
  ```
  (`health_check.py` is baked into the rebuilt image at `/app/scripts/`, so it is
  available right after `deploy.sh` finishes.)

- [ ] **Nightly cron OK** — next morning, the last `runs` row has
  `status = success` (or `scraped`) and `ran_at` from last night:
  ```bash
  docker exec job-tracker python -c \
    "import sqlite3;print(sqlite3.connect('/app/data/jobs.db').execute('SELECT ran_at,status,run_type FROM runs ORDER BY ran_at DESC, id DESC LIMIT 1').fetchone())"
  ```

- [ ] **Manual smoke test** of the tracker (open it in a browser):
  - [ ] Jobs feed loads and shows rows.
  - [ ] A status change on one job persists (reload shows it).
  - [ ] A job detail opens and renders its fields.

- [ ] **Parity green** — a fingerprint of a pre-change snapshot is byte-identical
  when recomputed with the same code + `--as-of`, and `compare` reports no diff
  against the pre-change baseline (see `quickstart.md`):
  ```bash
  python scripts/fingerprint.py --db <snapshot>.db --as-of <date> --out /tmp/after.json
  python scripts/compare_fingerprints.py <baseline.json> /tmp/after.json
  ```

---

## Notes

- A schema-touching step also follows **expand / contract**: add + backfill, keep the
  old column, drop it in a later step (Principle XI). This checklist is the "done"
  gate on top of that.
- If any item fails, roll back via `docs/restore-procedure.md` (live mode) before
  retrying the step.
