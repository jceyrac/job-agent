# Spec 023 — HeadHunter network scraper (`hh_network`) — rev. B

> Spec for Claude Code. Read `scrapers/base.py`, one existing board scraper
> (e.g. `scrapers/boards/linkedin.py` or the RSS/API boards), `scorer.py`
> (extraction prompt + `_VALID_LANGUAGES`), `score.py` (pre-filter build), and
> `profiles.py` before starting. Grounded against live source 2026-07-09.
>
> **rev. B (2026-07-09):** Spec 022 was reverted; this spec no longer depends
> on any code from it. The only prerequisite is a **prose-path** edit (no
> deploy): in Settings, `work_mode_geography.remote.countries` must include
> Russia (and optionally Kazakhstan/Georgia/Armenia/Uzbekistan), otherwise
> every hh job is Tier-0-rejected by the existing per-mode geography rule —
> by design, but pointlessly. See `specs/022-.../REVERTED.md`.
>
> Do **not** touch `storage.py`, `models.py`, `title_gate.py`, `main.py`,
> `filters.py`, `profiles.py`, or any tracker view except the geo-zone
> option lists noted in Part B.

---

## Context

hh.ru (HeadHunter) is the dominant job board for Russia and the CIS. The same
public REST API serves the whole network — `hh.ru`, `hh.kz`, `hh.by`, `hh.uz`,
`headhunter.ge`, `headhunter.kg` — via `area` IDs. Georgia, Armenia,
Kazakhstan and Uzbekistan host a real cluster of Web3 companies hiring in
English; Russia itself is in scope for remote/hybrid roles per the user's
`scoring_context` (soft judgment — no hard salary gate; the user evaluates
interesting roles himself).

**API status (verified via web research, July 2026):** vacancy search requires
no authentication. The applicant-side API (resumes, responses, applying) was
closed by hh.ru in **December 2024** — this scraper is read-only discovery;
no auto-apply path exists or should be attempted.

### The three language traps (verified against live code 2026-07-09)

1. **SQL pre-filter kills Cyrillic titles.** `score.py` injects
   `title_contains = profile.job_titles` into `get_jobs_for_scoring()` — an
   English substring match in SQL, applied to every job *before* extraction.
   «Менеджер по продукту» never survives it. **Decision (user-confirmed,
   Option A / MVP):** accept this. Many hh postings use Latin-script titles
   ("Product Manager", "Senior Product Owner") and those pass. The Cyrillic
   tail is a known, *measured* loss: the scraper logs a per-run counter of
   fetched jobs whose title contains no Latin PM keyword, so real data can
   later justify (or bury) an Option B (post-extraction gating for hh-sourced
   jobs). No pipeline change now.
2. **`language_required` vocabulary hole.** `_VALID_LANGUAGES` in `scorer.py`
   is `{english, french, german, italian, spanish, multiple, unknown}`.
   The parser **coerces out-of-vocabulary values to "unknown"**, and Tier-0
   passes "unknown" — so a Russian-language-required role currently sails
   through the language gate silently. This is a live defect today for
   Turkish postings and becomes a firehose with hh. Fix in this spec.
3. **Russia is currently classified `geo_zone: "europe"`.** Both prompts
   define europe as "UTC+0 to UTC+4"; Moscow is UTC+3. Without a vocabulary
   fix, Moscow jobs are indistinguishable from EU jobs in the tracker's geo
   filter. Fix in this spec.

---

## Goal

Add a `hh_network` board scraper (wide net, English title queries as
board-provided query scoping — constitution I), plus the extraction-
vocabulary amendments that make its output classifiable: `russian`/`turkish`
languages, a `russia_cis` geo zone, and RUB salary normalization
(informational — feeds `comp_annual_eur` and the existing `comp_flag`, no
hard gate). Job cards must remain readable: summaries in English regardless
of source language.

---

## Part A — the scraper (`scrapers/boards/hh_network.py`)

New `BaseScraper` subclass, aggregation-board category (constitution VIII):

```python
SOURCE_NAME = "HeadHunter"
ENABLED = True
ACQUISITION_MODEL = "board"      # match the exact attribute values used by
SUPPORTS_DISCOVERY = True        # existing boards — read base.py first
```

### Endpoint & parameters

