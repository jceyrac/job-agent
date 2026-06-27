# Spec 013 — DeepSeek as Sole LLM Backend

> Scope: `scorer.py`, `requirements.txt`, `README.md`
> Do not modify `main.py`, `scrape.py`, `tracker.py`, or any scraper.

---

## Goal

Remove Groq and Gemini entirely. Make DeepSeek the only LLM backend for both extraction and evaluation. Groq's free tier (30 RPM) is consistently rate-limited and produces empty/non-JSON responses. DeepSeek has proven reliable, produces valid JSON, and costs ~$0.01/run.

---

## Context

Groq's free tier is unusable for production workloads — all three models (gpt-oss-120b, qwen3.6-27b, gpt-oss-20b) consistently return empty or non-JSON responses. Gemini was tested and rejected (20 req/day free tier, truncated eval JSON). DeepSeek is the only backend that works reliably.

The user has a working DeepSeek API key (`DEEPSEEK_API_KEY` in `.env`).

---

## Changes required

### 1. `scorer.py` — Remove Groq entirely

Remove:
- `from groq import Groq` import
- Groq client init and `reload_client()` function
- `_parse_model_list()` helper
- `FALLBACK_MODELS`, `EXTRACTION_MODELS`, `EVALUATION_MODELS` lists
- `_exhausted_models` set and `_is_quota_exhausted()` helper
- `_call_groq()` function (entire retry/backoff logic)
- `_call_groq_fallback_chain()` function
- `generate_json()` — redirect to DeepSeek

### 2. `scorer.py` — Simplify extraction and evaluation

`extract_job_fields()`: call `_call_deepseek()` directly — no fallback chain.
`evaluate_for_profile()`: call `_call_deepseek()` directly — no fallback chain.
`generate_json()`: call `_call_deepseek()` directly.

### 3. `requirements.txt` — Remove Groq and Gemini

Remove `groq>=0.15.0` and `google-genai>=1.0.0`.

### 4. `README.md` — DeepSeek as sole LLM dependency

Document DeepSeek as the only required LLM service. Remove all Groq and Gemini references.

---

## Acceptance criteria

- [ ] All Groq code removed from `scorer.py` (imports, client, models, functions)
- [ ] All Gemini code removed from `scorer.py` (already done in Phase 1)
- [ ] `extract_job_fields()` calls DeepSeek directly
- [ ] `evaluate_for_profile()` calls DeepSeek directly
- [ ] `generate_json()` calls DeepSeek directly
- [ ] `requirements.txt` references neither `groq` nor `google-genai`
- [ ] `README.md` documents DeepSeek as sole required LLM
- [ ] All 189 existing tests pass
- [ ] Mock test: 6/6 extract AND evaluate successfully via DeepSeek
