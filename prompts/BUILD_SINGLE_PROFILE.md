# Build plan — Collapse to single-profile mode (UI only, no DB migration)

Four Claude Code prompts. Run in order.

Repo root: `/Users/jeanclaudevd/AI-Suite/job_agent`
DB path: `data/jobs.db`

Decisions locked in:
1. **Single-profile mode.** The UI manages exactly one profile
   (`unified_jc`). All profile selectors are removed.
2. **No DB migration.** `job_scores` keeps its `(job_id, profile_id)`
   shape. Historical rows under `web3_remote` / `ch_hybrid` stay
   untouched — the UI just never queries them.
3. **Dormant profiles kept.** `web3_remote` and `ch_hybrid` stay
   *defined* in `profiles.py` but are removed from `ALL_PROFILES`. This
   is the revert path: re-add them to `ALL_PROFILES` + restore the
   selectors and multi-profile is back.

The guiding idea: one source of truth — `profiles.get_active_profile()` —
that every read/write path resolves through. The UI never asks which
profile; it uses that one.

---

## Prompt 1 — Single source of truth (profiles.py + backend wiring)

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Make the codebase resolve ONE active profile everywhere, via a single
helper. No DB changes. web3_remote and ch_hybrid stay defined but dormant.

INVESTIGATION FIRST
1. Read profiles.py — note ALL_PROFILES (around line 289), DEFAULT_PROFILE_ID
   (around line 295), and the three profile definitions WEB3_REMOTE,
   CH_HYBRID, UNIFIED_JC.
2. Read score.py — note the --profile argument and the
   "Specify --profile or --extract" guard.
3. Read job_actions.py — note score_one(job_id, profile_id) signature.
4. Read prepare.py — note prepare_job_application's profile_id handling
   and the _auto_pick_profile helper.
5. Read main.py — note how it resolves active_profile_id from DB config.

CHANGES TO profiles.py

Keep all three profile definitions (WEB3_REMOTE, CH_HYBRID, UNIFIED_JC)
exactly as they are. Above the WEB3_REMOTE definition, add a comment
banner marking web3_remote and ch_hybrid as dormant:

    # ---------------------------------------------------------------------
    # DORMANT PROFILES — defined but not surfaced anywhere.
    # The app runs in single-profile mode (see ACTIVE_PROFILE below).
    # To return to multi-profile: add these back into ALL_PROFILES and
    # restore the profile selectors in the tracker UI.
    # ---------------------------------------------------------------------

Replace the registry block at the bottom with:

    # Active profile — single-profile mode.
    ACTIVE_PROFILE: SearchProfile = UNIFIED_JC
    ACTIVE_PROFILE_ID: str = UNIFIED_JC.id

    # Only the active profile is surfaced. Dormant profiles stay defined
    # above but are intentionally excluded from this registry.
    ALL_PROFILES: dict[str, SearchProfile] = {
        UNIFIED_JC.id: UNIFIED_JC,
    }

    DEFAULT_PROFILE_ID = UNIFIED_JC.id

    def get_active_profile() -> SearchProfile:
        """The single profile the app manages. One source of truth."""
        return ACTIVE_PROFILE

CHANGES TO score.py
- Keep --profile as an optional arg (default None).
- Keep the mutual-exclusivity check: if args.extract and args.profile → error.
- Replace the "Specify --profile or --extract" guard: when NOT extracting
  and no --profile given, default to the active profile instead of
  erroring:
      from profiles import get_active_profile
      ...
      if not args.extract:
          profile_id = args.profile or get_active_profile().id
- The --mock path that checks `args.profile in ALL_PROFILES` still works
  (unified_jc is in ALL_PROFILES). Leave it.

CHANGES TO job_actions.py
- score_one signature becomes:
      def score_one(job_id: str, profile_id: str | None = None) -> dict | None:
- At the top of the function:
      from profiles import get_active_profile
      profile_id = profile_id or get_active_profile().id
- Keep the existing validation (profile_id in ALL_PROFILES). With the
  default it will always pass for unified_jc.

CHANGES TO prepare.py
- In prepare_job_application, when profile_id is None, default to the
  active profile rather than calling _auto_pick_profile:
      from profiles import get_active_profile
      if profile_id is None:
          profile_id = get_active_profile().id
- Leave _auto_pick_profile defined (dormant) — don't delete it, it's part
  of the multi-profile revert path. Just stop calling it as the default.

CHANGES TO main.py
- Replace the DB-config lookup for active_profile_id with the helper:
      from profiles import get_active_profile
      active_profile_id = get_active_profile().id
