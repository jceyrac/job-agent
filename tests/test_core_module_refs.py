"""Guard tests for spec 034 (Domain Package `core/`, roadmap step 1c).

Three guards, each of which must fail loudly when its violation is reintroduced:

- **FR-004** — no `__file__` in runtime code, allow-listed by *pattern* (not by
  directory): `paths.py` (the single allowed `__file__`-derived location) and the
  exact call `is_active_page(__file__)` (call to `is_active_page` whose sole
  argument is `__file__`, matched via AST). `tracker_views/` is covered, not
  exempt.
- **FR-005** — every string module reference resolves: `-m` subprocess args,
  `importlib.import_module` literals / f-string static prefixes, and
  `mock.patch` / `monkeypatch.setattr` string targets in `tests/`.
- **FR-014** — the source-tree base dirs named by `SCRAPERS_SRC_DIR` exist.

Pre-move (US1) these scan the root layout; post-move (US2) they scan the `core/`
layout. Both are read off the live source tree, so one file covers both phases.
"""

import ast
import importlib
import importlib.util
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_RUNTIME_SUBDIRS = ("scrapers", "cv_agent", "tracker_views", "scripts")

# FR-004: the only file whose `__file__` use is allowed unconditionally.
_ALLOWED_FILE_FILES = ("paths.py", os.path.join("core", "paths.py"))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _rel(path: str) -> str:
    return os.path.relpath(path, REPO_ROOT)


def _runtime_files():
    """Yield every runtime .py file the guards scan (root modules + 4 packages)."""
    for name in sorted(os.listdir(REPO_ROOT)):
        if name.endswith(".py"):
            yield os.path.join(REPO_ROOT, name)
    for sub in _RUNTIME_SUBDIRS:
        sub_root = os.path.join(REPO_ROOT, sub)
        for dirpath, dirnames, filenames in os.walk(sub_root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in sorted(filenames):
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)


def _test_files():
    for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, "tests")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


# --- FR-004 ---------------------------------------------------------------

def _file_use_allowed(rel_path: str, node: ast.Name, parent: ast.AST | None) -> bool:
    if rel_path in _ALLOWED_FILE_FILES:
        return True
    # is_active_page(__file__) — call to is_active_page whose sole arg is __file__.
    if isinstance(parent, ast.Call):
        func = parent.func
        if (
            isinstance(func, ast.Name)
            and func.id == "is_active_page"
            and len(parent.args) == 1
            and parent.args[0] is node
            and not parent.keywords
        ):
            return True
    return False


def _file_violations(rel_path: str, source: str) -> list[str]:
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "__file__":
            if _file_use_allowed(rel_path, node, parents.get(node)):
                continue
            violations.append(f"line {node.lineno}")
    return violations


# --- FR-005 ---------------------------------------------------------------

def _is_sys_executable(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        and node.attr == "executable"
    )


def _constant_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _find_subprocess_m_refs(source: str) -> list[tuple[int, str]]:
    """`-m` module names in a `sys.executable` launch list (excludes `git commit -m`)."""
    tree = ast.parse(source)
    refs = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        elts = node.elts
        if not elts or not _is_sys_executable(elts[0]):
            continue
        for i, el in enumerate(elts):
            if isinstance(el, ast.Constant) and el.value == "-m" and i + 1 < len(elts):
                name = _constant_str(elts[i + 1])
                if name is not None:
                    refs.append((node.lineno, name))
    return refs


def _static_module_prefix(arg: ast.AST) -> str | None:
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr):
        prefix = ""
        for value in arg.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                prefix += value.value
            else:
                break  # stop at the first interpolated part
        return prefix or None
    return None


def _find_import_module_refs(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    refs = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
            and func.attr == "import_module"
            and node.args
        ):
            name = _static_module_prefix(node.args[0])
            if name is not None:
                refs.append((node.lineno, name))
    return refs


def _find_patch_targets(source: str) -> list[tuple[int, str]]:
    """`mock.patch`/`monkeypatch.setattr` string targets (object targets skipped)."""
    tree = ast.parse(source)
    refs = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        first = node.args[0]
        target = None
        if isinstance(func, ast.Name) and func.id == "patch":
            target = _constant_str(first)
        elif (
            isinstance(func, ast.Attribute)
            and func.attr == "patch"
            and isinstance(func.value, ast.Name)
            and func.value.id in ("mock", "mocker")
        ):
            target = _constant_str(first)
        elif (
            isinstance(func, ast.Attribute)
            and func.attr == "setattr"
            and isinstance(func.value, ast.Name)
            and func.value.id == "monkeypatch"
        ):
            # 2-arg string-target form only; `monkeypatch.setattr(Obj, "name", v)` skips.
            target = _constant_str(first)
        if target is not None:
            refs.append((node.lineno, target))
    return refs


