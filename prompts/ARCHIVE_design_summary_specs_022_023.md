# Design summary — Specs 022 & 023 (job_agent)

> Pre-spec design notes for review. Nothing here has been checked against live source code.
> Written from conversation only; stale spec markdown was NOT used as reference.

---

## Context

`job_agent` currently models geography as `work_mode_geography` (Spec 016): a nested dict
mapping `work_mode → allowed geo zones`, evaluated deterministically at Tier-0 via
`_geography_for_mode()` against `job.company_country` / `job.geo_zone`.

Constitution (immutable): **scraper = wide net, no filtering at scrape time; scorer = all
filtering.** Tier-0 handles hard gates; `scoring_context` prose handles soft signals.

Two separate specs are proposed. They are independent and can ship in either order,
though 021 should land first if both are done in sequence.

---

## Spec 021 — Residence & relocation model

### Problem

The current model assumes Swiss residence is fixed. It isn't. The user would relocate for a
sufficiently good role. This means a job does not resolve to *one* residence — it resolves to a
*set* of feasible residences, each with a different personal cost.

Two failed intermediate designs, recorded so they aren't re-proposed:

1. **`residence_base` as a derived scalar** (job → one residence → eligible/not).
   Fails because desirability would have to be an input to the feasibility gate, and
   desirability is the scorer's output. Circular. Any implementation collapses into a
   hardcoded "if Web3 PM, relax geography" special case — an undocumented filtering layer,
   which is exactly the class of bug Spec 020 removed.

2. **Relocation cost as a single scalar per residence.**
   Fails on Russia. "I'd move there for a good, well-paid, remote PM role" is three
   conditions, not a lower number. Lowering Russia's cost would also admit onsite Moscow
   roles and badly-paid Moscow roles at sufficiently high intrinsic score.

### Proposed design

Residence becomes a **cost plus hard entry conditions**, per residence. Mirrors Spec 016's
nesting logic, applied to residence rather than company geography.

```python
relocation = {
    "switzerland": {"cost": 0},
    "france":      {"cost": 2},
    "eu":          {"cost": 5},
    "turkiye":     {"cost": 6, "work_modes": ["remote"]},
    "russia":      {"cost": 4,
                    "work_modes": ["remote", "hybrid"],
                    "min_salary_eur": 90000},
}

contract_geo = {
    "permanent": ["switzerland", "france", "eu", "turkiye", "russia"],
    "freelance": ["switzerland", "france", "eu", "turkiye"],
    "contract":  ["switzerland", "france", "eu", "turkiye"],
}

fx_rates = {"RUB": 87.0}   # editable; converts stated salary → EUR for floor comparison
```

Rationale for the cost values: Switzerland is status quo (B permit, Pully). France is cheap —
French national, French company, native language, no permit friction; administratively closer
to moving to Geneva than to Istanbul. Russia at 4 (not 9) reflects genuine willingness, with
the *conditions* — not the cost — doing the filtering.

Rationale for `contract_geo`: Russia is **permanent-only**, and the asymmetry is deliberate.
Employed and resident in Russia, you are paid in RUB into a Russian account and spend it there —
no cross-border rail, no problem. Freelancing via the Estonian OÜ while resident in Switzerland
requires RU → EE → CH, which is where correspondent-bank de-risking bites. Same country,
opposite verdict, depending on contract type. Türkiye has no equivalent problem: an Estonian OÜ
invoicing a Turkish company is routine cross-border B2B, so it is included for all three
contract types.

### Resolution function

`_geography_for_mode()` is **superseded**, not extended:

```python
@dataclass
class Engagement:
    feasible_residences: dict[str, int]   # residence → relocation cost
    contract_ok: bool
    rationale: str                        # prose, injected into scoring_context
```

Feasibility rules:

