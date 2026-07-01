# Tasks: LLM retry + rate-limit fix (evaluation scoring failures)

**Input**: Design documents from `/specs/015-llm-retry-rate-limit/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: Not requested — existing 189 tests must continue to pass.

**Organization**: Two independent user stories (different files, no cross-dependency).

---

## Phase 1: Setup

**Purpose**: Verify current state before changes.

- [x] T001 Verify `llm.py` and `score.py` are importable and existing tests pass: 189 passed

---

## Phase 2: User Story 1 — Rate limiting in evaluation loop (Priority: P1) 🎯

**Goal**: Add `time.sleep(4)` between evaluation calls in `score.py` Phase 2 loop so DeepSeek's rate limit isn't triggered after ~15 back-to-back calls.

**Independent Test**: `grep "time.sleep(4)" score.py` shows the sleep in the evaluation loop (not just the extraction loop).

### Implementation

- [x] T002 [US1] Add `time.sleep(4)` between evaluation calls in `score.py` Phase 2 loop — after `score_one()` call, before next iteration. Insert at line ~385 (after the `scored_count += 1` block):
  ```python
  if i < len(jobs_to_score):
      time.sleep(4)
  ```

**Checkpoint**: Evaluation loop now has the same 4s cadence as the extraction loop.

---

## Phase 3: User Story 2 — Exponential backoff retry in `llm.call()` (Priority: P1)

**Goal**: Add retry with exponential backoff to `llm.call()` so transient 429/5xx/network errors don't permanently kill a job's scoring.

**Independent Test**: Call `llm.call()` — if a 429 occurs, see `[llm] retry 1/3 in 5s:` in logs followed by retry.

### Implementation

- [x] T003 [P] [US2] Add `import logging` and `logger = logging.getLogger(__name__)` to `llm.py` (after existing imports at line 16)
- [x] T004 [US2] Add `max_retries: int = 3` and `retry_base_delay: float = 5.0` keyword-only parameters to `llm.call()` signature in `llm.py:35-42`
- [x] T005 [US2] Add retry loop in `llm.call()` body — wrap the API call in `for attempt in range(max_retries + 1)`, detect retryable errors via string matching (`"429"`, `"rate limit"`, `"500"`, `"503"`, `"timeout"`, `"connection"`), apply exponential backoff, re-raise non-retryable errors immediately, propagate last exception after exhausting retries. Insert at `llm.py:74` (around the `_client.chat.completions.create` call)
- [x] T006 [US2] Update docstring for `llm.call()` to document the new `max_retries` and `retry_base_delay` parameters

**Checkpoint**: `llm.call()` now retries transient failures with 5s/10s/20s backoff.

---

## Phase 4: Polish & Validation

- [x] T007 Run tests: `python -m pytest tests/ -q` — all 189 must pass
- [x] T008 Verify `llm.call()` signature backward compatibility: `python -c "from llm import call; help(call)"` shows new params with defaults
- [x] T009 Verify both fixes are in place: `grep -n "time.sleep(4)" score.py` shows sleep in evaluation loop; `grep -n "max_retries" llm.py` shows the new parameter

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **US1 (Phase 2)**: Depends on Phase 1 — independent of US2
- **US2 (Phase 3)**: Depends on Phase 1 — independent of US1
- **Polish (Phase 4)**: Depends on US1 + US2 completion

### Within User Stories

- US1: Single task (T002)
- US2: T003 → T004 → T005 → T006 (sequential within same file)

### Parallel Opportunities

- **US1 ∥ US2**: After Phase 1, T002 (score.py) and T003-T006 (llm.py) can run in parallel — different files, no shared state

---

## Implementation Strategy

### MVP (Both fixes together)

Both fixes are needed to resolve the 90%+ failure rate:
1. Phase 1: Verify baseline (T001)
2. Phase 2 + 3 in parallel: Implement both fixes (T002-T006)
3. Phase 4: Validate (T007-T009)

### Notes

- No new pip dependencies — manual retry, no tenacity/backoff
- `llm.call()` signature is fully backward-compatible (new params have defaults)
- `scorer.py`, `job_actions.py`, `company_researcher.py` — all callers of `llm.call()` need zero changes
