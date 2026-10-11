# Quickstart — Single DB Access Path (spec 037)

Validation guide for the refactor. No behaviour change is expected; these checks prove the guard
tests work and that the tracker + pipeline behave exactly as before. See
[`data-model.md`](data-model.md) for the method surface and [`inventory.md`](inventory.md) for the
verbatim SQL.

## Prerequisites

- Fresh venv with dev extras: `pip install -e ".[dev]"`.
- A DB present at `data/jobs.db` (staging uses a restored prod backup — see below).
- Repo checked out on the spec branch `037-single-db-access-path`.

## Local validation (before staging)

```bash
# 1. Unit tests — one per new JobStorage method, in-memory DB
python -m pytest tests/test_storage.py -q

# 2. Guard tests — must be GREEN here, and must FAIL on each reintroduced form
python -m pytest tests/test_guard_db_access.py tests/test_guard_llm_provider.py -q

# 3. Full suite
python -m pytest tests/ -q

# 4. Provider residue swept from runtime code
grep -riE "groq|openai|anthropic|deepseek|ollama" core/ tracker_views/ tracker.py .env.example docker-compose.yml \
  | grep -v core/llm.py   # → expect no output

# 5. Raw SQL swept from runtime code (only core/storage.py may use _conn/sqlite3)
grep -rnE "_conn\(|sqlite3\.connect|import sqlite3" core/ tracker_views/ tracker.py \
  | grep -v core/storage.py   # → expect no output

# 6. Touched pipeline entry points still run
python -m core.score --extract          # completes normally
python -m core.scrape --monitored-only --no-score   # one cheap real scrape (no --source flag exists)
python -m core.prepare --help           # or the preference-report/context-tuner entry points touched
```

The two guard tests each carry a **negative fixture** — a self-check asserting that
`db._conn()`, `sqlite3.connect`, `import sqlite3`, and a provider name are each detected — so a
green run proves the guard is not vacuous (FR-005/FR-009).

## Staging validation (on verva, exact SHA)

```bash
# Stage the exact commit (never the branch name)
ssh verva 'cd /opt/job-agent && ./scripts/staging.sh up <full-sha>'
```

On the staging tracker (`:8502`, serving a copy of the latest backup), confirm each US3-1 action:

- [ ] Dashboard overview counts render (6 tiles).
- [ ] Settings overview counts + contacts-by-role render.
- [ ] Settings company-monitoring toggle (the two former `:488`/`:509` actions).
- [ ] Job detail page renders (both former reads: status + discovered contacts).
- [ ] Contact detail edit actions (mark verified / unverified / not-current / notes).
- [ ] Contacts page bulk-verify action.
- [ ] Company detail notes save.
- [ ] The `forms.py` add-company dialog saves notes.

Then parity, then tear down:

```bash
# Parity — fingerprint equals the pre-change baseline (byte-identical)
ssh verva 'cd /opt/job-agent && ./scripts/staging.sh run python -m scripts.fingerprint --db /app/data/jobs.db --as-of <date> --ref <full-sha> --out /tmp/after.json'
python -m scripts.compare_fingerprints <baseline.json> /tmp/after.json   # → "fingerprints identical"

ssh verva 'cd /opt/job-agent && ./scripts/staging.sh down'
```

## Deploy validation (on verva, after "go")

```bash
ssh verva 'cd /opt/job-agent && ./scripts/deploy.sh'
ssh verva 'tailscale serve --bg --tcp=8501 tcp://127.0.0.1:8501'

# SHA check — deployed HEAD equals the staged SHA
ssh verva 'git -C /opt/job-agent rev-parse HEAD'

# Health — tracker responds; latest full run succeeded and is < 26 h
ssh verva 'docker exec job-tracker python -m scripts.health_check --url http://localhost:8501 --db /app/data/jobs.db'

# email-monitor removed — no service in compose, no orphan container, image pruned
ssh verva 'docker compose -f /opt/job-agent/docker-compose.yml config --services'   # → tracker, agent (no email-monitor)
ssh verva 'docker rm -f job-email-monitor 2>/dev/null; docker image rm job-agent-email-monitor 2>/dev/null; echo done'

# Next scheduled cron run (07:00/11:00/16:00) → status = success
```

The nightly `full` run must still report `status = success` (or `scraped`) the next morning — this
confirms the scoring-loop queries (`delete_scores_for_profile`, `get_jobs_to_score`) moved with
identical behaviour.
