"""Guard test for spec 037 (Single LLM Provider, roadmap step 2c) — FR-009.

Fails when a provider name (`groq`, `openai`, `anthropic`, `deepseek`, `ollama`)
appears outside `core/llm.py` — in runtime Python, in `docker-compose.yml`, or in
`.env.example` as anything other than the *value* of an `LLM_*` variable.

`core/llm.py` is the one file allowed to name the provider (Constitution VII).
`.env.example` may name the provider only as the value of an `LLM_*` key (the
"LLM env-var values themselves" allow-list from FR-009); a provider name in a
key (`DEEPSEEK_API_KEY`), in a comment, or in a non-`LLM_*` value is a violation.
"""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ALLOWED_LLM_FILE = os.path.join("core", "llm.py")
_PROVIDER_NAMES = ("groq", "openai", "anthropic", "deepseek", "ollama")
_PROVIDER_RE = re.compile("|".join(_PROVIDER_NAMES), re.IGNORECASE)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _rel(path: str) -> str:
    return os.path.relpath(path, REPO_ROOT)


def _runtime_py_files():
    for sub in ("core", "tracker_views"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, sub)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in sorted(filenames):
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)
    yield os.path.join(REPO_ROOT, "tracker.py")


def _plain_violations(source: str) -> list[str]:
    """Provider names anywhere in a plain (non-env) file are violations."""
    return [f"line {i}" for i, line in enumerate(source.splitlines(), 1)
            if _PROVIDER_RE.search(line)]


def _env_example_violations(source: str) -> list[str]:
    """Provider names are allowed only as the value of an `LLM_*` key."""
    problems = []
    for i, raw in enumerate(source.splitlines(), 1):
        body = raw.split("#", 1)[0]
        comment = raw[len(body):] if "#" in raw else ""
        key = value = ""
        if "=" in body:
            key, _, value = body.partition("=")
            key = key.strip()
        if _PROVIDER_RE.search(key):
            problems.append(f"line {i} (key)")
        elif _PROVIDER_RE.search(comment):
            problems.append(f"line {i} (comment)")
        elif _PROVIDER_RE.search(value) and not key.startswith("LLM_"):
            problems.append(f"line {i} (value)")
    return problems


# ---------------------------------------------------------------------------
# FR-009 — only core/llm.py names a provider
# ---------------------------------------------------------------------------

def test_fr009_no_provider_in_runtime_py():
    problems = []
    for path in _runtime_py_files():
        rel = _rel(path)
        if rel == _ALLOWED_LLM_FILE:
            continue
        for msg in _plain_violations(_read(path)):
            problems.append(f"{rel}: {msg}")
    assert problems == [], "\n".join(problems)


def test_fr009_no_provider_in_compose():
    src = _read(os.path.join(REPO_ROOT, "docker-compose.yml"))
    problems = [f"docker-compose.yml: {m}" for m in _plain_violations(src)]
    assert problems == [], "\n".join(problems)


def test_fr009_env_example_only_llm_values():
    src = _read(os.path.join(REPO_ROOT, ".env.example"))
    problems = [f".env.example: {m}" for m in _env_example_violations(src)]
    assert problems == [], "\n".join(problems)


# --- negative fixtures ----------------------------------------------------

def test_fr009_negative_plain():
    bad = "from groq import Groq\nGROQ_API_KEY = os.getenv('GROQ_API_KEY')\n"
    assert _plain_violations(bad) != []


def test_fr009_llm_value_allowed():
    good = "LLM_PROVIDER=deepseek\nLLM_BASE_URL=https://api.deepseek.com/v1\n"
    assert _env_example_violations(good) == []


def test_fr009_non_llm_key_flagged():
    assert _env_example_violations("DEEPSEEK_API_KEY=sk-abc\n") != []


def test_fr009_comment_flagged():
    assert _env_example_violations("# Groq API key\n") != []
