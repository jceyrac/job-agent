# SPEC — tracker.py (Streamlit Job Tracker + Settings)

> Spec for Claude Code. Read `storage.py`, `profiles.py`, and `models.py` before starting.  
> Do not modify `main.py`, `storage.py`, `profiles.py`, or any scraper.

---

## Goal

Build `tracker.py` — a Streamlit app that:
1. Lets the user browse, filter, and update the status of scored jobs (Tab 1 — **Jobs**)
2. Lets the user switch between search profiles and edit their parameters (Tab 2 — **Settings**)

The app is the **only** interface to the SQLite DB for human interaction. It does not run the agent — it reads from what `main.py` has already written.

---

## How to run

```bash
streamlit run tracker.py
```

Default port: 8501. No auth required (local use only).

---

## Install dependency

Add to `requirements.txt` (or pip install manually):
```
streamlit
```

No other new dependencies. All data comes from existing `storage.py` methods.

---

## File to create

`tracker.py` at project root. Single file, ~300–400 lines max. No subfolders.

---

## Tab 1 — Jobs

### Layout

- Full-width table/card view of scored jobs
- Sidebar (left) for filters
- Main area for job list

### Sidebar filters

All filters are session-state only (do not persist to DB):

| Filter | Type | Values |
|--------|------|--------|
| Active profile | selectbox | All profile IDs from `storage.get_all_profiles()` — see note below |
| Score minimum | slider | 1–10, default 5 |
| Status | multiselect | `new`, `saved`, `applied`, `rejected`, `archived` — default: `new` |
| Work mode | multiselect | `remote`, `hybrid`, `on-site`, `unknown` — default: all |
| Geo zone | multiselect | `europe`, `global_remote`, `us_only`, `unknown` — default: all |
| Company size | multiselect | `startup`, `scaleup`, `sme`, `large`, `unknown` — default: all |
| Source | multiselect | Populated dynamically from `source` field in results |

> **Note on `get_all_profiles()`**: This method does not exist yet in `storage.py`. Add it:
> ```python
> def get_all_profiles(self) -> list[dict]:
>     with self._conn() as conn:
>         rows = conn.execute("SELECT id, name, criteria FROM search_profiles").fetchall()
>         return [dict(r) for r in rows]
> ```

### Job cards

Display each job as a card (use `st.container` + columns). Each card shows:

```
[Score badge]  Job Title @ Company             [Source tag]
               📍 Location  🌍 Geo  work_mode  company_size
               Summary (2–3 lines, truncated)
               [🔗 Open] [💾 Save] [✅ Applied] [❌ Reject] [🗃 Archive]
```

**Score badge colors:**
- 9–10 → red/hot 🔥
- 7–8 → orange ⭐  
- 5–6 → grey 👀

**Status buttons:** Clicking a status button calls `db.set_status(job_id, profile_id, status)` and refreshes the view via `st.rerun()`. Only show buttons for statuses the job is not already in. Always show `[🔗 Open]` which opens `job.url` in a new tab.

**Notes field:** Below the buttons, a small `st.text_area` (collapsed by default, expandable) that calls `db.set_status(job_id, profile_id, current_status, notes=text)` on change.

### Stats bar

Above the job list, a single row of metrics:

```
Total: 42  |  🔥 Hot (9-10): 16  |  ⭐ Solid (7-8): 8  |  👀 Maybe (5-6): 18
New: 35  |  Saved: 4  |  Applied: 2  |  Rejected: 1
```

Use `db.get_stats(profile_id)` for this.

### Data loading

```python
jobs = db.get_all_for_tracker(profile_id)
```

Then apply sidebar filters in Python (not SQL) for responsiveness. No pagination needed — dataset stays small (<500 rows).

---

## Tab 2 — Settings

### Purpose

Switch between existing profiles and edit their parameters. Changes are saved to the `search_profiles` table in the DB AND to the in-memory `profiles.py` dataclass instance. They take effect on the **next** `main.py` run.

### Layout

Two columns:
- Left (30%): Profile selector + "Active profile" indicator
- Right (70%): Edit form for the selected profile

### Profile selector (left)

- `st.radio` listing all profiles by name
- Displays which profile is currently active (read from a `config` key in the DB — see below)
- Button: **"Set as active"** → writes `active_profile_id` to DB config table

