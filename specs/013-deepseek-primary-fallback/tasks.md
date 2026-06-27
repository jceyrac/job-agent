# Tasks: DeepSeek as Primary LLM Fallback

**Feature**: 013-deepseek-primary-fallback

## Phase 1: Remove Gemini from scorer.py

- [ ] T001 Remove `from google import genai as google_genai` import in `scorer.py:7`
- [ ] T002 [P] Remove Gemini client init block (gemini_api_key, _gemini_client, GEMINI_MODEL) in `scorer.py:34-39`
- [ ] T003 [P] Remove `_call_gemini()` function entirely in `scorer.py:612-649`

## Phase 2: Simplify extraction fallback (Groq → DeepSeek directly)

- [ ] T004 Remove Gemini fallback block in `extract_job_fields()`, restore direct Groq → DeepSeek chain in `scorer.py`
- [ ] T005 Remove Gemini from empty-response retry block, restore direct DeepSeek retry in `scorer.py`

## Phase 3: Add DeepSeek to evaluation fallback

- [ ] T006 Replace `return None` on Groq eval exhaustion with DeepSeek fallback call in `evaluate_for_profile()` in `scorer.py`

## Phase 4: Documentation

- [ ] T007 [P] Remove `google-genai>=1.0.0` from `requirements.txt`
- [ ] T008 [P] Add DeepSeek dependency documentation to `README.md`

## Phase 5: Validation

- [ ] T009 Run existing tests: `python -m pytest tests/` — all 189 must pass
- [ ] T010 Run mock test: `python score.py --mock --profile unified_jc` — all 6 jobs must extract AND evaluate successfully

## Dependencies

```text
T001 ──► T004, T005
T002, T003 (parallel with T001)
T004, T005 ──► T006
T006 ──► T009, T010
T007, T008 (parallel, independent)
```