def _resolve_module(name: str) -> bool:
    """True if `name` is an importable module/package (f-string prefixes allowed)."""
    name = name.strip(".")
    if not name:
        return False
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _resolve_dotted(name: str) -> object:
    """Mirror `_pytest.monkeypatch.resolve`: walk a dotted path by attribute,
    importing each submodule on demand (parents may not re-export their children)."""
    parts = name.split(".")
    used = parts[0]
    found = importlib.import_module(used)
    for part in parts[1:]:
        used += "." + part
        try:
            found = getattr(found, part)
        except AttributeError:
            pass
        else:
            continue
        # The submodule isn't yet bound as an attribute on its parent — import it.
        importlib.import_module(used)
        found = getattr(found, part)
    return found


# --- FR-014 ---------------------------------------------------------------

def _check_scrapers_src_dir(scr_dir: str) -> list[str]:
    problems = []
    if not os.path.isdir(scr_dir):
        problems.append(f"{scr_dir} does not exist")
        return problems
    for sub in ("ats", "boards", "company_sites"):
        d = os.path.join(scr_dir, sub)
        if not os.path.isdir(d):
            problems.append(f"{d} does not exist")
    return problems


# ---------------------------------------------------------------------------
# FR-004 — no `__file__` in runtime code outside the allow-listed patterns
# ---------------------------------------------------------------------------

def test_fr004_no_file_outside_allowlist():
    problems = []
    for path in _runtime_files():
        rel = _rel(path)
        for msg in _file_violations(rel, _read(path)):
            problems.append(f"{rel}: {msg}")
    assert problems == [], "\n".join(problems)


def test_fr004_negative_fixture():
    bad = "import os\nROOT = os.path.dirname(os.path.abspath(__file__))\n"
    assert _file_violations("some_module.py", bad) != []


def test_fr004_is_active_page_allowed():
    good = "if is_active_page(__file__):\n    pass\n"
    assert _file_violations("tracker_views/jobs.py", good) == []


def test_fr004_paths_allowed():
    good = "import os\nPROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))\n"
    assert _file_violations("paths.py", good) == []


# ---------------------------------------------------------------------------
# FR-005 — every string module reference resolves
# ---------------------------------------------------------------------------

def test_fr005_string_module_refs_resolve():
    problems = []
    for path in _runtime_files():
        rel = _rel(path)
        source = _read(path)
        for line, name in _find_subprocess_m_refs(source):
            if not _resolve_module(name):
                problems.append(f"{rel}:{line}: unresolved -m module {name!r}")
        for line, name in _find_import_module_refs(source):
            if not _resolve_module(name):
                problems.append(f"{rel}:{line}: unresolved import_module {name!r}")
    for path in _test_files():
        rel = _rel(path)
        source = _read(path)
        for line, target in _find_patch_targets(source):
            try:
                _resolve_dotted(target)
            except Exception as exc:  # noqa: BLE001 — any failure is a guard hit
                problems.append(f"{rel}:{line}: unresolved patch target {target!r} ({exc})")
    assert problems == [], "\n".join(problems)


def test_fr005_subprocess_m_negative():
    bad = 'import subprocess, sys\nsubprocess.run([sys.executable, "-m", "nope_nope_nope"])\n'
    assert _find_subprocess_m_refs(bad) == [(2, "nope_nope_nope")]
    assert not _resolve_module("nope_nope_nope")


def test_fr005_git_commit_m_is_not_a_module():
    # `git commit -m "msg"` must NOT be treated as a -m module launch.
    src = 'import subprocess\nsubprocess.run(["git", "commit", "-m", "msg"])\n'
    assert _find_subprocess_m_refs(src) == []


def test_fr005_import_module_negative():
    bad = 'import importlib\nimportlib.import_module("nope_nope_nope")\n'
    assert _find_import_module_refs(bad) == [(2, "nope_nope_nope")]
    assert not _resolve_module("nope_nope_nope")


def test_fr005_patch_target_negative():
    bad = 'from unittest.mock import patch\npatch("nope.nope.nope")\n'
    assert _find_patch_targets(bad) == [(2, "nope.nope.nope")]
    with pytest.raises(Exception):
        _resolve_dotted("nope.nope.nope")


def test_fr005_patch_object_target_skipped():
    # 3-arg object form has a non-string first arg — not a string target.
    src = 'monkeypatch.setattr(JoinupScraper, "_fetch_page", fake)\n'
    assert _find_patch_targets(src) == []


# ---------------------------------------------------------------------------
# FR-014 — SCRAPERS_SRC_DIR and its subdirs exist
# ---------------------------------------------------------------------------

def test_fr014_scrapers_src_dir_exists():
    from paths import SCRAPERS_SRC_DIR

    assert _check_scrapers_src_dir(SCRAPERS_SRC_DIR) == []


def test_fr014_scrapers_src_dir_negative_fixture(tmp_path):
    assert _check_scrapers_src_dir(str(tmp_path)) != []  # missing ats/boards/company_sites
    for sub in ("ats", "boards", "company_sites"):
        (tmp_path / sub).mkdir()
    assert _check_scrapers_src_dir(str(tmp_path)) == []