```
GET https://api.hh.ru/vacancies
    ?text=<query>            # one call per title in profile.job_titles
    &search_field=name
    &period=30               # matches the pipeline's 30-day retention
    &per_page=100
    &page=<n>                # paginate until `pages` exhausted or cap hit
    &area=<area_id>          # one pass per configured area
Headers:
    User-Agent: job-agent/1.0 (jceyrac@pm.me)    # REQUIRED by the API
```

- **Areas:** module constant `HH_AREAS = {"113": "Russia", "40": "Kazakhstan",
  "16": "Belarus", "28": "Georgia", "97": "Uzbekistan", "48": "Kyrgyzstan"}`.
  ⚠️ **Verify each ID against `GET https://api.hh.ru/areas` during
  implementation** — the IDs above are indicative, not authoritative.
- **Queries:** `profile.job_titles` (canonical list, Spec 017). English
  queries against `search_field=name` are query scoping, not relevance
  filtering — identical in kind to the LinkedIn/Indeed term queries.
  Do **not** pass `schedule=` or `work_format=` — work-mode classification
  belongs to the extractor (constitution I / IV).
- **Cap:** stop at 5 pages per (query × area) — 500 results — to bound run
  time. Log when the cap is hit.

### Response mapping → `JobPosting`

| hh field | JobPosting field | Note |
|---|---|---|
| `name` | `title` | verbatim (Cyrillic allowed) |
| `employer.name` | `company` | |
| `area.name` + country | `location`, `base_location` | e.g. "Москва" → "Moscow, Russia"; map area→country via `HH_AREAS` |
| `alternate_url` | `url` | the human page, not the API URL |
| `published_at` | `posted_date` | ISO parse |
| `salary` (`from`/`to`/`currency`/`gross`) | `salary` | format as text, e.g. "1 200 000–1 500 000 RUR gross/yr" — extraction parses it later |
| `schedule` / `work_format` | `work_mode` hint | map `remote`→"remote", `fullDay`/office→"unknown" (never assert on-site from schedule alone — the extractor decides; Spec 021-closed precedent) |
| detail call | `description` | see below |

**Description fetch:** the search payload only carries a `snippet`. Fetch
`GET /vacancies/{id}` for the full `description` (HTML → strip tags), with a
short sleep (0.5s) between detail calls and a hard cap consistent with the
page cap. On detail-call failure, keep the job with `description=snippet` —
degraded, not dropped.

**Currency note:** hh returns currency code `RUR` (legacy) — treat as RUB.

**Dedup:** rely on the existing `canonical_url`/ID machinery — hh
`alternate_url` is stable per vacancy. No custom dedup.

**Egress:** the scraper runs through the existing scraping egress
(`gluetun-scrape`, port 8890) like other boards — no new network path
(constitution VII). If hh.ru rejects the VPN exit IPs (plausible), log and
surface the failure; do **not** bypass the proxy silently. Flag to user as an
ops decision if observed.

### Latin-title counter (the Option-A instrument)

At the end of `fetch()`, log:

```
HeadHunter: {n_total} fetched, {n_cyrillic_only} with no Latin PM keyword in title
(these will not survive the SQL title pre-filter)
```

where the check reuses `profile.job_titles` with a simple lowercase substring
test. This is observability only — no filtering in the scraper.

---

## Part B — extraction vocabulary amendments (`scorer.py`)

Four surgical prompt/vocab edits. Both `SYSTEM_PROMPT` and
`EXTRACTION_PROMPT` carry the vocabularies; edit **both** copies.

1. **Languages.** `_VALID_LANGUAGES` += `"russian", "turkish"`. In both
   prompts' `language_required` sections add:
   `- "Знание русского языка" / description written entirely in Russian with
   no English version → russian` and the Turkish equivalent. Update the JSON
   schema line to `<english|french|german|italian|spanish|russian|turkish|multiple|unknown>`.
   No Tier-0 change needed — the existing positive gate (Spec 017) rejects
   `russian`/`turkish` automatically once the values exist, because
   `languages_spoken=["french","english"]`.
