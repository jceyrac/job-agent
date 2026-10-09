# Migration checklist — job_agent

Spec 031, FR-019. This is the Principle-XI definition of done for **every roadmap
step** — tick each item before a step is declared complete. Run on verva (SSH).

---

## Release process — branch → staging → merge → deploy → SHA check

Every roadmap step ships the same way; nothing deploys until the exact commit is
validated on staging.

1. **Branch** — work on a spec branch; the branch tip must be exactly the commit to ship.
2. **Stage the exact SHA** — on verva: `scripts/staging.sh up <full-sha>` (never the branch
   name). Run the *Pre-deploy: staging validation* checklist, then `scripts/staging.sh down`.
3. **ff-only merge** — promote that SHA itself, not the branch tip:
   `git checkout main && git pull --ff-only && git merge --ff-only <full-sha> && git push origin main`.
   Refuse anything that is not fast-forward.
4. **Deploy** — on verva: `./scripts/deploy.sh` (git pull + pre-rebuild backup + rebuild +
   restart), then `tailscale serve --bg --tcp=8501 tcp://127.0.0.1:8501`.
5. **SHA check** — `git -C /opt/job-agent rev-parse HEAD` equals the merged SHA; health check
   OK; parity green.

---

## Pre-deploy: staging validation (spec 033, FR-011)

Before `deploy.sh`, validate the candidate commit on the staging tracker (`:8502`):

- [ ] `scripts/staging.sh up <candidate-sha>` — staging tracker serves a copy of the latest backup on 8502.
- [ ] Click every background-launch button (Jobs: fetch / score / extract; Settings: scrape / monitored-only / score / extract).
- [ ] Open `/job_detail?id=<id>` and confirm it renders.
- [ ] `scripts/staging.sh run python -m scripts.fingerprint …` — fingerprint equals the pre-change baseline (parity green).
- [ ] `scripts/staging.sh down` — no staging container, image, volume or worktree remains.

---

## Per-step definition of done

- [ ] **Backup taken (and verified) before the deploy**
  `deploy.sh` does this automatically (aborts on failure). Confirm a fresh
  `manifest_<STEM>.json` exists and its `files.jobs.integrity` is `ok`:
  ```bash
  docker exec job-tracker ls -1 /app/data/backups/manifest_*.json
  docker exec job-tracker cat /app/data/backups/manifest_<STEM>.json
  ```

- [ ] **Health check OK** (tracker responds; latest **full** run succeeded and is < 26 h)
  ```bash
  docker exec job-tracker python -m scripts.health_check --url http://localhost:8501 --db /app/data/jobs.db
  ```
  (`health_check.py` is baked into the rebuilt image at `/app/scripts/`, so it is
  available right after `deploy.sh` finishes.)

- [ ] **Nightly cron OK** — next morning, the latest `runs` row with
  `run_type = 'full'` has `status = success` (or `scraped`) and `ran_at` from last night:
  ```bash
  docker exec job-tracker python -c \
    "import sqlite3;print(sqlite3.connect('/app/data/jobs.db').execute(\"SELECT ran_at,status,run_type FROM runs WHERE run_type='full' ORDER BY ran_at DESC, id DESC LIMIT 1\").fetchone())"
  ```

- [ ] **Manual smoke test** of the tracker (open it in a browser):
  - [ ] Jobs feed loads and shows rows.
  - [ ] A status change on one job persists (reload shows it).
  - [ ] A job detail opens and renders its fields.

- [ ] **Parity green** — a fingerprint of a pre-change snapshot is byte-identical
  when recomputed with the same code + `--as-of`, and `compare` reports no diff
  against the pre-change baseline (see `quickstart.md`):
  ```bash
  python -m scripts.fingerprint --db <snapshot>.db --as-of <date> --out /tmp/after.json
  python -m scripts.compare_fingerprints <baseline.json> /tmp/after.json
  ```

---

## Spec 032 — Location Independence (deploy-time validations, queued)

Queued for the verva deploy (a separate, user-confirmed step — **not** run in the
implementation phase). From spec 032 `quickstart.md` §7:

- [ ] **SC-001 parity** — fingerprint a pre-change snapshot and a post-change
  snapshot with the same `--as-of`; `scripts.compare_fingerprints` reports no
  diff (spec 032 `quickstart.md`).
- [ ] **SC-002 drill tracker** — on the drill tracker (`:8502`, restored backup),
  click all 7 launch buttons (Jobs: fetch / score / extract; Settings: scrape /
  monitored-only / score / extract) — each completes with normal log output — then
  run `core/main.py`'s 4 stages.
- [ ] **SC-005 live** — health check OK; next nightly `full` run success; Reports
  CSV export works; the Preferences page context-suggest action (`preferences.py`)
  opens the DB.
- [ ] **Prod guard** — `tracker`/`agent`/`email-monitor` start with
  `JOB_AGENT_DATA_DIR=/app/data` + `JOB_AGENT_REQUIRE_DB=1` (compose `environment`),
  and a deliberately empty `JOB_AGENT_DATA_DIR` fails at startup without creating
  any directory or DB.

---

## Notes

- A schema-touching step also follows **expand / contract**: add + backfill, keep the
  old column, drop it in a later step (Principle XI). This checklist is the "done"
  gate on top of that.
- If any item fails, roll back via `docs/restore-procedure.md` (live mode) before
  retrying the step.
