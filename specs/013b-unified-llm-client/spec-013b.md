# Spec 013b — Unified LLM client

> Scope: `llm.py` (new), `scorer.py`, `prepare.py`, `profile_generator.py`,
> `context_tuner.py`. No schema change. No scraper changes.
> Read all five files before starting.
> Do not modify `main.py`, `scrape.py`, `storage.py`, `profiles.py`, or any scraper.

> **Prerequisite**: spec 013 (DeepSeek-only backend) must be implemented first.
> This spec assumes `scorer.py` already calls `_call_deepseek` directly with no
> Groq or Gemini code remaining.

---

## Context

After spec 013, DeepSeek is the sole LLM backend — but the integration is
inconsistent across files:

- `scorer.py` owns `_call_deepseek()` and `DEEPSEEK_MODEL`, defined privately
- `prepare.py` imports `_call_deepseek` from `scorer` (private API leak)
- `context_tuner.py` also imports `_call_deepseek` from `scorer` (same issue)
- `profile_generator.py` calls `scorer.generate_json()` which internally uses
  `_call_deepseek` — one level of indirection but still coupled to scorer internals
- The model name (`deepseek-chat`) and API base URL are hardcoded in `scorer.py`
- There is no single place to swap the LLM provider — changing from DeepSeek to
  another provider would require edits across multiple files

The goal is a single `llm.py` module that owns all LLM configuration and calling
logic, referenced by all other modules. Future spec 014 (user-configurable backend)
will only need to touch `llm.py`.

---

## Goal

Create `llm.py` as the single LLM entry point for the entire codebase. All LLM
calls across all files go through `llm.call()`. The provider, model, and API key
are configured once via `.env` — no hardcoded model names or provider references
anywhere else.

---

## `.env` configuration

Three new variables (all optional with sensible defaults):

```
# LLM backend — currently only openai-compatible is supported
LLM_PROVIDER=deepseek                          # deepseek | openai (future)
LLM_MODEL=deepseek-chat                        # model name passed to the API
LLM_API_KEY=sk-...                             # replaces DEEPSEEK_API_KEY
LLM_BASE_URL=https://api.deepseek.com/v1       # OpenAI-compatible endpoint
```

**Backward compatibility**: if `LLM_API_KEY` is not set but `DEEPSEEK_API_KEY`
is present, use `DEEPSEEK_API_KEY` as fallback. This means existing `.env` files
continue to work without changes after this spec is deployed.

---

## New file: `llm.py`

Create `llm.py` at project root. This is the only file that imports `openai.OpenAI`
and knows the provider details.

```python
"""
llm.py — Unified LLM client for job_agent.

All LLM calls across the codebase go through llm.call().
Provider, model, and API key are configured via .env:

    LLM_PROVIDER=deepseek          # openai-compatible provider name (informational)
    LLM_MODEL=deepseek-chat        # model name passed to the API
    LLM_API_KEY=sk-...             # API key (falls back to DEEPSEEK_API_KEY)
    LLM_BASE_URL=https://...       # OpenAI-compatible base URL

Future spec 014 will add user-selectable providers via Settings UI.
"""

import os
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────────────

PROVIDER  = os.getenv("LLM_PROVIDER",  "deepseek")
MODEL     = os.getenv("LLM_MODEL",     "deepseek-chat")
BASE_URL  = os.getenv("LLM_BASE_URL",  "https://api.deepseek.com/v1")
API_KEY   = os.getenv("LLM_API_KEY")   or os.getenv("DEEPSEEK_API_KEY")

_client: OpenAI | None = None
if API_KEY:
    _client = OpenAI(api_key=API_KEY, base_url=BASE_URL)


def call(
    messages: list[dict],
    *,
    json_mode: bool = True,
    max_tokens: int = 600,
    temperature: float = 0.2,
    sleep_after: float = 1.0,
) -> str:
    """
    Call the configured LLM and return the raw response text.

    Args:
        messages:     OpenAI-format message list (role/content dicts).
        json_mode:    If True, sets response_format={"type": "json_object"}.
        max_tokens:   Maximum output tokens.
        temperature:  Sampling temperature (default 0.2 for deterministic output).
        sleep_after:  Seconds to sleep after a successful call (rate-limit safety).

    Returns:
        Raw response text string.

    Raises:
        RuntimeError: if LLM_API_KEY / DEEPSEEK_API_KEY is not configured.
        Exception:    propagates API errors (caller decides how to handle).
    """
    if _client is None:
        raise RuntimeError(
            "No LLM API key configured. Set LLM_API_KEY (or DEEPSEEK_API_KEY) in .env."
        )

    kwargs: dict = dict(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    result = _client.chat.completions.create(**kwargs)
    raw = result.choices[0].message.content

    if sleep_after > 0:
        time.sleep(sleep_after)

    return raw


def is_configured() -> bool:
    """Return True if the LLM client is ready to use."""
    return _client is not None
```

