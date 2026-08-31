# 028 — Configurable Freshness Window (unify the hardcoded 30-day cutoffs)

## Input
Replace the two independent hardcoded "30 days" freshness cutoffs and the
dormant `JobFilter.date_from` with a single configurable value, `freshness_days`
(default 30), stored in the existing `config` table and editable in Settings
alongside the DB-purge widget. As a direct consequence, the Joinup scraper's
date-cutoff early-stop (spec 027 FR-004) starts working in the broad path,
dropping its fetch from all 112 pages to ~3.

## Motivation
Today the freshness window is expressed in three disconnected places:
- `filters.py:11` — `cutoff = date.today() - timedelta(days=30)`, hardcoded,
  applied post-fetch; comment says "always applied, not configurable". Drops
  jobs with posted_date older than 30d and drops undated jobs.
- `storage.py:1356` — a query window `posted_date >= date('now','-30 days')
  OR posted_date IS NULL`, hardcoded; KEEPS undated jobs.
- `models.py:104` — `JobFilter.date_from: Optional[date] = None`, the field the
  Joinup early-stop reads, but `_run_broad_scrape` never sets it, so the
  early-stop is inert and every broad run pages all ~112 Joinup pages to keep
  the ~28 fresh jobs.
This makes "30 days" un-tunable, duplicated (drift risk), and leaves the
pagination guard dead. Meanwhile DB purge IS already configurable
(`purge_retention_days`, Settings, user-set to 10) — the freshness window should
be equally first-class.

## Relationship to DB purge (document, do NOT change purge)
The two windows measure DIFFERENT dates and must not be conflated:
- Freshness (`freshness_days`, this spec) keys off `posted_date` (publication
  date) — governs ADMISSION: which jobs enter the list / are shown.
- Purge (`purge_retention_days`, unchanged) keys off `first_seen` (scrape date)
  and only untouched `new` jobs — governs SURVIVAL: how long an un-actioned job
  stays before it is swept.
If `freshness_days` > `purge_retention_days`, untouched jobs still vanish at the
purge horizon. This is expected; the Settings help text MUST state it.

## Context (verified against live source)
- Config plumbing exists: `config` table (storage.py:253), `get_config`
  (storage.py:2319), `set_config` (storage.py:2324). NO schema change.
- Precedent pattern: `db.get_config("purge_retention_days", default="30")`
  (main.py:38) + widget `_render_purge` (tracker_views/settings.py:341,
  st.number_input min 7 / max 180). Mirror this exactly for freshness.
- `_run_broad_scrape(db: JobStorage, profile)` (scrape.py:322) already has `db`
  — it can read config directly. `_run_monitored_only(db, profile)`
  (scrape.py:184) likewise.
- `scrape.py` currently imports `from datetime import datetime, timezone`
  (scrape.py:8) — add `date, timedelta`.
- `JobFilter.date_from` (models.py:104) is the carrier field; already present.
- Joinup early-stop already reads `date_from` (spec 027 FR-004) and has a test
  `test_date_cutoff_stops_pagination`. No scraper code change needed here.

## Design — one value, threaded through three call sites
`JobFilter.date_from` becomes THE single carrier of the freshness cutoff,
sourced from the `freshness_days` config, honored everywhere.

## Constitutional guardrails (MUST hold)
- §II Two paths: the CODE that implements the knob is a code-path change
  (dev → git → deploy). The VALUE (`freshness_days`) is config prose, editable
  in Settings with no deploy. Clean separation, exactly like purge.
- §IX Pipeline works without config: every read of `freshness_days` MUST fall
  back to 30 when the key is absent/malformed. Every consumer MUST keep a 30-day
  fallback so the pipeline runs with no config and no profile.
- §V Surgical: touch only `filters.py`, `scrape.py`, `storage.py`,
  `tracker_views/settings.py`. Do NOT touch `models.py` (date_from exists),
  purge logic, scoring, scrapers, or the schema. State non-goals explicitly.
- §IV Deterministic structure: `freshness_days` is a deterministic integer from
  config, never LLM-derived.
- §VI Empirical validation: with `freshness_days=30`, filtering output MUST be
  identical to today (regression); validate against the FELFEL case.

## Functional requirements
- FR-001: Introduce config key `freshness_days` (string int, default "30"),
  read via existing get_config / written via set_config. No schema change.
