# Spec 017 — Profile field consolidation (single DB source of truth)

> Spec for Claude Code. Read `profiles.py`, `scorer.py`, `score.py`,
> `title_gate.py`, `filters.py`, `scrape.py`, `scrapers/greenhouse.py`,
> `scrapers/boards/linkedin.py`, `scrapers/boards/indeed.py`, and
> `tracker_views/settings.py` **in full** before starting.
>
> This task **explicitly concerns** several files listed as *stable core* in the
> constitution — `profiles.py`, `scorer.py`, `scrape.py`, `title_gate.py`, and the
> files under `scrapers/`. The change is scoped precisely to the profile field
> model, the code paths that read profile search inputs, and the profile-editor
> form. Do **not** touch `models.py`, `storage.py`, `llm.py`, `main.py`, the DB
> schema, any migration script, `tracker_views/onboarding.py`, or
> `tracker_views/shared.py`. Do **not** touch the Settings *section layout*
> (`_render_scraper_toggles`, `_render_company_monitoring`) — that is spec 018.

---

## Context (investigation-first)

Builds directly on spec 016 (per-work-mode geography, already implemented). The
profile is already DB-backed at runtime: `load_active_profile(db)` reads the
`search_profiles` row and reconstructs via `from_criteria()`; the `UNIFIED_JC`
constant is only a first-run seed. So "everything comes from the DB" is already
true **for the profile object itself**. The problem is the set of **parallel
title lists** and **scattered search inputs** — several of them hardcoded in
modules, several duplicated as editable form fields — that all encode the same
user intent at different pipeline stages. This is the root of the confusing
Settings UX.

### The five title lists (the core duplication)

| List | Location | Consumed by | Editable today? |
|---|---|---|---|
| `search_query_titles` | profile | jobspy queries (`linkedin.py`, `indeed.py`) | yes — "Search query titles" |
| `scrape_titles` | profile | `JobFilter.titles` → `JobFilterEngine.apply` (scrape) | yes — "Scrape titles" |
| `pre_filter.title_contains` | profile | SQL in `score.py` → `get_jobs_for_scoring` | yes — "pre_filter: title_contains" |
| `_PM_TITLES` | `title_gate.py` | `is_product_management_title()` (scrape gate + monitored gate in `score.py`) | **no — hardcoded** |
| `PM_TITLE_KEYWORDS` | `scrapers/greenhouse.py` | `_is_pm_title()` inside Greenhouse fetch | **no — hardcoded** |

They are already **inconsistent**: `scrape_titles` contains `project manager` and
`product engineer`, which `_PM_TITLES` rejects. A "Project Manager" posting passes
the scrape net but is dropped by the per-job title gate.

**Semantic nuance the implementer must respect.** Search *queries* and the
recognition *gate* are not interchangeable if the list contains broad fragments.
`_PM_TITLES` is curated with full role phrases (`senior product manager`,
`product owner`, …) precisely so that substring matching has no false positives.
`search_query_titles` contains broad fragments like `senior product`, which as a
gate term would wrongly pass "Senior Product **Designer**". The consolidated list
MUST be **gate-quality full role phrases** — each is still a valid (if slightly
narrower) jobspy query, and jobspy token-matches so `product manager` already
catches "senior product manager" postings. Do not seed broad fragments.

### The scattered search inputs

- `search_locations` (profile) — where jobspy searches. `linkedin.py` uses
  `_p.search_query_titles or SEARCH_TERMS_LINKEDIN` and `_p.search_locations or
  LINKEDIN_LOCATIONS`; `indeed.py` mirrors this with a `_to_indeed_slug()` mapping.
  Distinct axis from `work_mode_geography` (which is *acceptance*, not *search*).
- `greenhouse_boards` (profile) — `GreenhouseScraper.fetch` uses
  `profile.greenhouse_boards or get_greenhouse_boards()`, where the latter merges
  the hardcoded `GREENHOUSE_BOARDS_SEED` with DB watching companies.
- Hardcoded scrape-time geography in `greenhouse.py`: `if base_location ==
  "United States": continue`. This is a **relevance/geo filter at scrape time**,
  which violates **Constitution Principle I** (fit judgment belongs to the scorer
  only; the company-keyed exception permits a coarse *family* filter, not geo).

### Dead / vestigial code confirmed during investigation

