# Build plan — per-job actions + sequential pipeline

Six Claude Code prompts to run in order. Each is self-contained.

Repo root: `/Users/jeanclaudevd/AI-Suite/job_agent`
DB path used everywhere: `data/jobs.db`

---

## Prompt 1 — Add `--extract` step to the pipeline runner

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Make `python main.py` run the full pipeline: scrape → extract → score, sequentially.

CURRENT STATE
main.py runs only scrape then score. Read main.py to confirm. It currently does:
    subprocess.run([sys.executable, "scrape.py"], check=True)
    subprocess.run([sys.executable, "score.py", "--profile", active_profile_id], check=True)

CHANGE
Insert a new Step 2 between scrape and score that runs profile-independent
extraction across all jobs:
    subprocess.run([sys.executable, "score.py", "--extract"], check=True)

Renumber the existing scoring step to Step 3 in its print banner.

WHY
score.py --profile already extracts the survivors that pass its pre-filter
(see score.py lines ~429-471), but the dedicated --extract pass covers ALL
jobs in the DB regardless of pre-filter, which the user wants as the canonical
pipeline.

CONSTRAINTS
- Keep the same arg parsing (--profile override).
- Keep check=True on every subprocess.run so a failure halts the pipeline.
- No other behavior changes.

VERIFY
1. `python main.py --help` still prints the same help text.
2. Read the diff — only main.py should change, and only the middle block.
3. Don't actually run scrape (network-heavy); just dry-check by reading the
   final file end-to-end.
