"""Rewrite imports and string module references to ``core.*`` after the spec-034 move.

Idempotent and re-runnable: every rule skips names already ``core.``-prefixed, so
a second run is a byte-for-byte no-op. Run from the repo root:

    python -m scripts.rewrite_core_imports            # rewrite in place, prints a summary
    python -m scripts.rewrite_core_imports --check    # exit 1 if any rewrite is pending

This is a migration tool, never imported by the app. It uses no ``__file__``
(FR-004 has no allow-list entry for it) and locates the repo via
``core.paths.PROJECT_ROOT``.
"""

import argparse
import os
import re

from core.paths import PROJECT_ROOT

# The 27 domain modules + the two packages moved into ``core/`` (data-model §1).
MOVED = [
    "ats_detection", "backfill_descriptions", "company_researcher", "context_tuner",
    "create_profile", "cv_extract", "email_monitor", "export_jobs", "export_seed",
    "filters", "job_actions", "llm", "main", "models", "monitoring_agent", "notifier",
    "paths", "preference_report", "prepare", "profile_generator", "profiles", "score",
    "scorer", "scrape", "seed", "storage", "title_gate",
    "scrapers", "cv_agent",
]
MOVED_SET = set(MOVED)
_MOVED_ALT = "|".join(sorted(MOVED, key=len, reverse=True))

_SKIP_DIRS = {
    ".venv", ".git", "__pycache__", "prompts", ".cv_pipeline", "data", "specs", ".specify",
}

# Import statements — matched at line start only, so prose/f-string text (e.g. the
# monitoring_agent generated-spec templates) is left for the hand edits, never here.
_IMPORT_BARE_RE = re.compile(rf'^(\s*)import(\s+)({_MOVED_ALT})(?![.\w])', re.MULTILINE)
_IMPORT_SUB_RE = re.compile(rf'^(\s*import\s+)({_MOVED_ALT})(?=\.)', re.MULTILINE)
_FROM_RE = re.compile(rf'^(\s*from\s+)({_MOVED_ALT})((?:\.[A-Za-z_]\w*)*\s+import\b)', re.MULTILINE)

# String module references.
_M_RE = re.compile(r'("-m"\s*,\s*["\'])([A-Za-z_][\w.]*)(["\'])')
_IMPORTLIB_RE = re.compile(r'(\bimport_module\s*\(\s*[fF]?\s*["\'])([A-Za-z_][\w]*)')
_PATCH_RE = re.compile(r'(\b(?:mock\.patch|mocker\.patch|patch)\s*\(\s*["\'])([A-Za-z_][\w.]*)')
_SETATTR_RE = re.compile(r'(\bmonkeypatch\.setattr\s*\(\s*["\'])([A-Za-z_][\w.]*)')


def _m_sub(match):
    name = match.group(2)
    if name in MOVED_SET:
        return f"{match.group(1)}core.{name}{match.group(3)}"
    return match.group(0)


def _importlib_sub(match):
    name = match.group(2)
    if name in MOVED_SET:
        return f"{match.group(1)}core.{name}"
    return match.group(0)


def _patch_sub(match):
    target = match.group(2)
    if target.split(".")[0] in MOVED_SET:
        return f"{match.group(1)}core.{target}"
    return match.group(0)


def rewrite_source(source, is_test):
    """Return ``(source, change_count)`` after rewriting imports and string refs."""
    changes = 0

    source, n = _IMPORT_BARE_RE.subn(r"\1from core import\2\3", source)
    changes += n
    source, n = _IMPORT_SUB_RE.subn(r"\1core.\2", source)
    changes += n
    source, n = _FROM_RE.subn(r"\1core.\2\3", source)
    changes += n

    def apply(regex, fn):
        nonlocal source, changes

        def wrapper(match):
            nonlocal changes
            before = match.group(0)
            after = fn(match)
            if after != before:
                changes += 1
            return after

        source = regex.sub(wrapper, source)

    apply(_M_RE, _m_sub)
    apply(_IMPORTLIB_RE, _importlib_sub)
    if is_test:
        apply(_PATCH_RE, _patch_sub)
        apply(_SETATTR_RE, _patch_sub)

    return source, changes


def _py_files():
    for dirpath, dirnames, filenames in os.walk(PROJECT_ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _is_test(path):
    rel = os.path.relpath(path, PROJECT_ROOT)
    return rel == "tests" or rel.startswith("tests" + os.sep)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rewrite core/ imports after the spec-034 move.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not rewrite; exit 1 if any file would change",
    )
    args = parser.parse_args(argv)

    pending = []
    for path in _py_files():
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        rewritten, changes = rewrite_source(source, _is_test(path))
        if changes:
            pending.append((path, os.path.relpath(path, PROJECT_ROOT), rewritten, changes))

    if not pending:
        print("no pending rewrites")
        return 0

    for path, rel, rewritten, changes in pending:
        if args.check:
            print(f"pending: {rel} ({changes} change(s))")
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(rewritten)
            print(f"rewrote: {rel} ({changes} change(s))")

    if args.check:
        print(f"{len(pending)} file(s) with pending rewrites")
        return 1
    print(f"rewrote {len(pending)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
