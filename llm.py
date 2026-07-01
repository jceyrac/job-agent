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

import logging
import os
import time

from dotenv import load_dotenv
from openai import OpenAI

logger = logging.getLogger(__name__)

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
    max_retries: int = 3,
    retry_base_delay: float = 5.0,
) -> str:
    """
    Call the configured LLM and return the raw response text.

    Args:
        messages:         OpenAI-format message list (role/content dicts).
        json_mode:        If True, sets response_format={"type": "json_object"}.
        max_tokens:       Maximum output tokens.
        temperature:      Sampling temperature (default 0.2 for deterministic output).
        sleep_after:      Seconds to sleep after a successful call (rate-limit safety).
        max_retries:      Number of retry attempts on transient errors (429/5xx/network).
        retry_base_delay: Base seconds for exponential backoff (5s → 10s → 20s).

    Returns:
        Raw response text string.

    Raises:
        RuntimeError: if LLM_API_KEY / DEEPSEEK_API_KEY is not configured.
        Exception:    propagates API errors after retries exhausted (caller handles).
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

    last_exc: Exception | None = None
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
            is_server_error = "500" in str(e) or "503" in str(e)
            is_network = "timeout" in str(e).lower() or "connection" in str(e).lower()
            if not (is_rate_limit or is_server_error or is_network):
                raise  # non-retryable — fail immediately
            if attempt < max_retries:
                delay = retry_base_delay * (2 ** attempt)
                logger.warning(
                    f"[llm] retry {attempt+1}/{max_retries} in {delay:.0f}s: {e}"
                )
                time.sleep(delay)
    raise last_exc


def is_configured() -> bool:
    """Return True if the LLM client is ready to use."""
    return _client is not None
