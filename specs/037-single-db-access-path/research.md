# Research — Single DB Access Path + Single LLM Provider (spec 037)

Decisions recorded during `/speckit-plan`. No new dependency is introduced; every choice
below is a "where does this live / what's the exact rule" decision, not a technology pick.

---

## D1. DB-access guard test: mechanism and scan scope (FR-005)

- **Decision**: One new test `tests/test_guard_db_access.py` walks every `*.py` file under
  `core/` (excluding `core/storage.py`), `tracker_views/` and `tracker.py`, parses each with
  `ast`, and fails on (a) an `Attribute` node with `attr == "_conn"` whose value is not `self`,
  (b) a `Call` to `sqlite3.connect`, or (c) `Import`/`ImportFrom` of `sqlite3`. It carries a
  **negative fixture per form**: a small in-test snippet that must be flagged, proving the guard
  is not vacuous.
- **Rationale**: The spec (FR-005) mandates AST-based detection with negative fixtures; AST is
  precise for `_conn`/`sqlite3.connect`/`import sqlite3` and won't false-positive on the string
  "sqlite3" in a comment or log.
- **Alternatives considered**: a regex/grep scan (rejected — noisy on strings/comments);
  scanning `scripts/` too (rejected — `scripts/` is dev/diagnostic tooling, not runtime code).

### Scan scope decision

- **Decision**: the guard scans **runtime code only** — `core/` + `tracker_views/` + `tracker.py`.
  The `scripts/` directory is out of scope (dev/diagnostic one-offs, launched manually, same
  category as `tests/`).
- **Rationale**: the app's runtime is `core/` + `tracker_views/` + `tracker.py`; `scripts/`
  holds ~10 standalone diagnostic tools (`audit_provenance.py`, `dedupe_jobs.py`,
  `diag_freelance.py`, `filter_funnel.py`, `duplicate_report.py`, `re_extract_sample.py`,
  `audit_work_mode.py`, `health_check.py`, …) that read SQLite directly by design and are not
  part of the running application. Refactoring them is explicitly out of scope (non-goal: no
  opportunistic refactor).
