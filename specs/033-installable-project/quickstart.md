# Quickstart / Validation — Installable Project + Staging Environment (spec 033)

Runnable scenarios that prove the feature works end-to-end. Details of the package
surface, staging CLI and network exposure live in [`contracts/`](./contracts/) and
[`data-model.md`](./data-model.md).

---

## 0. Prerequisites

- Dev Mac: Python 3.11, a disposable venv.
- verva (SSH): Docker access, the repo at `/opt/job-agent`, a non-tailnet LAN device
  (e.g. phone with Tailscale off) for the closed-port check.
- A baseline parity fingerprint from a pre-change snapshot (see spec 032
  `quickstart.md`).

---

## 1. Editable install, imports from anywhere (US1 / SC-001)

```bash
# fresh venv
python3.11 -m venv /tmp/qa033 && source /tmp/qa033/bin/activate
cd /Users/jeanclaudevd/AI-Suite/job_agent
pip install -e ".[dev]"

# from /tmp — imports must resolve with no CWD dependency
cd /tmp
python -m score --help
python -m scripts.health_check --help
pytest /Users/jeanclaudevd/AI-Suite/job_agent/tests
```

**Expected**: all three succeed. `pytest` exits 0 (full suite + new guard tests).

## 2. No `sys.path` hacks; modules declared (US1 / SC-002)

```bash
cd /Users/jeanclaudevd/AI-Suite/job_agent
grep -rn "sys.path" --include=*.py . | grep -v -e scripts/backup_db.py -e /specs/
python -m pytest tests/test_installable_project.py -q
```

**Expected**: `grep` finds nothing outside the allow-list; the guard tests pass.
To confirm the guards bite, temporarily add a `sys.path.insert(0, ".")` to any
runtime module (or remove one `py-modules` entry) and re-run — the test must fail.

## 3. Image builds and behaves as today (US1 / SC-004 partial)

```bash
cd /Users/jeanclaudevd/AI-Suite/job_agent
docker build -t job-agent:staging .
docker run --rm job-agent:staging python -m scripts.health_check --url http://localhost:8501 2>&1 | head
```

**Expected**: build succeeds; the health check runs (may fail only because no
tracker is listening in this bare run — the *import* path is what's validated).

## 4. Staging on verva (US3 / SC-003, SC-005)

```bash
ssh <verva> 'cd /opt/job-agent && scripts/staging.sh up <candidate-sha>'
# → tracker on http://100.74.139.28:8502, serving a copy of the latest backup
```

Then, in a browser (tailnet): open the staging tracker, click **every**
background-launch button (Jobs: fetch / score / extract; Settings: scrape /
monitored-only / score / extract), open `/job_detail?id=<id>`, and confirm the
feed renders. Run `scripts/staging.sh run python -m scripts.fingerprint …` and
compare the fingerprint against the baseline (must be byte-identical).

Confirm production is untouched: record the tracker container ID + image ID before
and after — they must be identical.

```bash
ssh <verva> 'cd /opt/job-agent && scripts/staging.sh down'
ssh <verva> 'docker ps -a | grep staging; docker images | grep staging; docker volume ls | grep staging'
```

**Expected**: `down` leaves no staging container, image or volume (SC-005); the
worktree at `$HOME/job-agent-staging` is gone.

## 5. Tailscale-only production (US4 / SC-007)

```bash
# on verva — deploy compose change first, then expose (FR-013 order)
ssh <verva> 'cd /opt/job-agent && docker compose up -d tracker && tailscale serve --bg --tcp=8501 tcp://127.0.0.1:8501 && tailscale serve status'
```

**Expected**: `tailscale serve status` lists the 8501 TCP forward; tailnet URL
`http://100.74.139.28:8501` and `/job_detail?id=<id>` work; the non-tailnet LAN
device is refused on `http://<verva LAN IP>:8501`. After
`systemctl restart docker tailscaled` (or a reboot), the tailnet URL works without
manual action.

---

## 6. Definition of done (Principle XI / FR-011)

Tick every item in `docs/migration-checklist.md` — including the **new pre-deploy
staging section** — plus: health check OK, next nightly cron OK, parity green, full
test suite green, port 8501 closed from LAN, and `staging.sh down` leaves nothing.