- The scrape → extract → score step sequence is otherwise unchanged.

CONSTRAINTS
- Do NOT touch storage.py. No schema change, no migration.
- Do NOT delete WEB3_REMOTE, CH_HYBRID, _auto_pick_profile, or the
  None-path in any function — they are the documented revert path.
- Do NOT change get_jobs_for_scoring / get_all_for_tracker / save_scored
  signatures — they take a profile_id and that still works.
- The DB config key "active_profile_id" becomes orphaned — that's fine,
  leave it. Don't write a migration to remove it.

VERIFY
1. `python -c "from profiles import get_active_profile, ALL_PROFILES; \
   print(get_active_profile().id, list(ALL_PROFILES))"`
   → prints "unified_jc ['unified_jc']".
2. `python -c "import profiles; print(profiles.WEB3_REMOTE.id, \
   profiles.CH_HYBRID.id)"` → still works (definitions intact).
3. `python score.py --help` — --profile still listed, now optional.
4. `python score.py --extract --limit 1` — runs, no error.
5. `python score.py --limit 1` (no --profile) — runs against unified_jc.
6. `python -c "from job_actions import score_one"` — imports clean.
7. `python main.py --help` — runs; --profile still accepted if present.
```

---

## Prompt 2 — Remove profile selectors from the UI

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

PREREQUISITE
Prompt 1 done — profiles.get_active_profile() and ACTIVE_PROFILE_ID exist,
ALL_PROFILES holds only unified_jc.

GOAL
Remove every profile selector from the tracker UI. The app silently uses
the active profile everywhere.

FILES: tracker_views/jobs.py, tracker_views/job_detail.py,
tracker_views/job_helpers.py, tracker_views/shared.py,
tracker_views/settings.py, tracker_views/preferences.py (if it exists),
tracker_views/dashboard.py, preference_report.py

--- tracker_views/shared.py ---
- load_jobs(profile_id, exclude_archived): give profile_id a default so
  callers can omit it:
      def load_jobs(profile_id: str | None = None,
                    exclude_archived: bool = False) -> list[dict]:
          from profiles import get_active_profile
          if profile_id is None:
              profile_id = get_active_profile().id
          return get_db().get_all_for_tracker(
              profile_id, exclude_archived=exclude_archived)
  (The old `get_all_jobs_best_score` branch for profile_id=None is no
  longer reachable from the UI — leave the method in storage.py, just
  stop routing to it here.)

--- tracker_views/jobs.py ---
- In _render_list, DELETE the entire "Profile" selectbox block from the
  sidebar (the `profiles = load_profiles()` / `profile_options` /
  `st.selectbox("Profile", ...)` lines, ~lines 28-41).
- Everywhere `profile_id` was used downstream, replace with the active
  profile id:
      from profiles import ACTIVE_PROFILE_ID
  Call `load_jobs()` with no argument. The score-distribution metric
  block currently gated on `if profile_id:` — make it unconditional
  (there is always a profile now).
- _render_card takes a `profile_id` param — either drop the param or
  default it to ACTIVE_PROFILE_ID; pick whichever is the smaller diff.

--- tracker_views/job_detail.py ---
- DELETE the multi-row "Scores" table block (the `if scores:` block that
  builds a markdown table with one row per profile).
- Replace it with a single score badge + reason for the active profile:
      from profiles import ACTIVE_PROFILE_ID
      active_score = next(
          (s for s in scores if s["profile_id"] == ACTIVE_PROFILE_ID),
          None)
      if active_score:
          st.markdown(f"**{score_badge(active_score['score'])}** — "
                      f"{active_score.get('reason','')}")
      else:
          st.info("Not yet scored.")
- DELETE the "Score against a different profile" expander entirely
  (the st.expander block with the profile selectbox + "Run score" button).
- Keep db.get_scores_for_job — it's still used to find the active score.

--- tracker_views/jobs.py (card) ---
- DELETE the "Score against a different profile" expander on the card too
  (same block as in job_detail).

--- tracker_views/job_helpers.py ---
- _resolve_active_profile(): simplify to always return the active profile,
  never None:
      def _resolve_active_profile() -> str:
          from profiles import get_active_profile
          return get_active_profile().id
- In _handle_action, the "score" branch currently has an
  `if active is None: st.error("Set a default profile…")` path. Since
  _resolve_active_profile never returns None now, DELETE that error
  branch — just call _run_score(job_id, active) directly.
- The action bar and ENABLED matrix are unchanged.

--- tracker_views/settings.py ---
- Read the file, find the profile-selection widget (the control that
  writes config key "active_profile_id"), and DELETE that section.
- If removing it leaves an empty settings section/header, remove the
  now-empty header too. Don't leave a dangling "Profile" subheader.

--- tracker_views/preferences.py (ONLY IF THIS FILE EXISTS) ---
- Remove the profile selectbox from the sidebar.
- The "Regenerate report" button should call generate_report with the
  active profile:
      from profiles import get_active_profile
      ... generate_report(profile_id=get_active_profile().id, ...)
- If the file does not exist, skip this bullet silently.

--- tracker_views/dashboard.py ---
- The "Hot Jobs Feed" calls load_jobs(profile_id=None, ...). With the new
  default that now resolves to the active profile — which is correct.
  Just change the call to load_jobs(exclude_archived=True) for clarity.
  No other dashboard change.

--- preference_report.py ---
- Make --profile optional, defaulting to the active profile:
      from profiles import get_active_profile
      profile_id = args.profile or get_active_profile().id
- If the report previously looped over ALL_PROFILES to produce a section
  per profile, it now naturally produces just one section (ALL_PROFILES
  has one entry) — that's fine, no extra change needed.

CONSTRAINTS
- Do NOT touch storage.py.
- Do NOT delete db.get_all_jobs_best_score, db.get_scores_for_job,
  _auto_pick_profile, or the dormant profiles — all part of the revert
  path.
- Watch for now-unused imports after deletions (load_profiles in jobs.py,
  ALL_PROFILES in job_detail.py, etc.) — remove imports that are no
  longer referenced so the files stay clean.
- Streamlit keys: deleting widgets is fine, but make sure no remaining
  widget references a session_state key that only the deleted selectbox
  set.

VERIFY
1. `streamlit run tracker.py` — starts clean, no import errors.
2. Jobs page — no Profile selectbox in the sidebar. Cards render, score
   badges show, action bar works.
3. Job detail page — shows a single score badge + reason (no table), no
   "Score against a different profile" expander.
4. Click Score on a card and on the detail page — scores against
   unified_jc with no profile prompt, no "set a default profile" error.
5. Settings page — no profile picker; the rest of settings intact.
6. Preferences page (if present) — no profile selectbox; Regenerate
   still works.
7. Dashboard — Hot Jobs Feed still renders.
```

