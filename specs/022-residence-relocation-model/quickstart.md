# Quickstart — Residence & Relocation Model

**Created**: 2026-07-09 | **Plan**: [plan.md](plan.md)

## Prerequisites

- Dev Mac, Python 3.11 venv
- Live DB snapshot available (via `scripts/sync_live_db.sh`) or mock-only testing
- DeepSeek API key configured for mock tests

## Step 1: Mock-only smoke test

This validates the core logic without DB schema changes:

```bash
python score.py --mock --profile unified_jc
```

Expected: all existing cases in band; 4 new cases show score/band output:
- MoscowChain (remote, salary ≥ floor) → score 4–8, Tier-1 (feasible, cost 4)
- MoscowBank (on-site RU) → score 2, Tier-0 (no feasible residence)
- MoscowStartup (salary below floor) → score 2, Tier-0 (salary floor)
- ParisChain (hybrid FR, Web3) → score 7–9, Tier-1 (feasible, cost 2)

Note: the two RUB cases use `requires_spec_023` band `(4, 8)` until RUB line lands in extraction prompt.

## Step 2: Run migration on Dev DB

```bash
# The migration runs automatically on first storage.py load
python -c "from storage import JobStorage; db = JobStorage('data/jobs.db'); print('Migrated')"
```

Verify columns exist:
```bash
sqlite3 data/jobs.db "PRAGMA table_info(job_scores)" | grep -E 'relocation_cost|residence_base'
```

Expected: two rows, `relocation_cost INTEGER` and `residence_base TEXT`.

## Step 3: Settings round-trip

1. Launch `streamlit run tracker.py`
2. Navigate to **Settings → Profile Editor**
3. Find the new **Residence & relocation** section
4. Add a France residence row: cost 2, modes remote/hybrid/on-site, employer countries from the remote list
5. Click **Save profile**
6. Verify persistence:
   ```bash
   sqlite3 data/jobs.db "SELECT json_extract(criteria, '$.relocation') FROM search_profiles WHERE id='unified_jc'" | python -m json.tool | head -20
   ```

## Step 4: Rescore with new model

```bash
python score.py --profile unified_jc --rescore
```

Expected: jobs that were previously filtered by per-mode geography now flow through Engagement resolution. Paris/FR hybrid jobs pass (cost 2, feasible). RU on-site jobs blocked.

## Step 5: Job card badge

1. In the tracker Jobs page, find a job where `residence_base` ≠ "switzerland"
2. Expected: small badge `🧳 france · cost 2` on the job card

## Step 6: FELFEL regression

```bash
python score.py --mock --profile unified_jc 2>&1 | grep "FELFEL"
```

Expected: FELFEL still scores 5–7 (Swiss hybrid employer, zero-cost residence, no change from current behavior).

## Step 7: Full test suite

```bash
python -m pytest tests/ -q
```

Expected: storage tests pass (146+), jobspy_helpers pass (6). The 15 pre-existing scorer_parsing failures remain (stale `_EvalProfile` stubs — out of scope).
