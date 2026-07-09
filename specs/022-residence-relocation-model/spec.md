# Spec 022 — Residence & relocation model (Engagement resolution)

> Spec for Claude Code. Read `profiles.py`, `scorer.py`, `score.py`,
> `storage.py`, `models.py`, and `tracker_views/settings.py` **in full**
> before starting. This spec was grounded against live source on 2026-07-09
> (Fable review); claims below marked ✅ were verified against the code on disk.
>
> This task **explicitly concerns** stable-core files `profiles.py` and
> `scorer.py`, plus a `job_scores` schema addition in `storage.py` following
> the `comp_flag` migration precedent. Do **not** touch `scrape.py`, any file
> under `scrapers/`, `title_gate.py`, `llm.py`, `main.py`,
> `tracker_views/onboarding.py`, or `tracker_views/shared.py`.

---

## Context (investigation-first)

The current model assumes Swiss residence is fixed. It isn't. The user would
relocate for a sufficiently good role — cheaply to France (French national,
French company), reluctantly to the EU, conditionally to Türkiye or Russia.
A job therefore does not resolve to *one* residence: it resolves to a **set of
feasible residences, each with a personal cost**, and the cost belongs to the
scorer, not the gate.

### Two failed designs — recorded so they are not re-proposed

1. **`residence_base` as a derived scalar** (job → one residence →
   eligible/not). Fails: desirability would have to be an input to the
   feasibility gate, but desirability is the scorer's *output*. Circular. Any
   implementation collapses into a hardcoded "if Web3 PM, relax geography"
   special case — the class of undocumented filtering layer Spec 020 deleted.
2. **Relocation cost as a single scalar per residence.** Fails on Russia:
   "I'd move there for a good, well-paid, remote PM role" is three conditions,
   not a lower number. Lowering Russia's cost admits onsite-Moscow and
   badly-paid-Moscow roles at high intrinsic scores.

Resolution: each residence carries a **cost (soft, scorer-side) plus hard
entry conditions (Tier-0)**. Feasibility is deterministic; appetite is prose.

### Live-code facts this spec builds on (all ✅ verified 2026-07-09)

- `salary_text` and `comp_annual_eur` already exist on `JobPosting` and in
  `EXTRACTION_PROMPT`, with in-prompt normalization (EUR 1:1, CHF ≈1:1,
  USD ×0.92). **No new salary extraction machinery is needed** — only a RUB
  line (Spec 023) and the floor comparison here.
- `comp_flag` [D5] already implements "flag unknown pay, never penalize
  absence" for non-CH remote roles, threaded through `_evaluation_result()`.
  New per-score fields follow this exact pattern.
- `_geography_for_mode()` + Tier-0 rule 5 in `evaluate_for_profile()` are the
  only geography enforcement point (Spec 020 deleted the SQL layer). Blast
  radius of superseding them is contained.
- `language_required` extraction + positive Tier-0 language gate already exist
  (Spec 017). This spec does **not** add a language field.
- `score.py` re-applies work_mode/sector/language/contract filters at digest
  assembly. New Tier-0 gates here must be mirrored there (see §Files, score.py).
- `comp_flag` hardcodes `"Switzerland"` as home country. Under this spec, home
  is *the zero-cost residence*; the hardcode is replaced.

---

## Goal

Replace per-work-mode geography acceptance with **Engagement resolution**:
a deterministic Tier-0 function answering *"is there any residence in which I
could legally and practically take this job?"* — and, when yes, forwarding
*what taking it would cost* to the LLM scorer as prose plus a numeric field.

After this change: no relocation special-casing exists anywhere; a Paris
hybrid Web3 role, a Berlin consulting gig, and a Moscow remote role all flow
through one rule and are separated by cost, not by hand-written exceptions.

---

## Target data model (`profiles.py`, stored in `criteria` JSON)

### New canonical field: `relocation`

```python
relocation: dict = field(default_factory=dict)
```

Seed value (all numbers user-confirmed 2026-07-09):