- `filters.py` `JobFilterEngine.apply` has `company_sizes`, `contract_types`, and
  `allowed_geo_zones` branches that are **never exercised**: `scrape.py` builds
  `JobFilter` only with `titles`, `exclude`, `remote_or_hybrid`. These branches are
  dead. The 30-day date filter *is* live and stays.
- `contract_type` is extracted by the LLM and displayed, but **no profile field
  filters on it**. Adding a filter is net-new capability, not a consolidation.
- `remote_or_hybrid` (profile) is unused (confirmed in spec 016). `location_keywords`
  is empty and merged into `pre_filter.location_contains` at `score.py`. Both stay
  inert (non-objectives below).

---

## Goal

Collapse the scattered/duplicated search inputs into a **single set of canonical,
DB-stored, user-editable profile fields**, and make every pipeline stage
(jobspy queries, scrape title gate, Greenhouse title filter, scrape net, scoring
pre-filter, Tier-0) *derive* from those fields. After this change there is exactly
**one** include-title list and **one** exclude-title list in the whole system, no
module hardcodes a title list as its primary source, and Greenhouse no longer
filters geography at scrape time.

Six user decisions drive this spec (all confirmed):

1. **One `job_titles` list**, DB-stored, no hardcoding. `title_gate.py` and
   `greenhouse.py` read it. (User will prune specific terms like `project manager`
   in the UI — they are just data.)
2. **`search_locations` derived** from the union of `work_mode_geography` countries.
3. **`banned_countries` removed** — the per-mode allowlist is the single geo gate.
4. **Sectors soft** — `excluded_sectors` defaults empty; sector preference lives in
   `scoring_context`. The Tier-0/digest sector rules stay but no-op when empty.
5. **Languages positive** — replace `excluded_languages` (denylist) with
   `languages_spoken` (allowlist), reject a role whose *required* language is known
   and not spoken.
6. **`contract_type` filter** added on the extraction vocabulary
   `{permanent, freelance, contract, internship, unknown}`.

---

## Target data model changes (`profiles.py`)

### New canonical fields on `SearchProfile`

```python
job_titles: list[str] = field(default_factory=list)           # single include list — gate + jobspy query + scrape net + pre_filter
title_exclude: list[str] = field(default_factory=list)         # single exclude list — scrape exclude + pre_filter exclude
allowed_contract_types: list[str] = field(default_factory=list)# empty = no restriction; else allowlist over extraction vocab
languages_spoken: list[str] = field(default_factory=list)      # positive allowlist; empty = no language restriction
```

### Fields to DEPRECATE (keep inert on the dataclass for round-trip, per the 016 idiom)

Add a one-line `# deprecated: superseded by <field> (spec 017); retained for
round-trip` comment above each:

- `scrape_titles`, `search_query_titles` → superseded by `job_titles`
- `scrape_exclude` → superseded by `title_exclude`
- `excluded_languages` → superseded by `languages_spoken`
- `banned_countries` → removed as an enforcement mechanism (per-mode allowlist)
- `search_locations` → now a *derived default* (kept as optional override; see below)

Do **not** delete any field from the dataclass. Deletion breaks round-trip of old
DB `criteria` rows. They stay, inert.

### `search_locations` becomes a derived default with optional override

Add a method:

```python
def effective_search_locations(self) -> list[str]:
    """Where jobspy should search: explicit override if set, else the union of
    all work_mode_geography countries (dedup, order-preserving)."""
    if self.search_locations:
        return list(self.search_locations)
    wmg = self.work_mode_geography or {}
    out: list[str] = []
    for mode in ("on-site", "hybrid", "remote"):
        out += (wmg.get(mode, {}) or {}).get("countries", []) or []
    return list(dict.fromkeys(out))
```

`search_locations` stays as an *optional override* field (empty by default in the
seed → derivation active). This matches the existing `X or CONSTANT` fallback idiom.

> **Run-time note (flag to user, not a code task):** the derived union of the
> `remote` allowlist is ~24 countries vs the previous hand-picked 14, so a broad
> scrape over jobspy will issue more queries and take longer. The escape hatch is
> the `search_locations` override (populate it to narrow the search surface without
> narrowing acceptance). This is exposed in Settings (spec 018 section reorg); for
> 017 the override field remains editable in its current place.

### `to_criteria_dict()` / `from_criteria()`