```

---

## Prompt 2 — Create `job_actions.py` with the three single-job helpers

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Create a new module `job_actions.py` that exposes three single-job helpers
the UI (and the CLI) can call:

    extract_one(job_id: str) -> dict | None
    score_one(job_id: str, profile_id: str) -> dict | None
    prepare_one(job_id: str, profile_id: str | None = None,
                redo: bool = False) -> dict | None

Each loads the job from DB, runs the corresponding scorer/prepare logic,
persists the result, and returns a small status dict (or None on hard failure).

CONTEXT — what already exists
- scorer.extract_job_fields(job: JobPosting) — extracts one job's structured
  fields; see scorer.py around line 613.
- scorer.evaluate_for_profile(job: JobPosting, profile) — scores one job for
  one profile; see scorer.py around line 711.
- prepare.prepare_job_application(job_id, profile_id, redo, mock) — full
  application package; see prepare.py around line 352. THIS ONE ALREADY
  WORKS ON A SINGLE JOB — just import and call it.
- storage.JobStorage.get_job_for_prepare(job_id) — loads a job dict with
  all extracted/joined fields (storage.py around line 1362).
- storage.JobStorage.update_job_extraction(job_id, fields) — persists the
  8 extraction fields (storage.py around line 1020).
- storage.JobStorage.save_scored(job, result, profile_id) — persists a score
  (storage.py around line 1099).
- storage.JobStorage.save_unscored(job) — fallback on LLM failure (~line 1128).
- models.JobPosting — the dataclass.
- profiles.ALL_PROFILES — dict of profile_id → SearchProfile.
- score.py `_dict_to_posting(d)` — reconstructs a JobPosting from a DB row dict.
  This helper is already in score.py around line 304. You can either:
    a) import it (cleanest), or
    b) move it into a small shared module if you find circular-import issues.
  Prefer (a) first; only do (b) if you hit a circular import.
- score.py `_discover_contacts(job, description, company_id, db)` — runs
  regex (+ optional LLM) contact extraction after extraction. Around line 85.
  Reuse this from job_actions too (import it from score).

IMPLEMENTATION

DB_PATH = "data/jobs.db"

def extract_one(job_id: str) -> dict | None:
    """
    Run profile-independent field extraction for one job.
    Idempotent: if job.extracted_at is already set, skip the LLM call and
    return {"status": "already_extracted"}.

    Returns a dict like:
      {"status": "ok", "extracted_by": "<model>", "summary": "...", ...}
      {"status": "already_extracted"}
      {"status": "error", "error": "<msg>"}
      None  -> only on truly unrecoverable load failure
    """
    1. db = JobStorage(DB_PATH)
    2. row = db.get_job_for_prepare(job_id); if None -> return None
    3. job = _dict_to_posting(row)  (imported from score)
    4. if job.extracted_at is not None: return {"status": "already_extracted"}
    5. result = extract_job_fields(job); if None -> return {"status":"error",...}
    6. db.update_job_extraction(result.id, { the 8 fields exactly like score.py
       does at lines ~257-267 and ~445-455 })
    7. _discover_contacts(result, result.description or "", row.get("company_id"), db)
    8. Return {"status":"ok","extracted_by":result.extracted_by,
              "summary":result.summary, ...other fields useful for UI feedback}

def score_one(job_id: str, profile_id: str) -> dict | None:
    """
    Run extract-if-needed + evaluate_for_profile for one job for one profile.
    If the job has no extraction yet, run extract_one first.

    Returns:
      {"status":"ok","score":int,"reason":str,"scored_by":str}
      {"status":"error","error":"<msg>"}
      None on unrecoverable load failure
    """
    1. Validate profile_id in ALL_PROFILES; if not, return {"status":"error",
       "error":f"unknown profile {profile_id}"}
    2. db = JobStorage(DB_PATH); row = db.get_job_for_prepare(job_id); if None -> None
    3. If row.get("extracted_at") is None:
         ext = extract_one(job_id)
         if ext is None or ext.get("status") == "error":
             return ext or {"status":"error","error":"extraction failed"}
         row = db.get_job_for_prepare(job_id)  # re-read post-extraction
    4. profile = ALL_PROFILES[profile_id]; db.upsert_profile(profile)
    5. job = _dict_to_posting(row)
    6. result = evaluate_for_profile(job, profile)
    7. If result is None:
         db.save_unscored(job); return {"status":"error","error":"evaluation failed"}
    8. db.save_scored(job, result, profile_id)
    9. Return {"status":"ok","score":result["score"],"reason":result["reason"],
              "scored_by":result["scored_by"]}

def prepare_one(job_id, profile_id=None, redo=False) -> dict | None:
    """Thin wrapper around prepare.prepare_job_application.
    Returns the result dict from prepare_job_application, or {"status":"error",...}.
    """
    from prepare import prepare_job_application
    try:
        result = prepare_job_application(
            job_id=job_id, profile_id=profile_id, redo=redo, mock=False)
        if result is None:
            return {"status":"already_prepared_or_missing"}
        return {"status":"ok", **result}
    except Exception as e:
        return {"status":"error","error":str(e)}

CONSTRAINTS
- New file ONLY. Do not modify scorer.py, prepare.py, storage.py, or score.py
  in this prompt — refactoring those is Prompt 3.
- Imports at top: from dotenv import load_dotenv; load_dotenv() (before any
  other project imports, mirroring how score.py and prepare.py start).
- Don't catch SystemExit. Don't `sys.exit()` from these helpers — they must
  return values so the UI can render errors instead of crashing.
- Don't print() inside these helpers (or keep prints minimal and use a
  module-level logger). The UI will surface results via st.spinner/st.success.

VERIFY
1. `python -c "from job_actions import extract_one, score_one, prepare_one; print('ok')"`
2. Pick a random job_id from the DB:
     sqlite3 data/jobs.db "SELECT id FROM jobs LIMIT 1"
   and call extract_one in a quick REPL to confirm no exceptions. If the job
   is already extracted you should get {"status":"already_extracted"}.
3. Do NOT call score_one or prepare_one in verification — they hit the LLM
   API and cost tokens. The user will exercise those via the UI.
```

---

## Prompt 3 — Refactor `score.py` and `prepare.py` to use the helpers

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Make score.py and prepare.py call the new job_actions helpers for their
per-job inner work, so the UI and the CLI run identical logic.