```python
relocation={
    "switzerland": {
        "cost": 0,
        "work_modes": ["remote", "hybrid", "on-site"],
        # Employers whose remote roles are compatible with living here.
        # Seeds from the current work_mode_geography["remote"]["countries"]
        # list — behaviour-preserving.
        "remote_employer_countries": [<copy of current remote countries list>],
    },
    "france": {
        "cost": 2,
        "work_modes": ["remote", "hybrid", "on-site"],
        "remote_employer_countries": [<same list>],
    },
    "eu": {
        "cost": 5,
        "work_modes": ["remote", "hybrid", "on-site"],
        "remote_employer_countries": [<same list>],
    },
    "turkiye": {
        "cost": 6,
        "work_modes": ["remote"],
        "remote_employer_countries": [<same list>],
    },
    "russia": {
        "cost": 4,
        "work_modes": ["remote", "hybrid"],
        "min_salary_eur": 90000,
        # A Russian residence only makes sense for a Russian employer
        # (payment rails; see contract_geo rationale).
        "remote_employer_countries": ["Russia"],
    },
}
```

Cost rationale (documentation, not code): Switzerland = status quo (B permit,
Pully). France = French national, French entity, native language — closer to
moving to Geneva than to Istanbul. Russia at 4, not 9: genuine willingness,
with the *conditions* — remote/hybrid only, €90k stated-salary floor — doing
the filtering, not the cost.

### New canonical field: `contract_geo`

```python
contract_geo: dict = field(default_factory=dict)
```

Seed:

```python
contract_geo={
    "permanent": ["switzerland", "france", "eu", "turkiye", "russia"],
    "freelance": ["switzerland", "france", "eu", "turkiye"],
    "contract":  ["switzerland", "france", "eu", "turkiye"],
}
```

Rationale: Russia is **permanent-only**, deliberately asymmetric. Employed and
resident in Russia → paid in RUB, spent in RUB, no cross-border rail. Freelance
via the Estonian OÜ while Swiss-resident → RU → EE → CH, where correspondent-
bank de-risking bites. Türkiye has no equivalent problem (routine cross-border
B2B), hence present in all three. Set to *reject* under uncertainty: a false
reject costs one listing; a false accept costs a month discovering the payment
rail is dead.

### Country → zone mapping (code constant, `scorer.py`)

```python
_RESIDENCE_ZONES = {
    "Switzerland": "switzerland",
    "France": "france",
    "Russia": "russia",
    "Türkiye": "turkiye", "Turkey": "turkiye",
    # EU-27 member names …: "eu"
}
def _zone_of(company_country: str) -> str | None: ...
# Most-specific match wins: France → "france", not "eu".
# Unknown / non-listed country → None.
```

This is deterministic code, not profile data — the EU membership list is a
fact, not a preference.

### Deprecated (kept inert, 016/017 idiom)

`work_mode_geography` → superseded by `relocation`. **Do not delete** — stays
on the dataclass and in `criteria` round-trip. `from_criteria()` synthesizes
`relocation` from it when absent (see below).

### `to_criteria_dict()` / `from_criteria()`

- Add `relocation` and `contract_geo` keys to both.
- Back-compat synthesis in `from_criteria()` when `relocation` is absent:

```python
relocation = criteria.get("relocation")
if not relocation:
    wmg = criteria.get("work_mode_geography") or {}
    remote_countries = (wmg.get("remote", {}) or {}).get("countries", []) or []
    relocation = {"switzerland": {
        "cost": 0,
        "work_modes": ["remote", "hybrid", "on-site"],
        "remote_employer_countries": remote_countries,
    }}
    # Hybrid/on-site acceptance previously listed non-CH countries?
    # They were acceptance-without-relocation, which the old model
    # conflated; the synthesized single-residence form is the honest
    # translation. Flag in deploy notes (Live-DB note below).
contract_geo = criteria.get("contract_geo") or {
    "permanent": ["switzerland", "france", "eu", "turkiye"],
    "freelance": ["switzerland", "france", "eu", "turkiye"],
    "contract":  ["switzerland", "france", "eu", "turkiye"],
}
```

(The synthesized defaults exclude Russia — an un-migrated row must never
silently start accepting Russian roles.)

---

## Engagement resolution (`scorer.py`)

New helper replacing `_geography_for_mode()` (delete it — no callers remain):

