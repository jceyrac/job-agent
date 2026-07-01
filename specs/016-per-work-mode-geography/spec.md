# Spec 016 — Per-work-mode geography

> Spec for Claude Code. Read `profiles.py`, `scorer.py`, `score.py`, and
> `tracker_views/settings.py` in full before starting. This task **explicitly
> concerns** `profiles.py` and `scorer.py` (both listed as stable core in the
> constitution) — the change is scoped to the geography fields and the Tier-0
> geography rules only. Do not touch `models.py`, `storage.py`, `llm.py`,
> `main.py`, `scrape.py`, or any scraper.

---

## Context (investigation-first)

Geography is currently expressed through four independent mechanisms, enforced at
four different points, over three different data axes — and only one of them is
work-mode-aware. The result is confusing to configure and unreliable in scoring.

| Mechanism | Axis checked | Enforcement point | Work-mode aware? |
|---|---|---|---|
| `pre_filter.exclude_location_contains` | raw `location` string | SQL, pre-extraction (`get_jobs_for_scoring`) | No |
| `allowed_countries` | `company_country` | Tier-0 (`scorer.py`, score 2) | No — one list for every mode |
| `banned_countries` | `company_country` | Tier-0 (score 1) | No (global, by design) |
| `hybrid_ok_countries` | `company_country` | Tier-0 (score 2) | **Yes — hybrid only** |
| scoring_context hard caps | LLM judgment | Tier-1 (LLM) | Partially, as prose |
| `allowed_geo_zones` | `geo_zone` | Digest build (`score.py`), post-LLM | No |

The user's real intent is different acceptable geography **per work mode**:

- **on-site** — only where they physically live and can commute: Switzerland.
- **hybrid** — a short reachable list (Switzerland, and optionally border-adjacent
  countries the user is willing to commute to).
- **remote** — a wide list bounded only by timezone (EU + Türkiye).

Today that intent can only be approximated with one mode-blind country allowlist,
one hybrid patch, a geo-zone list on a *different* axis at a *different* layer, and
prose caps the LLM is asked to honor. The concrete failure: an **on-site role
outside Switzerland** (e.g. Warsaw) passes every deterministic gate — Poland is in
`allowed_countries`, on-site is in `allowed_work_modes`, and `hybrid_ok_countries`
does not fire because the role is not hybrid — so nothing stops it except the LLM
honoring the prose cap "on-site outside Switzerland: MAX 3." Geography correctness
for on-site and hybrid rests entirely on LLM prose, which is exactly the fragility
observed in user testing. This violates the spirit of **Constitution Principle IV**
(control-flow-relevant decisions must be deterministic, not LLM-derived).

### Two vestigial facts confirmed during investigation (do not act on, just be aware)

- `profile.remote_or_hybrid = True` is never read by the current pipeline (scrape
  uses the separate `scrape_remote_or_hybrid = False`; score never references it).
  On-site-CH jobs therefore already survive to scoring. Leave the field alone.
- `JobFilterEngine`'s geo filter is off at scrape time (no `allowed_geo_zones` is
  passed into the scrape `JobFilter`), consistent with Principle I. Leave alone.

---

## Goal

Introduce a single, cohesive, work-mode-keyed geography structure on the profile,
enforce it **deterministically at Tier-0**, and retire the scattered geography
enforcement (mode-blind country allowlist, the hybrid patch, the post-LLM geo-zone
filter, and the LLM prose geography caps). After this change, the LLM ranks jobs
**within an already geography-correct candidate set** and no longer decides
geography.

---

## Target data model — nested `work_mode_geography`

A single nested dict on `SearchProfile`:

```python
work_mode_geography: dict = field(default_factory=dict)
# Shape:
# {
#   "on-site": {"countries": ["Switzerland"]},
#   "hybrid":  {"countries": ["Switzerland"]},
#   "remote":  {"countries": [<wide EU+TR list>],
#               "geo_zones": ["europe", "global_remote", "unknown"]},
# }
```

Semantics (these are the rules Claude Code must implement — read them carefully):

- **`countries` per mode** is an allowlist on `company_country`.
  - Empty list (`[]`) → **no country restriction for that mode** (any country passes).
  - Non-empty → `company_country` must be in the list.
