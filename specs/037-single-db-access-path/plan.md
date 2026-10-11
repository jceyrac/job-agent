# Implementation Plan: Single DB Access Path + Single LLM Provider

**Branch**: `037-single-db-access-path` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/037-single-db-access-path/spec.md`

## Summary

Route every database read/write through named `JobStorage` methods (14 raw-SQL sites in
`tracker_views/` + 7 in `core/`) and finish the move to a single LLM provider (remove Groq
**and** DeepSeek residue so only `core/llm.py` names a provider). Enforce both with guard
tests that fail on reintroduction. No schema change, no behaviour change, no new dependency;
released through the standard branch → staging → ff-only merge → deploy flow.

This is roadmap step 2a + 2c (`docs/roadmap-api.md`). It is the direct prerequisite of step 3:
after this step, adding `user_id` changes one module, not twenty.

## Technical Context

**Language/Version**: Python 3.11 (`X | Y` unions, f-strings, dataclasses).

**Primary Dependencies**: Streamlit (UI), `openai` SDK (DeepSeek is OpenAI-compatible; called
only through `core/llm.py`), stdlib `sqlite3` (only inside `core/storage.py`). No new
dependencies — this step removes a dependency (`groq`, already absent from `requirements.txt`).

**Storage**: SQLite (WAL mode), `data/jobs.db`, accessed exclusively through the `JobStorage`
class in `core/storage.py`. No schema change.

**Testing**: `pytest`. `tests/test_storage.py` (in-memory DB — one new test per new method),
plus two new AST/text guard tests (`tests/test_guard_db_access.py`,
`tests/test_guard_llm_provider.py`).

**Target Platform**: Linux server (prod "verva", Docker Compose) + macOS dev; Streamlit tracker
on :8501 (Tailscale) and the cron `agent`.

**Project Type**: Python CLI + Streamlit web app, monorepo migrating to an API architecture
(this step is pre-API).

**Performance Goals**: No N+1 in the scoring/extraction loop — the `core/score.py` and
`core/prepare.py` queries must keep the same SQL and call shape (single query, no per-row
round-trips).

**Constraints**: No schema change; no behaviour change (same rows, same writes, same transaction
boundaries); surgical diffs only; no new dependency; guard tests must fail on each reintroduced
form; `db._conn()` stays private and is no longer called outside `core/storage.py`.

**Scale/Scope**: 14 tracker raw-SQL sites + 7 core raw-SQL sites; ~10 provider-name residue
sites across 6 files; 1 deleted module (`core/email_monitor.py`) + 1 removed compose service;
~9 new or reused `JobStorage` methods (exact count finalised in `data-model.md`).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / Constraint | Status | Notes |
|---|---|---|
| I. Filet large | ✅ N/A | No scraping/ingestion change. |
| II. Deux chemins | ✅ | Code path; documented, tested, deployed. |
| III. Profil unique / `user_id` | ✅ N/A | `user_id` is step 3; this step only makes step 3 a one-module change. |
| IV. Structure déterministe | ✅ | No LLM-produced control flow. |
| V. Modification chirurgicale | ✅ | Explicit non-goals; minimal diffs; no new deps. |
| VI. Validation empirique | ✅ | Staging checklist + parity + smoke test (US3). |
| VII. Sécurité d'abord | ✅ **resolves** | Removes the remaining provider-name residue; only `core/llm.py` names a provider. |
| VIII. Scrapers par modèle | ✅ N/A | No scraper change. |
| IX. Scoring optionnel | ✅ | `llm.is_configured()` already exists; onboarding stays a soft gate. |
| X. Catalogue / espace user | ✅ | No user-dependent data on catalogue entities; prerequisite of step 3. |
| XI. Migration progressive | ✅ | No schema change; app operational after every step; standard release flow. |
| Architecture: DB access | ✅ **resolves** | "Tout accès à la base MUST passer par `JobStorage`" — this step enforces it with a guard test. |
| Architecture: Stable core | ⚠️ **justified** | `core/storage.py`, `core/scorer.py`, `tracker_views/onboarding.py` are edited — each is the explicit subject of step 2a/2c (see Complexity Tracking). |

## Project Structure

### Documentation (this feature)

```text
specs/037-single-db-access-path/
├── plan.md              # This file
├── spec.md              # Clarified specification
├── research.md          # Phase 0 output — decisions (guard-test mechanism, provider-residue scope, allow-lists)
├── data-model.md        # Phase 1 output — the JobStorage method surface (new + reused) and provider-residue map
├── quickstart.md        # Phase 1 output — validation scenarios
├── inventory.md         # Raw-SQL site inventory (generated during planning, feeds tasks.md)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created by /speckit-plan)
```

### Source Code (repository root)

No new directories. This step edits existing files only:

```text
core/
├── storage.py          # ADD named methods (only additions + reuse) — subject of step 2a
├── llm.py              # reuse is_configured(); the only file that names a provider
├── email_monitor.py    # DELETED (git rm) — step 2c / FR-010
├── scorer.py           # reword Groq + DeepSeek comments/log strings (no behaviour change)
├── score.py            # route 2 sqlite3 sites → JobStorage
├── prepare.py          # route 1 _conn() site → JobStorage; reword DeepSeek strings; return llm.MODEL
├── context_tuner.py    # route 1 _conn() site → JobStorage
├── export_seed.py      # route 1 sqlite3 site → JobStorage
├── preference_report.py# route 1 sqlite3 site → JobStorage
├── backfill_descriptions.py # route 1 sqlite3 site → JobStorage
├── company_researcher.py    # reword 1 DeepSeek comment
└── cv_agent/cli.py     # reword 1 provider string (already uses llm.is_configured())

tracker_views/
├── dashboard.py        # 1 read → shared method
├── settings.py         # 4 sites → methods; DEEPSEEK_API_KEY → LLM_API_KEY in secret list
├── job_detail.py       # 2 reads → methods
├── contact_detail.py   # 4 writes → methods
├── contacts.py         # 1 write → method
├── company_detail.py   # 1 write → method
├── forms.py            # 1 write → method
└── onboarding.py       # Groq wizard → llm.is_configured() (step 2c)

tests/
├── test_storage.py     # one new unit test per new JobStorage method
├── test_guard_db_access.py     # NEW — AST guard (FR-005)
└── test_guard_llm_provider.py  # NEW — provider-name guard (FR-009)

.env.example            # rewrite: LLM_* vars; drop Groq/Gemini/Hydroxide block
docker-compose.yml      # remove email-monitor service
docs/history.md         # append email_monitor removal record
```

**Structure Decision**: The feature is a horizontal refactor across the existing monorepo — no
new package, module, or directory is introduced. All DB access funnels into `core/storage.py`;
all provider knowledge stays in `core/llm.py`.

## Complexity Tracking

> Filled because the Constitution Check has justified Stable-core exceptions.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Edit `core/storage.py` (Stable core) | It is the single gateway that must gain the named methods replacing raw SQL. | Cannot route calls through `JobStorage` without adding methods to it. |
| Edit `core/scorer.py` (Stable core) | Holds the Groq comment (`:707`) and the DeepSeek log/error strings — the provider residue step 2c removes. Comment/log-string only, no behaviour change. | Leaving them keeps the Constitution VII violation ("no file but `llm.py` names a provider"). |
| Edit `tracker_views/onboarding.py` (Stable core) | Holds the Groq first-run wizard step (`:435–459`) that FR-007 replaces with `llm.is_configured()`. Config-flow correction, not a UI redesign (non-goal: no mockup needed). | Leaving it means a fresh user is asked for a useless Groq key and never asked for the `LLM_*` key. |