- `to_criteria_dict()` — add keys `job_titles`, `title_exclude`,
  `allowed_contract_types`, `languages_spoken`. Leave all existing keys.
- `from_criteria()` — read the new keys, and **synthesize from legacy fields when
  absent** so un-migrated rows keep working:

```python
job_titles = criteria.get("job_titles")
if not job_titles:
    legacy = (criteria.get("scrape_titles") or []) + (criteria.get("search_query_titles") or [])
    job_titles = list(dict.fromkeys(legacy))
title_exclude = criteria.get("title_exclude") or criteria.get("scrape_exclude") or []
languages_spoken = criteria.get("languages_spoken")
if languages_spoken is None:
    languages_spoken = ["french", "english"]   # positive default; NOT synthesized from excluded_languages
allowed_contract_types = criteria.get("allowed_contract_types")
if allowed_contract_types is None:
    allowed_contract_types = ["permanent", "freelance", "contract", "unknown"]
```

Note: `languages_spoken` is **not** algorithmically derived from `excluded_languages`
(inverting a denylist over the full language space is fragile) — default to
`["french", "english"]`, which matches the seed intent. Pass all four into the
constructed `SearchProfile`.

### Seed `UNIFIED_JC`

- `job_titles=[...]` — curated gate-quality phrases (final list is user-editable):
  ```python
  job_titles=[
      "product manager", "senior product manager", "staff product manager",
      "principal product manager", "lead product manager", "group product manager",
      "director of product", "head of product", "vp product",
      "chief product officer", "product owner", "technical product owner",
      "product lead",
  ],
  ```
  (Deliberately omits `project manager`, `product engineer`, `senior product`,
  `product director`, `cpo` — broad/false-positive-prone. The user can add any back
  in the UI.)
- `title_exclude=["junior", "intern", "stage", "apprentice"]`
- `allowed_contract_types=["permanent", "freelance", "contract", "unknown"]`
- `languages_spoken=["french", "english"]`
- `excluded_sectors=[]`  *(was a 7-sector hard list; now soft via scoring_context — decision 4)*
- `search_locations=[]`  *(empty → derive from work_mode_geography — decision 2)*
- `banned_countries=[]`  *(rule removed — decision 3; value now inert)*
- Leave `scrape_titles`, `search_query_titles`, `scrape_exclude`, `excluded_languages`
  values in place (inert) to minimise diff, or clear them — implementer's choice, but
  they MUST no longer be read by any code path after this spec.

### Module docstring

Update the top-of-file bullet list to describe the canonical fields (`job_titles`,
`title_exclude`, `languages_spoken`, `allowed_contract_types`) and note that search
inputs derive from these plus `work_mode_geography`.

---

## Files to modify

### 1. `title_gate.py`

Change the signature to accept an optional titles list, falling back to the
built-in when none is passed (keeps the module-load regression assert working and
preserves callers that pass nothing):

```python
def is_product_management_title(title: str, titles: list[str] | None = None) -> bool:
    if not title:
        return False
    keywords = titles if titles else _PM_TITLES
    t = title.lower().strip()
    return any(kw.lower() in t for kw in keywords)
```

Keep `_PM_TITLES` as the fallback default and keep the module-load
`assert is_product_management_title("Senior Tech Product Owner")` (FELFEL guard) —
it exercises the fallback and MUST still pass.

### 2. `scrapers/greenhouse.py`

- Remove `PM_TITLE_KEYWORDS` and `_is_pm_title`. Import
  `is_product_management_title` from `title_gate`.
- In `fetch()`, the profile is already loaded for `greenhouse_boards`; capture its
  `job_titles` and use `is_product_management_title(title, profile_titles)` in place
  of `_is_pm_title(...)`. In the monitoring path (`self._targets is not None`) there
  is no active-profile board lookup — load the active profile once at the top of
  `fetch()` for `job_titles` regardless of path (single `load_active_profile` call).
- **Remove** the hardcoded `if base_location == "United States": continue` line.
  US-located Greenhouse jobs now flow to the DB and are dropped cheaply by
  `pre_filter.exclude_location_contains` (kept) *before* extraction, and by the
  per-mode geography Tier-0 rule otherwise. (Constitution Principle I compliance.)

### 3. `scrapers/boards/linkedin.py` and `scrapers/boards/indeed.py`

