"""Agent ↔ domain import-boundary guard (spec 036, US6).

Three AST rules pin the boundary between ``core/cv_agent/`` (the LangGraph agent)
and the rest of ``core/`` (the domain):

- **(A)** the agent may import only an allow-listed set of domain modules
  (``models``/``llm``/``paths``/``profiles``/``scorer``/``storage``) — its own
  ``core.cv_agent.*`` imports are skipped;
- **(B)** the agent imports no ``_``-prefixed name from any ``core`` module
  (this is what removed the old ``from core.job_actions import _dict_to_posting``);
- **(C)** no domain ``core`` module imports ``core.cv_agent`` — the agent is
  launched only via ``python -m core.cv_agent.cli``, never imported by the domain.

Mirrors ``tests/test_installable_project.py``: enumeration is ``git ls-files``
(tracked files only), and the rule functions are pure over source text so each
negative fixture plants a violating import and asserts the guard flags it.
"""

import ast
import os
import subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (A) The only domain modules the agent may import (Constitution X / US6).
ALLOWED_CORE_MODULES = {
    "core.models",    # JobPosting, posting_from_dict (the public round-trip helper)
    "core.llm",       # the sole LLM client
    "core.paths",     # data/output/env paths
    "core.profiles",  # ALL_PROFILES, SearchProfile, load_active_profile
    "core.scorer",    # extract_job_fields, evaluate_for_profile (sole fit judge)
    "core.storage",   # JobStorage
}

# (C) Empty: no domain module may import the agent (launch is ``-m core.cv_agent.cli``).
CV_AGENT_IMPORT_ALLOW = set()


def _rel(path: str) -> str:
    return os.path.relpath(path, REPO_ROOT)


def _is_in_cv_agent(path: str) -> bool:
    rel = _rel(path)
    return rel == "core/cv_agent" or rel.startswith("core/cv_agent/")


def _is_core_module(path: str) -> bool:
    rel = _rel(path)
    return rel == "core" or rel.startswith("core/")


def _tracked_py_files() -> list[str]:
    """Tracked ``*.py`` files, via ``git ls-files`` (fails loudly without git)."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--", "*.py"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise AssertionError("git is not available — cannot enumerate tracked files") from None
    if result.returncode != 0:
        raise AssertionError(
            "git ls-files failed:\n" + (result.stderr.strip() or result.stdout.strip())
        )
    return [os.path.join(REPO_ROOT, p) for p in result.stdout.splitlines() if p.endswith(".py")]


# ── pure rule functions (over source text, so negative fixtures are trivial) ──

def _referenced_core_modules(tree):
    """Yield ``(lineno, core_module_path)`` for every ``core`` import."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "core" or alias.name.startswith("core."):
                    yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "core":
                for alias in node.names:
                    yield node.lineno, f"core.{alias.name}"
            elif node.module.startswith("core."):
                yield node.lineno, node.module


def _core_module_violations(src: str) -> list:
    """(A) core modules referenced that are neither internal nor allow-listed."""
    tree = ast.parse(src)
    return [
        (lineno, ref) for lineno, ref in _referenced_core_modules(tree)
        if not (ref == "core.cv_agent" or ref.startswith("core.cv_agent."))
        and ref not in ALLOWED_CORE_MODULES
    ]


def _private_import_violations(src: str) -> list:
    """(B) ``_``-prefixed names imported from any ``core`` module."""
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and \
                (node.module == "core" or node.module.startswith("core.")):
            for alias in node.names:
                if alias.name.startswith("_"):
                    out.append((node.lineno, f"{node.module}.{alias.name}"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if (alias.name == "core" or alias.name.startswith("core.")) \
                        and alias.name.rsplit(".", 1)[-1].startswith("_"):
                    out.append((node.lineno, alias.name))
    return out


def _cv_agent_import_violations(src: str) -> list:
    """(C) references to ``core.cv_agent`` from a non-agent core module."""
    tree = ast.parse(src)
    return [
        (lineno, ref) for lineno, ref in _referenced_core_modules(tree)
        if ref == "core.cv_agent" or ref.startswith("core.cv_agent.")
    ]


# ── (A) agent imports only allow-listed domain modules ──────────────────────

def test_cv_agent_imports_only_allowed_core_modules():
    offenders = []
    for path in _tracked_py_files():
        if not _is_in_cv_agent(path):
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for lineno, ref in _core_module_violations(src):
            offenders.append(f"{_rel(path)}:{lineno}: imports {ref}")
    assert not offenders, "agent imports a non-allow-listed core module:\n" + "\n".join(offenders)


# ── (B) agent imports no private name from any core module ──────────────────

def test_cv_agent_imports_no_private_core_names():
    offenders = []
    for path in _tracked_py_files():
        if not _is_in_cv_agent(path):
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for lineno, name in _private_import_violations(src):
            offenders.append(f"{_rel(path)}:{lineno}: imports {name}")
    assert not offenders, "agent imports a private core name:\n" + "\n".join(offenders)


# ── (C) the domain never imports the agent ──────────────────────────────────

def test_domain_never_imports_cv_agent():
    offenders = []
    for path in _tracked_py_files():
        if not _is_core_module(path) or _is_in_cv_agent(path):
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for lineno, ref in _cv_agent_import_violations(src):
            offenders.append(f"{_rel(path)}:{lineno}: imports {ref}")
    assert not offenders, "domain module imports core.cv_agent:\n" + "\n".join(offenders)


# ── negative fixtures (plant a violating import, assert the guard flags it) ──

def test_rule_a_flags_import_outside_allowlist(tmp_path):
    planted = tmp_path / "planted_a.py"
    planted.write_text("from core.scrape import discover_scrapers\n", encoding="utf-8")
    assert _core_module_violations(planted.read_text(encoding="utf-8"))
    # tmp_path teardown removes the planted file.


def test_rule_b_flags_private_import(tmp_path):
    planted = tmp_path / "planted_b.py"
    planted.write_text("from core.models import _helper\n", encoding="utf-8")
    assert _private_import_violations(planted.read_text(encoding="utf-8"))


def test_rule_c_flags_cv_agent_import(tmp_path):
    planted = tmp_path / "planted_c.py"
    planted.write_text("from core.cv_agent.cli import run\n", encoding="utf-8")
    assert _cv_agent_import_violations(planted.read_text(encoding="utf-8"))
