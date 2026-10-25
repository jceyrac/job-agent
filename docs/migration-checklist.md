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
   restart); verify `tailscale serve status` still lists `tcp 8501` and `https 8443 /api`
   (the config is persistent — no re-run needed). **Port 443 belongs to nginx (Nextcloud) —
   `tailscale serve` must never use it; `scripts/staging.sh` refuses a serve port already
   bound by another process (`ss -ltn`).**
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

- [ ] **Health check OK** (tracker + API respond; latest **full** run succeeded and is < 26 h)
  ```bash
  docker exec job-tracker python -m scripts.health_check --url http://localhost:8501 --db /app/data/jobs.db
  ```
  (`health_check.py` is baked into the rebuilt image at `/app/scripts/`, so it is
  available right after `deploy.sh` finishes. Its API probe reaches `http://api:8601`
  by service name — the `tracker` service sets `JOB_AGENT_API_URL=http://api:8601`;
  on the host, `--api-url http://127.0.0.1:8601` is the equivalent.)

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

## Spec 039 — User-space separation (step-3 release + rollback)

Step 3 ships through the standard flow above, plus these step-specific items.

### Step-specific validation

- [ ] `scripts/migration_report.py --db <staging-backup-copy>` **before** staging — the
  user-space tables show `without_user_id = n/a (column absent — pre-migration)`.
- [ ] `scripts/staging.sh up <exact-sha>` → the one-shot migration runs on the staging
  copy; `migration_report.py` **after** shows every user-space table with
  `without_user_id = 0`, `users = 1`, and `user_companies > 0` (one row per non-default
  company relationship).
- [ ] Parity fingerprint identical (`scripts/fingerprint` + `compare_fingerprints`) to the
  pre-change baseline.

### Rollback

`job_tracking` and `job_applications` are **rebuilt** by the migration (their primary
key is rekeyed from `(job_id)` to `(user_id, job_id)`), so the **previous image cannot
run on a migrated DB** — redeploying the old SHA alone is not a rollback. To roll back:

1. **Stop** the `tracker` and `agent` services (no further writes).
2. **Restore the pre-rebuild backup** (`docs/restore-procedure.md`, spec 031) — this
   returns the DB to the pre-migration shape.
3. **Redeploy the previous SHA** (the pre-039 commit) — it reads the old shape.

Any write made after the deploy (status changes, applications, interactions) is **lost**:
the restore discards everything since the backup. Rehearsed once on staging before the
prod deploy; record the measured duration here (FR-015 / SC-004): **___ min**
(stop + restore + redeploy + verify).

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
- [ ] **Prod guard** — `tracker`/`agent` start with
  `JOB_AGENT_DATA_DIR=/app/data` + `JOB_AGENT_REQUIRE_DB=1` (compose `environment`),
  and a deliberately empty `JOB_AGENT_DATA_DIR` fails at startup without creating
  any directory or DB.

---

## Spec 043 — Task queue, worker and SSE (step 6-part-2 release + rollback)

Step 6-part-2 ships through the standard flow above. Its schema change is
**additive only** — new `tasks` + `task_events` tables and a few `config` keys
(`pipeline.lock_holder`, `worker.heartbeat`) — so the previous image reads and
writes the DB normally and simply ignores the new tables. No shared or
user-space column is rebuilt or rekeyed (unlike step 3, spec 039).

### Rollback

If 043 must be reverted after deploy:

1. **Revert the commits** on `main` (no force-push): `git revert` the 043
   commits, commit, push.
2. **Stop and remove the worker container** on verva (the previous image has no
   `worker` service, so `deploy.sh` alone would leave it orphaned):
   `docker compose stop worker && docker compose rm -f worker`.
3. **Redeploy the previous image**: `./scripts/deploy.sh`.

The `tasks`/`task_events` tables and their `config` keys are left in place —
additive and harmless to the previous image. The pre-rebuild backup taken by
`deploy.sh` remains the restore point if a full DB rollback is ever needed.

---

## Notes

- A schema-touching step also follows **expand / contract**: add + backfill, keep the
  old column, drop it in a later step (Principle XI). This checklist is the "done"
  gate on top of that.
- If any item fails, roll back via `docs/restore-procedure.md` (live mode) before
  retrying the step.
