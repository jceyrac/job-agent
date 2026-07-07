# SPEC 020 — Scoring Pipeline Integrity (Profile Sync, Location Filter, UI Transparency)

> Spec for Claude Code (Dev). Read `storage.py`, `profiles.py`, `scorer.py`,
> `score.py`, `tracker_views/jobs.py`, `tracker_views/settings.py` before starting.
> This spec was authored after live investigation on verva (see Context below).
> Some pieces (job_helpers.py, score.py's exact extraction/scoring ordering)
> were NOT fully inspected during spec-writing — see "Investigation required
> before implementation".

---

## Context — what triggered this spec

On Live, clicking "Run scoring" against profile `unified_jc` did nothing
useful: the UI showed 596 "unscored" jobs, but `score.py` exited in ~0s with
0 candidates. Root-caused via direct DB queries on verva:

- `storage.py::get_jobs_for_scoring()` applies a SQL-level pre-filter,
  including `exclude_location_contains` substring matching
  (`LOWER(location) NOT LIKE '%usa%'` etc.) *before* any job reaches Tier-0
  or the LLM.
- The DB-stored profile had `"usa"` (no padding) in
  `exclude_location_contains`, which matches `"Lausanne"` as a substring —
  silently excluding the last 2 legitimate Swiss candidates and collapsing
  the pool from 97 → 0.
- Investigating *why* the DB had `"usa"` while `profiles.py` code had
  `" usa "` (space-padded, to avoid exactly this false positive) revealed
  the deeper issue: **`profiles.py`'s `UNIFIED_JC` object is only ever
  consulted once, to seed the DB row the first time it's created.** Once a
  `search_profiles` row exists, `load_active_profile()` / `_resolve_profile()`
  read exclusively from the DB. Editing `profiles.py` after that point has
  no runtime effect unless someone manually re-upserts. This is how the two
  copies silently diverged.
- Separately, confirmed against `scorer.py::evaluate_for_profile()` that
  **structured, correct geography filtering already exists** in Tier-0
  (`_geography_for_mode()`, using `job.company_country` / `job.geo_zone`
  against `profile.work_mode_geography`, introduced in Spec 016). The SQL
  `location_contains` / `exclude_location_contains` clauses in
  `get_jobs_for_scoring()` are a redundant, cruder, earlier-stage duplicate
  of logic that already works correctly downstream — not a gap that needs a
  new implementation.
- The "Unscored" metric shown on the Jobs and Settings pages is a raw
  `total_jobs - scored_distinct` count that ignores all pre-filtering
  (status, freshness, title/location), so it never matched what a "Run
  scoring" click would actually attempt.
- Reviewed `tracker_views/jobs.py`'s top control bar (scrape/score/re-extract
  buttons): the button-launch mechanism itself (Popen + session_state polling)
  is sound. The "stuck at 0m0s" appearance was the `get_last_run()` banner
  correctly reporting a real ~0s run, not a rendering bug. Two minor,
  unrelated hardening gaps were found in passing (see Goal 4).

Confirmed against `.specify/memory/constitution.md` and
`specs/014-pre-filter-exclusion-cleanup/spec.md`: the constitution's
sanctioned exception to "scraper = wide net" is a **title/family gate**,
applied at scrape time for broad sources and at score time (pre-LLM-call)
for monitored-company sources — never a location gate. There is no spec or
constitutional basis for the current SQL location exclusion; it's an
undocumented third filtering layer.

---

## Goals

1. **Remove the profile-sync leak.** `profiles.py` becomes a pure one-time
   bootstrap seed, with zero remaining code paths that can write into an
   already-existing DB profile row.
2. **Remove the SQL-layer location pre-filter** from
   `get_jobs_for_scoring()`. Tier-0 in `scorer.py` already does this
   correctly on structured, extracted fields — this is a deletion, not a
   redesign.
3. **Fix the "Unscored" UI metric** so it reflects the jobs a scoring run
   will actually attempt, not a naive DB delta.
4. **Minor hardening**: background-run success/error messages are currently
   likely invisible (flashed away by an immediate `st.rerun()`); the
   "Re-extract" button has no timeout handling and can crash on a long run.
5. **Provide a safe Live→Dev DB snapshot script**, since this spec needs to
   be tested against real data without ever developing directly on Live.

## Non-goals

- No change to Tier-0 geography logic itself (`_geography_for_mode`,
  `evaluate_for_profile`) — it is already correct.
- No change to the scrape-time / pre-LLM-call title gate behavior
  established in Spec 014 — title filtering is a separate, sanctioned
  mechanism and out of scope here, beyond confirming no duplication (see
  Investigation section).