- FR-002: Add a helper that returns the freshness window as an int, coercing
  bad/absent values to 30 (single place; consumers call it).
- FR-003: In scrape.py, wherever a `JobFilter` is built that will be passed to
  `JobFilterEngine.apply` (broad path at scrape.py:325, and the monitored path
  if it filters by date), set
  `date_from = date.today() - timedelta(days=freshness_days)`.
  Add `date, timedelta` to the datetime import.
- FR-004: In `filters.py`, replace the hardcoded cutoff with
  `cutoff = job_filter.date_from or (date.today() - timedelta(days=30))`.
  Undated-job handling UNCHANGED (still dropped).
- FR-005: In `storage.py:1356`, replace the hardcoded `-30 days` with the
  configured `freshness_days` (cast to int, interpolate the integer safely —
  never raw string into SQL). Undated-job handling UNCHANGED (still kept via
  `OR posted_date IS NULL`).
- FR-006: Add `_render_freshness(db)` to tracker_views/settings.py mirroring
  `_render_purge` — st.number_input (min 7, max 180, default 30), writing
  `freshness_days` via set_config on change, with help text stating (a) it takes
  effect on the next pipeline run and (b) the purge relationship from the
  section above.
- FR-007: The Joinup broad-path early-stop MUST become active as a consequence
  of FR-003 (no scraper edit) — verified, not implemented, here.

## Clarifications

### Session 2026-08-31

- Q: Undated-job handling — keep each layer's current behavior (filters.py drops
  undated, storage.py:1356 keeps undated) to preserve the byte-identical
  regression? → A: Yes, keep current per-layer behavior unchanged; only the
  integer becomes configurable this spec. The cross-layer inconsistency (one
  drops undated, the other keeps them) is logged as a separate future item, out
  of scope here.
- Q: Scope of `date_from`-setting — broad path only, or broad + monitored? → A:
  Broad path only. Confirmed against source: `JobFilterEngine.apply` is called
  solely at scrape.py:351 (broad path), and `_run_monitored_only` builds no
  `JobFilter` and does no date filtering — so only the broad path's `JobFilter`
  (scrape.py:325) gets `date_from`. Both paths keep today's freshness behavior.
- Q: Widget bounds and cross-window warning — min 7 / max 180 / default 30, plus
  a soft help-text note (no hard validation) when freshness_days > purge? → A:
  Yes — min 7 / max 180 / default 30; soft help-text note only, no hard
  validation.
- Q: Global config key vs per-profile — global `freshness_days` mirroring purge?
  → A: Global `freshness_days`, mirroring purge (simplest for the unified single
  profile; a future per-profile override stays possible).

## Acceptance scenarios
- Given `freshness_days` absent (default 30), When broad scrape + filter run,
  Then filtering output is IDENTICAL to pre-change, and the broad JobFilter's
  date_from == today − 30d. (Regression invariant.)
- Given the Joinup board and default 30, Then the broad run fetches ~3 pages
  instead of 112, and the surfaced jobs match the pre-change set within 30d.
  (The one intended behavioral change.)
- Given `freshness_days=10`, Then filters.py drops posted_date older than 10d,
  the storage query window uses 10d, and Joinup's early-stop halts at the 10d
  boundary.
- Given a malformed/absent `freshness_days`, Then every consumer falls back to
  30 without error (§IX).
- Given the Settings widget is changed and saved, Then set_config writes the new
  value and it applies on the next run (no deploy).
- FELFEL regression case still passes; spec 027 `test_date_cutoff_stops_
  pagination` still passes.

## Non-goals
- No change to purge logic or `purge_retention_days`.
- No change to scoring, to scrapers (Joinup benefits passively), or to the DB
  schema (config table already exists).
- No change to undated-job handling policy — only the day-count is configurable.
- No per-profile freshness window (global config only).
- `freshness_days` is never LLM-derived.

## Success criteria
- A single `freshness_days` config value is the source of the freshness window
  across filters.py, the scrape-path JobFilter `date_from`, and storage.py:1356.
- With default 30, filtering output is byte-identical to today; Joinup broad
  fetch drops from 112 pages to ~3.
- The Settings widget edits the value with no deploy, effective next run.
- `python -m pytest tests/` passes, including the Joinup pagination test and the
  storage suite; the FELFEL case is unchanged.
