# Build plan — Configurable data directory (`JOB_AGENT_DATA_DIR`)

One Claude Code prompt. Independent of the phase work — but run it on the current
feature branch so safe new-user testing is available while Phase 1/2 are built.

Repo root: `/Users/jeanclaudevd/AI-Suite/job_agent`
Branch: current feature branch (`feat/phase0-depersonalize-search-inputs` or
wherever Phase 1 is stacked). It's small and conflict-free; can also be
cherry-picked to main.

## Why this exists

Almost everything that is "personal" to a given user lives in `data/jobs.db`
(profile, config keys, scored jobs, CRM) and a couple of CV files under `data/`.
The code is identical for every user. So "test as a brand-new user" should mean
"point the app at an empty data directory" — not copy the whole project.

Today `DB_PATH = "data/jobs.db"` is hardcoded in ~14 places and the CV dir is
hardcoded in `prepare.py`. This change routes all of them through one resolver so
`JOB_AGENT_DATA_DIR=data_test streamlit run tracker.py` gives a pristine,
fully-isolated environment (same code, same `.env`, same Groq key), and deleting
`data_test/` resets it. The default stays `data/`, so nothing changes for normal
use. It also gives clean dev/prod separation for the Mac-vs-home-server split and
for Docker volumes.

## What this does NOT touch

- Export/output dirs (`notifier.py` digest `.md`, `preference_report.py`
  `outputs/preference_reports`). Those are notification artifacts, not per-user DB
  state; leave them out of scope (they can follow the same pattern later).
- No behavior change when `JOB_AGENT_DATA_DIR` is unset — must resolve to the
  existing `data/` location.

---

## Prompt — Centralize the data directory

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Introduce one resolver for the data directory and DB path, driven by the env var
JOB_AGENT_DATA_DIR (default: <repo>/data). Replace every hardcoded "data/jobs.db"
and the CV data dir with imports from it. No behavior change when the env var is
unset.

INVESTIGATION FIRST
1. Confirm the full set of references (grep):
   grep -rn 'DB_PATH *=' --include=*.py .
   grep -rn '"data/jobs.db"' --include=*.py .
   grep -rn "join(.*['\"]data['\"]" --include=*.py .
2. Note storage.py has the JobStorage.__init__ default db_path="data/jobs.db"
   (~line 283) and a docstring example (~line 10). Note prepare.py builds the CV
   dir at ~line 208: data_dir = os.path.join(os.path.dirname(__file__), "data").
   Note email_monitor.py argparse --db default "data/jobs.db" (~line 460).

CREATE paths.py (repo root, dependency-free — imports only os)

    """paths.py — single source of truth for the data directory.

    Set JOB_AGENT_DATA_DIR to run the app against an alternate data directory:
      - a throwaway dir for new-user testing (JOB_AGENT_DATA_DIR=data_test)
      - a mounted volume in Docker
    Defaults to <repo_root>/data, preserving previous behavior.
    """
    import os

    _ROOT = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.environ.get("JOB_AGENT_DATA_DIR") or os.path.join(_ROOT, "data")

    # Ensure the dir exists so a fresh (test) data dir works out of the box —
    # SQLite needs the parent dir present to create the DB file.
    os.makedirs(DATA_DIR, exist_ok=True)

    DB_PATH = os.path.join(DATA_DIR, "jobs.db")

    def data_path(*parts: str) -> str:
        """Build a path under the active data directory."""
        return os.path.join(DATA_DIR, *parts)

EDITS — replace each module's hardcoded DB_PATH with an import.
For each of these files, delete the local `DB_PATH = "data/jobs.db"` line and add
`from paths import DB_PATH` near the other imports:
  - main.py
  - scrape.py
  - score.py
  - job_actions.py
  - preference_report.py
  - create_profile.py
  - backfill_descriptions.py
  - migrate_single_status.py
  - migrate_profile_independent_tracking.py
For the two that compute it with os.path.join, replace the computed line with the
import the same way:
  - migrate_expired_status.py (~line 4)
  - scripts/re_extract_sample.py (~line 16)  # runs from repo root, so `from paths
    import DB_PATH` resolves; if it manipulates sys.path, keep that working.
tracker_legacy.py (~line 56): OPTIONAL — it's legacy. Update it too for
consistency if trivial; skip if it complicates anything.

EDITS — storage.py
  - Add `from paths import DB_PATH` at top.
  - Change JobStorage.__init__ signature default to use it:
        def __init__(self, db_path: str = DB_PATH):
  - The module docstring example (~line 10) can stay or be updated to
    JobStorage() (no arg) — cosmetic only.

EDITS — tracker_views/shared.py
  - Replace `DB_PATH = "data/jobs.db"` with `from paths import DB_PATH`.
    (shared.py already imports root modules like storage, so this resolves.)

EDITS — prepare.py
  - Replace the local DB_PATH with `from paths import DB_PATH, DATA_DIR`.
  - In _load_cv_bullets, replace
        data_dir = os.path.join(os.path.dirname(__file__), "data")
    with
        data_dir = DATA_DIR

EDITS — email_monitor.py
  - `from paths import DB_PATH` and set the argparse default:
        parser.add_argument("--db", default=DB_PATH, ...)

EDITS — .gitignore
  - Add a line to ignore alternate data dirs wholesale:
        /data_*/
    (keeps existing data/jobs.db etc. ignores intact; ensures data_test/ and
    similar are never committed.)

DOWNSTREAM NOTE (do not implement here, just be aware)
  - When Phase 2's save_uploaded_cv is built, it must write the uploaded CV via
    paths.data_path("cv_master" + ext), NOT a literal "data/cv_master", so a
    new-user test in data_test/ can't overwrite the real data/cv_master.docx.

DONE WHEN
- Default unchanged:
    python -c "import paths; print(paths.DB_PATH)"
  prints a path ending in /data/jobs.db (the existing location).
- Override works:
    JOB_AGENT_DATA_DIR=/tmp/ja_test python -c "import paths; print(paths.DB_PATH)"
  prints /tmp/ja_test/jobs.db, and /tmp/ja_test/ now exists.
- grep -rn '"data/jobs.db"' --include=*.py . returns only the storage.py docstring
  example (if left) and the scrapers/indeed.py docstring example — no runtime
  literals (migrations/legacy optionally updated).
- python scrape.py and streamlit run tracker.py both operate on paths.DB_PATH
  (run one against JOB_AGENT_DATA_DIR=data_test and confirm data_test/jobs.db is
  created while data/jobs.db is untouched).
- pytest stays green.
```

---

## How you'll use it (new-user testing)

```bash
# brand-new user, fully isolated, your real data/ never touched
JOB_AGENT_DATA_DIR=data_test streamlit run tracker.py
# ... walk the wizard / app as a new user ...
rm -rf data_test            # reset to a clean slate

# your normal setup is unchanged
streamlit run tracker.py    # uses data/jobs.db as before
```

For the cold-start install dress-rehearsal later (Phase 3), prefer a fresh Docker
volume — set `JOB_AGENT_DATA_DIR` in the container env or mount a throwaway volume
at the data path — which also validates the deployment path a self-hoster takes.

## One-line summary for the commit

> chore: route the data directory through JOB_AGENT_DATA_DIR (default data/) so
> the app can run against an isolated data dir for testing and deployment.