- Replace `terms = _p.search_query_titles or SEARCH_TERMS_*` with
  `terms = _p.job_titles or SEARCH_TERMS_*`.
- Replace `locations = _p.search_locations or LINKEDIN_LOCATIONS`
  (and the Indeed equivalent) with `_p.effective_search_locations() or <CONSTANT>`.
  For Indeed, keep the `_to_indeed_slug()` mapping applied to the result.
- Keep the module `SEARCH_TERMS_*` / `*_LOCATIONS` / `INDEED_COUNTRIES` constants
  **only as last-resort fallbacks** (in case a profile has an empty `job_titles`
  and an empty geography). They are no longer the primary source.

### 4. `scrape.py` — `_run_broad_scrape`

- Build `JobFilter` from the canonical fields:
  ```python
  job_filter = JobFilter(
      titles=profile.job_titles,
      exclude=profile.title_exclude,
      remote_or_hybrid=profile.scrape_remote_or_hybrid,  # unchanged, still inert/False
  )
  ```
- The per-job gate call becomes `is_product_management_title(job.title,
  profile.job_titles)`.

### 5. `score.py`

- **Monitored-company gate** (Phase 2): change
  `is_product_management_title(title)` to `is_product_management_title(title,
  profile.job_titles)`.
- **Pre-filter derivation:** when building `effective_pre_filter`, inject the
  canonical title lists so the SQL pre-filter derives from `job_titles` /
  `title_exclude` rather than a separately-stored `pre_filter` sub-key:
  ```python
  effective_pre_filter = dict(profile.pre_filter) if profile.pre_filter else {}
  if profile.job_titles:
      effective_pre_filter["title_contains"] = list(profile.job_titles)
  if profile.title_exclude:
      effective_pre_filter["exclude_title_contains"] = list(profile.title_exclude)
  # (existing location_keywords merge into location_contains stays as-is)
  ```
  This makes `pre_filter.title_contains` / `exclude_title_contains` *derived at run
  time* and no longer authored by hand. Leave `exclude_location_contains` alone.
- **Digest builder:** the `excluded_sectors` and `excluded_languages` post-scoring
  filters stay, but update the language filter to the positive form (see rule change
  below) — reject when `language_required` is known and **not** in
  `profile.languages_spoken`. Add a `contract_type` digest filter mirroring the new
  Tier-0 rule. Keep the `allowed_work_modes` filter.

### 6. `scorer.py` — `evaluate_for_profile()` Tier-0

Rewrite the deterministic block to this order (denylist / language / sector /
work-mode / geography unchanged in spirit from 016; **language rule flips to
positive**, **banned-country rule removed**, **contract-type rule added**):

1. `denylisted_companies` → score 1 (unchanged)
2. **language (positive):** reject when the role's required language is known and
   not spoken:
   ```python
   spoken = profile.languages_spoken or []
   if spoken and language_required not in ("unknown", "multiple") and language_required not in spoken:
       return _evaluation_result(1, f"filtered: language ({language_required}) not spoken", "tier_0", job, profile)
   ```
   Empty `languages_spoken` → no restriction. `unknown` and `multiple` always pass.
3. `excluded_sectors` → score 1 (unchanged; no-ops when the list is empty)
4. **contract type (new):**
   ```python
   allowed_ct = profile.allowed_contract_types or []
   ct = (job.contract_type or "unknown").strip().lower()
   if allowed_ct and ct not in allowed_ct:
       return _evaluation_result(1, f"filtered: contract_type ({ct})", "tier_0", job, profile, comp_flag=comp_flag)
   ```
   Empty → no restriction. `unknown` passes only if `unknown` is in the allowlist
   (the seed includes it).
5. `work_mode not in allowed_work_modes` → score 1 (unchanged)
6. **per-mode geography** (the 016 rule, unchanged) → score 2

**Remove** the old `banned_countries` Tier-0 block entirely (decision 3). Removal is
safe: with exhaustive per-mode allowlists, a US remote role (US not in
`remote.countries`) is already rejected by rule 6 at score 2, and any non-remote US
role by rule 6 as well. The only behavioural change is score 2 instead of score 1
for those rejects — both are below threshold. Keep `_geography_for_mode` as-is.

