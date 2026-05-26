# Build plan — Jobs list performance (kill the SQL storm, paginate, fragment)

Four Claude Code prompts. Each is self-contained.

Repo root: `/Users/jeanclaudevd/AI-Suite/job_agent`
DB path: `data/jobs.db`

Context (what we observed):
- The Jobs list renders 100–600 cards depending on filters.
- For each card, `db.get_application(job_id)` runs synchronously, generating
  hundreds of SQL round-trips per render. Clicking a hyperlink from the
  Jobs page is slow not because the next page is slow but because the
  *current* page is heavy: server worker busy + browser tearing down a
  large DOM tree.
- Detail-page nav from the Companies list feels fast because the Companies
  list is small and has no per-row queries.
- The detail-page split that landed in commit `2e2bd62` is solid and we
  are not touching it.

Order matters. Prompt 1 is the highest-impact, smallest-risk change.
Pause after Prompt 1 to confirm Jobs-page latency improved before doing 2 & 3.

---

## Prompt 1 — Bulk-load job_applications + state cleanup

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Eliminate per-card `db.get_application(job_id)` calls on the Jobs list. Today
each card on tracker_views/jobs.py issues one SQL query (see _render_card,
line ~181 and again at ~221 inside the "ready" expander). With 600 cards
that's 600+ round-trips per render. Bulk-load all applications once at the
top of the list view and pass the result down.

ALSO FIX (small, in scope)
- Remove the debug print at tracker_views/shared.py:326 (`print(f"[DETAIL] ...")`).
  It fires on every detail-page render and spams the terminal.
- The card meta block currently shows `Status: **{tracking_status}**`
  while the action bar caption below shows `State: **{derived_state}**`.
  Replace the meta caption's "Status:" line with the derived state so the
  two reads agree. The action bar caption itself stays — same source.

INVESTIGATION FIRST
1. Confirm job_applications size:
     python -c "import sqlite3; \
     c=sqlite3.connect('data/jobs.db'); \
     print(c.execute('SELECT COUNT(*) FROM job_applications').fetchone()[0])"
   Expected to be small (~120). If it ever grows past 5k, this approach
   needs to shift to a filtered query — but for now bulk-load is fine.

2. Read tracker_views/shared.py to confirm the @st.cache_data pattern.

NEW SHARED LOADER
Add to tracker_views/shared.py near the other cached loaders (around
`load_dashboard_data`):

    @st.cache_data(ttl=60, show_spinner=False)
    def load_applications_index() -> dict[str, dict]:
        """All job_applications keyed by job_id. ~100 rows, full table fetch.

        Cached for 60s. Caller should st.cache_data.clear() after any
        prepare/queue/apply action — existing handlers already do this.
        """
        rows = get_db().get_all_applications()
        return {r["job_id"]: r for r in rows}