- `onsite` / `hybrid` → residence must be commute-compatible with `job.company_country`
- `remote` → residence is free, subject to the employer's own hiring geography
- a residence is only feasible if `job.work_mode ∈ residence.work_modes` (when specified)
- `job.contract_type` must permit `job.company_country` per `contract_geo`

**Tier-0 rejects only when** `feasible_residences` is empty, or `contract_ok is False`.
Everything else — including relocation appetite — is passed to the scorer as
`relocation_cost` + `rationale` in `scoring_context`.

Consequence: no special-casing. An outstanding Web3 PM role in Paris is feasible at cost 2 and
scores near the top. A generic consulting gig in Berlin is feasible only at cost 5 and gets
marked down. A Moscow remote PM role at €110k arrives at cost 4 and competes fairly against a
Lausanne hybrid at cost 0. None of these required a rule.

### Salary floor semantics — three states, not two

```
salary present, ≥ floor  → pass, no penalty
salary present, < floor  → Tier-0 reject (hard, deterministic)
salary absent            → pass to scorer, flagged unknown
```

"Assume good when absent" was rejected: most postings omit salary, so the gate would fire on a
minority of jobs and — worse — reject postings that state a low number while admitting those
that stay silent. That rewards opacity.

`min_salary_eur` is therefore **a floor on stated salaries**, not a requirement that salary be
stated. When absent, `scoring_context` carries the uncertainty forward: *"relocation to Russia
required; no salary stated; a relocation of this magnitude is only justified by strong
compensation."* The LLM then reads seniority, company stage, and phrasing from the posting
body, which are usually more informative than a blank salary field.

### Upstream dependency

`min_salary_eur` requires `salary_min` / `salary_max` / `salary_currency` as **real extraction
fields with FX normalization**, not free text. Status unverified — must be checked against live
`models.py` and `scorer.py` before speccing.

FX: static rate constant stored in profile criteria, manually refreshed. A live FX call is a new
dependency for a field gating a handful of jobs per month. Store the EUR floor and the rate
separately — never a hardcoded RUB figure, which would silently drift.

### Two new extraction fields

- `languages_required: list[str]` — gated as **Tier-0** against `profile.languages_spoken`
  (FR native, EN professional; DE/ES limited). Currently absent, and decision-relevant for any
  non-Francophone/Anglophone market.
- `residence_requirement: str | null` — some employers contractually require physical presence
  in the country of hire (permanent-establishment reasons). Soft signal, scorer-side.

### Persistence & UI

`relocation`, `contract_geo`, and `fx_rates` live in the profile's `criteria` JSON alongside
`work_mode_geography`. No new table — `search_profiles` already carries arbitrary criteria.

Per Spec 018 sectioning, a new **"Residence & relocation"** Settings section: one row per
residence with a cost slider, a work-mode multiselect, and an optional salary floor. Rows are
addable and removable — the set of residences under consideration is itself a preference.

`feasible_residences` and `relocation_cost` become displayed tracker fields, so a job card shows
*what accepting it would cost*.

### Explicitly NOT in scope

- No changes to any scraper
- No change to the scoring prompt's structure (only `scoring_context` content)
- No new profile split — Spec 017's consolidation to `unified_jc` stands
- No live FX API

### Out-of-band note (not a spec input)

Istanbul is **not** a geography rule. Living there a few months a year on a Swiss or EU remote
contract does not change residence — Switzerland stays the tax and permit anchor, and UTC+3 vs
CET is a 1–2h offset. It is a soft preference for employers tolerant of working outside the
country of hire: `boost_keywords` ("work from anywhere", "location flexible", "async",
"no timezone requirement"), mild negative on "must reside in X" / "N days in office".
Pure scorer territory. Handled by `residence_requirement` above.

---

## Spec 022 — hh.ru / HeadHunter network scraper

### Feasibility

Technically the easiest scraper in the project. Public REST API, no auth for vacancy search, no
Playwright, no proxy.

