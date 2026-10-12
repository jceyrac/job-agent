# Feature Specification: CV contact profile decided deterministically — Swiss only for jobs based in Switzerland (cv_agent)

**Feature Branch**: `038-cv-contact-profile-eu-rule`

**Created**: 2026-10-11 — **Rewritten 2026-10-11**: the profile is now computed by code from the job's country, never chosen by the LLM (Constitution IV).

**Status**: Draft — ready for `/speckit.plan` (clarify decided inline, 2026-10-11)

**Input**: User decisions 2026-10-11: (1) "Use a French address for roles in the EU. The agent must be updated with this rule." (2) "Swiss profile only for jobs based in Switzerland." Review 2026-10-11: the contact profile drives the CV header (address, phone) — it is control-flow data, so it must be computed deterministically, not proposed by the LLM.

---

## The rule (single source of truth for this spec)

| Job situation | Contact profile |
|---|---|
| Work location in Switzerland (incl. a remote role explicitly posted for Switzerland) | `swiss` (Pully / Lausanne, +41) |
| Anything else: any EU member state, the UK, the US and any other non-Swiss country, remote roles not tied to Switzerland | `french` (Paris, +33) |
| Country cannot be determined, or the posting mixes Swiss and non-Swiss work locations | `french`, **flagged** in `review.json` for the reviewer |

"Swiss employer", "CH-viable remote" and the DACH shortcut no longer qualify for `swiss`.

---

## Context (verified against source at `d3c2c81`, 2026-10-11)

### How the profile is chosen today

- The LLM chooses it in `analyze_and_plan` (`core/cv_agent/prompts.py`, `ANALYZE_AND_PLAN_PROMPT`, `proposed_profile`), from prose instructions ("swiss when the role is in Switzerland / a Swiss employer / CH-viable remote … Default swiss when uncertain").
- Observed failure: chargecloud (Cologne, remote-first) → the LLM proposed `swiss`. Rewording the prompt would reduce such errors, not remove them — the field decides the CV header, so it falls under Constitution IV.
- `swiss` is also the hard-coded fallback in four places:

| Where | Today |
|---|---|
| `core/cv_agent/nodes.py:356` | `data.get("proposed_profile", "swiss")` |
| `core/cv_agent/nodes.py:358` | invalid LLM value → `"swiss"` |
| `core/cv_agent/nodes.py:584` | `state.get("proposed_profile", "swiss")` |
| `core/cv_agent/renderer.py:140` | `"CH" if proposed_profile != "french" else "FR"` (missing/unknown → CH) |

### Country information available

- **Jobs from the DB** (`--job <id>`): `jobs.country_code` (ISO 3166-1 alpha-2, normalized by `scorer._normalize_country_code`) and `jobs.location`. Note: `country_code` is itself produced by the extraction LLM today and normalized in code — a pre-existing Constitution IV gap in extraction, **out of scope here** (recorded as a known limitation). It is still a stored, visible, normalized field, far better than a fresh LLM choice at CV time.
- **Jobs from a URL or a paste**: `nodes.py:188` and `:265` build the posting with `location=""`; the only source is the posting text.
- No deterministic Swiss-location detector exists in the codebase today.

### Related defect (unchanged from the first draft)

The lint `contact_consistency` (`lint.py:147`, `lint_config.py` `FR_LOCATIONS`) substring-matches the bare word `france`. With contact `CH`, the legitimate profile sentence "ownership of France's market-leading bank-data aggregation API" fails (false positive, chargecloud run, thread `697f0545f86f478bab1d`).

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — The CV header follows the job's country, decided by code (Priority: P1)

As the applicant, I want the CV header to be Swiss only for jobs based in Switzerland and French otherwise, decided by a deterministic rule I can predict, so that a wrong address never reaches a recruiter.

**Independent Test**: For a set of fixtures (DB job with `country_code`, paste with a "Location:" line, paste with no location, mixed locations), the computed profile and its recorded source match the rule table; no LLM call is involved in the decision.

