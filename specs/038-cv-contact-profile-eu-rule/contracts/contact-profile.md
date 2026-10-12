# Contract: deterministic contact profile (spec 038)

## 1. `core/geo_ch.py` public API

```python
def classify_location(text: str) -> str:              # "CH" | "non_CH" | "mixed" | "unknown"
def normalize_country(raw: str) -> str | None:        # ISO 3166-1 alpha-2, or None
def compute_contact_profile(
    entry_kind: str,               # "id" | "url" | "paste"
    job: dict,                     # JobPosting.to_json()-shaped
    contact_override: str | None,  # from --contact
    country_override: str | None,  # from --country
) -> tuple[str, str, str | None]:  # (profile, source, flag)
```

Precedence and the source/flag mapping are the single source of truth in
`research.md` §3 (and the rule table in `spec.md`). The function is pure and raises
only on programmer error, never on missing data — missing data resolves to `french`
(`default` source, `unknown_country` flag).

## 2. CLI

- `--contact swiss|french` — unchanged; wins over everything (source `explicit`).
- `--country <ISO2|name>` — NEW, optional; profile = `swiss` iff the country is
  Switzerland (source `explicit_country`). `--contact` still overrides `--country`.

## 3. `review.json`

Adds `contact_profile`, `contact_profile_source`, `contact_profile_flag`
(data-model §4). Reviewers re-run `--contact swiss` when a mixed/unknown flag shows
a Swiss location was the one applied for.

## 4. Renderer

`render_documents(..., proposed_profile, ...)` now receives an explicit profile and
**raises** (rather than defaulting to CH) when it is missing or not `swiss`/`french`.
