# Research — Residence & Relocation Model

**Created**: 2026-07-09 | **Plan**: [plan.md](plan.md)

All structural claims in the spec were verified against live source by Fable
review on 2026-07-09. This file records the verification outcomes and the
implementation patterns identified.

## Live-code verification (2026-07-09)

| Claim | File | Lines | Verdict |
|-------|------|-------|---------|
| `comp_flag` migration pattern exists | `storage.py` | 498-502 | ✅ Phase 8 pattern: `ALTER TABLE ... ADD COLUMN comp_flag INTEGER DEFAULT 0` |
| `save_scored()` persists comp_flag | `storage.py` | 1516-1532 | ✅ INSERT/UPDATE includes `comp_flag` |
| `_evaluation_result()` threads comp_flag | `scorer.py` | 658-679 | ✅ `comp_flag: int = 0` parameter, included in dict |
| `_geography_for_mode()` is the sole geo enforcement | `scorer.py` | 682-699, 763-779 | ✅ Called only at Tier-0 rule 5 |
| comp_flag hardcodes `"Switzerland"` as home | `scorer.py` | 725 | ✅ `company_country != "Switzerland"` |
| profile_context injection point before LLM call | `scorer.py` | 781-796 | ✅ Monitoring note pattern for prose injection |
| `_run_mock()` extract→evaluate→assert-band pattern | `score.py` | 145-215 | ✅ `extract_job_fields()` → `evaluate_for_profile()` → band check |
| `from_criteria()` synthesis from legacy dicts | `profiles.py` | 107-160 | ✅ `work_mode_geography` synthesis from `allowed_countries` + `hybrid_ok_countries` |
| `to_criteria_dict()` round-trip | `profiles.py` | 76-105 | ✅ Returns dict of all fields for JSON storage |
| `work_mode_geography` in UNIFIED_JC seed | `profiles.py` | 268-310 | ✅ `remote.countries` list for remote_employer_countries seed |
| Digest uses scored fields (comp_flag) | `storage.py` | 1624-1640 | ✅ `get_digest()` SELECT includes `comp_flag` |

## Implementation patterns identified

### Migration pattern (from comp_flag Phase 8)

```python
cols = {row[1] for row in conn.execute("PRAGMA table_info(job_scores)").fetchall()}
if "comp_flag" not in cols:
    conn.execute("ALTER TABLE job_scores ADD COLUMN comp_flag INTEGER DEFAULT 0")
```

For this spec, replicate with two columns: `relocation_cost INTEGER` (nullable) and `residence_base TEXT` (nullable). Phase number: 9.

### save_scored() pattern

Lines 1515-1532: explicit column list in INSERT, `ON CONFLICT DO UPDATE SET`, values from `score_result.get(...)`. Add `relocation_cost` and `residence_base` to both clause lists. Nullable — no schema default needed.

### Profile field addition pattern

From spec 016/017 precedent: add field to `SearchProfile` dataclass with `field(default_factory=dict)`, add to `to_criteria_dict()`, add to `from_criteria()` with legacy synthesis when absent. Do NOT delete deprecated fields.

### EU-27 member list for `_RESIDENCE_ZONES`

Deterministic code constant in `scorer.py`. The current EU-27 membership as of 2026-07:
Austria, Belgium, Bulgaria, Croatia, Cyprus, Czech Republic, Denmark, Estonia, Finland, France, Germany, Greece, Hungary, Ireland, Italy, Latvia, Lithuania, Luxembourg, Malta, Netherlands, Poland, Portugal, Romania, Slovakia, Slovenia, Spain, Sweden.

Note: France has a specific entry ("france") that must match before the general "eu" entry.

### MOCK_JOBS additions

Insert after the WarsawSoft case (line 79) to keep the existing 11 cases indexed correctly. The four new cases are indexed 12-15.

### Settings UI pattern

From Spec 018 sectioning: `_render_profile_editor()` already has sections. Replace the per-mode geography widgets with a new "Residence & relocation" subsection. Each residence row: cost slider, work-mode multiselect, optional salary-floor number input, remote_employer_countries text area. Contract geo: per contract type, multiselect over residence zone keys. Addable/removable rows — `st.session_state` keyed by a counter.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Delete `_geography_for_mode()` | No callers remain; avoids dead code accumulation |
| Keep `work_mode_geography` inert | 016/017 precedent — deprecated fields stay for round-trip |
| No new geo_zone vocabulary | `russia_cis` deferred to Spec 023 (RUB extraction) |
| No `residence_requirement` extraction field | Not observed in real data yet (Constitution VI) |
| No live FX API | Principle V — RUB floor drift is acceptable noise |
| Digest unchanged | After `--rescore`, all rows carry new fields; no filter needed |
