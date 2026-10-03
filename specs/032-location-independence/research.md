# Research: Location Independence (roadmap step 1a)

All decisions below resolve the design questions raised by the spec. No external
research was needed — the codebase is the source of truth (verified at `61de958`).

---

## Decision 1 — Launch scripts as `-m <module>` (not absolute-path, not `python -c`)

**Decision**: Replace every `[sys.executable, "<name>.py", ...]` with
`[sys.executable, "-u"?, "-m", "<name>", ...]`, preserving `-u` exactly where it exists
today and preserving all other arguments.

**Rationale**:
- `-m` puts the CWD on `sys.path`, so it works in both dev (repo-root CWD) and the
  container (`WORKDIR /app`) — the same precondition `"<name>.py"` already relied on.
- It removes the dependency on the script *staying at the repo root*: the only thing
  that must remain is the module being importable from the CWD. That is the step-1b
  installed-package form, so this change is forward-compatible.
- Minimal diff: one token changes per launch; args and flags are byte-identical.

**Alternatives considered**:
- **Absolute path via `Path(__file__)`** — rejected: re-introduces exactly the
  file-location dependency this step removes.
- **`python -c "from X import main; main()"`** — rejected: rewrites the invocation,
  diverges from the "identical arguments" acceptance scenario, and loses the `-u`
  semantics cleanly.
- **`subprocess` + `runpy`** — rejected: unnecessary indirection.

**Flag-order note**: `-u` is an interpreter option and must precede `-m`
(`python -u -m score ...`). The `-u` flag appears on the four tracker
`-u`-launches (`jobs.py:119/123`, `settings.py:200/215/230`) and is preserved there;
the remaining ten launches have no `-u` and stay without it.

---

## Decision 2 — Guard is an import-time `RuntimeError`, checking `DB_PATH` existence only

**Decision**: In `paths.py`, when `JOB_AGENT_REQUIRE_DB == "1"` and
`os.path.exists(DB_PATH)` is false, raise `RuntimeError` naming the path and both env
vars, *before* any `os.makedirs`. When the env var is unset, keep the current
`os.makedirs(DATA_DIR, exist_ok=True)`.

**Rationale**:
- FR-002 mandates import-time failure; every entry point imports `paths` (directly or
  transitively), so a missing DB can never silently split live data.
- Checking existence only (not integrity, not counts) keeps the guard cheap and side-
  effect-free; integrity is `health_check.py`'s job (spec 031).
- The edge case (scripts with explicit `--db` that don't import `paths`, e.g.
  `backup_db.py`, `fingerprint.py`) is unaffected because the guard is scoped to
  `paths.py` import only.

**Alternatives considered**:
- **Lazy check in each `JobStorage` constructor** — rejected: touches `storage.py`
  (non-goal) and misses entry points that never open the DB.
- **`os.makedirs` then check** — rejected: would create `DATA_DIR` under the guard,
  violating the spec's "guard MUST NOT create `DATA_DIR`" edge case.

---

## Decision 3 — `OUTPUT_DIR` (ephemeral) vs `DATA_DIR` (persistent) are distinct

**Decision**: `paths.OUTPUT_DIR` defaults to `<PROJECT_ROOT>/outputs` and is used by
`notifier.py`, `score.py`, `prepare.py` (the ephemeral JSON/HTML/MD digest output).
`export_jobs.py` keeps writing CSVs under `DATA_DIR` (the volume), not `OUTPUT_DIR`.

**Rationale**:
- Matches US2 acceptance scenario 1: the Reports page "reads the DB from
  `paths.DB_PATH` and writes where it wrote before (`DATA_DIR`)".
- `outputs/` is git- and docker-ignored and not on the volume (ephemeral in prod
  today); `data/*.csv` exports persist on `job_data`. The spec explicitly keeps this
  split (non-goal "No persistence of `outputs/` on the volume").

**Alternatives considered**: routing the CSV export to `OUTPUT_DIR` — rejected: would
move persistent exports off the volume, changing prod behaviour.

---

## Decision 4 — Guard-test patterns and allow-list

**Decision**: `tests/test_location_independence.py` scans runtime `.py` files and fails on:

- **(a)** `__file__`-derived data/output/`.env` paths outside `paths.py`;
- **(b)** CWD-relative `"data/` string literals in runtime code;
- **(c)** `subprocess`/`Popen`/`run` calls passing a `".py"` filename.

Allow-list: `tests/`, `specs/`, `scripts/` diagnostic tools deferred to 1b
(`filter_funnel.py`, `audit_provenance.py`, `audit_work_mode.py`, `diag_freelance.py`),
`scrape.py:21` (scrapers dir — moves with `scrapers/` in 1c), `monitoring_agent.py`
`ROOT`, `scripts/duplicate_report.py` `ROOT`.

**Rationale**:
- (b) is exactly the spec wording — CWD-relative `"data/` literals only. The
  hard-coded `"/app/data` in `scripts/scraper_toggle.py:14` is converted via FR-004
  (T015), not caught by the scan; `backup_db.py`'s `/app/data` default is spec-031
  tooling and out of scope here.
- The allow-list is *scoped to the deferred diagnostic scripts*, not the whole
  `scripts/` directory, because `scripts/scraper_toggle.py` must be converted and must
  be caught if it regresses.

**Alternatives considered**: whole-`scripts/` allow-list — rejected (would exempt
`scraper_toggle.py`, defeating the guard).

---

## Decision 5 — `PROJECT_ROOT` is the sole `__file__` derivation; `scrape.py:21` stays

**Decision**: `paths.py` renames `_ROOT` → `PROJECT_ROOT` and derives it from
`__file__`. The remaining `__file__`-derived path, `scrape.py:21`
(`scrapers_dir = os.path.join(os.path.dirname(__file__), "scrapers")`), is left in
place and allow-listed: it is package-relative, moves together with `scrapers/` in
step 1c, and would fail loudly rather than silently.

**Rationale**: FR-003 mandates `PROJECT_ROOT` as the only `__file__` derivation in
`paths.py`; the spec explicitly defers `scrape.py:21` to 1c. No decision needed beyond
honouring that boundary.

---

## Decision 6 — Compose `environment:` block (precedence over `env_file`)

**Decision**: Add an `environment:` block to `tracker`, `agent`, `email-monitor` in
`docker-compose.yml` setting `JOB_AGENT_DATA_DIR=/app/data` and `JOB_AGENT_REQUIRE_DB=1`.

**Rationale**: `environment:` takes precedence over `env_file`, so a stray
`JOB_AGENT_DATA_DIR` in `.env` on verva cannot override the explicit prod location. The
spec's assumption ("verify no conflicting `JOB_AGENT_DATA_DIR` in verva's `.env`") is
checked before deploy, but precedence makes it moot.