Do not change `SYSTEM_PROMPT`, `EXTRACTION_PROMPT`, or `EVALUATION_PROMPT` — the
extraction vocabularies (`_VALID_SECTORS`, `_VALID_LANGUAGES`, contract types) are
already the vocabulary the new rules filter over.

### 7. `filters.py` — `JobFilterEngine.apply`

Remove the three dead branches: `company_sizes`, `contract_types`, and
`allowed_geo_zones` (none is ever populated from `scrape.py`). **Keep** the 30-day
date filter, the `exclude` word filter, the `titles` requirement, the `locations`
OR-match, `remote_only`, and `remote_or_hybrid`. Removing the geo branch also lets
you drop the `excluded_geo` counter if it becomes unused — verify the return tuple
`(results, excluded_date, excluded_geo)` is preserved (callers unpack three values);
if you drop the counter, return `0` in its slot rather than changing the signature.

### 8. `tracker_views/settings.py` — `_render_profile_editor()` ONLY

Reorganise the profile editor to the canonical field set. **Do not** touch
`_render_scraper_toggles` or `_render_company_monitoring` (spec 018).

Replace the current title/location/pre_filter widgets:

- **Remove** the "Search query titles" text area, the "Scrape titles" text area, and
  the entire `pre_filter: title_contains` / `exclude_title_contains` block inside the
  "🕸 Scrape net & advanced" expander.
- **Add**, in the main "Search" block, one `job_titles` text area ("Job titles — one
  per line; used as search queries, the PM title gate, and the scoring pre-filter")
  and one `title_exclude` text area ("Exclude titles containing — one per line").
- **Keep** the `search_locations` text area but relabel it "Scrape locations override
  (one per line — leave empty to search everywhere you accept jobs)". Its emptiness
  now activates derivation.
- **Keep** `greenhouse_boards` and `boost_keywords` in the "Scrape net & advanced"
  expander (they remain single-sourced profile fields). After removing the pre_filter
  and scrape-title widgets, the expander holds only `greenhouse_boards` +
  `boost_keywords` — rename it "🕸 Advanced scrape inputs".
- **Add** an `allowed_contract_types` multiselect (options: `permanent`, `freelance`,
  `contract`, `internship`, `unknown`) near work modes / company sizes.
- **Replace** the `excluded_languages` text area with a `languages_spoken` text area
  ("Languages you work in — one per line, e.g. french / english"). Keep it inside the
  "🌍 Countries & filters" expander or move it next to work modes — implementer's
  choice, one input only.
- **Remove** the "Banned countries" text area (decision 3).
- **Keep** `excluded_sectors` multiselect and `denylisted_companies` text area, and
  the per-mode geography inputs from 016, unchanged.

Update the `st.form_submit_button` save block accordingly:
- `profile.job_titles = _textarea_to_list(job_titles_input)`
- `profile.title_exclude = _textarea_to_list(title_exclude_input)`
- `profile.allowed_contract_types = allowed_contract_types` (multiselect value)
- `profile.languages_spoken = _textarea_to_list(languages_spoken_input)`
- `profile.search_locations = _textarea_to_list(search_locations_input)` (may be empty)
- Remove the assignments to `profile.scrape_titles`, `profile.search_query_titles`,
  `profile.scrape_exclude`, `profile.banned_countries`, `profile.excluded_languages`,
  and the `profile.pre_filter["title_contains"]` / `["exclude_title_contains"]`
  mutations. Leave `profile.pre_filter["exclude_location_contains"]` handling if the
  location expander still exposes it; otherwise drop it too. Keep every other
  assignment (work modes, company sizes, scoring_context, work_mode_geography,
  denylist, excluded_sectors, greenhouse_boards, boost_keywords) intact.

---

## scoring_context prose (payload of this code change, Principle II)

Two seed `scoring_context` edits in `profiles.py`, carried as the payload of this
code change:

- **Language:** remove the hard cap line "ROLES REQUIRING German or Spanish: MAX
  SCORE = 3" — non-spoken required languages are now Tier-0 rejects. Leave a single
  soft sentence if desired ("The candidate works in French and English"), but no
  numeric cap.
- **Sectors:** the previous hard sector exclusions are now soft. The existing
  industry-fit prose already covers "LOW FIT — non-fintech B2B SaaS, e-commerce,
  healthtech, edtech, media: score on PM fundamentals only, no industry bonus." Add
  one soft line to preserve the retired hard-exclusion intent: "AVOID (soft signal,
  not a hard filter): pharma, government, pure retail/manufacturing, energy — score
  low unless a genuine fintech/Web3 angle is present." Do **not** reintroduce a
  numeric cap. Keep the large-corporate cap (MAX 3) untouched — that is company
  type, not sector, and remains desired.