PREREQUISITE
job_actions.py exists with extract_one, score_one, prepare_one (see Prompt 2).

WHAT TO CHANGE IN score.py
- The `_run_extraction(limit)` function (around line 219) — replace its per-job
  body (the loop body that calls extract_job_fields, update_job_extraction,
  and _discover_contacts) with a call to job_actions.extract_one(job["id"]).
  Keep the outer loop, the print statements, the time.sleep(4), the
  limit handling, and the final summary print.
- In `main()` Phase 1 extraction (around lines 429-471) — same refactor: keep
  the loop and prints, replace the per-job body with extract_one().
- In `main()` Phase 2 evaluation (around lines 491-518) — keep the loop and
  prints, but replace the per-job body (evaluate_for_profile + save_scored/
  save_unscored) with a call to score_one(job["id"], profile.id). Make sure
  the tier0/tier1 counter logic still works by reading score_one's return dict.

WHAT TO CHANGE IN prepare.py
- The single-job path in `main()` (the `prepare_job_application(...)` call
  around line 813) and the batch loop in `_run_ready` (around line 738) can
  stay as-is — they already call prepare_job_application directly, which IS
  the same function prepare_one wraps. No change needed in prepare.py.
  (But re-read it to be sure nothing else duplicates the single-job logic.)

CONSTRAINTS
- The CLI behavior must remain identical: same prints, same exit codes, same
  --extract / --profile / --rescore / --mock / --limit semantics.
- `_dict_to_posting` MUST stay importable from score.py at the same name
  (job_actions imports it from there).
- Don't refactor anything not listed above. Keep the diff minimal.

