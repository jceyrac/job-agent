# Research: LLM retry + rate-limit fix

**Feature**: Spec 015
**Date**: 2026-07-01

## Research Tasks

### 1. Where exactly is the evaluation loop in `score.py`?

**Decision**: Lines 364-405 in `score.py` — the `for i, job_dict in enumerate(jobs_to_score, 1)` loop that calls `score_one()`.

**Rationale**: This is the only Phase 2 loop. The extraction loop (lines 331-343) already has `time.sleep(4)` — the evaluation loop is missing it.

**Alternatives considered**: Adding sleep in `job_actions.py:score_one()` or `scorer.py:evaluate_for_profile()` — rejected because callers of those functions may have different rate-limit contexts. The loop in `score.py` is the right place.

### 2. Does `llm.py` currently import `logging`?

**Decision**: No. `llm.py` imports `os`, `time`, `dotenv`, and `openai` only. We must add `import logging`.

**Rationale**: The spec explicitly requires `logger.warning()` for retry messages. `print()` would work but `logging` is the right tool for library-level diagnostic output.

### 3. Are the `sleep_after` and the new loop-level `sleep` redundant?

**Decision**: No — they serve different purposes. `sleep_after` (1.0s) is a courtesy delay after EVERY successful API call. The loop-level `sleep` (4s) is between DIFFERENT jobs. Together they provide ~5s between evaluation calls, matching the extraction loop's ~5s (4s explicit + 1s from `sleep_after`).

**Rationale**: The extraction loop has proven reliable with this cadence. Matching it is conservative and safe.

### 4. How does the OpenAI SDK surface HTTP errors?

**Decision**: Via exception classes (`openai.RateLimitError`, `openai.InternalServerError`, `openai.APITimeoutError`, `openai.APIConnectionError`) with status codes embedded in `str(e)`.

**Rationale**: The spec's string-matching approach (`"429" in str(e)`, `"rate limit" in str(e).lower()`) works because the SDK includes status codes and descriptive messages in the exception string representation.

### 5. Should retry count toward any observable metrics?

**Decision**: Not in this spec — non-objective explicitly states "No retry telemetry written to DB." Log to console via `logger.warning()` only.

**Rationale**: Keeps the change minimal. DB telemetry can be added in a future spec if needed.