```python
@dataclass
class Engagement:
    feasible_residences: dict[str, int]   # residence key → cost
    cheapest_residence: str | None
    relocation_cost: int | None           # cost of cheapest feasible residence
    blocked_reason: str | None            # set when infeasible
    rationale: str                        # prose for scoring_context

def _resolve_engagement(job, profile) -> Engagement: ...
```

Rules, in order:

1. **Contract legality.** `zone = _zone_of(job.company_country)`. If
   `company_country` is known and `contract_type` is known and
   `zone not in profile.contract_geo.get(contract_type, [])` → infeasible,
   `blocked_reason = f"contract {ct} not viable for {zone} employer"`.
   Unknown country or unknown contract type → skip this rule (never reject on
   absence of information — comp_flag precedent).
2. **Per-residence feasibility.** For each residence `r` in
   `profile.relocation`:
   - `job.work_mode ∈ r["work_modes"]` (unknown work_mode → treat as
     potentially any mode, i.e. pass — permissive, matching the current
     unknown-mode union behaviour ✅).
   - `on-site`/`hybrid`: feasible iff `_zone_of(company_country) == r_key`
     (commute compatibility). Unknown country → pass for the zero-cost
     residence only (current behaviour: unknown-country hybrid passes).
   - `remote`: feasible iff `company_country ∈ r["remote_employer_countries"]`
     — or, when country is unknown, fall back to the job's `geo_zone` against
     a derived zone set, preserving the current remote fallback ✅.
   - **Salary floor** (only when `r` has `min_salary_eur`): if
     `job.comp_annual_eur` is present and `< min_salary_eur` → `r` is
     infeasible. If absent → `r` stays feasible and the rationale carries the
     uncertainty (three-state semantics; see below).
