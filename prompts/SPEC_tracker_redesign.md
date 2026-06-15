# SPEC — Tracker UI Redesign

> Spec for Claude Code. Read existing `tracker_views/dashboard.py`, `tracker_views/jobs.py`,
> `tracker_views/settings.py`, and `tracker_views/preferences.py` before starting.
> Do NOT modify `storage.py`, `profiles.py`, `models.py`, `main.py`, `scrape.py`, `score.py`, or any scraper.
> Surgical edits only — preserve all existing logic, just reorganise and restyle.

---

## Goal1

Improve layout clarity and information architecture across four pages:
- **Dashboard** — add DB stats widget (moved from Settings)
- **Jobs** — move Run controls + Clear Cache + Re-extract to the top of this page (above the job list)
- **Settings** — remove Run controls, DB stats, Clear Cache, Re-extract (they now live on Jobs). Keep only Profile Editor, Scraper Toggles, Setup, Re-onboard.
- **General** — tighten visual design throughout using Streamlit-native components + targeted `st.html` CSS.

No new dependencies. No new DB tables. Preserve all existing logic.

---

## 1. Jobs page (`tracker_views/jobs.py`)

### 1a. Add a "Controls" strip at the very top (above the last-run banner)

This replaces the Run section currently in Settings. Insert it before the `_render_list()` body renders the last-run banner.

Layout: single row of columns.

```
[ 🕸 Run scrape ]  [ 🎯 Run scoring ]  |  [ 🔄 Clear Cache ]  [ 🔍 Re-extract Fields ]  |  ⬜ Unscored: N
```

Exact implementation — copy the subprocess/session-state logic from `settings.py::_render_run_controls()` and `_render_stats_actions()` verbatim, but render it as a tight horizontal bar at the top of the Jobs page:

```python
def _render_controls_bar(db):
    """Compact run + cache strip at top of Jobs page."""
    with db._conn() as conn:
        total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        scored_distinct = conn.execute(
            "SELECT COUNT(DISTINCT job_id) FROM job_scores"
        ).fetchone()[0]
        unscored = total_jobs - scored_distinct

    c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 1])

    # Scrape button
    proc = st.session_state.get("bg_process")
    label = st.session_state.get("bg_label", "")

    if proc is not None and proc.poll() is None:
        # Process running
        elapsed = int(time.time() - st.session_state.get("bg_start", time.time()))
        mins, secs = divmod(elapsed, 60)
        with c1:
            st.info(f"⏳ {label.title()} — {mins}m {secs}s")
        with c2:
            if st.button(f"⏹ Stop", use_container_width=True):
                proc.kill()
                proc.wait(timeout=5)
                st.session_state.bg_process = None
                st.session_state.bg_label = ""
                st.rerun()
        with c3:
            if st.button("🔄 Refresh", use_container_width=True):
                st.rerun()
    else:
        # No process running
        with c1:
            if st.button("🕸 Run scrape", use_container_width=True):
                _launch_bg("scrape", [sys.executable, "-u", "scrape.py"])
        with c2:
            if st.button("🎯 Run scoring", use_container_width=True):
                active_id = db.get_config("active_profile_id", DEFAULT_PROFILE_ID)
                _launch_bg("score", [sys.executable, "-u", "score.py", "--profile", active_id])
        with c3:
            if st.button("🔄 Clear Cache", use_container_width=True):
                st.cache_data.clear()
                st.success("Cache cleared.")
                st.rerun()
        with c4:
            if st.button("🔍 Re-extract", use_container_width=True,
                         help="Run score.py --extract to re-extract job fields"):
                with st.spinner("Re-extracting…"):
                    result = subprocess.run(
                        [sys.executable, "score.py", "--extract"],
                        capture_output=True, text=True, timeout=600,
                    )
                    st.cache_data.clear()
                    if result.returncode == 0:
                        st.success("Re-extract done.")
                    else:
                        st.error(result.stderr[:500])

    with c5:
        st.metric("Unscored", unscored)
```

Add the required imports at top of `jobs.py`:
```python
import subprocess
import sys
import time
from profiles import DEFAULT_PROFILE_ID
```