```
GET https://api.hh.ru/vacancies
    ?text=product+owner
    &search_field=name
    &schedule=remote        # or work_format=REMOTE (newer param)
    &period=30
    &per_page=100
    &page=N
Headers: User-Agent: job-agent/1.0 (contact@example)   # mandatory
```

Returns paginated JSON: `name`, `employer`, `area`, `salary`, `alternate_url`, `published_at`,
`schedule`, `snippet`. Full description requires a second call to `/vacancies/{id}`.

Fits `BaseScraper` with `ACQUISITION_MODEL` / `SUPPORTS_DISCOVERY` unchanged. ~90 lines.

### Scope: `hh_network`, not `hh_ru`

The same API serves the whole HeadHunter network via different `area` IDs: `hh.ru`, `hh.kz`,
`hh.uz`, `headhunter.ge`, `hh.by`, `headhunter.kg`. Georgia, Armenia, Kazakhstan and Uzbekistan
host a real cluster of Web3 companies hiring in English.

Searching English title strings (`text=product owner`, `search_field=name`) is a **query
parameter**, not scrape-time filtering — it does not violate the wide-net constitution.

### Known constraints

- Applicant-side API was closed by hh.ru in **December 2024**. Read-only discovery only; no
  automated applying is possible through the public API.
- Postings are predominantly in Russian. DeepSeek reads Russian, so LLM extraction is fine —
  but **anything doing English string matching breaks**. `title_gate` and `title_exclude` must
  run *after* the LLM emits a normalized English title, not before. **This ordering must be
  verified against live `scorer.py` / `title_gate.py`.**
- "Remote" on hh.ru usually means remote *within* Russia/CIS with RF tax residency assumed —
  not `global_remote`. Needs its own geo zone.
- hh.ru is unusually salary-transparent vs. Western boards (RUB ranges stated far more often
  than Greenhouse or LinkedIn state anything). The Spec 021 salary floor may therefore actually
  bite on this source. Tune the number empirically after a first run.

### Depends on Spec 021

A `russia_cis` (or broader `cis`) geo zone, and the `russia` residence entry. Without 021, every
Moscow role dies at Tier-0 before the LLM sees it.

---

## Open items / things a reviewer should challenge

1. **Cost values are the user's, not derived.** 0/2/5/6/4. Are the relative gaps right? Is
   `eu: 5` too high given French nationality confers EU freedom of movement?
2. **`contract_geo` — Russia is permanent-only.** Reasoning is that freelance repatriation of
   RUB via an Estonian OÜ to a Swiss resident hits correspondent-bank refusal, whereas salaried
   RUB spent in-country does not. This is a judgement call made under acknowledged uncertainty,
   and it was set to *reject* deliberately: a false reject costs one job listing, a false accept
   costs a month spent discovering the payment rail does not work. A reviewer should challenge
   whether the asymmetry is real or whether it is over-cautious. Türkiye is included for all
   three contract types.
3. **FX rate 87.0 RUB/EUR is unverified.** It is implied by the user's own figures
   (€90,000 ≈ ₽7,827,556), not independently checked, and is volatile.
4. **Salary extraction status unknown.** Whether `salary_*` fields exist as structured
   extraction output is the single biggest unverified assumption in Spec 021.
5. **`title_gate` ordering unknown.** Whether it runs pre- or post-LLM determines whether
   Spec 022 is 90 lines or a refactor.
6. **`_geography_for_mode()` is superseded.** This is a real blast radius on the Tier-0 path.
   Regression check: the FELFEL job (Senior Tech Product Owner, Zürich, Indeed
   `jk=f7e16c8a6a3d7af7`) must still score 6–8 under `unified_jc`.

---

## Next step

Read live source before writing either spec: `models.py`, `profiles.py`, `scorer.py`,
`storage.py`, `title_gate.py`, `scrapers/`, and the actual `specs/016-*` tree.
Do not use stale spec markdown as architecture reference.