**Acceptance Scenarios**:

1. **Given** `--contact swiss|french`, **Then** it wins over everything (unchanged behaviour), source `explicit`.
2. **Given** `--country <ISO2 or name>` (new, optional), **Then** profile = `swiss` iff the country is Switzerland, source `explicit_country`.
3. **Given** a DB job with a non-null `jobs.country_code`, **Then** profile = `swiss` iff `CH`, source `db_country`.
4. **Given** a URL or paste job, **Then** a deterministic detector reads only location statements of the posting (location fields / lines such as "Location:", "Standort:", "Lieu:", "Arbeitsort:", "Based in") and returns `CH`, `non_CH`, `mixed` or `unknown`; profile = `swiss` only for `CH`, source `detected`.
5. **Given** `unknown` or `mixed`, **Then** profile = `french`, and `review.json` carries a visible flag (`contact_profile_flag: "unknown_country" | "mixed_locations"`) so the reviewer can re-run with `--contact swiss` if needed.
6. **Given** the LLM output of `analyze_and_plan`, **Then** no field of it influences the profile: `proposed_profile` is removed from the prompt's required output (or ignored if returned), and the four `swiss` fallbacks are removed — a missing value can only come from the deterministic function, which never returns empty.
7. **Given** `review.json`, **Then** it records `contact_profile`, `contact_profile_source` (`explicit` / `explicit_country` / `db_country` / `detected` / `default`) and the flag if any.

---

### User Story 2 — The contact lint only flags a claimed residence (Priority: P1)

**Acceptance Scenarios**:

1. **Given** contact `CH` and the text "ownership of France's market-leading bank-data aggregation API", **Then** `contact_consistency` passes.
2. **Given** contact `FR` and "Based in Lausanne", **Then** it fails.
3. **Given** "Open to relocating to …", **Then** it is not a violation.
4. **Given** role sub-lines such as "Paris, France" for a past employer, **Then** they are not violations.

---

### User Story 3 — Validated on real postings (Priority: P1)

**Acceptance Scenarios**:

1. **Given** the chargecloud posting (Cologne), **When** re-run `--paste --auto` on the Mac, **Then** profile = `french` (`detected` or flagged default), lint clean.
2. **Given** a real Swiss-based posting (e.g. Sygnum, Zurich, or Neho, Lausanne), **Then** profile = `swiss`, source `detected` or `db_country`.
3. **Given** a remote posting with no country, **Then** profile = `french` with the `unknown_country` flag visible in `review.json`.

---

### User Story 4 — Optional master material is accepted when the agent uses it (Priority: P2)

The master CV gained (2026-10-11) three optional sections: `optional_roles` (IT & Product Instructor, Verva, 2023; Product Owner, The Business of Fashion, 2013–2014), `optional_bullets` (indexed by exact role title) and `optional_education` (drone pilot certification). They are real facts, never included by default; the `_note` tells the tailoring step when to use them. The tailoring prompt already receives the whole master, so no prompt change is needed — but the lint check `roles_match_master` (`lint.py`, compares rendered roles with `master["roles"]` + allowlist `extra_roles`) would fail on an optional role the agent legitimately picked.

**Acceptance Scenarios**:

1. **Given** a rendered CV that includes an `optional_roles` entry with its exact title, dates and sub, **Then** `roles_match_master` passes.
2. **Given** a rendered role whose title matches an optional role but with altered dates or location, **Then** it fails (same strictness as for regular roles).
3. **Given** the empty-LLM-output fallback in `tailor_cv` (`nodes.py:472`), **Then** it keeps using `master["roles"]` only — optional material never appears by default.
4. **Given** a posting with no reason to use optional material (e.g. a fintech PM role), **Then** a validation run shows no optional role in the CV.

**FR-010**: `roles_match_master` MUST accept roles from `master["optional_roles"]` with the same exact-match rules; no other behaviour change; tests for US4-1..3.

---

### Edge Cases