- **`company_country == "unknown"` always passes the country check** for on-site and
  hybrid (never reject on missing data; the LLM + score threshold handle it). This
  preserves the existing "unknown always passes" convention from `allowed_countries`.
- **`geo_zones` is only meaningful for `remote`.** It is the fallback used *when a
  remote role's `company_country` is `"unknown"`*: if `geo_zones` is non-empty and
  the job's `geo_zone` is known and not in the list → reject. If `geo_zones` is empty
  or the job's `geo_zone` is unknown → pass (don't reject on missing data).
- **`work_mode == "unknown"`** → use the **union** of the three modes' `countries`
  (permissive: don't reject when we don't even know the mode), and the union of
  `geo_zones` (only `remote` contributes any) as the remote-style fallback.
- **`banned_countries` remains a global hard block** across all modes, checked
  *before* the per-mode geography rule so an explicitly banned country keeps its
  score-1 disposition rather than the score-2 per-mode disposition.

`allowed_work_modes` is unchanged and orthogonal ("do I consider this mode at all").

---

## Files to modify

### 1. `profiles.py`

**(a)** Add the field to the `SearchProfile` dataclass:

```python
work_mode_geography: dict = field(default_factory=dict)
```

Place it near the other geography fields (`allowed_countries`, `banned_countries`,
`hybrid_ok_countries`). Keep `allowed_countries`, `hybrid_ok_countries`, and
`allowed_geo_zones` on the dataclass **as deprecated/inert fields** — they are no
longer used for enforcement but are retained so old DB `criteria` rows round-trip
without loss and nothing else breaks. Add a one-line `# deprecated: superseded by
work_mode_geography (spec 016); retained for back-compat round-trip` comment above
each.

**(b)** `to_criteria_dict()` — add `"work_mode_geography": self.work_mode_geography`
to the returned dict. Leave the existing keys in place.

**(c)** `from_criteria()` — read the new key, and **synthesize it from legacy fields
when absent** so un-migrated DB rows keep working:

```python
wmg = criteria.get("work_mode_geography")
if not wmg:
    legacy_allowed = criteria.get("allowed_countries") or []
    legacy_hybrid  = criteria.get("hybrid_ok_countries") or []
    legacy_zones   = criteria.get("allowed_geo_zones") or []
    onsite = legacy_hybrid or (["Switzerland"] if legacy_hybrid == [] and False else [])
    # on-site: historically CH-only intent; fall back to hybrid list, else Switzerland
    onsite_countries = legacy_hybrid or ["Switzerland"]
    wmg = {
        "on-site": {"countries": onsite_countries},
        "hybrid":  {"countries": legacy_hybrid or ["Switzerland"]},
        "remote":  {"countries": legacy_allowed, "geo_zones": legacy_zones},
    }
```

(Simplify the dead `onsite`/`False` scaffold above into the clean `onsite_countries`
assignment — it is shown only to make the intent explicit. Final code should read:
on-site ← `legacy_hybrid or ["Switzerland"]`, hybrid ← `legacy_hybrid or
["Switzerland"]`, remote.countries ← `legacy_allowed`, remote.geo_zones ←
`legacy_zones`.)

Pass `work_mode_geography=wmg` into the constructed `SearchProfile`.

**(d)** Seed `UNIFIED_JC` with an explicit `work_mode_geography` derived losslessly
from its current values (this is the source-of-truth seed for fresh installs):

```python
work_mode_geography={
    "on-site": {"countries": ["Switzerland"]},
    "hybrid":  {"countries": ["Switzerland"]},   # user may widen in Settings (e.g. add "France")
    "remote":  {
        "countries": [
            "Switzerland", "France", "Spain", "Portugal", "Italy", "Netherlands",
            "Germany", "Ireland", "United Kingdom", "Belgium", "Austria",
            "Sweden", "Denmark", "Finland", "Norway", "Estonia", "Czech Republic",
            "Poland", "Romania", "Greece", "Luxembourg", "Türkiye", "Turkey",
        ],
        "geo_zones": ["europe", "global_remote", "unknown"],
    },
},
```

Keep `banned_countries` on `UNIFIED_JC` exactly as-is. The existing
`allowed_countries` / `hybrid_ok_countries` / `allowed_geo_zones` values on
`UNIFIED_JC` may be left in place (inert) to minimise diff.

**(e)** Update the module docstring's bullet list to mention that geography is now
defined per work mode via `work_mode_geography`.

