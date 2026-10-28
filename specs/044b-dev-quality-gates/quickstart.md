# Quickstart — Development quality gates (spec 044b)

How to run and validate the gates by hand. Implementation detail lives in
`tasks.md`; the hook contract is in `contracts/hooks.md`; measurements are in
`research.md`.

## Prerequisites

- Python 3.11 venv at `.venv/` with dev extras: `pip install -e ".[dev]"` (adds
  `ruff`).
- Claude Code at a version that supports the hook semantics (verified on 2.1.311).
- GitHub admin on `jceyrac/job-agent` for the manual secret-scanning step.

## Local gates (by hand)

```bash
ruff check .                     # lint (E9/F63/F7/F82) — must be clean
python -m pytest tests/ -q       # 698 tests, ~15 s — must pass
```

## Hooks

- View configured hooks in Claude Code: `/hooks`.
- Smoke test (SC-002): write a `.py` file with an undefined name and save it — the
  `PostToolUse` hook must flag it in the same step.
- Smoke test (SC-001): break a guard (e.g. `import sqlite3` in a tracker view),
  then stop — the `Stop` hook must block once and Claude Code fixes it before the
  turn ends.
- Re-check hook semantics after a Claude Code upgrade (version changes can alter
  matcher/exit-code behaviour).

### Time budget & offline

The Stop hook's pytest step is budgeted at **60 s** (the hook timeout); the full
suite (~15 s today) runs by default. Above 60 s the hook prints a non-blocking
warning ("suite exceeded 60 s budget — consider a subset spec") — **no subset is
implemented** (YAGNI).

Offline is enforced, not just assumed:

- `tests/conftest.py` installs an autouse fixture that makes `socket.connect` to
  any non-loopback address raise; `TestClient`/ASGI transport (loopback) keeps
  working. One test proves a non-loopback connect fails.
- The Stop hook runs pytest with `LLM_API_KEY` / `DEEPSEEK_API_KEY` removed, so a
  forgotten real LLM call fails instead of spending money.

## CI

- Push the branch; watch `.github/workflows/tests.yml` run `ruff check .` +
  `python -m pytest tests/ -q`.
- A deliberately red commit must turn the workflow red (SC-003).

## Secrets (manual, GitHub)

- Settings → Code security → enable secret scanning + push protection.
- Validate (SC-004): push a fake but well-formed provider key on a scratch branch
  and confirm the push is rejected. Use a GitHub-documented test pattern, never a
  real key.

## Branch protection (deferred — clarify Q3)

- After a few weeks of green CI runs, enable branch protection on `main` requiring
  the CI check. Not part of this spec's implementation.

## Definition of done

`ruff check .` clean · `pytest tests/` green · workflow badge green · a
test-secret push rejected · constitution v2.1.0 + `CLAUDE.md` + the gates agree
(`/speckit.analyze` finds no inconsistency — SC-005).
