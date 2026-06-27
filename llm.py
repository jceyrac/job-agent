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
    _client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=120.0)


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
