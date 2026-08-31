# Tasks: Configurable Freshness Window

**Input**: Design documents from `specs/028-configurable-freshness-window/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `quickstart.md`

**Tests**: Required — new `tests/test_freshness.py` locks the §IX fallback and the
FR-004 date_from honoring. Existing suites (storage, title-gate/FELFEL, Joinup
pagination) are the regression gate. No network I/O in any new test.

**Organization**: This spec has functional requirements (FR-001…FR-007), not user
stories. Tasks are grouped by delivery phase mirroring the plan; FR mapping is
noted inline. The two stable-core touches (storage.py, scrape.py) are justified
in the plan's Complexity Tracking.

## Phase 1: Core threading (helper + filters.py + scrape.py + storage.py)

**Goal**: One `freshness_days` value flows through the three call sites. At
default 30 the output is byte-identical to today.

**Independent Test**: `python -c "from storage import JobStorage; print(JobStorage.__dict__.get('get_freshness_days'))"`
prints the method; then `python -m pytest tests/test_freshness.py -q` passes once
Phase 3 exists. No schema, purge, models.py, or scraper change.

- [X] T001 [FR-002] Add `JobStorage.get_freshness_days(self) -> int` in `storage.py`, placed after `set_config` (~line 2330): `try: return int(self.get_config("freshness_days", default="30") or "30")` `except (TypeError, ValueError): return 30`
- [X] T002 [FR-004] In `filters.py` (~line 11), replace the hardcoded cutoff with `cutoff = job_filter.date_from or (date.today() - timedelta(days=30))`; keep undated-job handling (still dropped) and the `date.today()` mechanism unchanged
- [X] T003 [FR-003] In `scrape.py`, change line 8 import to `from datetime import date, datetime, timedelta, timezone`, and in `_run_broad_scrape` (~line 325) add `date_from=date.today() - timedelta(days=db.get_freshness_days())` to the broad `JobFilter(...)` constructor (leave `_run_monitored_only` untouched)
- [X] T004 [FR-005] In `storage.py` `get_jobs_for_scoring` (~line 1356), replace the literal `-30 days` in the query window with the configured value: `days = self.get_freshness_days()` then `f"(j.posted_date >= date('now', '-{days} days') OR j.posted_date IS NULL OR j.posted_date = '')"` — preserve the `OR ... IS NULL OR ... = ''` undated-keep clause, interpolate the coerced int (never raw config string)

**Checkpoint (PAUSE)**: core threading complete — review before the Settings widget.

---

## Phase 2: Settings widget

**Goal**: Edit `freshness_days` in Settings with no deploy, mirroring the purge
widget.

**Independent Test**: `streamlit run tracker.py` → Settings → Freshness Window →
set a value → Save → `sqlite3 data/jobs.db "SELECT value FROM config WHERE key='freshness_days'"`.

- [X] T005 [FR-006] Add `_render_freshness(db)` to `tracker_views/settings.py`, mirroring `_render_purge` (~line 341): `st.subheader` + `st.number_input("Freshness window (days)", min_value=7, max_value=180, value=db.get_freshness_days(), help=...)` + Save button → `db.set_config("freshness_days", str(new))`; help text states (a) it takes effect on the next pipeline run and (b) the freshness-vs-purge relationship (freshness keys off `posted_date`/admission, purge keys off `first_seen`/survival — if freshness > purge, untouched jobs still vanish at the purge horizon)
- [X] T006 Wire `_render_freshness(db)` into `render()` in `tracker_views/settings.py` with a `st.divider()` (mirroring the purge placement)

**Checkpoint (PAUSE)**: Settings widget complete — review before tests.

---

## Phase 3: Offline tests (tests/test_freshness.py)

**Goal**: Lock the §IX 30-day fallback and the FR-004 `date_from` honoring.

**Independent Test**: `python -m pytest tests/test_freshness.py -q` → all pass, zero network I/O.

- [X] T007 Create `tests/test_freshness.py` with: (a) `get_freshness_days()` returns 30 when `freshness_days` absent, coerces a valid string to int, and returns 30 on malformed values (`"abc"`, `""`, `"-5"`) — via an in-memory `JobStorage`; (b) `JobFilterEngine.apply` drops jobs with `posted_date` older than `job_filter.date_from` and keeps fresher ones when `date_from` is set; (c) `JobFilterEngine.apply` falls back to ~30 days when `date_from` is None

---

## Phase 4: Validation & Polish

**Goal**: Confirm byte-identical-at-30, Joinup early-stop, and no regressions.

- [X] T008 Run `python -m pytest tests/ -q` → full suite green (incl. the 162-test storage suite and `tests/test_title_gate.py` FELFEL case)
- [X] T009 Run `python -m pytest tests/test_joinup.py::test_date_cutoff_stops_pagination -q` and confirm it still passes; confirm the Joinup broad fetch pages ~3 pages instead of ~112 (via `python scrape.py`, grep `[Joinup]`), and that `get_freshness_days()` defaults to 30 with no config

---

## Dependencies & Execution Order

- **Phase 1** (core threading): no dependencies — start immediately. T001 must land before T003/T004 (they call `get_freshness_days`).
- **Phase 2** (Settings widget): depends on T001 (calls `get_freshness_days`); independent of T002–T004.
- **Phase 3** (tests): depends on T001–T002 (imports `JobStorage` + `JobFilterEngine`).
- **Phase 4** (validation): depends on Phases 1–3.

### Parallel Opportunities

- T002 (filters.py) and T004 (storage.py window) touch different files and are
  independent of T003 (scrape.py) once T001 lands — but all three are small;
  implement in one pass within Phase 1.
- Phase 2 (settings.py) and Phase 3 (test_freshness.py) touch different files and
  can proceed in parallel after Phase 1.

### Pause Points (per the implement workflow)

1. **After Phase 1** (core threading) — review the three call-site edits.
2. **After Phase 2** (Settings widget) — review `_render_freshness`.

## Notes

- Every task names an exact file path; no other module is touched.
- No new dependencies, no schema change, no purge change, no models.py change.
- The Joinup early-stop (FR-007) is a *consequence* of T003 — verified in T009, not implemented as a scraper edit.
- §IX fallback is enforced in T001 (helper) and asserted in T007.
