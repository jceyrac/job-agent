# Build plan — bug fixes + UX redesign for job cards/detail

Five Claude Code prompts. Each is self-contained.

Repo root: `/Users/jeanclaudevd/AI-Suite/job_agent`
DB path: `data/jobs.db`

Design decisions (locked in before writing):
1. **Derived state, not stored.** The new "statuses" (scraped / extracted /
   scored / prepared) are computed at render time from `jobs.extracted_at`,
   `job_scores` rows, and `job_applications.prepared_at`. Existing tracking
   states (`queued` / `applied` / `rejected` / `archived`) stay as the only
   ones written to `job_tracking.status`. No DB migration.
2. **Score = pick a profile.** "Model" in the spec was interpreted as
   "profile". The picker defaults to the active profile from Settings.
3. **All buttons always visible**, disabled if not applicable for the
   derived state. Same button set on the card and the detail page.

---

## Prompt 1 — Show all scores on the detail page (fix bugs #1, #2, #3, #4)

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent
File: tracker_views/jobs.py
Symbol: _render_detail (around line 249)

GOAL
The job detail page must show every score the job has, one row per profile,
in a table near the top — not just the active profile's score (and currently
it shows none at all because get_job_for_prepare doesn't JOIN job_scores).

CURRENT BUGS THIS FIXES
- Score badge never renders on the detail page (job.get("score") is None
  because get_job_for_prepare doesn't join job_scores).
- Score button always shows on the detail page even when scores exist.
- Clicking Score on the detail page does nothing (visibility check fails or
  active_profile_id isn't read on the detail view).

INVESTIGATION FIRST
1. Read storage.py and locate the job_scores schema and any existing helper
   that returns scores for a job. If none, you'll add one.
2. Confirm the structure of job_scores: it has columns (job_id, profile_id,
   score, reason, scored_by, scored_at, status, ...). Run:
     sqlite3 data/jobs.db ".schema job_scores"

NEW STORAGE METHOD
Add to storage.py (near get_job_for_prepare, around line 1362):

    def get_scores_for_job(self, job_id: str) -> list[dict]:
        """Return all profile scores for a job, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT profile_id, score, reason, scored_by, scored_at, status
                   FROM job_scores
                   WHERE job_id = ?
                   ORDER BY scored_at DESC""",
                (job_id,),
            ).fetchall()
            return [dict(r) for r in rows]

DETAIL VIEW CHANGES (_render_detail)
1. After loading `job = db.get_job_for_prepare(job_id)`, also load:
     scores = db.get_scores_for_job(job_id)
2. Replace the single-score banner (the line that does
     `st.markdown(f"**{score_badge(score)}** — {job.get('reason', '')}")`
   ) with a small table block:

     if scores:
         st.subheader("Scores")
         # Render as a markdown table or st.dataframe — markdown is fine:
         lines = ["| Profile | Score | Reason | Model |",
                  "|---|---|---|---|"]
         for s in scores:
             lines.append(
                 f"| {s['profile_id']} | {score_badge(s['score'])} "
                 f"| {(s['reason'] or '')[:120]} | {s['scored_by'] or ''} |"
             )
         st.markdown("\n".join(lines))
     else:
         st.info("Not yet scored.")

3. Score button visibility on the detail page (next prompt handles the
   action wiring; for now just fix the visibility condition):
   - Hide the Score button if `scores` contains an entry for the currently
     active profile (read it via `get_db().get_config("active_profile_id")`
     since the sidebar selectbox isn't available on the detail view).
   - If active_profile_id is empty or set to "-- All profiles --", still
     SHOW the button — it'll prompt for a profile when clicked (Prompt 2).

CONSTRAINTS
- Do NOT change get_job_for_prepare's signature or behavior. Other callers
  (prepare.py, job_actions.py) rely on it.
- Don't break the list view (_render_card). The card score badge still
  reads from the active-profile-joined query in load_jobs — leave it alone.

VERIFY
1. Restart streamlit. Open a job you know is scored against unified_jc.
2. Detail page shows the Scores table with at least one row.
3. The Score button is hidden on that detail page (because unified_jc is
   the active profile and a score exists for it).
4. Open a job that has NO score for the active profile but exists in the
   DB — Score button is visible.
5. `python -c "from storage import JobStorage; print(JobStorage('data/jobs.db').get_scores_for_job('SOME_JOB_ID'))"`
   returns a list.
```

---

## Prompt 2 — Profile picker dialog for Score action

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent
File: tracker_views/jobs.py

GOAL
When the user clicks the Score button (on card OR detail), the app should:
  1. Look up the active profile from settings.
  2. If a single profile is selected → call score_one(job_id, that_profile)
     directly (no extra click).
  3. If the active profile is unset OR set to a sentinel meaning "all
     profiles" → show an error toast: "Please set a default profile in
     Settings before scoring individual jobs." Do not call score_one.
  4. Always offer an "advanced" path: a small expander or popover under
     the Score button labelled "Score against a different profile" that
     lets the user pick from ALL_PROFILES via st.selectbox and then runs
     score_one(job_id, chosen_profile).

INVESTIGATION FIRST
1. Read tracker_views/settings.py to find:
   - The exact config key used (probably "active_profile_id")
   - What value represents "all" / unset (check default_value in
     db.get_config calls and any UI option labels — likely an empty string
     or the literal "-- All profiles --")
2. Read profiles.py to confirm ALL_PROFILES keys.

IMPLEMENTATION

Add a helper at the top of tracker_views/jobs.py (after imports):

    from profiles import ALL_PROFILES

    def _resolve_active_profile() -> str | None:
        """Return the active profile id, or None if unset/'all'."""
        pid = (get_db().get_config("active_profile_id") or "").strip()
        if pid and pid in ALL_PROFILES:
            return pid
        return None

    def _run_score(job_id: str, profile_id: str) -> None:
        """Run score_one and show feedback. Caller must st.rerun() after."""
        with st.spinner(f"Scoring against {profile_id}…"):
            result = score_one(job_id, profile_id)
        if result is None or result.get("status") == "error":
            st.error(f"Score failed: {(result or {}).get('error','unknown')}")
            return False
        st.success(f"[{profile_id}] scored {result['score']}/10 "
                   f"— {(result['reason'] or '')[:80]}")
        return True

WIRE THE SCORE BUTTON (both _render_card and _render_detail)

When the user clicks Score:
  active = _resolve_active_profile()
  if active is None:
      st.error("Set a default profile in Settings before scoring "
               "individual jobs (or use the advanced picker below).")
  else:
      if _run_score(job_id, active):
          st.cache_data.clear()
          st.rerun()

ADVANCED PROFILE PICKER
Directly below each Score button, add an expander:

  with st.expander("Score against a different profile", expanded=False):
      chosen = st.selectbox(
          "Profile",
          list(ALL_PROFILES.keys()),
          key=f"score_pick_{scope}_{job_id}",  # scope = 'card' or 'detail'
      )
      if st.button("Run score", key=f"score_pick_run_{scope}_{job_id}"):
          if _run_score(job_id, chosen):
              st.cache_data.clear()
              st.rerun()

CONSTRAINTS
- Don't break Prompt 1's visibility logic — when a score already exists
  for the active profile, the main Score button stays hidden, but the
  "Score against a different profile" expander should remain available so
  the user can score against other profiles.
- Use distinct streamlit keys per scope (card vs detail) to avoid the
  duplicate-element-id error.

VERIFY
1. With Settings → active profile = unified_jc, click Score on an
   extracted-but-unscored job — toast shows score + reason, score table
   refreshes.
2. With Settings → active profile cleared, click Score — error toast
   appears, no score is written.
3. Open the expander on an already-scored job, pick web3_remote, click
   Run score — a second row appears in the Scores table.
```

---

## Prompt 3 — Replace "Open" button with a linked source label

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent
File: tracker_views/jobs.py

GOAL
Both _render_card and _render_detail currently render a "🔗 Open" button
linking to job.url. Replace it with the source name (e.g. "LinkedIn",
"Indeed", "Wellfound", "Welcome to the Jungle") rendered as a hyperlink.
This frees one button slot and makes the source instantly visible.

DESIGN
- Card: in col_meta (around line 160 in current file), the source caption is:
      st.caption(job.get("source") or "")
  Replace it with a markdown link when both source and url exist:
      src = job.get("source") or ""
      url = job.get("url") or ""
      if src and url:
          st.markdown(f"🔗 [{src}]({url})")
      elif src:
          st.caption(src)
- Detail: in the chips line ("**Source:** {source}"), replace the plain
  text with a markdown link when url exists. Then remove the "🔗 Open"
  entry from the Actions column row (it becomes redundant).
- Card: ALSO remove the "🔗 Open" from btn_defs (around line 170-171).

PRETTY SOURCE NAMES
The source field today is whatever the scraper sets (probably lowercase:
"linkedin", "indeed", "wellfound", "welcome_to_the_jungle"). Add a small
formatter at the top of the file:

    SOURCE_LABELS = {
        "linkedin": "LinkedIn",
        "indeed": "Indeed",
        "wellfound": "Wellfound",
        "welcome_to_the_jungle": "Welcome to the Jungle",
        "wttj": "Welcome to the Jungle",
        "remotive": "Remotive",
        "weworkremotely": "We Work Remotely",
        "remoteok": "RemoteOK",
        # add more as you discover them
    }

    def _source_label(src: str) -> str:
        if not src:
            return ""
        return SOURCE_LABELS.get(src.lower(), src)

Use it both places: f"🔗 [{_source_label(src)}]({url})".

INVESTIGATION
Before locking the SOURCE_LABELS dict, query the DB for the actual values
present so you don't miss any:
    sqlite3 data/jobs.db "SELECT DISTINCT source FROM jobs"
Add any you find to SOURCE_LABELS.

CONSTRAINTS
- Don't remove the source from anywhere else — filters, exports, etc.
  This is a presentation-only change.

VERIFY
1. Card: source appears as a clickable link in the meta column, no
   "🔗 Open" button in the action row.
2. Detail: source appears as a clickable link in the chips line, no
   "🔗 Open" in the Actions row.
3. Cards/details for jobs missing a URL still render (just plain text).
```

---

## Prompt 4 — Unified action bar: all buttons always shown, disabled per derived state

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent
File: tracker_views/jobs.py

GOAL
Replace the current dynamically-built action button list with a fixed,
ordered set of action buttons that is identical on the card AND the
detail view. Buttons not applicable for the job's current derived state
are rendered but DISABLED.

DERIVED STATE
Add a helper at the top of tracker_views/jobs.py:

    def _derive_state(job: dict, scores: list[dict] | None,
                      app: dict | None) -> str:
        """
        Returns one of:
          scraped, extracted, scored, queued, prepared,
          applied, rejected, archived
        Order of precedence (highest wins):
          archived > rejected > applied > prepared > queued
          > scored > extracted > scraped
        """
        tracking = (job.get("status") or "new").strip().lower()
        if tracking == "archived":   return "archived"
        if tracking == "rejected":   return "rejected"
        if tracking == "applied":    return "applied"
        if app and app.get("prepared_at"):
            return "prepared"
        if tracking in ("queued", "ready"):
            return "queued"
        if scores:                   return "scored"
        if job.get("extracted_at"):  return "extracted"
        return "scraped"

ACTION SET (fixed, in this order)
    ACTIONS = ["extract", "score", "queue", "prepare",
               "applied", "rejected", "not_relevant"]

    ACTION_LABELS = {
        "extract":      "🔍 Extract",
        "score":        "🎯 Score",
        "queue":        "🚀 Queue",
        "prepare":      "📝 Prepare",
        "applied":      "✅ Applied",
        "rejected":     "❌ Rejected",
        "not_relevant": "🚫 Not relevant",
    }

ENABLED MATRIX
    ENABLED = {
        "scraped":      {"extract", "applied", "not_relevant"},
        "extracted":    {"score", "queue", "prepare", "applied", "not_relevant"},
        "scored":       {"queue", "prepare", "applied", "not_relevant"},
        "queued":       {"prepare", "applied", "not_relevant"},
        "prepared":     {"applied", "not_relevant"},
        "applied":      {"rejected", "not_relevant"},
        "rejected":     {"not_relevant"},
        "archived":     set(),
    }

RENDERER
Add a helper:

    def _render_action_bar(job: dict, scope: str, scores: list[dict] | None,
                           app: dict | None) -> None:
        state = _derive_state(job, scores, app)
        enabled = ENABLED[state]
        cols = st.columns(len(ACTIONS))
        for i, action in enumerate(ACTIONS):
            label = ACTION_LABELS[action]
            disabled = action not in enabled
            key = f"act_{scope}_{action}_{job['id']}"
            if cols[i].button(label, key=key, disabled=disabled):
                _handle_action(action, job, scope)
        # Render the small "state" caption so the user knows why some
        # buttons are disabled:
        st.caption(f"State: **{state}**")

ACTION HANDLER
    def _handle_action(action: str, job: dict, scope: str) -> None:
        job_id = job["id"]
        db = get_db()
        try:
            if action == "extract":
                with st.spinner("Extracting…"):
                    res = extract_one(job_id)
                if res and res.get("status") == "ok":
                    st.success(f"Extracted: {(res.get('summary') or '')[:80]}")
                else:
                    st.error(f"Extract failed: {(res or {}).get('error','unknown')}")
            elif action == "score":
                active = _resolve_active_profile()  # from Prompt 2
                if active is None:
                    st.error("Set a default profile in Settings before scoring.")
                    return
                _run_score(job_id, active)  # from Prompt 2 — also from this fn:
            elif action == "prepare":
                with st.spinner("Preparing (4 LLM calls, ~10-30s)…"):
                    res = prepare_one(job_id)
                if res and res.get("status") == "ok":
                    st.success(f"Prepared by {res.get('prepared_by','?')}")
                else:
                    st.error(f"Prepare failed: {(res or {}).get('error','unknown')}")
            elif action == "queue":
                db.set_status(job_id, "queued")
            elif action == "applied":
                db.set_status(job_id, "applied")
            elif action == "rejected":
                db.set_status(job_id, "rejected")
            elif action == "not_relevant":
                # Keep the "require a note" prompt for not_relevant — see
                # current implementation around lines 192-227 — preserve it.
                _request_archive(job, scope)
                return  # _request_archive handles its own rerun
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(str(e))

ARCHIVE-NOTE FLOW
Lift the existing archive-with-note prompt (currently lines ~192-227 of
_render_card and ~348-362 of _render_detail) into a single helper:

    def _request_archive(job: dict, scope: str) -> None:
        """Show inline note prompt; archive on confirm."""
        # Use scope to distinguish session_state keys.

Call this from _handle_action when action == "not_relevant".

REWRITE _render_card AND _render_detail
- Remove the old btn_defs / column-loop block in _render_card.
- Remove the old per-button if-blocks in _render_detail's Actions section.
- In its place, call _render_action_bar(job, scope, scores, app).
- On _render_card, scores can be cheaply derived by reading the existing
  job dict (load_jobs already joins for the active profile — but we need
  ALL scores for the derived state). Add a new storage method or extend
  load_jobs to include a count:
     SELECT j.*, ..., (SELECT COUNT(*) FROM job_scores s
                       WHERE s.job_id = j.id) AS score_count
  Then in _derive_state replace `if scores:` with `if scores or
  job.get('score_count'):` — accept either shape.
- On _render_detail, call db.get_scores_for_job(job_id) and
  db.get_application(job_id) once and pass both to _render_action_bar.

CONSTRAINTS
- Score against a different profile (the expander from Prompt 2) MUST
  still be reachable even when the Score button is disabled (because the
  job is already "scored" against the default profile but the user might
  want to add web3_remote). Render the expander unconditionally below
  the action bar.
- Do NOT introduce a new column in job_tracking. Derived state is
  computed at read time.
- Keep the Notes section and the Application preview section as they are.
- Use unique streamlit keys per scope to avoid collisions.

VERIFY
1. Open a job that's still in 'scraped' state (no extracted_at) — only
   Extract / Applied / Not relevant are clickable; the others are visibly
   disabled. State caption reads "scraped".
2. Open an extracted-but-unscored job — Score / Queue / Prepare / Applied /
   Not relevant clickable; Extract and Rejected disabled.
3. Open an already-applied job — Rejected and Not relevant clickable;
   everything else disabled.
4. Click Queue on an 'extracted' job — state becomes 'queued', button set
   updates to (Prepare / Applied / Not relevant).
5. The card and the detail page render the SAME action bar for the same job.
```

---

## Prompt 5 — Smoke test

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Verify Prompts 1-4 end-to-end. DO NOT modify code; only run and report.

CHECKS
1. `streamlit run tracker.py` — starts without errors.
2. Open the Jobs list. Pick a job already scored against unified_jc.
   - Card shows the linked source label, not an Open button.
   - Action bar has all 7 buttons in this order:
     Extract / Score / Queue / Prepare / Applied / Rejected / Not relevant
   - Extract is disabled (job is already extracted); Score is disabled
     (scored against active profile); the others reflect tracking state.
   - State caption reads either 'scored' or whatever the tracking is.
3. Click into the detail. Confirm:
   - Scores table renders with one row (unified_jc).
   - Action bar is identical to the card.
   - "Score against a different profile" expander is present.
4. Use the expander to score against web3_remote. A second row appears in
   the Scores table.
5. Find a job in the DB with `extracted_at IS NULL` (if any):
     sqlite3 data/jobs.db "SELECT id FROM jobs WHERE extracted_at IS NULL LIMIT 1"
   If none exists, skip the next two checks.
   - Card for that job has Extract enabled; click it; state transitions
     to 'extracted'; Score becomes enabled.
6. With Settings → active profile cleared (or set to "-- All profiles --"):
   - Click Score on an extracted job. An error toast appears; no DB write.
   - The expander still works (you can pick a profile manually).
7. Click Not relevant on any job — the note prompt appears; submit with a
   note. State becomes 'archived'.

REPORT
A short pass/fail per check. Quote any error verbatim.
```

---

## Run order
2 → 3 → 4 → 5

Each prompt depends only on the ones before it. Pause after Prompt 1 to confirm the score table actually renders before doing the bigger UX refactor in Prompt 4.