2. **Geo zone `russia_cis`.** Add to both prompts' geo_zone lists:
   `- "russia_cis" : Base location or description mentions Russia, Belarus,
   Kazakhstan, Uzbekistan, Kyrgyzstan, Armenia, Georgia, Moscow/SPb, or a
   .ru/.by/.kz employer domain` — and **amend the europe definition** to
   `"UTC+0 to UTC+4 (excluding Russia and CIS countries — use russia_cis)"`.
   Update the JSON schema enums. `_parse_result`/`_parse_extraction_result`
   have no geo_zone allowlist ✅ (no coercion to fix), but grep
   `tracker_views/` for geo_zone option lists used in filters and add
   `russia_cis` there (display-only change, permitted).
   **Note:** the Tier-0 remote fallback compares `job.geo_zone` against
   `work_mode_geography.remote.geo_zones` when company_country is unknown —
   after this change the user should add `russia_cis` to that list in
   Settings (prose path, flag in deploy note; do not edit the live DB from
   code).
3. **RUB salary line.** In `EXTRACTION_PROMPT`'s salary rules add:
   `- RUB (or legacy code RUR): 87 RUB ≈ EUR 1 (divide by 87, round to int)`.
   In-prompt rate, matching the existing USD 0.92 pattern. Also add:
   `- Salaries stated per month (common on hh.ru) MUST be multiplied by 12
   before normalizing.` ← hh salaries are monthly by convention; without
   this line every Russian salary parses ~12× too low. This feeds
   `comp_annual_eur` for **display and comp_flag only** — there is no hard
   salary gate anywhere (Spec 022 reverted).
4. **Summary language.** In both prompts' Summary sections add one line:
   `Always write the summary in English, regardless of the language of the
   description.` (Card readability; the raw description stays untranslated.)

> These prompt edits are the payload of this code change (constitution II) —
> vocabulary enums and parser allowlists are code, not prose.

---

## Part C — prerequisite prose-path edits (operator, NOT code)

Performed by the user in Settings on Live, before or right after deploy.
Claude Code MUST NOT modify `profiles.py` seeds or the live DB for these:

1. `work_mode_geography.remote.countries` += `Russia` (+ `Kazakhstan`,
   `Georgia`, `Armenia`, `Uzbekistan` to make the non-RU areas useful).
2. `work_mode_geography.hybrid.countries` += `Russia` (only if Moscow-hybrid
   offers should surface) and += `France` (independent decision, see
   REVERTED.md).
3. `work_mode_geography.remote.geo_zones` += `russia_cis` (see Part B.2 note).
4. `scoring_context` += the relocation prose from
   `specs/022-residence-relocation-model/REVERTED.md`.

---

## Non-objectives

- **No auto-apply** — the applicant API is closed (Dec 2024); do not attempt
  cookie/session workarounds.
- **No translation of descriptions** and no Cyrillic-capable title gating
  (Option B) — deferred until the Latin-title counter shows the loss matters.
- **No salary gates, floors, or relocation-cost logic** — Spec 022 is
  reverted; desirability judgment is the user's.
- No `schedule`/`work_format` scrape-time filtering.
- No monitored-companies/ATS integration for hh employers.
- No new dependencies (stdlib `requests` pattern as other boards).
- No changes to `storage.py`, `models.py`, `profiles.py`, schema, or
  migrations. No Settings UI changes of any kind.

---

## Constitution alignment

- **I (filet large):** the scraper applies zero relevance filtering; English
  `text=` queries are board query scoping (explicitly the permitted kind).
  The Latin-title counter observes a *pre-existing* pipeline filter, it does
  not add one.
- **II (deux chemins):** Part C is pure prose path (Settings edits on Live);
  Parts A/B are the code path. They are listed together for sequencing but
  never mixed — Part C requires no deploy.
- **IV (structure déterministe):** work_mode/geo/language classification stays
  in the extractor; the scraper only forwards a work-mode hint.
- **V (chirurgical):** one new file + vocabulary line-edits in `scorer.py`;
  no pipeline rewiring, no UI.
