# Implementation Plan: LLM retry + rate-limit fix (evaluation scoring failures)

**Branch**: `main` | **Date**: 2026-07-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/015-llm-retry-rate-limit/spec.md`

## Summary

Two fixes to resolve 90%+ LLM failure rate in the evaluation phase:

1. **`score.py`**: Add `time.sleep(4)` between evaluation calls in Phase 2 loop (matching the extraction loop cadence)
2. **`llm.py`**: Add exponential backoff retry to `llm.call()` — retry on 429/5xx/network errors, fail fast on 4xx

Backward-compatible — `llm.call()` signature adds defaults, all existing callers work unchanged.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: `openai` SDK (already in use), `logging` stdlib (new import in `llm.py`)
**Storage**: No changes
**Testing**: Existing 189 tests should pass; manual pipeline run validates scoring throughput
**Target Platform**: Linux server (Docker Compose), macOS dev
**Project Type**: CLI pipeline
**Performance Goals**: >80% of jobs scored per pipeline run (currently 4-9%)
**Constraints**: No new pip dependencies (no tenacity, no backoff); backward-compatible signatures
**Scale/Scope**: 2 files changed (`llm.py`, `score.py`), ~30 lines added

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Filet large | N/A | No scraping changes |
| II. Deux chemins | ✅ PASS | Code path — dev → git → deploy |
| III. Profil unifié | N/A | Profile-independent reliability fix |
| IV. Structure déterministe | ✅ PASS | Retry logic is deterministic, no LLM in control flow |
| V. Modification chirurgicale | ✅ PASS | Two files, minimal diffs, no opportunistic refactors |
| VI. Validation empirique | ✅ PASS | Pipeline run verifies scoring throughput improvement |
| VII. Sécurité | ✅ PASS | No new secrets, no new egress, no new dependencies |
| VIII. Scrapers par modèle | N/A | No scraper changes |
| IX. Scoring optionnel | ✅ PASS | Retry makes scoring more reliable, doesn't make it mandatory |

**Verdict**: All applicable gates pass. No violations.

## Project Structure

### Documentation (this feature)

```text
specs/015-llm-retry-rate-limit/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code

```text
llm.py                  # Fix 2: retry logic in call()
score.py                # Fix 1: sleep between evaluation calls
```

## Complexity Tracking

> No violations. Table intentionally left empty.
