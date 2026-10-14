# Contract — Tier 0 title rule (FR-005)

## Location & order

Inside `core/scorer.py::evaluate_for_profile`, inserted as the **first** Tier 0 rule
(before the denylist rule at line 789). Deterministic, no LLM call.

## Inputs

- `job.title` (may be empty/`None`)
- `profile.job_titles` (include list; may be empty → open)
- `profile.title_exclude` (exclude words; may be empty → no exclude check)

## Matching semantics (unchanged from today)

| Rule | Semantics | Helper |
|------|-----------|--------|
| Include | case-insensitive **substring**; empty list = every title passes | `core.title_gate.title_matches_profile` (existing) |
| Exclude | case-insensitive **word-boundary** `\b{word}\b` on the title | `core.title_gate.title_excluded` (new, returns first offending word or `None`) |

## Output

| Condition | `score` | `reason` | `scored_by` |
|-----------|---------|----------|-------------|
| title does not match `job_titles` | `1` | `filtered: title not in profile` | `tier_0` |
| title contains an exclude word `w` | `1` | `filtered: excluded title word ({w})` | `tier_0` |

`comp_flag` (already computed at the top of `evaluate_for_profile`) is passed through
on both branches. `country_code` stays `None`.

## Guarantees

- Applies to **every** job (monitored and non-monitored) — there is no
  `monitored_company_id` special case.
- Empty `job_titles` ⇒ the include branch never fires (open), matching the old
  ingestion behaviour.
- A job already failing an earlier rule would not reach the title rule; a job passing
  the title rule continues to the existing Tier 0 rules (denylist → language → sector
  → contract → work mode → geography) unchanged.

## Negative fixtures (guard tests must catch)

- A title that matches nothing and an empty `title_exclude` → `filtered: title not in profile`.
- A title containing `intern` with `title_exclude=["intern"]` → `filtered: excluded title word (intern)`, but `international` is **not** excluded (word boundary).
- `job_titles=[]` → no title filtering.