> **Live-DB note (not a code task):** the active profile's `scoring_context` and all
> the new fields live in the DB. Editing the seed does not rewrite the live row. On
> first `load_active_profile` after deploy, `from_criteria` synthesizes `job_titles`
> / `title_exclude` / `languages_spoken` / `allowed_contract_types` from legacy keys
> or defaults, so the live profile keeps working without manual migration. The
> user should then open Settings once, review the pre-filled canonical fields, set
> `excluded_sectors` to empty and `banned_countries` UI is gone, and Save to persist
> the consolidated shape. Flag this; do not mutate the Live DB from this spec.

---

## Non-objectives

- **Settings section layout, broad-vs-ATS toggle split, and the monitoring
  master-switch cascade are spec 018.** 017 only reorganises `_render_profile_editor`
  and leaves `_render_scraper_toggles` / `_render_company_monitoring` untouched.
- No changes to `models.py`, `storage.py`, the DB schema, or migrations. All new
  fields ride inside the existing `search_profiles.criteria` JSON.
- No deletion of deprecated fields from the `SearchProfile` dataclass — they stay
  inert for round-trip (`scrape_titles`, `search_query_titles`, `scrape_exclude`,
  `excluded_languages`, `banned_countries`, `remote_or_hybrid`, `location_keywords`).
- No change to `remote_or_hybrid` or `scrape_remote_or_hybrid` behaviour (both stay
  inert/False) and no change to `location_keywords` merging in `score.py`.
- No change to the extraction prompts or the extraction vocabularies.
- No change to `onboarding.py` / `profile_generator.py` / `create_profile.py` in this
  spec. They currently emit legacy keys; `from_criteria` back-compat covers them. A
  small follow-up may wire them to emit `job_titles` directly (note for 018+).
- No re-scoring logic. `python score.py --rescore --profile unified_jc` after deploy
  is the operator step to refresh rows against the new Tier-0 rules.
- `pre_filter.exclude_location_contains` is retained as a cheap pre-extraction US
  dropper (now also the mitigation for the removed Greenhouse US filter).

---

## Constitution alignment

- **I (filet large, point de filtrage unique):** strengthened — the hardcoded
  Greenhouse US geo filter is removed; geography returns to the scorer alone. The
  retained title gate is the permitted coarse *family* filter for company-keyed
  sources, and it now reads the profile rather than a private constant.
- **IV (deterministic control flow):** language and contract-type filtering move
  from prose/absent to deterministic Tier-0 over already-extracted fields.
- **II (two paths):** this is a code change; the `scoring_context` edits are its
  payload, not a separate prose-path change.
- **V (surgical):** additive canonical fields + derivation helpers; deprecated fields
  retained inert; dead `filters.py` branches removed; no new dependency; blast radius
  bounded to the listed files; the Settings *section* redesign is explicitly deferred.
- **VI (empirical validation):** new mock regression cases assert the new
  deterministic rejects; the existing eight 016 cases must remain in-band.

---

## Mock regression cases (`score.py` `_run_mock` + `MOCK_JOBS`)

Keep all eight existing 016 cases in band. Add three cases exercising the new rules:

```python
{
    "title": "Senior Product Manager",
    "company": "USRemoteCo",
    "location": "Remote (United States)",
    "base_location": "New York, United States",
    "description": "USRemoteCo is a US fintech hiring a fully-remote Senior PM. US-based team.",
},
{
    "title": "Senior Product Manager",
    "company": "BerlinBank",
    "location": "Berlin, Germany",
    "base_location": "Berlin, Germany",
    "description": "Deutschkenntnisse erforderlich. On-site product role at a Berlin institution.",
},
{
    "title": "Product Manager Intern",
    "company": "InternCo",
    "location": "Remote (Switzerland)",
    "base_location": "Zurich, Switzerland",
    "description": "6-month product internship, Swiss startup.",
},
```

Matching `expectations` (same order appended):

