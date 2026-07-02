# Plan — Spec 017: Profile field consolidation

## Summary

Collapse five scattered title lists and redundant search inputs into canonical
DB-stored profile fields (`job_titles`, `title_exclude`,
`allowed_contract_types`, `languages_spoken`). Eleven pipeline touch-points
derive from these fields. Three deprecated fields go inert; two hardcoded
module constants become fallbacks only.

## Technical Context

- **Language**: Python 3.11
- **Storage**: SQLite via `JobStorage` (no schema change — fields ride in `criteria` JSON)
- **UI**: Streamlit `st.form` in `tracker_views/settings.py`
- **LLM**: DeepSeek extraction (vocabularies unchanged)
- **Scrapers**: LinkedIn, Indeed (jobspy), Greenhouse
- **Gate**: `title_gate.py` substring match over `_PM_TITLES` fallback

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I (filet large) | ✅ Strengthened | Greenhouse US geo filter removed → geography returns to scorer |
| II (two paths) | ✅ Compliant | Code change; `scoring_context` edits are payload |
| IV (deterministic) | ✅ Strengthened | Language + contract-type → Tier-0 deterministic over extracted fields |
| V (surgical) | ✅ Compliant | Additive fields, deprecated inert, dead code removed, blast radius bounded |
| VI (empirical) | ✅ Compliant | 3 new mock cases + existing 8 must stay in-band |
| VIII (scrapers) | ✅ No change | Greenhouse still reads profile; LinkedIn/Indeed still query-driven |
| IX (scoring optional) | ✅ No change | All new fields default to permissive (empty = no restriction) |

No violations.

## Files changed (11 files, in dependency order)

| # | File | Change | Risk |
|---|---|---|---|
| 1 | `profiles.py` | New fields + deprecations + `effective_search_locations()` + `to`/`from_criteria` + seed + docstring + `scoring_context` prose | Medium |
| 2 | `title_gate.py` | Parameterised signature (`_PM_TITLES` fallback) | Low |
| 3 | `scorer.py` | Tier-0: positive language, contract-type, drop banned_countries | Medium |
| 4 | `score.py` | Pre-filter injection + digest filters + 3 mock cases | Medium |
| 5 | `scrapers/greenhouse.py` | Read `job_titles`, drop US filter, drop local consts | Low |
| 6 | `scrapers/boards/linkedin.py` | Derive terms + locations from profile | Low |
| 7 | `scrapers/boards/indeed.py` | Derive terms + locations from profile | Low |
| 8 | `scrape.py` | `JobFilter` from canonical fields + parameterised gate | Low |
| 9 | `filters.py` | Remove dead branches, preserve return arity | Low |
| 10 | `tracker_views/settings.py` | `_render_profile_editor` reorg + save block | Medium |
| 11 | Validate | `python score.py --mock --profile unified_jc` (11 cases) | — |

## Key design decisions

1. **`from_criteria` synthesis**: `job_titles` ← deduped union of `scrape_titles` + `search_query_titles`; `languages_spoken` ← `["french", "english"]` (not inverted from `excluded_languages` — fragile); `allowed_contract_types` ← `["permanent", "freelance", "contract", "unknown"]`
2. **`effective_search_locations()`**: returns explicit `search_locations` if non-empty; else union of all `work_mode_geography` country lists
3. **`banned_countries` removal safety**: per-mode geography already covers all cases; score changes from 1→2 for these rejects (both below threshold)
4. **`filters.py` arity**: return `0` for the removed `excluded_geo` counter slot; callers in `scrape.py` still unpack 3 values

## Data model diff

```python
# New canonical fields
job_titles: list[str]             # single include list
title_exclude: list[str]          # single exclude list
allowed_contract_types: list[str] # allowlist; empty = no restriction
languages_spoken: list[str]       # positive allowlist; empty = no restriction

# Deprecated (kept inert for round-trip)
scrape_titles, search_query_titles → superseded by job_titles
scrape_exclude → superseded by title_exclude
excluded_languages → superseded by languages_spoken
banned_countries → removed as enforcement mechanism
```

## Mock regression cases

Keep all 8 existing 016 cases. Add 3:

| Case | Expected | Rule exercised |
|---|---|---|
| USRemoteCo (US remote) | 1–2 (Tier-0, score 2) | Per-mode geography (US not in remote.countries) |
| BerlinBank (German required) | 1 (Tier-0, score 1) | Positive language rule (German not spoken) |
| InternCo (internship) | 1 (Tier-0, score 1) | Contract-type rule (internship not in allowlist) |

## Validation steps

1. `python -m pytest tests/test_storage.py` — 145 tests pass
2. `python score.py --mock --profile unified_jc` — all 11 cases in band
3. `python -c "from profiles import SearchProfile; ..."` — legacy synthesis round-trip
4. Settings save + `sqlite3` round-trip: `criteria` JSON contains `job_titles`, `languages_spoken`
