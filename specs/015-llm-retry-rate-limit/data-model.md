# Data Model: LLM retry + rate-limit fix

**Feature**: Spec 015
**Date**: 2026-07-01

## Schema Impact

**No changes.** This feature modifies only the calling patterns around existing API calls. It does not:

- Add, remove, or modify any table or column
- Change any `JobStorage` method
- Introduce new statuses or state transitions

## Interface Change: `llm.call()`

Two new keyword-only parameters with defaults:

| Parameter | Type | Default | Purpose |
|-----------|------|---------|---------|
| `max_retries` | `int` | `3` | Number of retry attempts before giving up |
| `retry_base_delay` | `float` | `5.0` | Base seconds for exponential backoff (5s, 10s, 20s) |

Existing callers are unaffected — all new params have defaults and the return type (`str`) is unchanged.

## Call Flow Change

```
Before:
  score.py loop → score_one() → evaluate_for_profile() → llm.call() → OpenAI API
  ↑ no delay between iterations                     ↑ no retry on failure

After:
  score.py loop → time.sleep(4) → score_one() → evaluate_for_profile() → llm.call()
  ↑ 4s between jobs                                                    ↑ up to 3 retries
                                                                         with exponential backoff
```