Add a helper at module level:
```python
def _launch_bg(label: str, cmd: list[str]):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=False)
    st.session_state.bg_process = proc
    st.session_state.bg_label = label
    st.session_state.bg_output = ""
    st.session_state.bg_start = time.time()
    st.rerun()
```

Call `_render_controls_bar(db)` at the TOP of `_render_list()`, before the last-run banner. `db = get_db()` is already called inside the function — move it to the top if needed.

Show live output in an expander below the bar when a process is running (same as the existing Settings implementation).

### 1b. Visual improvements to the job list

**Stats row — replace loose metrics with a compact card strip**

Replace the current `mc1/mc2/mc3/mc4 = st.columns(4)` score distribution block with a single `st.html` styled strip:

```python
st.html(f"""
<div style="display:flex;gap:8px;margin:0.5rem 0 1rem;">
  <div style="flex:1;background:var(--secondary-background-color);border-radius:8px;padding:10px 14px;text-align:center">
    <div style="font-size:11px;color:var(--text-color);opacity:.6;margin-bottom:2px">🔥 Hot (9-10)</div>
    <div style="font-size:22px;font-weight:600;color:var(--text-color)">{hot}</div>
  </div>
  <div style="flex:1;background:var(--secondary-background-color);border-radius:8px;padding:10px 14px;text-align:center">
    <div style="font-size:11px;color:var(--text-color);opacity:.6;margin-bottom:2px">⭐ Solid (7-8)</div>
    <div style="font-size:22px;font-weight:600;color:var(--text-color)">{solid}</div>
  </div>
  <div style="flex:1;background:var(--secondary-background-color);border-radius:8px;padding:10px 14px;text-align:center">
    <div style="font-size:11px;color:var(--text-color);opacity:.6;margin-bottom:2px">👀 Maybe (5-6)</div>
    <div style="font-size:22px;font-weight:600;color:var(--text-color)">{maybe}</div>
  </div>
  <div style="flex:1;background:var(--secondary-background-color);border-radius:8px;padding:10px 14px;text-align:center">
    <div style="font-size:11px;color:var(--text-color);opacity:.6;margin-bottom:2px">Showing</div>
    <div style="font-size:22px;font-weight:600;color:var(--text-color)">{total}</div>
  </div>
</div>
""")
```

**Last-run banner — keep as-is**, just ensure it comes after the controls bar.

**Sidebar — add a thin divider between filter groups** for visual separation:

```python
# After the View radio and score slider:
st.markdown("---")
# After date/scraped filters:
st.markdown("---")
# Before source filter:
st.markdown("---")
```

**Job cards — tighten the score badge**

The current `### {score_badge(score)}` renders as an h3, which is too large. Replace with:

```python
# In _render_card(), col_badge block:
with col_badge:
    score_str = score_badge(score)
    st.html(f'<div style="font-size:18px;font-weight:700;padding-top:4px">{score_str}</div>')
```

---

## 2. Dashboard page (`tracker_views/dashboard.py`)

### 2a. Add DB stats widget (moved from Settings)

Insert a new "Database" section between the "Pipeline" row and the "Hot Jobs Feed" section.

```python
# ── Row 2b: DB stats ───────────────────────────────────────────────────
st.subheader("Database")
with db._conn() as conn:
    total_jobs      = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    total_scored    = conn.execute("SELECT COUNT(*) FROM job_scores").fetchone()[0]
    unscored        = total_jobs - conn.execute(
        "SELECT COUNT(DISTINCT job_id) FROM job_scores").fetchone()[0]
    total_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    total_contacts  = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    unverified      = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE is_unverified = 1").fetchone()[0]
    total_ixns      = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]

d1, d2, d3, d4 = st.columns(4)
d1.metric("Jobs", total_jobs, f"{total_scored} scored")
d2.metric("Unscored", unscored)
d3.metric("Companies", total_companies)
d4.metric("Contacts", total_contacts, f"{unverified} unverified")
```

### 2b. Improve visual hierarchy