- A posting that mentions a Swiss HQ in the company description but a Berlin work location → the detector reads location statements only, not the description → `non_CH` → `french`.
- "Remote (Switzerland)" / "Remote – CH only" → `CH` → `swiss`.
- "Zurich or Berlin" in the location line → `mixed` → `french` + flag.
- Country names in several languages: Switzerland / Schweiz / Suisse / Svizzera / Svizra; ISO `CH`; Swiss postal-code pattern `CH-\d{4}`; a curated list of Swiss cities and cantons (versioned in code, generic — no personal data).
- `--country` given together with `--contact` → `--contact` wins; both are recorded.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A pure, deterministic function (e.g. `core/geo_ch.py`, no LLM, no network) MUST classify location text as `CH` / `non_CH` / `mixed` / `unknown`, reading only location statements, with unit tests per edge case above.
- **FR-002**: The cv_agent MUST compute the contact profile with this precedence: `--contact` → `--country` → `jobs.country_code` (DB jobs) → detector on the posting's location statements → `french` default. `swiss` iff the resulting country is Switzerland.
- **FR-003**: `analyze_and_plan` MUST no longer decide the profile: the prompt no longer asks for `proposed_profile` (and contains no "DACH", "Swiss employer", "CH-viable" wording); any returned value is ignored.
- **FR-004**: The four hard-coded `swiss` fallbacks (`nodes.py:356, 358, 584`, `renderer.py:140`) MUST be removed; the renderer receives an explicit profile and raises if it is missing (no silent CH header).
- **FR-005**: `--country` (ISO-2 or common country name) MUST be added to the CLI as an optional argument; `--contact` keeps overriding.
- **FR-006**: `review.json` MUST include `contact_profile`, `contact_profile_source` and `contact_profile_flag` (when `unknown`/`mixed`).
- **FR-007**: `contact_consistency` MUST fire only on a claim of residence/base in the wrong country (US2), with tests US2-1..4.
- **FR-008**: Tests, no live LLM call: detector cases; precedence (each source); renderer raises on missing profile; golden-prompt check that `proposed_profile` and the forbidden wording are absent from `ANALYZE_AND_PLAN_PROMPT`; lint cases.
- **FR-009**: Validation and release: empirical runs of US3 on the Mac (the cv_agent runs there, not in the prod containers), then ff-only merge to `main` and a standard `deploy.sh` to keep the image aligned. Staging is not required: no code path used by the tracker or the cron changes (`core/geo_ch.py` is new and only imported by the cv_agent) — to be confirmed in the plan by an import check.
- **FR-010**: `roles_match_master` MUST accept roles from `master["optional_roles"]` with the same exact-match rules; no other behaviour change (US4).

### Non-goals

- Fixing the extraction LLM's `country_code` / `geo_zone` (pre-existing Constitution IV gap in `core/scorer.py`) — candidate for a later spec.
- CV content rules, relocation phrasing, Nextcloud filing.
- The Claude.ai skill `job-application-docs` (outside the repo): updated separately by the user with the same rule table.

---

## Success Criteria *(mandatory)*

- **SC-001**: The profile decision involves no LLM output (verified by test: a mocked LLM returning `proposed_profile: "swiss"` for a Cologne posting still yields `french`).
- **SC-002**: chargecloud re-run → `french`; a Swiss posting → `swiss`; a country-less remote posting → `french` + visible flag.
- **SC-003**: `contact_consistency` passes on the chargecloud output with `CH` and still fails on "Based in Lausanne" with `FR`.
- **SC-004**: Full suite green; no `swiss` fallback left in `core/cv_agent/`.
- **SC-005**: Deployed SHA = merged SHA; health OK.

---

## Clarifications already decided (2026-10-11)

1. Non-EU, non-Swiss jobs (UK, US, Gulf, Türkiye) → `french`.
2. Unknown country → `french`, flagged.
3. Mixed Swiss / non-Swiss work locations → `french`, flagged; the reviewer re-runs with `--contact swiss` when the Swiss location is the one applied for.