VERIFY
1. `python score.py --help` shows the same help.
2. `python score.py --extract --limit 1` runs without errors on one job
   (it's fine if that job is already extracted — should print 0 to extract).
3. `python score.py --profile unified_jc --limit 1` runs without errors.
4. `python prepare.py --help` shows the same help.
5. Diff is contained to score.py only (and possibly a tiny touch in prepare.py
   if you spot duplication).
```

---

## Prompt 4 — Action buttons on job cards

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent
File: tracker_views/jobs.py

GOAL
Add three action buttons to each job card in the jobs list view:
  🔍 Extract     — runs job_actions.extract_one(job_id)
  🎯 Score       — runs job_actions.score_one(job_id, active_profile_id)
  📝 Prepare     — runs job_actions.prepare_one(job_id, profile_id=None)

PREREQUISITE
job_actions.py exists with extract_one, score_one, prepare_one.

LOCATION
_render_card() function in tracker_views/jobs.py (around line 117). The
existing button row is at lines 168-211 — add the new buttons to the same
row (`btn_defs` list) but be careful: Streamlit columns blow up if you
have too many buttons in one row. If btn_defs would exceed 6 entries, split
into a second row of columns below.

VISIBILITY RULES
- 🔍 Extract: show only when job.get("extracted_at") is None. Tooltip:
  "Run profile-independent extraction (work mode, sector, country, etc.)"
- 🎯 Score: show only when active_profile_id is set in sidebar AND
  job.get("score") is None for that profile. Read active profile from
  st.session_state or recompute from `get_db().get_config("active_profile_id")`.
- 📝 Prepare: always show. If app already exists (db.get_application(job_id)
  returns truthy with prepared_at), change label to "📝 Re-prepare" and
  pass redo=True.

ON CLICK BEHAVIOR
Each button, when clicked:
  with st.spinner("Extracting…"):  # or "Scoring…" / "Preparing…"
      result = extract_one(job_id)  # or score_one / prepare_one
  if result is None or result.get("status") == "error":
      st.error(f"Failed: {result.get('error') if result else 'unknown'}")
  else:
      st.success(f"Extracted: {result.get('summary','')[:80]}")  # vary per action
      st.cache_data.clear()
      st.rerun()

For Score, the success message should include score + reason:
  st.success(f"Scored {result['score']}/10 — {result['reason'][:80]}")
For Prepare, success message:
  st.success(f"Prepared by {result.get('prepared_by','?')}")

IMPORTS
At top of tracker_views/jobs.py, add:
  from job_actions import extract_one, score_one, prepare_one

job_agent root needs to be on sys.path when running the tracker. Check
tracker.py for how it sets up the path — if it appends the repo root,
the import works as-is. If not, add `sys.path.insert(0, os.path.dirname(
os.path.dirname(__file__)))` near the top of tracker_views/jobs.py.

CONSTRAINTS
- Don't break the existing status-change buttons (Queue, Applied, Rejected,
  Not relevant). Their click handlers are right next to where you're adding.
- Each new button needs a unique `key=` ending in job_id, matching the
  convention of the others (e.g., key=f"extract_{job_id}").
- The Prepare action is slow (10-30s, 4 LLM calls). The spinner must wrap
  the whole call so the user sees feedback.

VERIFY
1. `streamlit run tracker.py` starts without import errors.
2. Open the Jobs view — cards render the new buttons under the right
   visibility rules.
3. Click Extract on a job whose extracted_at is NULL — spinner shows,
   then success toast, then the card re-renders without the Extract button.
4. Don't click Prepare during verification unless you want to burn tokens.
```

---

## Prompt 5 — Same buttons on the detail view

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent
File: tracker_views/jobs.py

GOAL
Add the same Extract / Score / Prepare buttons to the job detail view's
Actions row, using the same job_actions helpers and the same visibility
rules and spinner/success/error UX defined in Prompt 4.

LOCATION
_render_detail() function in tracker_views/jobs.py (around line 249). The
existing actions row is at lines 320-345 (`st.subheader("Actions")` and the
`btn_cols = st.columns(6)` block).

CONSTRAINTS
- Use distinct key= suffixes (e.g., `detail_extract_{job_id}`) to avoid
  collisions with the card buttons.
- If 6 columns isn't enough, bump to 8 or split into two rows.
- Keep the existing "🔗 Open / 🚀 Queue / ✅ Applied / ❌ Rejected / 🚫 Not
  relevant" buttons intact.

VERIFY
1. Open the detail view of a job — three new buttons render alongside the
   status buttons.
2. The buttons respect the same visibility rules as the card.
3. Clicking Score (with active profile set) re-renders the page with the
   updated score badge and reason at the top.
```

---

## Prompt 6 — End-to-end smoke test

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Verify the full chain works after Prompts 1-5: CLI pipeline + UI buttons.

DO NOT modify code. Only run commands and report findings.

CHECKS
1. `python main.py --help` — confirm same args as before.
2. `python score.py --help` and `python prepare.py --help` — unchanged.
3. `python score.py --extract --limit 1` — runs without error.
4. `python score.py --profile unified_jc --limit 1` — runs without error.
5. `python -c "from job_actions import extract_one, score_one, prepare_one"`
6. Pick the most recent unextracted job (if any):
     sqlite3 data/jobs.db "SELECT id FROM jobs WHERE extracted_at IS NULL
                            ORDER BY scraped_at DESC LIMIT 1"
   Run extract_one on it from a REPL. Re-query the DB and confirm
   extracted_at is now set.
7. `streamlit run tracker.py` — start the tracker, then STOP it after
   confirming no import errors in the console (no need to click through).
8. Read outputs/applications/ — confirm the directory exists. It's fine if
   no new files were written; this test isn't supposed to call prepare.

REPORT
A short pass/fail line per check, plus any surprises. If anything fails,
quote the error verbatim and stop — don't try to fix it inside this prompt.
```

---

## Run order
1. → 2. → 3. → 4. → 5. → 6.

Pause after Prompt 3 to confirm the CLI still behaves the way you remember. The UI work in 4 & 5 depends on the helpers in 2 being solid, but doesn't depend on the refactor in 3 — so if 3 looks risky you can defer it.
