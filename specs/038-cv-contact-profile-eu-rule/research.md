# Research: CV contact profile decided deterministically (spec 038)

All decisions below resolve the spec's open points against the current source
(verified at `d3c2c81`). No external research was needed — this is internal code
understanding plus the rule table already fixed in `spec.md`.

## 1. Where the profile is computed today (and what changes)

- `analyze_and_plan` (`core/cv_agent/nodes.py:331`) reads the LLM's
  `proposed_profile` and defaults it to `"swiss"`; two further `swiss` fallbacks
  live in `nodes.py` (`:358` invalid-value, `:584` render call) and one in
  `renderer.py:140` (`"CH" if proposed_profile != "french" else "FR"`).
- Decision: **compute the profile in `resolve_reference`** (the first node), not in a
  new graph node. `resolve_reference` already returns `entry_kind`, `job` (including
  `jobs.country_code` for id entries — `get_job_for_prepare` selects it,
  `JobPosting.to_json()` serialises it) and `description_text`; all four precedence
  inputs are available there. This keeps the graph topology (and
  `node-io-contract`) unchanged.

## 2. Detector classification (`core/geo_ch.py`)

- `classify_location(text) -> "CH" | "non_CH" | "mixed" | "unknown"`.
- Reads **location statements only** — lines/fields matched by a small set of labels
  (`Location:`, `Standort:`, `Lieu:`, `Arbeitsort:`, `Ort:`, `Based in`, `Remote`,
  …) plus the posting's `location` field — never the company description.
- Signal vocabularies (curated, generic, versioned in code — no personal data):
  - **Swiss**: country names (Switzerland, Schweiz, Suisse, Svizzera, Svizra), ISO
    `CH`, the `CH-\d{4}` postal pattern, Swiss cities (Zurich/Zürich, Lausanne,
    Pully, Geneva/Genève, Bern/Basel/Bâle, Zug, Winterthur, Lucerne/Luzern, …),
    cantons (Vaud, Geneva, Zurich, Bern, Zug, …).
  - **Non-Swiss**: country names (Germany/Deutschland/Allemagne, France/Frankreich,
    UK/United Kingdom/Royaume-Uni/Großbritannien, USA/United States/États-Unis, …),
    ISO-2 codes, and major non-CH cities (Berlin, Cologne/Köln, Paris, London,
    New York, …) — enough to turn the spec's real cases (Cologne, Berlin) into `non_CH`.
- Classification: `CH` = ≥1 Swiss signal, 0 non-Swiss; `non_CH` = 0 Swiss, ≥1
  non-Swiss; `mixed` = ≥1 of each; `unknown` = neither (or no location statement).
- `normalize_country(raw) -> ISO-2 | None` maps a `--country` value (ISO-2 code or a
  common name, Swiss or otherwise) to a 2-letter code — "Suisse" → `CH`,
  "Germany" → `DE`, "ch" → `CH`.

## 3. Precedence function (FR-002)

`compute_contact_profile(entry_kind, job, contact_override, country_override)
-> (profile, source, flag)`:

| Order | Input | Result | source | flag |
|---|---|---|---|---|
| 1 | `contact_override` ∈ {swiss, french} | that value | `explicit` | — |
| 2 | `country_override` | swiss iff `normalize_country` == `CH` | `explicit_country` | — |
| 3 | `entry_kind == "id"` and `job.country_code` | swiss iff `CH` | `db_country` | — |
| 4 | `classify_location(...)` == `CH` | swiss | `detected` | — |
| 5 | == `non_CH` | french | `detected` | — |
| 6 | == `mixed` | french | `detected` | `mixed_locations` |
| 7 | == `unknown` (or no signal) | french | `default` | `unknown_country` |

The spec lists sources `explicit / explicit_country / db_country / detected / default`
and flags `unknown_country / mixed_locations`; the mapping above is the only
assignment consistent with US1 scenarios 1–7.

## 4. Import-boundary guard

- `tests/test_cv_agent_boundary.py` rule (A) allow-lists the domain modules the agent
  may import. `core/geo_ch.py` is a new domain module imported by the agent, so
  `"core.geo_ch"` is added to `ALLOWED_CORE_MODULES`. Rule (C) (no domain module
  imports the agent) is unaffected — `geo_ch` imports nothing from `core.cv_agent`.

## 5. `profile_confidence` is vestigial

- It meant "how confident the LLM is in its proposed profile". Once the profile is
  deterministic, confidence has no meaning, so it is removed with `proposed_profile`
  from the prompt, `fit_analysis`, `state.py`, the CLI gate display, and `review.json`.
  (Spec FR-003 mandates removing `proposed_profile`; removing its paired
  `profile_confidence` is the same surgical excision of the removed LLM decision.)

## 6. `contact_consistency` fix (US2 / FR-007)

- Today `_check_contact_consistency` (`lint.py:147`) substring-matches
  `FR_LOCATIONS`/`CH_LOCATIONS` against `profile`+`relocation` — so "France's
  market-leading bank" flags when the contact is `CH`.
- Fix: only fire on a **claim of residence/base**, not on a bare country token:
  require a base-verb context (`based in`, `residing in`, `located in`, `living in`,
  `currently in`, `resident of`, …) next to the location token, and exclude future
  intent (`open to relocating to`, `willing to relocate to`, `relocation`). `role.sub`
  is already outside the checked blob (only `profile`+`relocation`), so past-employer
  sub-lines stay clean (US2-4).

## 7. `roles_match_master` accepts `optional_roles` (US4 / FR-010)

- `_check_roles_match_master` matches each rendered role's `(dates, sub)` against
  `master["roles"]`. `run_lint` passes `master.get("roles")`; the fix passes
  `(master.get("roles") or []) + (master.get("optional_roles") or [])`. Same exact
  `(dates, sub)` strictness ⇒ US4-2 still fails on altered dates/location.
- `tailor_cv`'s empty-output fallback (`nodes.py:472`) is unchanged — it keeps using
  `master["roles"]` only (US4-3), so optional material never appears by default.

## 8. FR-009 staging skip — import check

- `core/geo_ch.py` is imported only by `core/cv_agent/` (confirmed by
  `grep -rn "geo_ch" core/ tracker_views/ tracker.py`). No tracker/cron code path
  touches it, and the cv_agent is launched only as `python -m core.cv_agent.cli` on
  the Mac. So staging is unnecessary; release is empirical US3 runs → ff-only merge →
  standard `deploy.sh` (image alignment only).