> **Config table**: Add a simple key-value table to `storage.py` schema:
> ```sql
> CREATE TABLE IF NOT EXISTS config (
>     key TEXT PRIMARY KEY,
>     value TEXT
> );
> ```
> Add two methods to `JobStorage`:
> ```python
> def get_config(self, key: str, default=None) -> str | None:
>     with self._conn() as conn:
>         row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
>         return row["value"] if row else default
>
> def set_config(self, key: str, value: str) -> None:
>     with self._conn() as conn:
>         conn.execute(
>             "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
>             (key, value)
>         )
> ```
> The active profile is stored as: `db.set_config("active_profile_id", profile_id)`  
> `main.py` should read it at startup: `db.get_config("active_profile_id", default=DEFAULT_PROFILE_ID)`

### Profile edit form (right)

For the selected profile, show an editable form using `st.form`:

| Field | Widget | Maps to |
|-------|--------|---------|
| Profile name | `text_input` | `profile.name` |
| Allowed geo zones | `multiselect` | `allowed_geo_zones` — options: `europe`, `global_remote`, `us_only`, `apac`, `latam`, `unknown` |
| Allowed work modes | `multiselect` | `allowed_work_modes` — options: `remote`, `hybrid`, `on-site`, `unknown` |
| Location keywords | `text_area` (one per line) | `location_keywords` — split on newline |
| Boost keywords | `text_area` (one per line) | `boost_keywords` |
| Company sizes | `multiselect` | `company_sizes` — options: `startup`, `scaleup`, `sme`, `large` |
| Score threshold | `slider` | `score_threshold` — range 1–10 |
| Remote or hybrid only | `checkbox` | `remote_or_hybrid` |

**Save button**: On submit, call `db.upsert_profile(updated_profile)` and show `st.success("Profile saved. Changes apply on next run.")`.

**No delete profile** for now — keep it simple.

### Adding a new profile (stretch goal — implement only if clean)

A button "New profile" that opens a form pre-populated with `WEB3_REMOTE` defaults. On save, appends to `ALL_PROFILES` dict in memory and calls `db.upsert_profile()`. Skip if it complicates the implementation significantly.

---

## Changes required to existing files

### `storage.py`

Three additions only — do not touch existing methods:

1. Add `config` table to `SCHEMA` string
2. Add `get_config(key, default)` method  
3. Add `set_config(key, value)` method
4. Add `get_all_profiles()` method

### `main.py`

One change only:

Replace the hardcoded `profile = WEB3_REMOTE` with:

```python
from profiles import ALL_PROFILES, DEFAULT_PROFILE_ID

db = JobStorage(DB_PATH)
active_profile_id = db.get_config("active_profile_id", default=DEFAULT_PROFILE_ID)
profile = ALL_PROFILES.get(active_profile_id, ALL_PROFILES[DEFAULT_PROFILE_ID])
```

This makes `main.py` respect whatever profile was set in the Streamlit UI.

---

## State management

Use `st.session_state` for:
- Selected profile in the sidebar
- Filter values
- Expanded/collapsed notes per job

Do not use `st.cache_data` on the main job list — data should refresh on `st.rerun()` after status changes.

Use `@st.cache_data(ttl=0)` or no cache at all. The dataset is small enough.

---

## Error handling

- If `data/jobs.db` does not exist → show `st.warning("No data yet. Run main.py first.")` and stop
- If a profile has no scored jobs → show `st.info("No jobs found for this profile and filters.")`
- If `db.set_status()` fails → show `st.error(...)` with the exception message

---

## Style notes

- Dark theme compatible (Streamlit default dark mode)
- No custom CSS required — use native Streamlit components only
- Score badges: use `st.markdown` with colored inline HTML if needed, or emoji prefix
- Keep the UI functional over decorative — this is a personal tool

---

## Explicit non-goals (do not build)

- No authentication
- No ability to trigger `main.py` from the UI (cron handles this)
- No charts or analytics views (future tracker v2)
- No WebDAV / Nextcloud / Joplin integration in this file
- No drag-and-drop kanban

---

## Acceptance criteria

- [ ] `streamlit run tracker.py` launches without error on a populated `data/jobs.db`
- [ ] Switching profile in sidebar reloads the job list for that profile
- [ ] Clicking "Save" on a job updates its status immediately (no page reload required — `st.rerun()`)
- [ ] Editing a profile in Settings and saving → verifiable in DB with `sqlite3 data/jobs.db "SELECT * FROM search_profiles"`
- [ ] Setting an active profile → `main.py` uses it on next run
- [ ] All sidebar filters work correctly in combination
- [ ] App shows warning gracefully when DB is empty