- Move the `🔄 Refresh` button to the sidebar (less visual noise on the main canvas).
- Add `st.divider()` between each major section (At a Glance / Pipeline / Database / Hot Jobs).
- Hot Jobs Feed: increase the score badge from `**{score_badge(...)}**` to a small `st.html` badge for better visual weight (same pattern as job card fix above).

---

## 3. Settings page (`tracker_views/settings.py`)

### 3a. Remove items now on Jobs/Dashboard

Remove the following from `render()` call order:
- `_render_run_controls(db)` — now on Jobs
- `_render_stats_actions(db)` — now on Jobs (Clear Cache, Re-extract) and Dashboard (DB stats)

The `_render_run_controls()` and `_render_stats_actions()` function definitions can stay in the file (no breakage if called elsewhere) but remove them from the `render()` call sequence.

Updated `render()`:
```python
def render():
    ensure_db()
    db = get_db()
    st.title("⚙️ Settings")
    _render_setup(db)
    _render_profile_editor(db)
    _render_scraper_toggles(db)
    _render_reonboard(db)
```

### 3b. Add clear section headers

Wrap the major sections in clear visual groups. Before each `st.subheader` or `st.expander`, add a `st.divider()` so sections breathe. Current order after the above change:

1. 🔧 Setup (expander, collapsed by default — keep as-is)
2. `st.divider()`
3. 🎯 Profile Editor
4. `st.divider()`
5. 🔌 Scraper Toggles
6. `st.divider()`
7. 🔄 Re-run onboarding (expander)

### 3c. Profile Editor — improve the scoring_context field label

The current label "Scoring context (injected at top of LLM scorer system prompt)" is clear but long. Replace with:

```python
scoring_context = st.text_area(
    "Scoring context",
    value=profile.scoring_context,
    height=400,
    help="Injected at the top of the LLM scorer system prompt. Describe your ideal role, priorities, and hard filters.",
)
```

---

## 4. Global CSS (apply in `tracker.py`)

After the existing `st.html(...)` block that hides nav items, append:

```python
st.html("""
<style>
/* Tighten metric label size */
[data-testid="stMetricLabel"] { font-size: 12px !important; }

/* Make job card containers slightly more compact */
[data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] {
    padding: 0.6rem 0.8rem !important;
}

/* Sidebar filter section headers */
.sidebar-section-header {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    opacity: 0.5;
    margin: 1rem 0 0.25rem;
}
</style>
""")
```

---

## 5. Acceptance criteria

- [ ] Jobs page shows Run scrape / Run scoring / Clear Cache / Re-extract buttons at the top, with Unscored count
- [ ] Live subprocess output still works (expander below the controls bar when running)
- [ ] Dashboard shows DB stats row (Jobs, Unscored, Companies, Contacts)
- [ ] Settings page no longer has a "Run" section or "Database Stats" section
- [ ] Settings page retains full Profile Editor, Scraper Toggles, Setup, and Re-onboard
- [ ] Score badge in job cards is smaller (not h3)
- [ ] No regressions: all status buttons, filters, pagination, and notes still work
- [ ] `streamlit run tracker.py` launches without error

---

## Files to modify

| File | Change |
|------|--------|
| `tracker_views/jobs.py` | Add `_render_controls_bar()`, call at top of `_render_list()`, restyle stats row and score badge |
| `tracker_views/dashboard.py` | Add DB stats section, move Refresh to sidebar, add dividers |
| `tracker_views/settings.py` | Remove `_render_run_controls` and `_render_stats_actions` from `render()`, add dividers, improve scoring_context label |
| `tracker.py` | Add global CSS to existing `st.html` block |

## Files to NOT modify

`storage.py`, `profiles.py`, `models.py`, `main.py`, `scrape.py`, `score.py`, any scraper, `tracker_views/shared.py`, `tracker_views/job_helpers.py`, `tracker_views/onboarding.py`, `tracker_views/companies.py`, `tracker_views/contacts.py`, `tracker_views/forms.py`, `tracker_views/job_detail.py`, `tracker_views/company_detail.py`, `tracker_views/contact_detail.py`, `tracker_views/preferences.py`
