# Implementation Plan: DeepSeek as Sole LLM Backend

**Branch**: `013-deepseek-primary-fallback` | **Date**: 2026-06-27 | **Spec**: [spec.md](./spec.md)

## Summary

Remove Groq and Gemini entirely. Make DeepSeek the only LLM backend. Simplify scorer.py from ~300 lines of multi-model fallback logic to ~100 lines of direct DeepSeek calls.

## Changes

### scorer.py — Remove
- Groq import, client init, `reload_client()`
- `_parse_model_list()`, all model list constants
- `_exhausted_models`, `_is_quota_exhausted()`
- `_call_groq()` (~80 lines of retry/backoff)
- `_call_groq_fallback_chain()` (~40 lines of fallback loop)
- JSON validity check in fallback chain (moved inline)

### scorer.py — Simplify
- `extract_job_fields()`: call `_call_deepseek()` directly + parse
- `evaluate_for_profile()`: call `_call_deepseek()` directly + parse
- `generate_json()`: call `_call_deepseek()` directly

### requirements.txt — Remove
- `groq>=0.15.0`

### README.md — Update
- DeepSeek as sole required LLM (no Groq)

## Files touched
- `scorer.py` — ~250 lines removed, ~30 lines rewritten
- `requirements.txt` — 1 line removed
- `README.md` — Groq references updated to DeepSeek