### 2. `scorer.py` — `evaluate_for_profile()` Tier-0

Rewrite the Tier-0 block to the following order. This **removes** old rule 3
(`allowed_countries`) and old rule 6 (`hybrid_ok_countries`), **promotes**
`banned_countries` above the geography rule, and **adds** one per-mode geography
rule. Keep rules 0/1/2 (denylist / language / sector) and the work-mode-allowed
rule unchanged in behaviour.

Target order:

1. `denylisted_companies` → score 1 (unchanged)
2. `excluded_languages` → score 1 (unchanged)
3. `excluded_sectors` → score 1 (unchanged)
4. `banned_countries` (any mode, global) → score 1  *(moved up)*
5. `work_mode not in allowed_work_modes` → score 1 (unchanged)
6. **per-mode geography** → score 2  *(new; replaces old 3 + 6)*

Per-mode geography rule (rule 6) — implement a small helper inside the function or
module-private:

```python
def _geography_for_mode(profile, mode):
    """Return (countries, geo_zones) acceptable for this work_mode.
    Empty countries = no restriction. geo_zones only used as remote fallback."""
    wmg = getattr(profile, "work_mode_geography", None) or {}
    if mode in ("on-site", "hybrid", "remote"):
        spec = wmg.get(mode, {}) or {}
        return spec.get("countries", []) or [], spec.get("geo_zones", []) or []
    # unknown work_mode → union across all modes (permissive)
    countries, zones = [], []
    for m in ("on-site", "hybrid", "remote"):
        spec = wmg.get(m, {}) or {}
        countries += spec.get("countries", []) or []
        zones += spec.get("geo_zones", []) or []
    # dedupe, preserve order
    return list(dict.fromkeys(countries)), list(dict.fromkeys(zones))
```

Rule 6 body:

```python
allowed_geo, geo_zones = _geography_for_mode(profile, work_mode)
if allowed_geo:
    if company_country != "unknown":
        if company_country not in allowed_geo:
            return _evaluation_result(
                2, f"filtered: {work_mode} not available in {company_country}",
                "tier_0", job, profile, comp_flag=comp_flag)
    else:
        # company_country unknown: for remote / unknown modes, fall back to geo_zone
        if work_mode in ("remote", "unknown") and geo_zones:
            jz = (job.geo_zone or "unknown").strip().lower()
            if jz != "unknown" and jz not in geo_zones:
                return _evaluation_result(
                    2, f"filtered: remote geo_zone ({jz}) not allowed",
                    "tier_0", job, profile, comp_flag=comp_flag)
        # on-site/hybrid with unknown country, or no geo_zone signal → pass
```

Notes for the implementer:
- `work_mode`, `company_country`, and `comp_flag` are already computed earlier in
  `evaluate_for_profile`; reuse them. Do not recompute.
- Preserve the existing `_evaluation_result(...)` call signature (including
  `comp_flag`) so the comp flag continues to propagate.
- Delete the old rule-3 (`allowed_countries`) and rule-6 (`hybrid_ok_countries`)
  blocks entirely. Move the `banned_countries` block up to position 4.

### 3. `score.py` — digest builder

In the post-scoring digest loop, **remove** the two geography re-filters that are
now enforced deterministically at Tier-0 and whose backing fields are deprecated:

- the `profile.allowed_geo_zones` block (the `excl_geo` branch)
- the `profile.allowed_countries` block (the `excl_country` branch)

**Keep** the `allowed_work_modes`, `excluded_sectors`, and `excluded_languages`
blocks (they remain valid and cheap). Update the `excl_*` counters and the printed
stats line so they no longer reference the removed `excl_geo` / `excl_country`
counters (drop them from the summation and the print). Do not change any other part
of `score.py`.

### 4. `tracker_views/settings.py` — `_render_profile_editor()`

Replace the geography widgets with a per-work-mode section. Concretely:

- **Remove** the `allowed_geo_zones` multiselect from the three-column row (leave
  `allowed_work_modes` and `company_sizes` in that row).
- Inside the `🌍 Countries & filters` expander, **remove** the `allowed_countries`
  and `hybrid_ok_countries` text areas and **add** a "Geography by work mode"
  block with four inputs plus the retained banned-countries and denylist inputs:

