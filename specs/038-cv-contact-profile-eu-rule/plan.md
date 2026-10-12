# Implementation Plan: CV contact profile decided deterministically — Swiss only for CH-based jobs

**Branch**: `038-cv-contact-profile-eu-rule` | **Date**: 2026-10-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/038-cv-contact-profile-eu-rule/spec.md`

## Summary

The CV header (address, phone) is control-flow data, so it must be derived
deterministically from the job's country (Constitution IV) — never proposed by the
LLM. Today `analyze_and_plan` lets the LLM choose `proposed_profile`
(`"swiss"`/`"french"`), with four hard-coded `swiss` fallbacks. This spec replaces
that with a pure classifier `core/geo_ch.py` and a fixed precedence
(`--contact` → `--country` → `jobs.country_code` → location detector → `french`),
records the source in `review.json`, and fixes two adjacent defects (the
`contact_consistency` lint false-positive, and `roles_match_master` rejecting
`optional_roles`).

## Technical Context

- **Language/Version**: Python 3.11.
- **Primary Dependencies**: none new — LangGraph (existing); `re`/`json` (stdlib)
  for the classifier; `pytest` for tests.
- **Storage**: SQLite **unchanged** — no schema change. The decision is in-memory
  graph state plus `review.json`; it reads the existing `jobs.country_code` (ISO-2).
- **Testing**: pytest. New unit tests are deterministic (no live LLM); a
  golden-prompt test asserts `proposed_profile` and the forbidden wording are absent
  from `ANALYZE_AND_PLAN_PROMPT`.
- **Target Platform**: the cv_agent runs only on the dev Mac
  (`python -m core.cv_agent.cli`), never in the prod containers (tracker/agent) —
  no staging required (FR-009).
- **Project Type**: CLI agent (LangGraph, human-in-the-loop), in the `core/` domain.
- **Performance Goals**: n/a — a few regex passes over one posting.
- **Constraints**: no LLM/network in the classifier; surgical diffs; `core/cv_agent/`
  is **not** stable-core, so it is editable.
- **Scale/Scope**: 1 new domain module, ~6 edited cv_agent files, ~3 new/edited test files.

## Constitution Check

*GATE: pass — re-checked after design (no new violations introduced).*

- **IV (Structure déterministe)** — this feature *is* the correction of a §IV
  violation: it removes the LLM's `proposed_profile` and computes the profile
  deterministically. PASS.
- **V (Modification chirurgicale)** — no new dependency, no schema change, no
  opportunistic refactor; the two adjacent defects are explicitly in-scope per the
  spec. PASS.
- **X (Catalogue partagé / espace utilisateur)** — no catalogue entity is changed;
  `core/geo_ch.py` adds a pure domain function, not catalogue data. PASS.
- **VII (Sécurité d'abord)** — no network egress, no provider naming, no secrets. PASS.
- **VI (Validation empirique)** — US3 mandates empirical re-runs (chargecloud, a
  Swiss posting, a country-less remote posting) before release. PASS.

No violations → **Complexity Tracking** is empty.

## Project Structure

### Documentation (this feature)

```text
specs/038-cv-contact-profile-eu-rule/
├── plan.md              # this file
├── research.md          # decisions
├── data-model.md        # decision tuple + state/review.json changes
├── quickstart.md        # validation runs
├── contracts/
│   └── contact-profile.md   # geo_ch API + --country + review.json schema
└── tasks.md             # (next step, /speckit-tasks)
```

### Source Code (repository root)

```text
core/
├── geo_ch.py                     # NEW — pure classifier + precedence (no LLM/network/DB)
└── cv_agent/
    ├── nodes.py                  # analyze_and_plan stops deciding the profile; resolve_reference computes it
    ├── state.py                  # add contact_override/country_override + source/flag; drop profile_confidence
    ├── cli.py                    # --country arg; seed overrides; gate shows source, not confidence
    ├── prompts.py                # drop proposed_profile + forbidden wording from ANALYZE_AND_PLAN_PROMPT
    ├── renderer.py               # render_documents takes an explicit profile; raises on missing
    ├── review.py                 # write contact_profile / _source / _flag
    └── lint.py                   # contact_consistency residence-claim fix; roles_match_master accepts optional_roles
tests/
├── test_geo_ch.py                # NEW — detector + precedence + renderer-raises + golden-prompt cases
├── test_cv_agent_lint.py         # extend — contact_consistency US2-1..4; roles_match_master US4-1..3
└── test_cv_agent_boundary.py     # add core.geo_ch to ALLOWED_CORE_MODULES
```

**Structure Decision**: single-project; the classifier is a domain module at
`core/geo_ch.py` (only the cv_agent imports it today), admitted by the existing
agent import-boundary guard.

## Complexity Tracking

*(empty — no constitution violations)*
