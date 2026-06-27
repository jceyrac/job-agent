# Spec 013 — DeepSeek-only LLM backend

> Scope: `scorer.py`, `job_actions.py`, `requirements.txt`. No schema change. No scraper changes.
> Read `scorer.py` and `job_actions.py` before starting.
> Do not modify `main.py`, `scrape.py`, `storage.py`, `profiles.py`, or any scraper.

---

## Context

The pipeline currently has a multi-model fallback chain across Groq, Gemini, and DeepSeek.
Testing has shown that all free-tier models (Groq gpt-oss-120b, qwen/qwen3.6-27b,
gpt-oss-20b, Gemini 2.5 Flash) are unreliable in production — returning empty responses,
non-JSON text, or truncated output at volume. DeepSeek is the only backend that produces
valid structured JSON consistently. The Groq and Gemini code paths add complexity, latency,
and wasted API calls with zero benefit.

> ⚠️ IMPORTANT: The current fallback chain is Groq (120b → qwen → 20b) → Gemini → DeepSeek.
> Every Groq call fails in production (empty response, non-JSON, or 429), burning 2–3 wasted
> API calls before DeepSeek succeeds. This spec removes BOTH Groq AND Gemini entirely.
> `_call_deepseek` must be called directly — no fallback chain, no Groq SDK, no Gemini SDK.

Multi-model support will return in a future spec (spec 014) as a user-configurable feature
with a built-in test tool. For now, the pipeline is simplified to DeepSeek only.

---

## Goal

Remove all Groq and Gemini LLM calls from the scoring pipeline. DeepSeek becomes the sole
backend for both extraction and evaluation. The pipeline should be simpler, faster, and
fully reliable for any user who sets `DEEPSEEK_API_KEY` in their `.env`.

---

## Changes required

### `scorer.py`

This is the primary file to change. Apply the following:

**1. Remove Groq and Gemini client initialization**
Remove the `Groq` client, `_exhausted_models`, `_call_groq`, `_call_groq_fallback_chain`,
`_call_gemini`, and all Groq/Gemini model list constants (`FALLBACK_MODELS`,
`EXTRACTION_MODELS`, `EVALUATION_MODELS`, `GEMINI_MODEL`). Keep `_call_deepseek` and
`DEEPSEEK_MODEL` unchanged.

**2. Remove Groq and Gemini imports**
Remove `from groq import Groq` and `from google import genai as google_genai` imports.
Keep `from openai import OpenAI` (used by DeepSeek via OpenAI-compatible endpoint).

**3. Simplify `extract_job_fields()`**
Replace the entire multi-model fallback chain with a single direct DeepSeek call:

```python
def extract_job_fields(job: JobPosting) -> JobPosting | None:
    if job.extracted_at is not None:
        return job

    base_loc = job.base_location or ""
    prompt = (
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location}\n"
        f"Base location: {base_loc}\n"
        f"Description: {job.description or ''}"
    )
    messages = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user",   "content": prompt},
    ]

    try:
        raw = _call_deepseek(messages, json_mode=True, max_tokens=600)
        model = DEEPSEEK_MODEL
        time.sleep(1)
    except Exception as e:
        print(f"  ❌  extract_job_fields failed for '{job.title}': {e}")
        return None

    if not raw or not raw.strip():
        print(f"  ❌  Empty response from DeepSeek for '{job.title}'")
        return None

    try:
        result = _parse_extraction_result(raw)
        job.company_country   = result["company_country"]
        job.industry_sector   = result["industry_sector"]
        job.language_required = result["language_required"]
        job.work_mode         = result["work_mode"]
        job.geo_zone          = result["geo_zone"]
        job.country_code      = result.get("country_code")
        job.company_size      = result["company_size"]
        job.contract_type     = result["contract_type"]
        job.summary           = result["summary"]
        job.company_summary   = result.get("company_summary")
        job.company_website   = result.get("company_website")
        job.salary_text       = result.get("salary_text")
        job.comp_annual_eur   = result.get("comp_annual_eur")
        job.extracted_at      = datetime.now()
        job.extracted_by      = model
        print(f"  ✅ [{result['geo_zone']}] {job.company_country} / "
              f"{result['industry_sector']} / {result['language_required']} "
              f"(model: {model})")
        return job
    except Exception as e:
        print(f"  ❌  extract_job_fields parse failed for '{job.title}': {e}")
        return None
```