NEW STORAGE METHOD
Add to storage.py near `get_application` (around line 1302):

    def get_all_applications(self) -> list[dict]:
        """Return every row of job_applications. Used by the list view to
        avoid N+1 queries when deriving prepared state for each card."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM job_applications"
            ).fetchall()
            return [dict(r) for r in rows]

WIRE THE LOADER INTO _render_list (tracker_views/jobs.py)
At the top of _render_list, after the filters block and load_jobs, add:

    apps_index = load_applications_index()

Pass it to _render_card:

    for job in jobs:
        _render_card(job, profile_id, apps_index=apps_index)

UPDATE _render_card SIGNATURE AND BODY
- Change the signature to accept apps_index: dict[str, dict].
- Replace the line `app = db.get_application(job_id)` (around line 181) with:
      app = apps_index.get(job_id)
- Inside the "ready" expander (around line 221), replace
      app_data = db.get_application(job_id)
  with:
      app_data = apps_index.get(job_id)
- The card meta caption currently has:
      st.caption(f"Status: **{status}**")
  Replace with the derived state read from job_helpers._derive_state.
  Since _derive_state needs (job, scores, app), and we only have app from
  the index here, pass scores=None (the score_count fallback covers it):

      from tracker_views.job_helpers import _derive_state
      derived = _derive_state(job, scores=None, app=app)
      st.caption(f"State: **{derived}**")

  (Then the action bar's own `st.caption(f"State: **{state}**")` becomes a
  duplicate — keep it for now, we can collapse them later. Don't sweat it.)

CLEANUP — DEBUG PRINT
Open tracker_views/shared.py and DELETE line 326:
    print(f"[DETAIL] qp={qp_id!r} ss={ss_id!r} → {result!r}", flush=True)
The function should still return `result` on the next line.

CONSTRAINTS
- Don't touch _render_detail. It already calls db.get_application(job_id)
  exactly once — that's correct and cheap.
- Don't change the existing cache invalidation pattern. Every handler that
  does `st.cache_data.clear()` will now also flush the new index — perfect.
- Don't add an `id` column to job_applications or change the schema; the
  table is keyed by job_id which the existing get_application already uses.

VERIFY
1. `python -c "from storage import JobStorage; \
   print(len(JobStorage('data/jobs.db').get_all_applications()))"`
   prints a small integer.
2. Restart streamlit. Open the Jobs page with the default filter (status=new).
   Note rough render time. Then bump to "All profiles" + status filter empty
   so 1500+ cards would render — confirm it still completes in seconds, not
   tens of seconds. (Filter cap from Prompt 2 will help even more later.)
3. Open a job's detail page — the [DETAIL] log line should no longer appear
   in the streamlit terminal.
4. The "State: scored" caption appears on cards instead of "Status: new"
   for jobs that have at least one score.
5. Click Queue on an extracted job — the card re-renders with "State: queued".
```

---

## Prompt 2 — Paginate the Jobs list

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent
File: tracker_views/jobs.py

GOAL
Cap the number of cards rendered in a single pass. Even after Prompt 1's
bulk-load fix, rendering 600 cards × 7 buttons each is heavy DOM work that
slows initial page load and makes hyperlink navigation feel laggy because
the browser has to tear down all that DOM before transitioning.

DESIGN
- Add a sidebar "Per page" selector: 25 / 50 / 100 / 250 / All. Default 50.
- Add a "Page" number-input that goes from 1 to ceil(total / per_page).
  Both above the cards.
- Slice the filtered `jobs` list before iterating.
- Persist current page in `st.session_state["jobs_page"]` so it survives
  filter tweaks where possible — but RESET page to 1 whenever the filter
  signature changes (otherwise you can land on an empty page after a tighter
  filter cuts the result set).

IMPLEMENTATION

Near the other sidebar filter widgets, add:

    with st.sidebar:
        per_page = st.selectbox(
            "Per page", [25, 50, 100, 250, "All"],
            index=1,  # default 50
            key="jobs_per_page",
        )

After `jobs = apply_filters(...)`, compute the filter signature and reset
page on change:

    # Reset page when filter set or per_page changes
    sig = (
        profile_id, min_score, show_stale, date_filter, scraped_filter,
        tuple(location_filter or ()), tuple(work_mode_filter or ()),
        tuple(geo_zone_filter or ()), tuple(company_size_filter or ()),
        tuple(sector_filter or ()), tuple(language_filter or ()),
        tuple(source_filter or ()), tuple(status_filter or ()),
        show_archived, per_page,
    )
    if st.session_state.get("jobs_filter_sig") != sig:
        st.session_state["jobs_page"] = 1
        st.session_state["jobs_filter_sig"] = sig

    total = len(jobs)
    if per_page == "All":
        page_jobs = jobs
        n_pages = 1
        current_page = 1
    else:
        n_pages = max(1, (total + per_page - 1) // per_page)
        current_page = st.session_state.get("jobs_page", 1)
        current_page = max(1, min(current_page, n_pages))
        start = (current_page - 1) * per_page
        end = start + per_page
        page_jobs = jobs[start:end]

Render a pagination row above the cards:

    if per_page != "All" and n_pages > 1:
        pcols = st.columns([1, 2, 1, 1])
        if pcols[0].button("◀ Prev", disabled=current_page <= 1):
            st.session_state["jobs_page"] = current_page - 1
            st.rerun()
        pcols[1].caption(
            f"Page {current_page} of {n_pages} · "
            f"showing {len(page_jobs)} of {total} jobs"
        )
        if pcols[2].button("Next ▶", disabled=current_page >= n_pages):
            st.session_state["jobs_page"] = current_page + 1
            st.rerun()
        # Direct jump
        jump = pcols[3].number_input(
            "Go to", min_value=1, max_value=n_pages,
            value=current_page, key="jobs_page_input",
            label_visibility="collapsed",
        )
        if jump != current_page:
            st.session_state["jobs_page"] = int(jump)
            st.rerun()

Loop over `page_jobs` instead of `jobs`:

    for job in page_jobs:
        _render_card(job, profile_id, apps_index=apps_index)

CONSTRAINTS
- The existing `st.caption(f"{len(jobs)} of {total} jobs shown ...")` line
  near the top of _render_list now refers to the full filtered count, not
  the page slice. Keep it as is — it's the right number for "matches".
- Don't break the "Score distribution" metric row — it should also reflect
  the full filtered set (hot/solid/maybe across all matches, not just the
  visible page).
- "All" remains an option so power-use cases (export, manual scan) still
  work. Make it clear to the user that picking "All" disables pagination.

VERIFY
1. With per_page=50 and the default filter, exactly 50 cards render.
   The page indicator reads "Page 1 of N".
2. Click Next ▶ — different 50 cards render. URL doesn't change (page is
   in session state).
3. Add a status filter that cuts the result set hard — page resets to 1.
4. Switch per_page to "All" — pagination row disappears, all cards render.
5. The hot/solid/maybe metric row still shows totals across the full
   filtered set, not just the visible page.
```

---

## Prompt 3 — Wrap each card in st.fragment

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent
File: tracker_views/jobs.py

GOAL
When a user clicks a button inside a card (Queue, Applied, Extract, etc.),
Streamlit currently reruns the whole page top-to-bottom: re-executes filters,
re-fetches the jobs query, re-renders all cards. With st.fragment, only the
clicked card reruns. This shortens the latency of common in-page actions.

PREREQUISITE
- Streamlit >= 1.33. Confirm with `pip show streamlit | grep Version` from
  the project venv. If older, upgrade first:
     pip install -U "streamlit>=1.33"
  and verify nothing else breaks.

DESIGN
- Decorate _render_card with @st.fragment.
- Fragment-scoped reruns inside the card replace full-page reruns where
  appropriate.
- BUT some actions must still trigger a full rerun:
    - "Not relevant" / archive — the card should disappear from the
      "Active jobs" view, requires full re-filter.
    - Queue / Applied / Rejected — same, the status filter on the sidebar
      may exclude the card afterward.
    - Extract / Score — the card's state changes but it stays visible.
      Can stay fragment-scoped IF the filter doesn't include "extract
      status" — currently it doesn't, only `status_filter`. So fragment
      scope is safe for Extract and Score.

IMPLEMENTATION

1. Import nothing new — st.fragment is part of streamlit.

2. Decorate _render_card:

       @st.fragment
       def _render_card(job: dict, profile_id: str | None,
                        apps_index: dict[str, dict]) -> None:
           ...

3. Inside _handle_action (tracker_views/job_helpers.py), the function ends
   with `st.cache_data.clear(); st.rerun()`. That `st.rerun()` from inside
   a fragment defaults to fragment scope, which is what we want for Extract
   and Score, but NOT for status changes.

   Change _handle_action so:
   - For action in {"extract", "score"} → keep `st.rerun()` (fragment scope).
   - For action in {"queue", "applied", "rejected", "not_relevant", "prepare"}
     → use `st.rerun(scope="app")` to force a full-page rerun (so the card
     re-evaluates against the sidebar filter).

   Pseudocode at the end of _handle_action:

       st.cache_data.clear()
       if action in ("queue", "applied", "rejected", "prepare"):
           st.rerun(scope="app")
       else:
           st.rerun()  # fragment scope by default when called from a fragment

   Note: not_relevant has its own rerun path inside _request_archive — also
   change those to `st.rerun(scope="app")`.

4. The "Run score" button inside the advanced profile picker expander
   (tracker_views/jobs.py around line 191) ALSO calls st.rerun(). Keep that
   as a fragment-scoped rerun (the card just re-derives state).

CONSTRAINTS
- Fragments capture the arguments by reference. `apps_index` is a dict —
  fine. `job` is a dict — fine. profile_id is a string — fine. No
  hashability traps.
- Don't decorate _render_list or _render_detail. Only _render_card.
- The expander for "Score against a different profile" lives inside the
  card body and will work inside the fragment.
- If you hit a "fragments cannot be nested" error, an inner st.expander or
  st.container is fine — those aren't fragments. Only @st.fragment nesting
  is forbidden.

VERIFY
1. Click Extract on a scraped card with 100 other cards visible. Only that
   card flashes/updates — other cards do not visibly re-render. Compare
   to before by looking at Streamlit's "Running…" indicator in the top
   right: it should be brief.
2. Click Queue — the page reruns fully (sidebar Status filter re-applies,
   and if the filter excludes "queued", the card disappears).
3. Click Score (via the action bar, with active profile set) — only the
   card reruns; score badge updates in place.
4. Open the "Score against a different profile" expander, pick a profile,
   click Run score — only the card reruns.
5. Click Not relevant on a card — fill the note, confirm — page reruns
   fully, archived card disappears from view.
```

---

## Prompt 4 — Smoke test the perf changes

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Verify Prompts 1-3 end-to-end. Don't modify code; run and report.

CHECKS
1. `python -c "from tracker_views.shared import load_applications_index, get_db; \
   import streamlit as st; print(type(load_applications_index))"`
   — confirm the new loader is importable. (It's a cached function, so
   actually calling it outside Streamlit context will warn — just import.)

2. `python -c "from storage import JobStorage; \
   print(len(JobStorage('data/jobs.db').get_all_applications()))"`
   — prints an int.

3. `streamlit run tracker.py` — starts clean. No [DETAIL] log lines on
   detail page loads.

4. On the Jobs page with default filters:
   - Per-page selector visible in sidebar, defaults to 50.
   - Pagination row visible above cards if total > 50.
   - Card meta shows "State: ..." (derived) rather than "Status: ...".

5. Click Next ▶ — new page of cards. Click Prev — back. Pagination state
   survives.

6. Apply a tight status filter that returns < per_page jobs. Pagination
   row hides. Page resets to 1.

7. Click Extract on a scraped card — measure roughly how quickly the card
   updates. Compare to memory of pre-change latency. Expected: clearly
   faster, especially with the page set to "All".

8. Click Queue on a card while looking at the "Running…" indicator —
   confirm it triggers a full-page rerun (the indicator runs longer than
   for Extract).

9. From the Jobs page, click a company hyperlink — note time-to-render
   of the company detail page. With the per-card SQL storm gone and the
   page sliced to 50 cards, this should be visibly snappier than before.

REPORT
A short pass/fail per check. If latency is hard to eyeball, you can wrap
key operations with simple `time.time()` prints temporarily, but remove
them before commit.
```

---

## Run order
1 → 2 → 3 → 4

Pause after Prompt 1. If Jobs-page → detail-page navigation already feels acceptable, Prompts 2 and 3 become optional polish. They're worth doing anyway, but the SQL storm fix is the one that moves the needle most.
