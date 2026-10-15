# Implementation Plan: Platform Ingestion Scope — title judgement moves to scoring

**Branch**: `040-platform-ingestion-scope` | **Date**: 2026-10-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/040-platform-ingestion-scope/spec.md`

## Summary

Roadmap step 4 closes Phase B: today the **active profile** decides what enters the
shared catalogue (scrape `JobFilter.titles`, the title gate before
`save_unscored`, the Greenhouse title filter, and a scoring-time gate that writes
`jobs.filtered_non_product` on the catalogue row). This step makes ingestion read a
single **platform scope** (`config.ingestion_scope`, JSON) — re-keying the pre-save
title gate onto the scope so broad ingestion stays byte-identical — and moves title
judgement into the scorer as a **new first Tier 0 rule** stored in `job_scores` (user
space, through the profile) — no LLM call.

Two code paths change (Constitution II kept clean):
- **Prose path** — `config.ingestion_scope` (edited by `scripts/ingestion_scope.py`,
  no deploy). Initialised once from the active profile's `job_titles` /
  `title_exclude` / `scrape_remote_or_hybrid` so day one is identical.
- **Code path** — remove the profile-derived ingestion gate, add the Tier 0 title
  rule, remove `set_job_filtered_non_product` **and the scoring pre-filter title
  clauses** (`title_contains` / `exclude_title_contains`), and add guard tests. No
  backfill: the first `core.score --profile` run scores the currently-flagged jobs via
  the new rule (byte-identical parity before that run).

## Technical Context

**Language/Version**: Python 3.11 (`X | Y` unions, dataclasses, f-strings).

**Primary Dependencies**: none new — `json`, `re`, existing `core/` (stdlib + Streamlit).

**Storage**: SQLite WAL via `JobStorage` (`data/jobs.db`). The scope lives in the
existing `config` key-value store as a JSON string; typed accessors wrap it. The
migration writes only the `config` key (no `job_scores` rows). `jobs.filtered_non_product`
is kept unused (expand/contract — dropped at step 10).

**Testing**: `pytest` — extend `tests/test_storage.py`, add a guard suite
(`tests/test_ingestion_scope.py`) analogous to `tests/test_legacy_company_columns.py`.
Integration (`tests/run_all.py`) stays intentional-only.

**Target Platform**: Linux server (prod `verva`, Docker Compose) + Mac dev.

**Project Type**: CLI pipeline + Streamlit tracker (monolith mid-migration to API).

**Performance Goals**: none new — the title rule is `O(len(title) × len(titles))`
substring/word-boundary matching, no LLM call. Ingestion volume is unchanged on day
one (scope == old profile values).

**Constraints**: no LLM in Tier 0 (Constitution IV); no `sqlite3` outside
`core/storage.py`; no profile fields read by ingestion; no new dependency.

**Scale/Scope**: single user today; ~25 scrapers (boards + ATS/company-keyed);
~160 MB DB.

## Constitution Check

*GATE: passed. Re-checked after Phase 1 design — still passed.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I — Filet large, point de filtrage unique | ✅ **resolves the known violation** | Ingestion reads only the platform scope; title judgement moves to Tier 0. The Sync Impact Report's "scrape.py applique `title_matches_profile` avant sauvegarde" violation is removed (FR-010, PATCH). |
| II — Deux chemins | ✅ | Scope edit = prose path (CLI, config); Tier 0 rule + gate removal = code path. |
| III — `user_id` seam, `profile_id` scoring key | ✅ | Scoring stays keyed by `profile_id`; the scope is platform-level (no user). |
| IV — Structure déterministe | ✅ | Title rule is deterministic (`title_matches_profile` substring + word-boundary exclude); no LLM. |
| V — Modification chirurgicale | ✅ | Non-goals explicit; diffs minimal; no opportunistic refactor. |
| VIII — Sources company-keyed, filtre grossier | ✅ | Greenhouse filters on `ingestion_scope.title_families` (the allowed coarse family filter), not a profile. |
| IX — Pipeline sans profil/clé LLM | ✅ | Empty `title_families` = open ingestion; extraction/scoring unaffected. |
| X — Catalogue partagé, espace utilisateur | ✅ | Removes the profile judgement (`filtered_non_product`) from the catalogue; title judgement now in `job_scores`. |
| XI — Migration progressive | ✅ | Expand/contract (column kept to step 10), one-shot recorded migration, backup before deploy, parity. **See the parity-delta note below.** |

**Parity note (resolved — `research.md` §1):** there is no backfill, so the step-4
fingerprint is **byte-identical** to the pre-step image (as for step 3). The expected
change is observed only *after* the first `core.score --profile` run: exactly `N`
fresh, unscored jobs whose title fails the profile's `job_titles` / `title_exclude`
gain a Tier 0 title row, and no other score changes. This requires FR-006 to also drop
the scoring pre-filter title clauses (see `research.md` §9).

## Project Structure

### Documentation (this feature)

```text
specs/040-platform-ingestion-scope/
├── plan.md              # This file
├── research.md          # Phase 0 output — decisions + the parity finding
├── data-model.md        # Phase 1 output — config.ingestion_scope + job_scores tier_0 row
├── quickstart.md        # Phase 1 output — validation runbook
├── contracts/           # Phase 1 output — accessor + CLI + tier_0 rule contracts
│   ├── ingestion_scope.md
│   └── tier0_title.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
core/
├── storage.py           # + _migrate_ingestion_scope() (seeds config key only);
│                        #   + get/set_ingestion_scope(); − set_job_filtered_non_product()
├── scrape.py            # JobFilter built from scope; title gate re-keyed onto scope (line 379)
├── scorer.py            # + first Tier 0 title rule in evaluate_for_profile()
├── score.py             # − monitored title gate + set_job_filtered_non_product call (442–448);
│                        #   − title_contains/exclude_title_contains in effective_pre_filter (376–383)
├── title_gate.py        # (reused) title_matches_profile(); + word-boundary exclude helper
└── scrapers/greenhouse.py  # filter on scope.title_families instead of profile.job_titles
scripts/
└── ingestion_scope.py   # FR-004 — view/edit the scope (--show/--set-titles/--set-exclude/--remote-or-hybrid)
tests/
├── test_storage.py      # extend for get/set_ingestion_scope + migration
└── test_ingestion_scope.py  # guard: no ingestion reads profile title fields; no runtime write to filtered_non_product
tracker_views/
└── settings.py          # FR-011 — one-line read-only note next to job-titles field
.specify/memory/constitution.md  # FR-010 — PATCH v2.0.3: drop the known violation
CLAUDE.md                # ingestion scope + where to edit it
```

**Structure Decision**: single existing project; the feature is a surgical change
across `core/` (domain), `scripts/` (CLI), one tracker view, and the constitution.
No new top-level directories. The only new file is `scripts/ingestion_scope.py`.

## Complexity Tracking

No Constitution violations to justify — all principles pass (see above). The
FR-007-vs-SC-001 parity tension is resolved: no backfill, byte-identical parity, with
the expected change confined to the first scoring run (documented in `research.md` §1
and reflected in `quickstart.md` §2).
