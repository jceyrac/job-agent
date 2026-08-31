# Implementation Plan: Configurable Freshness Window

**Branch**: `main` | **Date**: 2026-08-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/028-configurable-freshness-window/spec.md`

## Summary

Unify the three disconnected "30 days" freshness expressions behind one
config value `freshness_days` (default 30, global, in the existing `config`
table). `JobFilter.date_from` becomes the single carrier, sourced from the
config, honored in `filters.py` (admission filter), `scrape.py` (the broad
`JobFilter`), and `storage.py:1356` (scoring-eligibility query window). A
Settings widget mirrors the existing purge widget. Side effect: the Joinup
broad-path date-cutoff early-stop (spec 027 FR-004) starts firing, cutting its
fetch from 112 pages to ~3 — verified, not implemented, here.

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: None new. Stdlib `datetime`; existing Streamlit
(`st.number_input`) and SQLite.

**Storage**: `config` table already exists (key/value). `get_config`/`set_config`
already exist (storage.py:2319/2324). **No schema change.**

**Testing**: `python -m pytest tests/` (208 tests today, incl. the 162-test
storage suite). New `tests/test_freshness.py`; regression against FELFEL
(`tests/test_title_gate.py`), the storage suite, and spec 027
`tests/test_joinup.py::test_date_cutoff_stops_pagination`.

**Target Platform**: DEV (Mac) + prod server; the config value is prose (no
deploy to change it).

**Performance Goals**: Joinup broad fetch 112 → ~3 pages (the one intended
behavioral change at default 30).

**Constraints**: Byte-identical filtering output at `freshness_days=30`
(regression invariant). No purge change, no `models.py` change, no scraper
change, no schema change.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Wide net, single filter point | ✅ PASS | `freshness_days` is a *date* admission bound, not a relevance filter; the scorer stays the sole fit judge. The Joinup early-stop is a freshness/efficiency guard, exactly as spec 027 already permits. |
| II. Two improvement paths | ✅ PASS | The **code** that implements the knob (this diff) goes dev→git→deploy. The **value** `freshness_days` is config prose, editable in Settings with no deploy — the same clean split as `purge_retention_days`. |
| III. Unified profile | ✅ PASS | Global key (mirrors purge); no profile multiplication. `date_from` stays a `JobFilter` field, not a profile field. |
| IV. Deterministic structure | ✅ PASS | `freshness_days` is a deterministic int from config; never LLM-derived. |
| V. Surgical modification | ✅ PASS | Touch only `filters.py`, `scrape.py`, `storage.py`, `tracker_views/settings.py` (+ a new test file). `models.py`, purge, scoring, scrapers, schema untouched. `storage.py`/`scrape.py` are on the stable-core list — justified in Complexity Tracking below. |
| VI. Empirical validation | ✅ PASS | With `freshness_days=30`, filters.py and storage.py:1356 emit byte-identical output (each layer keeps its own date mechanism — `date.today()` in filters, `date('now')` in SQL — only the "30" becomes a parameter). FELFEL (title gate, untouched) and the storage suite must still pass. |
| VII. Security first | ✅ PASS | No new egress; the SQL parameter is interpolated as a coerced `int` (never raw config string into SQL), so no injection surface. |
| VIII. Acquisition models | ✅ PASS | No scraper change. `date_from` is set only in the broad path (the sole `JobFilterEngine.apply` caller); the monitored path still fetches unfiltered. |
| IX. Scoring/pipeline optional | ✅ PASS | Every `freshness_days` read falls back to 30 on absent/malformed value: `get_freshness_days()` coerces to 30; `filters.py` falls back to `today − 30d` when `date_from` is None. Pipeline runs with no config and no profile. |

**Result**: No violations. Complexity Tracking documents the two stable-core
touches (storage.py, scrape.py) as task-scoped.

## Project Structure

### Documentation (this feature)

```text
specs/028-configurable-freshness-window/
├── spec.md
├── plan.md              # this file
├── research.md          # call-site map + date-mechanism audit
├── data-model.md        # config key + JobFilter.date_from carrier
└── quickstart.md        # validation commands
```

### Source Code (repository root)

```text
storage.py                 # EDIT — get_freshness_days() helper + query window (1356)
scrape.py                  # EDIT — import date/timedelta + set date_from (325)
filters.py                 # EDIT — cutoff = job_filter.date_from or today−30d (11)
tracker_views/settings.py  # EDIT — _render_freshness() widget + render() wiring
tests/test_freshness.py    # NEW  — helper coercion + filters date_from unit tests
```

## Implementation design

### Helper (FR-002) — `storage.py`, after `set_config`

```python
def get_freshness_days(self) -> int:
    try:
        return int(self.get_config("freshness_days", default="30") or "30")
    except (TypeError, ValueError):
        return 30
```

Single place; consumers are `scrape.py`, `storage.py:1356`, `settings.py`.

### FR-003 — `scrape.py` `_run_broad_scrape`

- Import: `from datetime import datetime, timezone` → add `date, timedelta`.
- `JobFilter(...)` gains `date_from=date.today() - timedelta(days=db.get_freshness_days())`.

Only the broad path (scrape.py:325) builds a `JobFilter` that reaches
`JobFilterEngine.apply` (scrape.py:351). `_run_monitored_only` builds none and
does no date filtering — untouched.

### FR-004 — `filters.py:11`

```python
cutoff = job_filter.date_from or (date.today() - timedelta(days=30))
```

Undated handling unchanged (still dropped). `date.today()` mechanism unchanged.

### FR-005 — `storage.py:1356`

```python
days = self.get_freshness_days()
clauses.append(
    f"(j.posted_date >= date('now', '-{days} days') OR j.posted_date IS NULL OR j.posted_date = '')"
)
```

Undated handling unchanged (kept). `date('now')` mechanism unchanged; `days` is
a coerced int (safe interpolation).

### FR-006 — `tracker_views/settings.py`

`_render_freshness(db)` mirroring `_render_purge`: `st.number_input`
(min 7 / max 180 / value=`db.get_freshness_days()`), Save button → `set_config`,
help text stating (a) next-run effect, (b) the freshness-vs-purge relationship.
Wire into `render()` with `st.divider()`.

### FR-007 — Joinup early-stop (verified, not implemented)

At default 30, `date_from` is now truthy in the broad path, so the Joinup
scraper's existing `page_all_old` early-stop fires. No scraper edit.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `storage.py` is on the stable-core ("never modify") list | FR-005 requires replacing the hardcoded `-30 days` in `get_jobs_for_scoring` (1356), and FR-002's helper lives next to the config store. The freshness window *is* a storage concern. | A `filters.py`-only change would leave the scoring-eligibility window hardcoded, re-splitting the value the spec exists to unify. |
| `scrape.py` is on the stable-core ("never modify") list | FR-003 requires setting `date_from` on the broad `JobFilter` (325) — the single point where the freshness cutoff is threaded into `JobFilterEngine.apply` and, via it, into the Joinup early-stop. | Passing `date_from` from anywhere else would require moving the `JobFilter` construction, a larger blast radius. |

Both are minimal, behavior-preserving-at-30 diffs; neither alters schema,
scoring, or the monitored path.
