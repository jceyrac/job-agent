# Data model — Development quality gates (spec 044b)

**No new entities, no schema change, no storage.** This feature adds tooling
(Claude Code hooks, a linter, GitHub Actions CI) that operates on the repository
itself, not on the application's data. `data/`, `core/storage.py`, and every DB
table are untouched (spec non-goal: no application code change — the R2
`core/prepare.py` `F821` was fixed by a separate hotfix, out of this spec's scope).

The only state the gates introduce is configuration on disk:

| Artifact | Shape | Notes |
|---|---|---|
| `.claude/settings.json` | JSON (`hooks` map) | committed; two hooks — `PostToolUse` → `lint_edited.py`, `Stop` → `stop_tests.py` |
| `.claude/hooks/lint_edited.py` | stdlib Python script | stdin JSON, exit 0 / 2 / non-blocking (see `contracts/hooks.md`) |
| `.claude/hooks/stop_tests.py` | stdlib Python script | stdin JSON, exit 0 / 2 / non-blocking (see `contracts/hooks.md`) |
| `.github/workflows/tests.yml` | YAML | CI job (ruff + pytest), read-only permissions |
| `pyproject.toml` | `[project.optional-dependencies] dev` + `[tool.ruff]` | add `ruff`; `target-version = "py311"`; `lint.select = ["E9","F63","F7","F82"]` |

No validation rules beyond the hook exit-code contract (`contracts/hooks.md`).
No lifecycle/state transitions.