**4. Simplify `evaluate_for_profile()`**
In the Tier 1 LLM evaluation block, replace the `_call_groq_fallback_chain` call with a
direct `_call_deepseek` call:

```python
try:
    raw = _call_deepseek(messages, json_mode=True, max_tokens=300)
    result = _parse_result(raw)
    score = int(result["score"])
    reason = result.get("reason", "")
    return _evaluation_result(score, reason, DEEPSEEK_MODEL, job, profile,
                              comp_flag=comp_flag)
except Exception as e:
    print(f"  ❌  evaluate_for_profile failed for '{job.title}': {e}")
    return None
```

**5. Simplify `score_job()`**
Replace the `_call_groq_fallback_chain` call with `_call_deepseek`. This function is used
by the mock test and direct scoring calls.

**6. Simplify `generate_json()`**
This is used by onboarding and other UI flows. Replace its internals with `_call_deepseek`.

**7. Update contact extraction in `job_actions.py`**
The `_discover_contacts` LLM contact extraction path references `_call_groq_fallback_chain`.
Replace that reference with `_call_deepseek`.

---

### `requirements.txt`

Remove `groq>=0.15.0` and `google-genai>=1.0.0`. Keep `openai>=1.0.0` (DeepSeek uses
the OpenAI-compatible endpoint).

---

### `.env` documentation (no code change)

`DEEPSEEK_API_KEY` is now the only required LLM key. `GROQ_API_KEY` and `GEMINI_API_KEY`
are unused and can be removed from `.env`.

---

## What to preserve

- All prompt text (`SYSTEM_PROMPT`, `EXTRACTION_PROMPT`, `EVALUATION_PROMPT`,
  `CONTACT_EXTRACTION_PROMPT`) — unchanged
- All parser functions (`_parse_result`, `_parse_extraction_result`) — unchanged
- `_call_deepseek` and `DEEPSEEK_MODEL` — unchanged
- All Tier 0 deterministic filtering in `evaluate_for_profile()` — unchanged
- `reload_client()` — remove (was Groq-specific); check for references before deleting
- `_exhausted_models` tracking — remove entirely (Groq-specific)

---

## Validation

Run the mock test:

```bash
python score.py --mock --profile unified_jc
```

All 6 jobs must extract and evaluate successfully via DeepSeek. Expected results:

| Job | Expected band | Notes |
|---|---|---|
| FELFEL AG | 5–7 | CH food-tech hybrid |
| Consensys MetaMask | 6–8 | Web3 remote, CV stretch |
| SIX Group | 1–3 | Large corporate hard-exclusion |
| SwissNeo | 8–9 | Crypto-fintech, CH hybrid |
| TokenBridge Labs | 6–8 | Web3 RWA, EU remote |
| Istanbul Fintech | 4–5 | TR fintech, tier 6 |

Report which model handled each job (should be `deepseek-chat` for all) and whether all
6 passed their expected bands. Do not commit until results are reviewed.

---

## Acceptance criteria

- [ ] `scorer.py` imports no Groq or Gemini libraries
- [ ] `requirements.txt` does not include `groq` or `google-genai`
- [ ] No `_call_groq`, `_call_groq_fallback_chain`, `_call_gemini`, or `_exhausted_models` anywhere in codebase
- [ ] All 6 mock jobs extract and evaluate successfully via `deepseek-chat`
- [ ] All 6 mock scores fall within expected bands
- [ ] `job_actions.py` contact extraction uses `_call_deepseek` not `_call_groq_fallback_chain`

---

## Future work (spec 014)

Add user-configurable LLM backend support: allow users to set their preferred provider
(DeepSeek, Groq, Gemini, Mistral, OpenAI) via `.env` or the Settings UI, with a built-in
test tool that validates the chosen model produces valid JSON before enabling it in the
pipeline.