3. **Result.** Empty set → Tier-0 reject at score 2,
   reason `f"filtered: no feasible residence ({blocked_reason or work_mode}/{company_country})"`.
   Non-empty → pass to Tier 1 with `relocation_cost` = min cost, and a
   rationale string:
   - cost 0: `""` (no prose — don't waste tokens on the default case)
   - cost > 0: `"Accepting this job would require relocating to {residence}
     (personal relocation cost {cost}/10). Only a role clearly worth this
     disruption should score above 7."`
   - cost > 0 and salary unknown and the residence has a floor:
     append `"No salary is stated; a relocation of this magnitude is only
     justified by strong compensation — judge from seniority and company
     stage."`

### Salary floor semantics — three states, not two

```
salary stated, ≥ floor  → residence stays feasible, no penalty
salary stated, < floor  → residence infeasible (deterministic)
salary absent           → residence feasible, uncertainty forwarded as prose
```

"Assume good when absent" is rejected: most postings omit salary, so the gate
would fire on a minority and — worse — reject honest low-ballers while
admitting silent ones. That rewards opacity. The floor gates **stated**
salaries only. FX for RUB rides in the extraction prompt (Spec 023), matching
the existing in-prompt USD 0.92 pattern; a code-side FX table was considered
and rejected for now (Principle V — the drift on a hard floor meant to
exclude blatant low-balls is acceptable noise).

### Tier-0 integration (`evaluate_for_profile`)

Replace rule 5 (per-mode geography) with the Engagement resolution. Rules
0–4 (denylist, language, sector, contract-type-allowlist, work-mode) are
unchanged. Note rule 3 (contract *type* allowlist) and Engagement rule 1
(contract *geography*) are different rules; both stay.

Replace the `comp_flag` home-country hardcode:

```python
home = min(profile.relocation, key=lambda r: profile.relocation[r]["cost"]) \
       if profile.relocation else "switzerland"
# comp_flag fires for remote + known country + country's zone != home zone
```

Thread two new values through `_evaluation_result()` exactly like `comp_flag`:
`relocation_cost: int | None`, `residence_base: str | None` (the cheapest
feasible residence). Append the rationale (when non-empty) to
`profile_context` before the Tier-1 prompt, same as the monitoring note ✅.

---

## `storage.py` — schema addition

Following the `comp_flag` migration precedent exactly:

- `job_scores`: add `relocation_cost INTEGER` (nullable) and
  `residence_base TEXT` (nullable), via a `migrate_*.py` script in the
  existing convention. **Confirm the live `job_scores` schema before
  altering.**
- `save_scored()` persists both when present; `get_all_for_tracker()` /
  `get_digest()` return them.

Run `python -m pytest tests/` after touching `storage.py` (constitution,
Development Workflow).

---

## `score.py` — digest mirror + mocks

- Digest assembly ✅ re-applies Tier-0-equivalent filters. Add the mirror:
  drop a job when `residence_base` is NULL **and** the score row predates this
  spec is *not* detectable — so instead mirror positively: recompute
  `_resolve_engagement` cheaply? **No** — that re-runs profile logic at digest
  time. Decision: the digest mirror filters only on the *persisted*
  `relocation_cost IS NOT NULL OR scored_by == 'tier_0'` being consistent;
  practically, after the operator `--rescore` step (below) all rows carry the
  new fields, so the digest needs **no new filter** — document this in the
  deploy note instead of adding code. (Surgical choice; revisit only if stale
  rows are observed.)
- `MOCK_JOBS` + `expectations` — add four cases:

```python
{  # Moscow remote, salary above floor → feasible at cost 4
   "title": "Senior Product Manager — DeFi",
   "company": "MoscowChain",
   "location": "Remote (Russia)",
   "base_location": "Moscow, Russia",
   "description": "Russian DeFi platform. Fully remote within Russia. "
                  "Compensation: 12,000,000 RUB per year. Series B, 150 employees.",
},
{  # Moscow on-site → no feasible residence (russia.work_modes excludes on-site)
   "title": "Senior Product Manager",
   "company": "MoscowBank",
   "location": "Moscow, Russia (On-site)",
   "base_location": "Moscow, Russia",
   "description": "On-site product role, 5 days a week in our Moscow office.",
},
{  # Moscow remote, salary below floor → deterministic reject
   "title": "Product Manager",
   "company": "MoscowStartup",
   "location": "Remote (Russia)",
   "base_location": "Moscow, Russia",
   "description": "Remote PM role. Salary: 4,500,000 RUB per year.",
},
{  # Paris hybrid Web3 → feasible at cost 2, scores high
   "title": "Senior Product Manager — RWA",
   "company": "ParisChain",
   "location": "Paris, France (Hybrid)",
   "base_location": "Paris, France",
   "description": "French Web3 protocol tokenizing real-world assets. Hybrid, "
                  "2 days in our Paris office. Series B, 90 employees.",
},
```

```python
(4, 8, "MoscowChain — remote RU above floor → feasible cost 4, LLM decides"),
(1, 2, "MoscowBank — on-site RU → no feasible residence (Tier-0)"),
(1, 2, "MoscowStartup — stated salary below RU floor → Tier-0 reject"),
(7, 9, "ParisChain — Web3 PM hybrid FR → feasible cost 2, strong fit"),
```

All existing cases (FELFEL 5–7 band above all) MUST stay in band — the
Engagement resolution must be behaviour-preserving for them.

> Note: the two Moscow-RUB cases exercise the floor only once Spec 023's RUB
> line lands in `EXTRACTION_PROMPT`. Until then, mark them with a
> `requires_spec_023` comment and expected band `(4, 8)` / `(4, 8)` (salary
> unparsed → floor silent), then tighten after 023.

---

## `tracker_views/settings.py` — "Residence & relocation" section

Per Spec 018 sectioning, inside `_render_profile_editor` replace the per-mode
geography widgets (016) with a **Residence & relocation** block:

- One row per residence key: cost slider (0–10), work-mode multiselect,
  optional salary-floor number input, and (for remote) a
  `remote_employer_countries` text area (one per line).
- Rows addable/removable — the set of residences is itself a preference.
- A `contract_geo` editor: per contract type, a multiselect over the residence
  zone keys.
- Save block writes `profile.relocation` and `profile.contract_geo`; remove
  the `work_mode_geography` widget assignments (field stays inert).

Job cards (`tracker_views/jobs.py`): when `residence_base` is present and not
the zero-cost residence, show a small badge `🧳 {residence} · cost {n}`.
One-line change per card renderer; no layout redesign.

---

## Non-objectives

- **No scraper changes** — Spec 023 is separate and depends on this one.
- No new geo-zone vocabulary and no extraction-prompt changes except those in
  Spec 023 (RUB, summary language, `russia_cis`).
- No `residence_requirement` extraction field in this spec: the
  "employer requires presence in country of hire" signal is deferred until
  observed in real data (constitution VI — no speculative builds). The
  Istanbul few-months-a-year preference is **not** a geography rule; it lives
  in `scoring_context` prose only.
- No live FX API. No new dependencies.
- No deletion of `work_mode_geography` from the dataclass or criteria.
- No onboarding wizard changes (`onboarding.py` emits legacy keys;
  `from_criteria` synthesis covers it — follow-up note, as in 016).
- No multi-profile split — `unified_jc` stands (Principle III).

---

## Constitution alignment

- **I (filet large):** unchanged — this is scorer-side only.
- **II (deux chemins):** code change; the seed `scoring_context` gains no new
  hard caps (relocation prose is injected at runtime, not authored).
- **IV (structure déterministe):** strengthened — relocation feasibility,
  contract geography, and the salary floor are deterministic rules over
  already-extracted fields, per the 016/017 precedent. The LLM receives cost
  as prose and produces only score + reason.
- **V (chirurgical):** `_geography_for_mode` deleted with its single call
  site; new fields additive; deprecated fields inert; digest untouched by
  decision (documented); schema addition follows the `comp_flag` precedent.
- **VI (validation empirique):** four new mock cases; FELFEL band preserved;
  operator step `python score.py --rescore --profile unified_jc` after deploy.

> **Live-DB note (not a code task):** the active profile's criteria row does
> not contain `relocation`. On first load, `from_criteria` synthesizes the
> single-residence Swiss form (behaviour-preserving, Russia excluded). The
> user must open Settings, review the new section, add the france / eu /
> turkiye / russia rows with the confirmed values, and Save. Then `--rescore`.

---

## Acceptance criteria

- [ ] `SearchProfile` has `relocation` + `contract_geo`; round-trip through
      `to_criteria_dict`/`from_criteria`; synthesis from a legacy criteria
      dict (only `work_mode_geography`) yields the Swiss-only form with
      Russia absent.
- [ ] `_geography_for_mode` no longer exists; `_resolve_engagement` is the
      single geography/feasibility enforcement point; Tier-0 rules 0–4
      unchanged.
- [ ] Contract-geography rule rejects a freelance role at a Russian employer
      and passes a freelance role at a Turkish employer (unit-testable pure
      function).
- [ ] Salary floor: stated-below rejects, stated-above passes, absent passes
      with rationale text present in the Tier-1 prompt.
- [ ] `comp_flag` home country derives from the zero-cost residence.
- [ ] `job_scores` has `relocation_cost` + `residence_base` via migration;
      tests green (`python -m pytest tests/`).
- [ ] `python score.py --mock --profile unified_jc` — all prior cases in
      band, four new cases in band (Moscow-RUB pair per the 023 note).
- [ ] Settings shows the Residence & relocation section; saving persists
      `relocation` and `contract_geo` in `criteria`
      (`sqlite3 data/jobs.db "SELECT criteria FROM search_profiles"`).
- [ ] Job card badge renders for `residence_base != home`.
- [ ] No changes to `scrape.py`, `scrapers/`, `title_gate.py`, `main.py`,
      `llm.py`, `onboarding.py`, `shared.py`.

---

## Suggested phase order for `/speckit-tasks`

1. `profiles.py` — fields, seed, round-trip, synthesis, docstring.
2. `scorer.py` — `_zone_of`, `Engagement`, `_resolve_engagement`, Tier-0
   rewrite, comp_flag home derivation, rationale injection.
3. `storage.py` — migration + persistence of the two new score fields; pytest.
4. `score.py` — four mock cases + expectations.
5. `tracker_views/settings.py` — Residence & relocation section + save block.
6. `tracker_views/jobs.py` — card badge.
7. Validate: mocks in band → Settings round-trip → deploy → operator
   Live steps (Settings review + `--rescore`) → FELFEL check.
