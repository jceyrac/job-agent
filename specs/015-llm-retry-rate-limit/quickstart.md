# Quickstart: LLM retry + rate-limit fix

**Feature**: Spec 015
**Date**: 2026-07-01

## Prerequisites

- Working DB at `data/jobs.db` with unscored jobs
- `LLM_API_KEY` configured in `.env`
- Python 3.11 venv activated

## Validation Scenarios

### Scenario 1: Normal scoring works (no regressions)

```bash
# Score a few jobs to verify the retry logic doesn't break normal flow
python score.py --profile unified_jc --limit 3
```

**Expected**: Extracts and scores 3 jobs normally. No retry messages. Normal output format preserved.

### Scenario 2: Retry message appears on rate limit

Force rate limit by scoring many jobs:
```bash
python score.py --profile unified_jc --limit 50
```

**Expected**: If 429 is hit, see messages like:
```
[llm] retry 1/3 in 5s: Error code: 429 - Rate limit reached
```
Followed by successful completion after backoff.

### Scenario 3: Backward compatibility

```python
# Existing callers should work unchanged
from llm import call
result = call([{"role": "user", "content": "Hello"}])
```

**Expected**: Works with no code changes needed in callers (`scorer.py`, `company_researcher.py`, etc.).

### Scenario 4: Non-retryable errors fail fast

A 400 (bad request) should NOT retry — it should raise immediately. Verify by inspecting the retry logic: only 429, 5xx, timeout, and connection errors trigger retry.

### Scenario 5: Existing tests still pass

```bash
python -m pytest tests/ -q
```

**Expected**: All 189 tests pass.

## Acceptance Criteria Trace

| Criteria | Validation |
|----------|------------|
| >80% scoring rate | Run full pipeline, check scored/total ratio |
| 429 triggers logged retry | Check console output for `[llm] retry` messages |
| After 3 retries, error propagates | Job stays unscored, logged to console |
| Existing callers work unchanged | `python -m pytest tests/` passes |
| `time.sleep(4)` in evaluation loop | `grep "time.sleep(4)" score.py` finds it in Phase 2 loop |
| No new pip dependency | `git diff main -- requirements.txt` is empty |