- **Allow-list** (inside the scan scope): only `core/storage.py` itself. `scripts/backup_db.py`
  and `scripts/fingerprint.py` (the spec's Context "allowed raw access") are already outside the
  scan because they live in `scripts/`; they remain untouched and are documented in `spec.md`
  Context.

---

## D2. LLM-provider guard test: mechanism and provider list (FR-009)

- **Decision**: One new test `tests/test_guard_llm_provider.py` reads every runtime `*.py` under
  `core/` (excluding `core/llm.py`), `tracker_views/`, `tracker.py`, plus `.env.example` and
  `docker-compose.yml`, and fails on a case-insensitive match of any of
  `groq | openai | anthropic | deepseek | ollama`. It carries a negative fixture (a sample
  string that must be flagged).
- **Rationale**: provider names are text, not syntax — a case-insensitive text scan is simpler
  and fully sufficient (no AST needed). `core/llm.py` is excluded wholesale because it is the
  one file allowed to name the provider (Constitution VII).
- **Non-issues**: `requirements.txt` (contains `openai>=1.0.0`) is **not** scanned — it is a
  dependency manifest, not runtime code, and `openai` is the legit OpenAI-compatible SDK
  DeepSeek is served through. `docs/`, `specs/`, `prompts/` are not scanned (historical
  reference, per CLAUDE.md "prompts/ holds legacy … historical reference only").

---

## D3. Provider-residue scope is larger than the spec's Context (FR-009 + Constitution VII)

- **Decision**: step 2c removes **all** provider names from runtime code outside `core/llm.py`,
  not just Groq. The spec's "Groq residue" table under-inventoried: `deepseek` / `DeepSeek` /
  `DEEPSEEK_API_KEY` also appears in runtime code, and FR-009 explicitly lists `deepseek` in the
  guard, matching Constitution VII ("aucun autre fichier ne nomme un fournisseur LLM").
- **Rationale**: FR-009 is unambiguous and authoritative; the Context table is incomplete.
- **Residue map** (see `data-model.md` §2 for the exact edits):
  - `core/email_monitor.py` — deleted (Groq SDK, unimportable).
  - `core/scorer.py` — 12 `DeepSeek`/`DEEPSEEK_API_KEY` comments/log strings → "LLM" wording;
    one is a control-flow string-match (see D4).
  - `core/prepare.py` — docstring + `"deepseek-chat"` hardcoded model + a log string (see D5).
  - `core/company_researcher.py` — one `# DeepSeek` comment.
  - `core/cv_agent/cli.py` — error string "set LLM_API_KEY / DEEPSEEK_API_KEY" → "set LLM_API_KEY".
  - `tracker_views/settings.py` — secret-list entry `DEEPSEEK_API_KEY` → `LLM_API_KEY`.
  - `tracker_views/onboarding.py` — Groq wizard step → `llm.is_configured()` (see D6).
  - `.env.example` — full rewrite (see D7).
- **Alternatives considered**: leaving `deepseek` strings (rejected — fails FR-009 guard and
  Constitution VII); purging `deepseek` from `core/llm.py` too (rejected — llm.py is the one
  place allowed to name it; its `BASE_URL` default `https://api.deepseek.com/v1` is required).

---

## D4. `scorer.py` control-flow string-match on `DEEPSEEK_API_KEY`

- **Decision**: reword the three `if "DEEPSEEK_API_KEY" in msg or "not set" in msg:` guards
  (`scorer.py:609`, `:653`, `:885`) to `if "LLM_API_KEY" in msg or "not set" in msg:` and the
  paired `DeepSeek not configured` prints to `LLM not configured`.
- **Rationale**: `core/llm.call()` raises `RuntimeError("No LLM API key configured. Set
  LLM_API_KEY (or DEEPSEEK_API_KEY) in .env.")`. `"LLM_API_KEY"` is a generic key name (not a
  provider name), so it stays guard-clean, and it still matches the message. The `"not set"`
  clause is retained unchanged.
- **Alternatives considered**: introducing a custom exception class in `llm.py` (rejected — a
  behaviour change to the Stable-core `llm.py` for no functional gain); checking
  `llm.is_configured()` up front (rejected — `call()` still raises mid-run, so the catch-path
  reword is still needed and is the minimal diff).

---

## D5. `prepare.py:279` hardcoded `"deepseek-chat"`

- **Decision**: change `return raw, "deepseek-chat"` to `return raw, llm.MODEL`, and reword the
  surrounding `Call DeepSeek for preparation tasks` docstring and the
  `Fallback calls (DeepSeek): {fallback_total}` print to provider-agnostic wording.
- **Rationale**: `scorer.py` already returns `llm.MODEL` in the same situation
  (`score_job` → `result["scored_by"] = llm.MODEL`); `prepare.py` is the odd one out hardcoding a
  provider+model string. This is a correctness-alignment, not a behaviour change (the returned
  model name now reflects the configured model).
- **Alternatives considered**: leaving the literal (rejected — fails the `deepseek` guard and is
  inconsistent with `scorer.py`).

---

## D6. Onboarding wizard (FR-007): reuse `llm.is_configured()`

- **Decision**: replace the `if not env_is_set("GROQ_API_KEY"):` block in
  `tracker_views/onboarding.py:435–459` with `if not llm.is_configured():`; the inline prompt
  asks for `LLM_API_KEY` (writes it via the existing `shared.set_secret`), and the Groq copy
  (console.groq.com link, "gsk_…" placeholder) is replaced with DeepSeek/LLM-agnostic copy.
- **Rationale**: `core/llm.is_configured()` already exists (returns `_client is not None`) and
  is already the check `cv_agent/cli.py` uses — no new `llm.py` method is needed (FR-007 is a
  reuse). The wizard's "what key do I need" is the whole point of the fix.
- **Alternatives considered**: adding a new `llm.required_env_vars()` accessor (rejected —
  overkill; `is_configured()` + the existing `set_secret` are sufficient and minimal).

---

## D7. `.env.example` rewrite (FR-008) and `LLM_API_KEY` canonical key

- **Decision**: rewrite `.env.example` to document `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`,
  `LLM_BASE_URL` (matching `core/llm.py`), drop `GROQ_API_KEY` and the dead `GEMINI_API_KEY`, and
  remove the Hydroxide/`email_monitor` block. Keep `GMAIL_*`, `NOTIFY_TO`, `JOPLIN_TOKEN`,
  `X_RAPIDAPI_KEY`.
- **Decision**: `LLM_API_KEY` is the canonical key. `core/llm.py:31` keeps its
  `os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")` fallback — it lives inside `llm.py`
  (allowed) and preserves backward compatibility for any existing `.env` that still sets
  `DEEPSEEK_API_KEY`. `tracker_views/settings.py:21`'s secret entry is changed
  `DEEPSEEK_API_KEY` → `LLM_API_KEY`.
- **Rationale**: the fallback inside `llm.py` is the one permitted place to mention a provider;
  every other file must use the generic `LLM_*` vocabulary.

---

## D8. `email_monitor` removal (FR-010)

- **Decision**: `git rm core/email_monitor.py`; remove the `email-monitor` service block from
  `docker-compose.yml`; drop `email-monitor` from the `docker compose build` list in
  `scripts/deploy.sh:37` (→ `docker compose build tracker agent`); update references:
  - `CLAUDE.md` Infrastructure section (drop the "`email-monitor` exists in compose but is not in
    use" clause),
  - `README.md` (`:69`, `:164`, `:290`),
  - `docs/freelance-sourcing-map.md` (`:17`, `:47`, `:99`, `:109`, `:119`),
  - `docs/restore-procedure.md` (`:101`, `:103`),
  - `docs/migration-checklist.md` (`:92`, the prod-guard row),
  - `core/storage.py:3445` docstring (`find_jobs_by_company` — reword to drop "Used by
    email_monitor.py"; the method stays, per the "only additions + reuse" non-goal),
  - `.env.example` Hydroxide block (covered by D7).
  Record the removal in `docs/history.md` (append a row: purpose, last commit containing it,
  `git show <sha>:core/email_monitor.py`).
- **Decision**: no `pyproject.toml` edit — it uses `packages = ["core", …]` package discovery,
  and `email_monitor` is not named in any explicit module list (the spec's "pyproject module
  list" note is stale).
- **Decision**: `scripts/rewrite_core_imports.py` (a 034 one-off that lists `email_monitor` in a
  tuple) is left as-is — it is historical dev tooling, not runtime, and outside the guard's scan.
- **Decision**: `docs/roadmap-api.md:66` is left as-is — its mention is the step's own plan
  description, not residue.
- **Deploy cleanup (edge case)**: `email-monitor` is `profiles: [manual]`, so no container is
  normally running; on deploy, remove any orphan `job-email-monitor` container
  (`docker rm -f job-email-monitor` or `docker compose up -d --remove-orphans`) and optionally
  prune the `job-agent-email-monitor` image. Captured in `quickstart.md`.

---

## D9. JobStorage method strategy (feeds data-model.md)

- **Decision**: add a named `JobStorage` method only where no existing one does the job (FR-005
  acceptance 5); reuse existing methods verbatim where behaviour is identical. New methods carry
  the same SQL and transaction boundaries as the raw site they replace (FR-004), and a unit test
  each (FR-002). The two identical overview-count blocks (dashboard + settings) collapse to one
  shared method (FR-003). The exact method list is derived from the raw-SQL inventory in
  `inventory.md` and specified in `data-model.md`.