- No new profile reconciliation system, startup diffing, or "smart sync."
  The user has explicitly chosen: DB is sole runtime source of truth after
  first seed; `profiles.py` values only apply via the Settings UI or a
  deliberate re-seed, never silently.
- No UI redesign beyond the specific "Unscored" count fix.
- No changes to `job_helpers.py` (per-job action bar / `_run_score`) — this
  file could not be read during spec-writing (local Filesystem MCP timed
  out twice). Do not assume it's clean; see Investigation section.

---

## Investigation required before implementation

Claude Code should confirm these before or during implementation, since they
were not fully verified while writing this spec:

1. **Read `job_helpers.py`** (`_render_action_bar`, `_run_score`,
   `_derive_state`). Confirm there's no analogous per-job scoring bug (e.g.
   a single-job "Score this job" action that bypasses or duplicates the
   pre-filter logic being removed here).
2. **Confirm extraction/scoring ordering in `score.py`.** `evaluate_for_profile()`'s
   Tier-0 geography check depends on `job.company_country` / `job.geo_zone`
   being populated by `extract_job_fields()`. Confirm whether `score.py`
   extracts each job inline before evaluating it, or whether extraction
   (`get_jobs_for_extraction()`) and scoring are decoupled passes that could
   run out of order. If a job reaches Tier-0 unextracted, `company_country`/
   `geo_zone` default to `"unknown"`, which Tier-0 already treats
   permissively (falls through to LLM evaluation) — confirm this is
   acceptable, or add a guard if not.
3. **Grep for other consumers of `pre_filter["exclude_location_contains"]`
   and `pre_filter["location_contains"]`** before removing them from
   `get_jobs_for_scoring()`'s call site, to confirm nothing else (e.g. a
   scraper-side query builder) depends on these specific dict keys staying
   populated. If another consumer exists, keep the field but stop passing it
   into `get_jobs_for_scoring()`.

---

## Changes

### 1. Profile sync — remove the `scoring_context` code fallback

**File: `profiles.py`** (wherever `load_active_profile()` / `_resolve_profile()`
live)

- Remove the fallback branch that backfills `scoring_context` (or any other
  field) from `ALL_PROFILES`/`UNIFIED_JC` into the DB when the DB value is
  empty. Once a profile row exists in `search_profiles`, nothing in code
  should ever write into it implicitly.
- Add a clear docstring/comment directly above `UNIFIED_JC` (and any other
  entries in `ALL_PROFILES`) stating: *this object is a one-time bootstrap
  seed, consulted exactly once when a profile_id has no existing DB row.
  Once seeded, all further edits go through the Settings UI (Profile Editor
  in `tracker_views/settings.py`) or a direct DB write — editing this file
  after first run has no effect on a running instance.*
- No schema change needed; this is a pure code-path removal.

### 2. Remove the SQL-layer location pre-filter

**File: `storage.py::get_jobs_for_scoring()`**

- Remove the `location_contains` and `exclude_location_contains` clauses
  and their parameter-building blocks entirely.
- Leave `title_contains` / `exclude_title_contains` in place for now — title
  gating is the constitution's sanctioned exception (Spec 014) — but per
  Investigation item 3, confirm this SQL-level title clause isn't a
  duplicate of the scrape-time gate that's now redundant too; if it is,
  flag it for a future cleanup rather than removing it in this pass (avoid
  scope creep).
- **File: `tracker_views/settings.py`** — the Profile Editor's "Advanced
  scrape inputs" expander has a field bound to
  `pre_filter["exclude_location_contains"]`. Once nothing reads this key
  for scoring purposes, either remove the field from the form or relabel it
  clearly (e.g. "legacy — no longer used by scoring") depending on the
  Investigation item 3 outcome.
- Effect: jobs are no longer excluded from ever reaching scoring based on a
  fragile substring match against free-text location strings. The LLM /
  Tier-0 pipeline becomes the sole authority on geographic relevance,
  consistent with the constitution's "scraper = wide net, scorer = all
  filtering" principle.

### 3. UI transparency — fix the "Unscored" count

**Files: `tracker_views/jobs.py::_render_controls_bar()`,
`tracker_views/settings.py::_render_run_controls()`**

- Current: `unscored = total_jobs - scored_distinct` — a raw count ignoring
  status (rejected/archived/expired), freshness, and title pre-filter.
- Change: compute this using the same candidate set
  `get_jobs_for_scoring()` actually returns for the active profile, e.g.
  `unscored = len(db.get_jobs_for_scoring(active_profile_id))`. At current
  data volumes (low hundreds of rows) this is cheap enough to call directly
  on each render; if it becomes a perf concern later, add a dedicated
  `count_jobs_for_scoring()` that mirrors the same WHERE clause without
  fetching full rows.
