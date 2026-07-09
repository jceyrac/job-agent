# Data Model — Residence & Relocation Model

**Created**: 2026-07-09 | **Plan**: [plan.md](plan.md)

## Schema Changes

### `job_scores` — two new columns

```sql
ALTER TABLE job_scores ADD COLUMN relocation_cost INTEGER;   -- nullable
ALTER TABLE job_scores ADD COLUMN residence_base TEXT;       -- nullable
```

Added via migration script (Phase 9 pattern, following comp_flag Phase 8).

| Column | Type | Nullable | Default | Source |
|--------|------|----------|---------|--------|
| `relocation_cost` | INTEGER | Yes | NULL | `_evaluation_result()` → `score_result["relocation_cost"]` |
| `residence_base` | TEXT | Yes | NULL | `_evaluation_result()` → `score_result["residence_base"]` |

`NULL` means either (a) the row predates this spec and hasn't been rescored, or (b) the job was tier_0 rejected for a non-geography reason. Only filled when Engagement resolution runs and finds a feasible residence.

### `search_profiles.criteria` — two new JSON keys

```json
{
  "relocation": {
    "switzerland": {"cost": 0, "work_modes": ["remote","hybrid","on-site"], "remote_employer_countries": [...]},
    "france":      {"cost": 2, "work_modes": ["remote","hybrid","on-site"], "remote_employer_countries": [...]},
    "eu":          {"cost": 5, "work_modes": ["remote","hybrid","on-site"], "remote_employer_countries": [...]},
    "turkiye":     {"cost": 6, "work_modes": ["remote"],              "remote_employer_countries": [...]},
    "russia":      {"cost": 4, "work_modes": ["remote","hybrid"],     "min_salary_eur": 90000, "remote_employer_countries": ["Russia"]}
  },
  "contract_geo": {
    "permanent": ["switzerland","france","eu","turkiye","russia"],
    "freelance": ["switzerland","france","eu","turkiye"],
    "contract":  ["switzerland","france","eu","turkiye"]
  }
}
```

### Back-compat synthesis (`from_criteria()`)

When `relocation` is absent from criteria dict:
```python
wmg = criteria.get("work_mode_geography") or {}
remote_countries = (wmg.get("remote", {}) or {}).get("countries", []) or []
relocation = {
    "switzerland": {
        "cost": 0,
        "work_modes": ["remote", "hybrid", "on-site"],
        "remote_employer_countries": remote_countries,
    }
}
```
Behaviour-preserving: a single-residence Swiss-only form. Russia excluded.

When `contract_geo` is absent:
```python
contract_geo = {
    "permanent": ["switzerland", "france", "eu", "turkiye"],
    "freelance": ["switzerland", "france", "eu", "turkiye"],
    "contract":  ["switzerland", "france", "eu", "turkiye"],
}
```
Russia excluded by default.

## New Code Entities

### `_RESIDENCE_ZONES` (scorer.py constant)

Maps `company_country` to a residence zone key. Most-specific match wins.
```python
_RESIDENCE_ZONES = {
    "Switzerland": "switzerland",
    "France": "france",
    "Russia": "russia",
    "Türkiye": "turkiye", "Turkey": "turkiye",
    "Austria": "eu", "Belgium": "eu", ...  # EU-27
}
```

### `Engagement` dataclass (scorer.py)

```python
@dataclass
class Engagement:
    feasible_residences: dict[str, int]   # residence key → cost
    cheapest_residence: str | None
    relocation_cost: int | None
    blocked_reason: str | None
    rationale: str                        # prose for scoring_context
```

### `relocation` and `contract_geo` fields (SearchProfile)

```python
relocation: dict = field(default_factory=dict)
contract_geo: dict = field(default_factory=dict)
```

## Deprecated (kept inert)

`work_mode_geography` — stays on dataclass and in criteria round-trip. Synthesis source only. Never consumed by the new code path.
