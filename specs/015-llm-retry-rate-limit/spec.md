# Spec 013 — LLM retry + rate-limit fix (evaluation scoring failures)

> Spec for Claude Code. Read `llm.py` and `score.py` before starting.
> Do not modify `storage.py`, `profiles.py`, `models.py`, or any scraper.
> Only `llm.py` and `score.py` should change.

---

## Context

Live pipeline runs show a 90%+ LLM failure rate during the evaluation phase:

- Run #143: 397 scraped → 15 scored (96% failure)
- Run #141: 433 scraped → 40 scored (91% failure)
- Run #139: 425 scraped → 35 scored (92% failure)

Root cause (confirmed by Claude Code investigation):

1. **No rate limiting in the evaluation loop** — `score.py` Phase 2 fires all
   `evaluate_for_profile()` calls back-to-back with zero delay between them.
   After ~15 calls, DeepSeek returns 429 (rate limit exceeded). Every subsequent
   job fails.

2. **No retry logic in `llm.call()`** — a single 429, network blip, or transient
   error kills the call permanently. The job stays unscored forever and is retried
   on every future run until it ages out of the 30-day window.

The fix has two parts: add `time.sleep` between evaluation calls in `score.py`,
and add exponential backoff retry inside `llm.call()`.

---

## Fix 1 — Rate limiting in `score.py` evaluation loop

In `score.py`, Phase 2 evaluation loop (around line 364), add a sleep between
calls. The extraction loop already has `time.sleep(4)` — the evaluation loop
needs the same treatment.

```python
# BEFORE (broken — no delay between evaluation calls)
for i, job_dict in enumerate(jobs_to_score, 1):
    ...
    result = score_one(job_dict["id"], profile.id)
    ...

# AFTER — add sleep between evaluations (not after the last one)
for i, job_dict in enumerate(jobs_to_score, 1):
    ...
    result = score_one(job_dict["id"], profile.id)
    ...
    if i < len(jobs_to_score):
        time.sleep(4)   # match extraction loop cadence
```

The `sleep_after` parameter already exists in `llm.call()` (default 1.0s) — that
sleep is INSIDE the LLM call and is separate. The loop-level sleep above is
BETWEEN jobs and should be 4 seconds, consistent with the extraction loop.

---

## Fix 2 — Exponential backoff retry in `llm.call()`

Add retry with exponential backoff directly in `llm.call()`. No new dependency —
implement manually using `time.sleep`.

```python
def call(
    messages: list[dict],
    *,
    json_mode: bool = True,
    max_tokens: int = 600,
    temperature: float = 0.2,
    sleep_after: float = 1.0,
    max_retries: int = 3,        # NEW parameter
    retry_base_delay: float = 5.0,  # NEW parameter
) -> str:
```

Retry logic:

- Retry on: HTTP 429 (rate limit), HTTP 5xx (server error), network/timeout errors
- Do NOT retry on: HTTP 4xx other than 429 (bad request, auth failure — these won't
  self-heal)
- Backoff formula: `retry_base_delay * (2 ** attempt)` — so 5s, 10s, 20s
- After `max_retries` exhausted: re-raise the original exception (caller handles)
- Log each retry: `logger.warning(f"[llm] retry {attempt+1}/{max_retries} after {delay}s (reason: {e})")`

Implementation pattern:

```python
import logging
logger = logging.getLogger(__name__)

# Inside call():
last_exc = None
for attempt in range(max_retries + 1):
    try:
        result = _client.chat.completions.create(**kwargs)
        raw = result.choices[0].message.content
        if sleep_after > 0:
            time.sleep(sleep_after)
        return raw
    except Exception as e:
        last_exc = e
        # Check if retryable
        is_rate_limit = "429" in str(e) or "rate limit" in str(e).lower()
        is_server_error = "5" in str(type(e).__name__) or "500" in str(e) or "503" in str(e)
        is_network = "timeout" in str(e).lower() or "connection" in str(e).lower()
        if not (is_rate_limit or is_server_error or is_network):
            raise  # non-retryable — fail immediately
        if attempt < max_retries:
            delay = retry_base_delay * (2 ** attempt)
            logger.warning(f"[llm] retry {attempt+1}/{max_retries} in {delay:.0f}s: {e}")
            time.sleep(delay)
raise last_exc
```

---

## Parameters and defaults

| Parameter | Default | Rationale |
|---|---|---|
| `sleep_after` | `1.0` | Unchanged — post-call courtesy sleep |
| `max_retries` | `3` | 3 retries = up to 4 total attempts (5+10+20 = 35s max wait) |
| `retry_base_delay` | `5.0` | Conservative — avoids hammering after 429 |
| Loop sleep in `score.py` | `4.0` | Matches existing extraction loop cadence |

---

## What does NOT change

- `llm.call()` signature remains backward-compatible — all existing callers work
  unchanged (new params have defaults)
- No change to `scorer.py`, `job_actions.py`, or any file that calls `llm.call()`
- No new dependency — no `tenacity`, no `backoff` library
- The `sleep_after` parameter behavior is unchanged

---

## Non-objectives

- No circuit breaker or per-run budget cap (future concern)
- No provider failover (spec 014 covers multi-provider)
- No retry telemetry written to DB
- No change to extraction loop (already has 4s sleep and works correctly)

---

## Acceptance criteria

- [ ] A pipeline run with 400+ jobs scores significantly more than 15-40 (target >80%)
- [ ] A 429 response triggers a logged retry with delay, not an immediate failure
- [ ] After 3 retries, the error propagates normally (job stays unscored, logged)
- [ ] `llm.call()` existing callers (scorer.py, company_researcher.py, etc.) work
      unchanged with the new signature
- [ ] The evaluation loop in `score.py` has a `time.sleep(4)` between jobs
- [ ] No new pip dependency introduced
