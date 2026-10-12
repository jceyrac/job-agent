# Data model: CV contact profile decision (spec 038)

**No SQLite schema change.** This spec introduces one in-memory value object and
adjusts graph state + the `review.json` bundle.

## 1. `ContactProfileDecision` (new value object)

Returned by `core.geo_ch.compute_contact_profile(...)`:

| Field | Type | Values |
|---|---|---|
| `profile` | str | `"swiss"` \| `"french"` |
| `source` | str | `"explicit"` \| `"explicit_country"` \| `"db_country"` \| `"detected"` \| `"default"` |
| `flag` | str \| None | `"mixed_locations"` \| `"unknown_country"` \| `None` |

Invariants:
- `profile == "swiss"` ⟺ the resolved country is Switzerland (every other path ⇒ `french`).
- `flag` is set ⟺ the country was mixed or unknown (and then `profile == "french"`).
- The function is pure (no I/O) — the same inputs always yield the same tuple.

## 2. Detector verdict

`classify_location(text) -> "CH" | "non_CH" | "mixed" | "unknown"` (pure).

## 3. `CVAgentState` changes (`core/cv_agent/state.py`)

- Added inputs (seeded by the CLI): `contact_override: str | None`,
  `country_override: str | None`.
- Added computed: `contact_profile_source: str`, `contact_profile_flag: str | None`.
- Kept (now deterministic): `proposed_profile: str` — carries the computed
  `"swiss"`/`"french"`.
- Removed: `profile_confidence` (vestigial — research §5).

## 4. `review.json` additions (`core/cv_agent/review.py`)

Top-level additions (replacing `fit_analysis.proposed_profile`):

```json
{
  "contact_profile": "swiss" | "french",
  "contact_profile_source": "explicit" | "explicit_country" | "db_country" | "detected" | "default",
  "contact_profile_flag": "mixed_locations" | "unknown_country" | null,
  "...": "..."
}
```

## 5. `fit_analysis` shape change

`analyze_and_plan` now writes `{fit_recap, angle, gaps}` only (no `proposed_profile`,
no `profile_confidence`).