```python
st.markdown("**Geography by work mode**")
wmg = profile.work_mode_geography or {}
onsite_countries = st.text_area(
    "On-site countries (one per line — where you can commute)",
    value="\n".join((wmg.get("on-site", {}) or {}).get("countries", [])),
    height=80,
)
hybrid_countries = st.text_area(
    "Hybrid countries (one per line)",
    value="\n".join((wmg.get("hybrid", {}) or {}).get("countries", [])),
    height=80,
)
remote_countries = st.text_area(
    "Remote countries (one per line — timezone-bounded)",
    value="\n".join((wmg.get("remote", {}) or {}).get("countries", [])),
    height=150,
)
GEO_ZONES = ["europe", "global_remote", "us_only", "apac", "latam", "unknown"]
remote_geo_zones = st.multiselect(
    "Remote geo-zone fallback (used when a remote role's country is unknown)",
    GEO_ZONES,
    default=(wmg.get("remote", {}) or {}).get("geo_zones", []),
)
```

In the save block (`st.form_submit_button`), build and assign the nested dict, and
keep `banned_countries` as today:

```python
profile.work_mode_geography = {
    "on-site": {"countries": _textarea_to_list(onsite_countries)},
    "hybrid":  {"countries": _textarea_to_list(hybrid_countries)},
    "remote":  {
        "countries": _textarea_to_list(remote_countries),
        "geo_zones": remote_geo_zones,
    },
}
```

Remove the now-dead `profile.allowed_countries = ...`,
`profile.hybrid_ok_countries = ...`, and `profile.allowed_geo_zones = ...`
assignments from the save block. Leave every other field in the save block intact.
`_textarea_to_list` already exists in this module — reuse it.

### 5. `score.py` — mock regression cases

Add two cases to `MOCK_JOBS` and matching `expectations` entries in `_run_mock()`
that exercise the *new* deterministic behaviour, then make the final summary print
count-based instead of the hardcoded "All 6 cases".

Add to `MOCK_JOBS`:

```python
{
    "title": "Senior Product Manager",
    "company": "WarsawSoft",
    "location": "Warsaw, Poland (On-site)",
    "base_location": "Warsaw, Poland",
    "description": (
        "WarsawSoft is a Polish B2B SaaS company. On-site role in our Warsaw "
        "office, 5 days a week. Series A, 60 employees."
    ),
},
{
    "title": "Senior Product Manager — Payments",
    "company": "ParisPay",
    "location": "Paris, France (Hybrid)",
    "base_location": "Paris, France",
    "description": (
        "ParisPay is a French fintech. Hybrid role, 3 days in our Paris office. "
        "Series B payments platform, 180 employees."
    ),
},
```

Add matching `expectations` entries (same order):

```python
(1, 2, "WarsawSoft — on-site outside CH → Tier-0 per-mode geography reject (score 2)"),
(1, 2, "ParisPay — hybrid outside CH → Tier-0 per-mode geography reject (score 2)"),
```

Change the final summary from the hardcoded `"✅ All 6 cases in expected bands."`
to use `len(MOCK_JOBS)` (e.g. `f"✅ All {len(MOCK_JOBS)} cases in expected bands."`).

---

## scoring_context prose (payload of this code change, Principle II)

The hard **geography** caps in `UNIFIED_JC.scoring_context` become dead once
geography is enforced at Tier-0 (those jobs never reach the LLM). Update the seed
`scoring_context` string in `profiles.py` to **remove the geography hard caps** and
**keep the soft-ranking geography prose**:

- **Remove** from the `# HARD EXCLUSIONS` block: `ON-SITE outside Switzerland: MAX
  SCORE = 3` and `US-ONLY remote: MAX SCORE = 3` (both are now deterministic).
- **Remove** the `# Geography and work mode` sub-bullets that restate hard
  exclusions ("EXCLUDE: On-site or hybrid outside Switzerland (Tier-0 enforced)…").
- **Keep** everything that expresses *soft ranking*: the Swiss-employer certainty
  discount, hybrid 2–3 days ranking near remote vs rigid 4+ days lower, "on-site in
  Switzerland is OK — score on domain not work mode", and the IDEAL/ALSO STRONG
  ordering. These still shape ranking *within* the geography-correct set.
- Keep the language and large-corporate caps untouched — this spec is geography only.

