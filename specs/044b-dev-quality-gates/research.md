# Research — Development quality gates (spec 044b)

Phase 0 output. Resolves the unknowns from the spec and the plan's Technical
Context. Measurements taken on the dev Mac (M5 Pro), repo at
`/Users/jeanclaudevd/AI-Suite/job_agent`, 2026-10-27.

## R1 — Claude Code hook semantics (installed version)

- **Decision**: verified against **Claude Code 2.1.311** (`claude --version`).
  The spec's "Claude Code hook behaviour relied on" section holds for this
  version: hooks are configured in `settings.json` (user `~/.claude/settings.json`,
  project `.claude/settings.json` — committed —, local `.claude/settings.local.json`).
  `PostToolUse` takes a `matcher` on tool names and exit code 2 shows stderr to
  Claude (the tool already ran); `Stop` takes no matcher and exit code 2 blocks
  the stop; the Stop input carries `stop_hook_active: true` when Claude is
  re-entering because of a stop hook (loop protection). Any other non-zero exit
  is non-blocking. Hooks run in the Claude Code harness, independent of the model
  behind the proxy.
- **Rationale**: hooks are the only mechanism that runs deterministically in the
  Claude Code harness; the spec's whole premise is to replace "written
  instructions followed by a proxy model" with these deterministic gates.
- **Alternatives considered**: none — a linter daemon or a pre-commit framework
  is explicitly a non-goal (no pre-commit framework).
- **Open item for implementation** (carried into tasks.md): confirm against
  `/hooks` the exact `PostToolUse` matcher syntax and the tool names to list
  (`Write|Edit|MultiEdit`). `Write` and `Edit` are present in this session's tool
  set; `MultiEdit` is to be confirmed — an unmatched alternative is harmless. Also
  confirm the `$CLAUDE_PROJECT_DIR` variable name and the stdin field
  `tool_input.file_path` before writing the scripts.

## R2 — Ruff baseline (E9/F63/F7/F82)

- **Decision**: `ruff 0.17.3`, `ruff check . --select E9,F63,F7,F82` → **2
  findings**, both `F821 Undefined name 'PREPARE_LIGHT_MODELS'` at
  `core/prepare.py:454` and `core/prepare.py:491`.
- **Root cause**: `core/prepare.py` passes
  `preferred_models=PREPARE_LIGHT_MODELS` to `_call_prepare_model`, which has no
  such parameter and no model-chain (the project is DeepSeek-only: `llm.call()`
  uses the single `LLM_MODEL`). It is a dead leftover from the pre-spec-037
  multi-provider era. This is a **real bug on an active path**
  (`core/job_actions.prepare_one` → `core.prepare.prepare_job_application` →
  `tracker_views/job_helpers.py`): those two branches would raise `NameError`
  (undefined name) and, even if the name were defined, `TypeError` (unexpected
  keyword).
- **Disposition (2026-10-28)**: OUT OF 044b SCOPE — fixed by a separate hotfix
  branched from `main` (remove the two `preferred_models=PREPARE_LIGHT_MODELS`
  kwargs + a regression test). 044b itself makes no application-code change; its
  ruff gate is validated against a clean baseline once the hotfix lands.
- **Reachability (2026-10-28)**: reachable today via **two** entry points — the
  tracker "prepare" action (`tracker_views/job_helpers.py:319` →
  `core.job_actions.prepare_one` → `prepare_job_application` steps 3–4) and the
  `python -m core.prepare --job/--ready` CLI (`core/prepare.py:700`/`:776`). The
  `NameError` is raised *before* `llm.call`, caught by the surrounding `except`,
  and persisted as an `error` dict — so cover letter + screening succeed but **CV
  bullets and company research silently no-op**, yielding an incomplete
  application package behind a green "Prepared by …" message. `core/cv_agent/`
  and `core/tasks.py` do **not** reach this path (no `prepare` wiring / no
  `prepare` task type).
