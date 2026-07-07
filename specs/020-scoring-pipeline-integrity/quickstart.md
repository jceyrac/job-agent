# Quickstart — Scoring Pipeline Integrity

**Created**: 2026-07-07 | **Plan**: [plan.md](plan.md)

Validation guide for testing the changes end-to-end.

## Prerequisites

- Dev environment (Mac, Python 3.11 venv, `data/jobs.db`)
- Tailscale connected to verva (for DB sync)
- Access to `scripts/sync_live_db.sh` (Goal 5)

## Step 1: Sync Live DB to Dev

```bash
# Pull a read-only snapshot from verva over Tailscale
bash scripts/sync_live_db.sh

# Back up current dev DB, then swap in the snapshot
cp data/jobs.db data/jobs_dev_backup.db
cp data/jobs_live_snapshot.db data/jobs.db
```

Expected: `data/jobs.db` now contains verva's production data. Streamlit launches without migration errors.

## Step 2: Verify profile sync fix (Goal 1)

1. Launch Streamlit: `streamlit run tracker.py`
2. Go to **Settings → Profile Editor**
3. Inspect `unified_jc`'s `scoring_context` value
4. Edit the `scoring_context` in the DB (via `sqlite3`) to something different from `profiles.py`'s `UNIFIED_JC`
5. Restart Streamlit → **Settings → Profile Editor**
6. **Expected**: The DB value persists unchanged. No code fallback overwrites it.

```bash
# Manual test: check the DB directly
sqlite3 data/jobs.db "SELECT json_extract(criteria, '$.scoring_context') FROM search_profiles WHERE id='unified_jc'" | head -c 200
```

## Step 3: Verify location pre-filter removal (Goal 2)

1. With the synced Live DB, run scoring from the UI:
   - **Jobs page** → "🎯 Run scoring"
2. **Expected**: Candidate count > 0 (previously showed 0 because `"usa"` matched `"Lausanne"`).
3. **Expected**: The 2 Lausanne jobs identified in the original investigation reach Tier-0/LLM evaluation.
4. Check the Settings → Profile Editor → "Advanced scrape inputs" expander:
   - The `exclude_location_contains` field should show "(legacy — no longer used by scoring)" in its label or help text.

```bash
# Command-line verification
python score.py --profile unified_jc
# Should show: "Pre-filter applied: N jobs → M after SQL filter" with M > 0
```

## Step 4: Verify Unscored metric (Goal 3)

1. On the **Jobs** page, note the "Unscored" number.
2. Run `len(get_jobs_for_scoring("unified_jc"))` in a Python shell:
   ```python
   from storage import JobStorage
   from profiles import load_active_profile
   db = JobStorage("data/jobs.db")
   profile = load_active_profile(db)
   print(len(db.get_jobs_for_scoring(profile.id)))
   ```
3. **Expected**: The UI number matches the Python call.
4. Repeat on the **Settings** page (if the "Unscored" metric is still shown there).

## Step 5: FELFEL regression test (Goals 1-2)

```python
from storage import JobStorage
from scorer import evaluate_for_profile
from profiles import load_active_profile

db = JobStorage("data/jobs.db")
profile = load_active_profile(db)

# Find the FELFEL job
jobs = db.get_jobs_for_scoring(profile.id)
felfel = [j for j in jobs if "felfel" in (j.get("company") or "").lower()]
# Should still score 6-8 for unified_jc
```

## Step 6: Verify hardening fixes (Goal 4)

### Re-extract timeout

Run extraction on many jobs with a short timeout:
```bash
# This should time out gracefully, not crash the page
# (Hard to trigger organically — the key check is code review:
#  try/except subprocess.TimeoutExpired wraps the subprocess.run call)
```

### Message flash

1. Click "Run scrape" or "Run scoring" on the Jobs page.
2. **Expected**: When the process completes, the success/error message is visible for one full render before the next automatic refresh.
3. **Expected**: The message does NOT flash and disappear instantly.

## Step 7: Clean up

```bash
# Restore original dev DB (when done testing)
cp data/jobs_dev_backup.db data/jobs.db
```
