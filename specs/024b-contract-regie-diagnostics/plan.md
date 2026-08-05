# Implementation Plan: Contrat régie + diagnostics + titres DE/FR

**Branch**: `024b-contract-regie-diagnostics` | **Date**: 2026-08-05 | **Spec**: [024b-spec.md](024b-spec.md)

**Status**: Ready. Extracted from spec 024 (abandoned) — no scraper, no new dependencies.

## Summary

Four independent changes, all small:
1. **`regie` vocabulary** — add to prompts, defaults, badge maps (the `regie` label is already in the prose sections of both scorer prompts)
2. **`filter_funnel.py`** — standalone diagnostic script for per-source filter attribution
3. **`AUTHORITATIVE_CONTRACT_SOURCES`** — empty scaffold for future scraper use
4. **DE/FR job_titles** — prose path via Settings UI

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: None new — `sqlite3` stdlib for filter_funnel.py
**Storage**: No schema changes. `contract_type` is TEXT, `regie` accepted without migration.
**Testing**: pytest (190 tests), no new tests required
**Target Platform**: Dev Mac → Docker/Linux production
**Project Type**: CLI pipeline + Streamlit UI
**Performance Goals**: N/A (no new runtime code paths; filter_funnel.py is offline)
**Constraints**: No `models.py`/`storage.py` changes. No new scrapers. No title normalizer.

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| **I. Filet large** | ✅ | No filtering changes. `regie` in defaults ensures these jobs are not excluded. |
| **II. Deux chemins** | ✅ | US3 (job_titles) is prose path via Settings. All other changes are code path. |
| **IV. Structure déterministe** | ✅ | `regie` is a scraper-populated value, not LLM-inferred. `AUTHORITATIVE_CONTRACT_SOURCES` mechanism enforces this. |
| **V. Chirurgical** | ✅ | Each change is a single-concern edit in one file. No refactoring. |
| **VII. Sécurité** | ✅ | No new egress, no credentials. `filter_funnel.py` is read-only. |
| **IX. Scoring optionnel** | ✅ | `regie` badge displays without scoring; filter_funnel.py works on unscored jobs. |

**Gate: PASS.**

## Project Structure

### Documentation

```text
specs/024b-contract-regie-diagnostics/
├── 024b-spec.md
├── plan.md
```

### Source Code

```text
scorer.py                    # Contract type enum lines + AUTHORITATIVE_CONTRACT_SOURCES scaffold
profiles.py                  # allowed_contract_types defaults (add "regie")
notifier.py                  # Badge maps (HTML + Markdown) — add 🤝 Régie
tracker_views/shared.py      # Badge map (Streamlit) — add 🤝 Régie
scripts/filter_funnel.py     # NEW — standalone diagnostic

# Prose path (no code change):
#   tracker_views/settings.py → add DE/FR titles to job_titles via UI
```

## Implementation Phases

### Phase 1: `regie` vocabulary — scorer.py prompts (US1)

**What**: The prose sections of `SYSTEM_PROMPT` and `EXTRACTION_PROMPT` already mention `regie` (lines 45, 244). Add `regie` to the JSON enum lines.

**Actual changes**:
- Line ~171: `"contract_type": "<permanent|freelance|contract|internship|unknown>"` → add `|regie`
- Line ~192: `"contract_type": "<permanent|freelance|contract|unknown>"` → add `|regie`

**Why first**: Independent of other changes. No runtime effect until a scraper produces `regie` jobs.

### Phase 2: `regie` vocabulary — profiles.py defaults (US1)

**What**: Add `"regie"` to the default `allowed_contract_types` lists so regie jobs aren't filtered out at Tier-0.

**Actual changes**:
- Line 49: update docstring `{permanent, freelance, contract, internship, unknown}` → add `regie`
- Line 129-131: `from_criteria()` fallback: add `"regie"` to the list
- Line 361: default profile `UNIFIED_JC`: add `"regie"` to the list

### Phase 3: `regie` vocabulary — UI badges (US1)

**What**: Add `🤝 Régie` to badge maps so regie jobs are visually distinct.

**Actual changes**:
- `notifier.py` line 53: `contract_badge_map` dict — add `"regie": "🤝"`
- `notifier.py` line 168: `CONTRACT_BADGE` dict — add `"regie": "🤝 Régie"`
- `tracker_views/shared.py`: search for the badge map (used in filter display) — add `"regie": "🤝 Régie"`

**Note**: The contract_type filter in `tracker_views/jobs.py` gathers distinct values from the DB — `regie` appears automatically when a regie job exists.

### Phase 4: `AUTHORITATIVE_CONTRACT_SOURCES` scaffold (US4)

**What**: Add an empty set and guarded override block to `scorer.py`. With an empty set, this is a no-op.

**Actual changes** (in `scorer.py`):
1. After `_EVAL_PASSTHROUGH_KEYS`, add:
   ```python
   # Sources whose contract_type takes authority over Groq inference.
   # When a scraper populates contract_type from source data, add its
   # SOURCE_NAME here. See spec 024b, FR-008.
   AUTHORITATIVE_CONTRACT_SOURCES: set[str] = set()
   ```
2. In `extract_job_fields()`, after `job.contract_type = result["contract_type"]`, add:
   ```python
   if (job.source in AUTHORITATIVE_CONTRACT_SOURCES
           and job.contract_type
           and job.contract_type != "unknown"):
       result["contract_type"] = job.contract_type
   ```

**Verification**: `python -m pytest tests/` — 190/190 pass (empty set → branch never taken).

### Phase 5: `scripts/filter_funnel.py` (US2)

**What**: Standalone read-only script. Same pattern as `scripts/audit_provenance.py`.

**Key design**:
- argparse: `--source` (required), `--db` (default `data/jobs.db`)
- Read-only SQLite connection
- Reconstruct `JobPosting` objects from DB rows
- Import `JobFilterEngine` from `filters.py` and `load_active_profile` from `profiles.py`
- Apply filters in sequence, logging cumulative counts

### Phase 6: Prose path — DE/FR job_titles (US3)

**What**: Add 3 titles to the active profile via Streamlit Settings UI. No code.

```
Leiter Produktmanagement
Chef de produit
Responsable produit
```

### Phase 7: Validation

- `python -m pytest tests/` — 190/190 pass
- Verify `regie` in prompts: `grep -c '"regie"' scorer.py` ≥ 4 (2 prose + 2 enum)
- Verify badge maps have `regie` entry
- Run `filter_funnel.py --source LinkedIn` (or any active source) — output is coherent

## Complexity Tracking

No constitution violations — this section intentionally left empty.