---

## Prompt 3 — CLI cleanup, tests, and README

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

PREREQUISITE
Prompts 1 and 2 done — get_active_profile() exists, ALL_PROFILES holds
only unified_jc, UI selectors removed.

GOAL
Make every CLI entry point usable without a --profile argument, update
the test suite for single-profile mode, and correct the README.

PART A — CLI AUDIT

First, find every CLI surface that mentions profiles:
    grep -rn "add_argument(\"--profile\"\|ALL_PROFILES\|DEFAULT_PROFILE_ID" \
      --include="*.py" . | grep -v tests/ | grep -v migrate_

Then handle each entry point:

1. main.py
   - Prompt 1 already switched the body to get_active_profile(). Now
     REMOVE the --profile argparse argument entirely — with a single
     profile its `choices` list collapses to one value and it serves no
     purpose. Update the module docstring / startup banner accordingly.

2. score.py
   - --profile STAYS as an optional override (default None). Running
     `python score.py` with no args must score against the active
     profile; `python score.py --extract` must still work.
   - Update the --profile help string to:
     "(optional; defaults to the active profile)".
   - Update the module docstring usage examples — drop any mandatory
     `--profile <id>`.

3. prepare.py
   - `python prepare.py --job <id>` must work with NO --profile (Prompt 1
     made prepare_job_application default to the active profile when
     profile_id is None — confirm the CLI path honours that).
   - Update the usage docstring at the top of prepare.py and the
     --profile help text to present --profile as optional.

4. preference_report.py
   - Prompt 2 made --profile optional. Update its --help text and any
     usage docstring to match.

5. create_profile.py
   - This tool creates NEW profiles — a multi-profile concept. Do NOT
     delete it and do NOT change its behaviour. Add one note to its
     module docstring: "Dormant in single-profile mode — the app manages
     only profiles.ACTIVE_PROFILE. Kept as part of the multi-profile
     revert path."