- **Alternatives considered**: define `PREPARE_LIGHT_MODELS = []` — rejected: it
  would silence the lint but leave the latent `TypeError` and dead code. Removing
  the kwarg matches `_call_prepare_model`'s real signature.

## R3 — Pytest baseline (Stop-hook runtime budget)

- **Decision**: `python -m pytest tests/ -q --no-header -p no:cacheprovider` →
  **698 passed in 14.1 s** (14.9 s wall; pytest 9.0.3).
- **Rationale**: the full suite is ~15 s, far under the 60 s budget in the spec's
  Edge Cases. The Stop hook runs the **full** suite; the fast-subset rule is not
  needed (SC-006 satisfied with margin). The subset fallback is documented in
  quickstart.md as a contingency only.
- **Offline confirmation (2026-10-28)**: the suite is fully offline — API tests
  run `fastapi.testclient.TestClient` against a temp-file DB (`tests/conftest.py`
  `api_env`, never the live `data/jobs.db`), scraper tests monkeypatch the network
  fetch (`test_free_work_wide_net.py`), and no test calls `llm.call` (only
  `test_guard_llm_provider.py` reads `LLM_API_KEY` from env — never connects).
  Practical proof: 698 tests in ~15 s is impossible with real LLM/network calls.
- **Budget & fallback**: the Stop hook's pytest step is budgeted at **60 s** (the
  hook timeout). If a full-suite run ever exceeds it, the hook falls back to the
  documented fast subset (see `contracts/hooks.md` + `quickstart.md`) and prints
  "full suite deferred to CI" — so a stop is never blocked for >60 s.

## R4 — .gitignore (versioning the committed hooks)

- **Decision**: `.gitignore` currently has `.claude/` (line 31), ignoring the
  whole directory. To version `.claude/settings.json` and `.claude/hooks/` while
  keeping `.claude/settings.local.json` (permissions) ignored, the rule becomes
  `.claude/*` plus `!` exceptions — Git cannot re-include a file under an ignored
  directory, so the wildcard-then-unignore form is required (US5-3).
- **Evidence**: `git check-ignore -v` is captured in the implementation commit;
  `.claude/commands/`, `.claude/skills/`, `.claude/ingest_parity.sh` and
  `.claude/settings.local.json` stay ignored.

## R5 — GitHub Actions workflow

- **Decision**: `.github/workflows/tests.yml` on `ubuntu-latest`, Python 3.11,
  triggers `push` (all branches) and `pull_request`. Steps: `actions/checkout`
  and `actions/setup-python` (both pinned to a full commit SHA with the version
  in a comment), then `pip install -e ".[dev]"`, `ruff check .`,
  `python -m pytest tests/ -q`. `permissions: contents: read`; no secrets; job
  `timeout-minutes: 15`; pip cache enabled.
- **Rationale**: mirrors the local gate exactly (ruff + full pytest), read-only,
  bounded time. Tests are offline (ASGI `TestClient`/fixtures) — no test reaches
  the network beyond `pip install`.

## R6 — Secret scanning & push protection (manual, no code)

- **Decision**: enabled by the user in GitHub → Settings → Code security (secret
  scanning + push protection); documented in quickstart.md. Validated with a
  fake, well-formed provider-key pattern documented by GitHub (never a real key).
- **Rationale**: a repo-level GitHub setting, not versionable code.

## R7 — Local permissions (deferred, separate spec)

- **Decision**: out of scope for 044b (clarify Q4 → separate follow-up spec).
- **Evidence**: `.claude/settings.local.json` pre-approves broad commands —
  `Bash(git *)`, `Bash(ssh *)`, `Bash(curl *)`, `Bash(bash *)`, `Bash(docker *)`,
  `Bash(python *)`, `.venv/bin/python *` — a prompt-injection path to verva
  without confirmation. Narrowed in a follow-up spec.