```python
(1, 2, "USRemoteCo — US remote, no banned_countries rule → per-mode geography reject (score 2)"),
(1, 1, "BerlinBank — German required, not spoken → Tier-0 language reject (score 1)"),
(1, 1, "InternCo — internship not in allowed_contract_types → Tier-0 contract reject (score 1)"),
```

Ensure the final summary print already uses `len(MOCK_JOBS)` (added in 016).

---

## Acceptance criteria

- [ ] `SearchProfile` has `job_titles`, `title_exclude`, `allowed_contract_types`,
      `languages_spoken`; `to_criteria_dict` / `from_criteria` round-trip them, and
      `from_criteria` synthesizes them from legacy keys / defaults when absent
      (verify by loading a criteria dict that has only `scrape_titles` +
      `search_query_titles` + `scrape_exclude`).
- [ ] No code path reads `_PM_TITLES` or `PM_TITLE_KEYWORDS` as its primary title
      source; `title_gate._PM_TITLES` remains only as the parameter fallback and the
      FELFEL module-load assert still passes.
- [ ] `scrapers/greenhouse.py` imports `is_product_management_title`, passes
      `profile.job_titles`, and no longer contains the `base_location == "United
      States"` filter.
- [ ] `linkedin.py` / `indeed.py` derive terms from `job_titles` and locations from
      `effective_search_locations()`.
- [ ] `scrape.py` builds `JobFilter` from `job_titles` / `title_exclude` and gates
      with `is_product_management_title(title, profile.job_titles)`.
- [ ] `score.py` injects `title_contains` / `exclude_title_contains` into the
      effective pre-filter from the canonical lists; the digest language filter uses
      the positive `languages_spoken` form; a contract-type digest filter exists.
- [ ] `scorer.py` Tier-0: positive language rule, contract-type rule present; the
      `banned_countries` block is gone; per-mode geography rule unchanged.
- [ ] `filters.py` no longer has `company_sizes` / `contract_types` /
      `allowed_geo_zones` branches; the date filter and title/exclude/location logic
      remain; the return tuple arity is unchanged.
- [ ] `python score.py --mock --profile unified_jc` passes all **eleven** cases:
      the eight 016 cases in their bands, plus USRemoteCo 1–2, BerlinBank 1, InternCo 1.
- [ ] Settings profile editor shows one `job_titles` input, one `title_exclude`
      input, `allowed_contract_types`, `languages_spoken`; the "Banned countries",
      "Search query titles", "Scrape titles", and `pre_filter` title inputs are gone;
      saving persists the canonical fields (verify:
      `sqlite3 data/jobs.db "SELECT criteria FROM search_profiles"` contains
      `job_titles` and `languages_spoken`, and does not require the removed inputs).
- [ ] With `search_locations` empty, `effective_search_locations()` returns the union
      of the three work-mode country lists.
- [ ] `scoring_context` seed no longer contains the German/Spanish numeric cap; the
      soft sector line is present; the large-corporate cap is retained.
- [ ] No change to `models.py`, `storage.py`, schema, migrations, `main.py`,
      `onboarding.py`, `shared.py`, or the Settings section layout.

---

## Suggested phase order for `/speckit-tasks`

1. `profiles.py` — canonical fields + deprecations + `effective_search_locations` +
   `to_criteria_dict` / `from_criteria` back-compat + seed values + docstring.
2. `title_gate.py` — parameterised signature with `_PM_TITLES` fallback (assert intact).
3. `scorer.py` — Tier-0 rewrite (positive language, contract type, drop banned_countries).
4. `score.py` — pre-filter injection + digest language/contract filters + 3 mock cases.
5. `scrapers/greenhouse.py` — read `job_titles`, drop US filter, drop local title consts.
6. `scrapers/boards/linkedin.py` + `indeed.py` — derive terms + locations from profile.
7. `scrape.py` — `JobFilter` from canonical fields + parameterised gate call.
8. `filters.py` — remove dead branches, preserve date filter + return arity.
9. `tracker_views/settings.py` — `_render_profile_editor` reorg + save block.
10. `scoring_context` seed prose edits (language cap out, soft sector line in).
11. Validate: `python score.py --mock --profile unified_jc` (all 11 in band), then a
    Settings save + `sqlite3` round-trip check, then a dry broad scrape to confirm
    jobspy uses the derived locations and Greenhouse no longer drops US at scrape.