- Apply the same fix in both files — they currently duplicate this metric
  independently.

### 4. Minor hardening

**Files: `tracker_views/jobs.py`, `tracker_views/settings.py`**

- **Message flash**: both files currently do `st.success(...)` /
  `st.error(...)` immediately followed by `st.cache_data.clear()` +
  `st.rerun()` in the same branch, which likely prevents the user from
  actually seeing the message. Restructure so the completion message
  survives one full render — e.g. store it in `st.session_state` on
  completion, clear the process state, `st.rerun()` once, then render and
  clear the stored message on the following pass (so it shows exactly
  once).
- **Re-extract timeout**: the "🔍 Re-extract" button's
  `subprocess.run([...], timeout=600)` call has no exception handling.
  Wrap it in `try/except subprocess.TimeoutExpired` and show
  `st.error("Re-extract timed out after 10 minutes — it may still be running in the background.")`
  instead of crashing the page. Apply in both `tracker_views/jobs.py` and
  `tracker_views/settings.py::_render_stats_actions()`.

### 5. Live → Dev DB snapshot script

**New file: `scripts/sync_live_db.sh`** (adjust path/name to project
convention if a `scripts/` dir doesn't exist yet)

- Purpose: pull a read-only copy of the Live DB over Tailscale into Dev, so
  this spec (and future work) can be tested against real data without ever
  developing against Live directly.
- Behavior:
  1. Use `scp` (or `rsync -avz`) to copy the live SQLite file from verva
     (`100.74.139.28`, Tailscale) to a local snapshot path, e.g.
     `data/jobs_live_snapshot.db`. Confirm the exact remote path against the
     Docker Compose volume mapping on verva before hardcoding it.
  2. Never write directly to `data/jobs.db` — always land the snapshot at
     a separate filename, and print the manual swap-in steps (backup
     current dev DB, then copy the snapshot over `data/jobs.db`) rather than
     doing the swap automatically, so a bad pull can't silently destroy
     dev data.
  3. Print a one-line reminder that this is one-way (Live → Dev only) and
     that `storage.py`'s migrations are additive/idempotent, so opening the
     snapshot with current Dev code is safe — the reverse direction must
     never be attempted this way.
- This closes out the "Live DB sync script" item already tracked on Trello
  and is a prerequisite for testing the rest of this spec.

---

## Acceptance criteria

- [ ] `load_active_profile()` / `_resolve_profile()` contain no code path
      that writes into an existing DB profile row from `ALL_PROFILES`
- [ ] `get_jobs_for_scoring()` no longer builds or applies
      `location_contains` / `exclude_location_contains` SQL clauses
- [ ] The FELFEL regression job
      (`https://ch.indeed.com/viewjob?jk=f7e16c8a6a3d7af7`) still scores
      6–8 for `ch_hybrid` and 2–4 for `web3_remote` — unaffected, since this
      spec targets candidate selection, not scoring logic
- [ ] On the synced Dev snapshot, running scoring for `unified_jc` picks up
      the previously-blocked candidates, and the 2 Lausanne jobs identified
      in the original investigation reach Tier-0/LLM evaluation instead of
      being excluded beforehand
- [ ] The "Unscored" metric on both the Jobs page and Settings page matches
      `len(get_jobs_for_scoring(...))` for the active profile
- [ ] The Re-extract button no longer crashes on a run exceeding 600s;
      shows a clear error instead
- [ ] Background scrape/score completion messages are visibly shown to the
      user, not instantly replaced by a rerun
- [ ] `scripts/sync_live_db.sh` successfully pulls a Live snapshot into
      `data/jobs_live_snapshot.db` over Tailscale, and swapping it into
      `data/jobs.db` (manually, per the printed instructions) works without
      migration errors

---

## Testing plan (Dev)

1. Run `scripts/sync_live_db.sh`; back up current Dev `data/jobs.db`, then
   swap in the Live snapshot per the printed instructions.
2. Launch `streamlit run tracker.py` locally. In Settings → Profile Editor,
   confirm `unified_jc`'s `exclude_location_contains` field shows the DB's
   true (possibly still-buggy) value — proving no code fallback is masking
   it.
3. Click "Run scoring" and confirm the candidate count matches the
   corrected, unblocked pool (not 0).
4. Confirm the FELFEL regression job still scores as expected across
   profiles.
5. Confirm the 2 Lausanne jobs from the original investigation now reach
   scoring instead of being silently dropped.
6. Exercise the Re-extract button hardening and message-flash fix manually.