---

## Changes required

### `scorer.py`

- Remove the `_call_deepseek` function and `DEEPSEEK_MODEL` constant
- Remove the DeepSeek `OpenAI` client initialization block
- Remove `from openai import OpenAI` import (now lives in `llm.py`)
- Add `import llm` at the top
- Replace every `_call_deepseek(messages, json_mode=..., max_tokens=...)` call
  with `llm.call(messages, json_mode=..., max_tokens=...)`
- Replace every reference to `DEEPSEEK_MODEL` (used as `scored_by` / `extracted_by`)
  with `llm.MODEL`
- Keep `generate_json()` but rewrite its internals to use `llm.call()`

### `prepare.py`

- Remove `from scorer import _call_deepseek`
- Add `import llm`
- Remove `PREPARE_HEAVY_MODELS` and `PREPARE_LIGHT_MODELS` constants (dead code
  after spec 013 — the model list was for Groq, not used by DeepSeek)
- In `_call_prepare_model()`, replace `_call_deepseek(...)` with `llm.call(...)`
  and remove the unused `preferred_models` parameter from the function signature
- Update all call sites of `_call_prepare_model()` accordingly (remove the
  `preferred_models=...` argument)

### `profile_generator.py`

- No import change needed — it calls `scorer.generate_json()` which will
  internally use `llm.call()` after the scorer.py change above
- No other changes required

### `context_tuner.py`

- Remove `from scorer import _call_deepseek`
- Add `import llm`
- Replace `_call_deepseek(messages, ...)` with `llm.call(messages, ...)`

### `requirements.txt`

- No change needed — `openai` is already present (DeepSeek uses the OpenAI SDK)

### `.env.example`

Update to document the new variables and show the DeepSeek defaults:

```
# LLM backend (OpenAI-compatible)
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-your-deepseek-key-here
LLM_BASE_URL=https://api.deepseek.com/v1

# Legacy fallback (still works if LLM_API_KEY is not set)
# DEEPSEEK_API_KEY=sk-your-deepseek-key-here
```

---

## What to preserve

- All prompt text in `scorer.py`, `prepare.py`, `context_tuner.py` — unchanged
- All parser functions (`_parse_result`, `_parse_extraction_result`) — unchanged
- All Tier 0 deterministic filtering in `evaluate_for_profile()` — unchanged
- `reload_client()` in `scorer.py` — remove (was Groq-specific; if a Settings UI
  reload is needed, add `llm.reload()` instead — but check for callers first)
- `sleep_after` default in `llm.call()` is 1.0s — callers that need a different
  delay (e.g. the extraction loop uses 4s between jobs in `score.py`) keep their
  own `time.sleep()` and pass `sleep_after=0` to avoid double-sleeping

---

## Validation

Run the mock test:

```bash
python score.py --mock --profile unified_jc
```

All 6 jobs must extract and evaluate successfully. The `scored_by` / `extracted_by`
field in results should show `llm.MODEL` (i.e. `deepseek-chat` by default).

Then run a quick smoke test for prepare and profile generation:

```bash
python -c "import llm; print(f'Provider: {llm.PROVIDER}, Model: {llm.MODEL}, Configured: {llm.is_configured()}')"
```

Should print: `Provider: deepseek, Model: deepseek-chat, Configured: True`

---

## Acceptance criteria

- [ ] `llm.py` exists at project root with `call()`, `is_configured()`, `MODEL`, `PROVIDER`
- [ ] `scorer.py` has no `_call_deepseek`, no `DEEPSEEK_MODEL`, no `OpenAI` import
- [ ] `prepare.py` has no `_call_deepseek` import, no `PREPARE_HEAVY_MODELS` / `PREPARE_LIGHT_MODELS`
- [ ] `context_tuner.py` has no `_call_deepseek` import
- [ ] No file outside `llm.py` imports `openai.OpenAI` or references `deepseek` by name
- [ ] `LLM_API_KEY` and `DEEPSEEK_API_KEY` both work as API key sources (backward compat)
- [ ] All 6 mock jobs pass their expected score bands
- [ ] `llm.is_configured()` returns True when key is set

---

## Future work (spec 014)

Add user-selectable LLM provider via Settings UI and `.env`. The user picks a
provider (DeepSeek, OpenAI, Groq, Gemini, Mistral), enters their API key, and
runs a built-in JSON extraction test before the provider is activated. All changes
will be contained in `llm.py` and the Settings page — no other file needs to change.
