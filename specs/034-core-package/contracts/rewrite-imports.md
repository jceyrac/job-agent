# Contract: `scripts/rewrite_core_imports.py` (FR-007)

The only supported way to rewrite imports and string module references when the
domain moves into `core/`. Run from the repo root, after the `git mv`, before the
Phase-B commit.

## Invocation

```
python -m scripts.rewrite_core_imports            # rewrite in place, prints a summary
python -m scripts.rewrite_core_imports --check    # fail (exit 1) if any rewrite is pending
```

No arguments required; repo root is resolved via `core.paths.PROJECT_ROOT`.

## Behaviour

For every `*.py` under the repo root (including `core/`, `tracker_views/`,
`scripts/`, `tests/`):

1. Rewrite imports of moved top-level names (27 modules ∪ `scrapers` ∪
   `cv_agent`) to absolute `core.` form — `from core.X import …`,
   `from core.X.Y import …`, `from core import X`, `import core.X.Y`.
2. Rewrite string module references (FR-008):
   - `-m` subprocess args → `core.`-prefixed;
   - `importlib.import_module` literals and f-string static prefixes →
     `core.`-prefixed;
   - `mock.patch` / `monkeypatch.setattr` targets in `tests/` → `core.`-prefixed
     (stdlib targets untouched).
   Source-tree *file* paths (`scrapers/…`) are **not** rewritten by this script:
   they are derived from `SCRAPERS_SRC_DIR` in `paths.py` (FR-002/FR-014), so
   Phase B changes only that constant — never the strings that use it.
3. **Never** double-prefix: a name already starting with `core.` is left alone.

## Contract invariants

- **Idempotent**: running twice is a byte-for-byte no-op.
- **No behaviour change**: only import statements and string literals change; no
  logic, no formatting, no docstring churn.
- **No shims**: it produces no alias modules and deletes none.
- **Report**: prints, per file, the number of imports/strings rewritten; exits
  non-zero only on `--check` with pending changes or on an unreadable file.

## Validation

- `python -m scripts.rewrite_core_imports --check` is green after the rewrite.
- FR-004/FR-005 guards green; `grep` finds no root-form import of a moved module
  (SC-002).