- **VI (validation empirique):** mock cases below + a documented first-run
  checklist (verify area IDs, verify VPN egress acceptance, read the
  Latin-title counter, sanity-check 5 Russian jobs' extraction by hand).
- **VII (sécurité):** existing egress path only; User-Agent identifies the
  agent honestly; no credentials involved.

---

## Mock regression cases (`score.py`)

Four additions. Bands assume the Part C prose edits are mirrored into the
mock profile fixture (check how `_run_mock` resolves the profile — if it
reads the seed, add Russia to the seed's mock-only variant is NOT allowed;
instead construct the test profile in `_run_mock` from the seed plus the
Part C values, keeping the seed untouched).

```python
{  # Russian-language-required role → Tier-0 language reject (new vocab)
   "title": "Product Manager",
   "company": "YandexLike",
   "location": "Moscow, Russia (Hybrid)",
   "base_location": "Moscow, Russia",
   "description": "Требуется свободное владение русским языком. "
                  "Продуктовая роль в крупной технологической компании.",
},
{  # English-speaking, well-paid Moscow remote → passes gates, LLM decides
   "title": "Senior Product Manager — DeFi",
   "company": "MoscowChain",
   "location": "Remote (Russia)",
   "base_location": "Moscow, Russia",
   "description": "Russian DeFi platform, English-speaking team. Fully remote "
                  "within Russia. Compensation: 1,000,000 RUB per month. "
                  "Series B, 150 employees.",
},
{  # Moscow on-site → Tier-0 per-mode geography reject (Russia not in on-site)
   "title": "Senior Product Manager",
   "company": "MoscowBank",
   "location": "Moscow, Russia (On-site)",
   "base_location": "Moscow, Russia",
   "description": "On-site product role, 5 days a week in our Moscow office. "
                  "English-speaking international team.",
},
{  # English-speaking Kazakh Web3 remote → passes, scores on merit
   "title": "Senior Product Manager — Web3",
   "company": "AstanaChain",
   "location": "Remote (Kazakhstan)",
   "base_location": "Astana, Kazakhstan",
   "description": "English-speaking Web3 infrastructure company in Astana. "
                  "Fully remote. Compensation in USDC. Series A, 40 employees.",
},
```

```python
(1, 1, "YandexLike — russian required, not spoken → Tier-0 language reject"),
(4, 8, "MoscowChain — EN remote RU, ~€138k/yr parsed → gates pass, LLM decides"),
(1, 2, "MoscowBank — on-site RU → per-mode geography reject"),
(4, 8, "AstanaChain — Web3 remote KZ → gates pass, LLM decides"),
```

The MoscowChain case doubles as the **monthly×12 rule check**: 1,000,000
RUB/month must parse to `comp_annual_eur ≈ 137931`, not ≈ 11494.
FELFEL and all prior cases stay in band.

---

## Acceptance criteria

- [ ] `scrapers/boards/hh_network.py` exists, auto-discovered, togglable in
      Settings like other boards (`scraper.hh_network.enabled`).
- [ ] A live fetch against area 113 with `text=product manager` returns
      mapped `JobPosting`s with descriptions, RUR salaries as text, and a
      work-mode hint of `remote`/`unknown` only.
- [ ] Area IDs verified against `GET /areas` and corrected in `HH_AREAS`.
- [ ] Latin-title counter appears in run logs.
- [ ] `_VALID_LANGUAGES` contains `russian`, `turkish`; both prompts updated;
      the YandexLike mock is Tier-0 rejected at score 1.
- [ ] `russia_cis` exists in both prompts + JSON enums; europe definition
      excludes Russia/CIS; tracker geo filters list `russia_cis`.
- [ ] RUB/RUR normalization + monthly×12 rule present; MoscowChain parses to
      `comp_annual_eur ≈ 137931`.
- [ ] Summaries of Russian-language descriptions come out in English (spot-
      check 5 live jobs).
- [ ] `python score.py --mock --profile unified_jc` — all cases in band.
- [ ] No changes outside `scrapers/boards/hh_network.py`, `scorer.py`
      (prompts + `_VALID_LANGUAGES`), tracker geo-zone option lists, and
      `score.py` (mocks). `profiles.py` untouched.

---

## Suggested phase order for `/speckit-tasks`

1. `scorer.py` — Part B vocabulary amendments (independent, ship first).
2. `score.py` — four mock cases (with the mock-profile construction note).
3. `scrapers/boards/hh_network.py` — scraper, area verification, counter.
4. Deploy; then Part C prose edits in Settings on Live (operator).
5. First-run checklist: VPN egress check, area IDs, counter reading, manual
   extraction spot-check of 5 Russian postings, digest sanity.