6. Any other hit from the grep above (scripts/*.py etc.): same rule —
   --profile optional, default to the active profile, help text updated.
   Leave migrate_*.py and backfill_*.py untouched (historical scripts).

PART B — TEST SUITE

1. Baseline: `python -m pytest -q` — capture which tests fail.

2. Expected breakage and the fix for each:
   - Asserts on `len(ALL_PROFILES) == 3` or the three ids → expect
     exactly {'unified_jc'}.
   - Asserts `DEFAULT_PROFILE_ID == 'web3_remote'` → now 'unified_jc'.
   - Tests that look up web3_remote / ch_hybrid via ALL_PROFILES → if
     the test genuinely exercises that profile's config, import it
     directly (`from profiles import WEB3_REMOTE`) so dormant-profile
     coverage survives; otherwise repoint the test at unified_jc.
   - Tests asserting score.py / prepare.py EXIT NON-ZERO when --profile
     is omitted → invert them: omission now succeeds and uses the active
     profile.

3. Add these new tests (put them in the existing tests/ files that fit):
   - get_active_profile() returns the unified_jc profile object.
   - ALL_PROFILES == {'unified_jc'} (exact set of keys).
   - WEB3_REMOTE and CH_HYBRID are still importable from profiles
     (the dormant-but-defined invariant — this is the revert guarantee).
   - score_one(job_id) with no profile_id resolves to the active
     profile (unit test the default-resolution branch; mock the DB if
     needed so it doesn't hit the network).

4. Re-run `python -m pytest -q` until green. Report the final pass count.

PART C — README

Update README.md for single-profile mode. Keep it concise — correct,
don't expand:
   - Workflow / quickstart: the pipeline is `python main.py`
     (scrape → extract → score), or the individual steps
     `python scrape.py`, `python score.py --extract`, `python score.py`.
     Remove every `--profile <id>` from the documented commands.
   - Any "Profiles" section presenting web3_remote / ch_hybrid /
     unified_jc as selectable: rewrite it to state the app runs in
     single-profile mode on unified_jc, that the other two remain
     defined but dormant, and that reverting to multi-profile means
     re-adding them to ALL_PROFILES and restoring the UI selectors.
   - Fix any feature-list or tracker-walkthrough text that mentions
     choosing a profile in the UI.

CONSTRAINTS
- Do NOT touch storage.py or the DB.
- Do NOT delete create_profile.py, the dormant profiles, _auto_pick_profile,
  get_all_jobs_best_score, or migration scripts.
- Keep dormant-profile test coverage alive via direct imports.

VERIFY
1. `python main.py --help` — no --profile argument shown.
2. `python score.py --help` — --profile shown, marked optional.
3. `python prepare.py --help` — --profile shown, marked optional.
4. `python score.py` with no args — scores against unified_jc.
5. `python prepare.py --job <some_queued_id>` with no --profile — runs.
6. `python -m pytest -q` — all green; report the pass count.
7. `grep -n "profile" README.md` — no command is documented as
   requiring --profile; the Profiles section describes single-profile
   mode + the revert path.
```

---

## Prompt 4 — Smoke test

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Verify single-profile mode end-to-end after Prompts 1-3. Don't modify
code — run & report.

CHECKS
1. `python -c "from profiles import get_active_profile, ALL_PROFILES, \
   WEB3_REMOTE, CH_HYBRID; print(get_active_profile().id, \
   list(ALL_PROFILES), WEB3_REMOTE.id, CH_HYBRID.id)"`
   → "unified_jc ['unified_jc'] web3_remote ch_hybrid"
   (active resolved, registry trimmed, dormant profiles still defined).

2. DB untouched — historical multi-profile scores still present:
   python -c "import sqlite3; c=sqlite3.connect('data/jobs.db'); \
   print(c.execute('SELECT profile_id, COUNT(*) FROM job_scores \
   GROUP BY profile_id').fetchall())"
   → still shows rows for web3_remote / ch_hybrid if they existed before.

3. `python main.py --help` — NO --profile argument.
4. `python score.py --help` — --profile present, optional.
5. `python prepare.py --help` — --profile present, optional.
6. `python score.py --limit 1` (no --profile) — scores against unified_jc.
7. `python score.py --extract --limit 1` — runs.
8. `python preference_report.py` (no --profile) — generates a report.
9. `python -m pytest -q` — full suite green; report the pass count.
10. `streamlit run tracker.py` — starts; click through Jobs, a Job
    detail, Companies, Settings, Dashboard, Preferences (if present).
    No profile selector anywhere. No console errors.
11. On a Job detail page, the score shows as a single badge, not a table.

REPORT
Pass/fail per check. Quote any error verbatim.
```

---

## Run order
1 → 2 → 3 → 4.

The revert path, for the record: re-add `WEB3_REMOTE` and `CH_HYBRID` to `ALL_PROFILES`, restore the profile selectboxes in `jobs.py` / `settings.py` / `preferences.py`, and restore the scores table in `job_detail.py`. Because the DB schema and history were never touched, multi-profile comes back fully intact.