> Live-DB note (not a code task): the active profile's `scoring_context` lives in
> the DB, so editing the seed does not rewrite the live row. Because the removed
> caps are now dead (Tier-0 dominates), syncing the live `scoring_context` via the
> Settings editor is **optional and non-blocking** post-deploy. Flag this to the
> user; do not attempt to mutate the Live DB from this spec.

---

## Non-objectives

- No city / canton-level granularity — country-level only (on-site-CH is served by
  commuting within Switzerland). City granularity is a future extension the nested
  shape already accommodates (`{"cities": [...]}`).
- No change to `models.py`, `storage.py`, the DB schema, or any migration script.
  `work_mode_geography` rides inside the existing `search_profiles.criteria` JSON.
- No change to `pre_filter.exclude_location_contains` — it stays as a cheap
  pre-extraction cost-saver (it drops obvious US-string jobs before the LLM spend).
  It is now logically subsumed by the remote geography rule but harmless to keep.
- No removal of the deprecated `allowed_countries` / `hybrid_ok_countries` /
  `allowed_geo_zones` fields from the dataclass — they stay inert for round-trip.
- No re-scoring logic in this spec. A `python score.py --rescore --profile
  unified_jc` after deploy is the operator step to refresh stale rows.
- No change to `remote_or_hybrid` (confirmed unused).

---

## Constitution alignment

- **IV (deterministic control flow)**: strengthened — geography moves from LLM prose
  to deterministic Tier-0 rules over already-extracted `work_mode` / `company_country`.
- **II (two paths)**: this is a code change; the `scoring_context` prose edit is
  carried as its payload, not a separate prose-path change.
- **V (surgical)**: additive field + one Tier-0 rule rewrite + one Settings section;
  deprecated fields retained; no new dependency; blast radius contained to four files.
- **VI (empirical validation)**: two new mock regression cases assert the new
  deterministic rejects; existing six cases must remain in-band.

---

## Acceptance criteria

- [ ] `SearchProfile` has `work_mode_geography`; `to_criteria_dict` / `from_criteria`
      round-trip it, and `from_criteria` synthesizes it losslessly from legacy fields
      when the key is absent (verify by loading a criteria dict with only
      `allowed_countries` + `hybrid_ok_countries` + `allowed_geo_zones`).
- [ ] Tier-0 has a single per-mode geography rule; old `allowed_countries` and
      `hybrid_ok_countries` Tier-0 blocks are gone; `banned_countries` runs before it.
- [ ] `python score.py --mock --profile unified_jc` passes all cases:
      FELFEL 5–7, Consensys 6–8, SIX 1–3, SwissNeo 8–9, TokenBridge 6–8,
      Istanbul Fintech 4–5, **WarsawSoft 1–2 (on-site outside CH, Tier-0)**,
      **ParisPay 1–2 (hybrid outside CH, Tier-0)**.
- [ ] Istanbul Fintech (remote, Türkiye) is **not** Tier-0 filtered — remote list
      includes Türkiye/Turkey.
- [ ] A remote role with `company_country == "unknown"` and `geo_zone == "europe"`
      passes the geography rule; the same with `geo_zone == "us_only"` is rejected.
- [ ] Settings shows On-site / Hybrid / Remote country inputs + a Remote geo-zone
      fallback multiselect; saving persists `work_mode_geography` to the DB
      (verify: `sqlite3 data/jobs.db "SELECT criteria FROM search_profiles"` contains
      the nested key).
- [ ] The digest builder no longer references `allowed_geo_zones` or
      `allowed_countries`; stats line prints without the dropped counters.
- [ ] `scoring_context` seed no longer contains the on-site-outside-CH or US-only
      remote hard caps; soft-ranking geography prose is retained.
- [ ] No change to `models.py`, `storage.py`, schema, scrapers, or `main.py`.

---

## Suggested phase order for `/speckit-tasks`

1. `profiles.py` — field + seed + `to_criteria_dict` / `from_criteria` back-compat.
2. `scorer.py` — Tier-0 rewrite + `_geography_for_mode` helper.
3. `score.py` — digest builder cleanup + mock cases.
4. `tracker_views/settings.py` — per-mode geography UI + save block.
5. `scoring_context` seed prose edit.
6. Validate: `python score.py --mock --profile unified_jc` (all cases in band),
   then a Settings save + `sqlite3` round-trip check.
