# Build plan — Phase 0: one source of truth for search inputs (de-personalization, no behavior change)

Four Claude Code prompts. Run in order.

Repo root: `/Users/jeanclaudevd/AI-Suite/job_agent`
DB path: `data/jobs.db`

## Why this exists

Other people want to run this tool for their own search. Before any UI work,
the search inputs that are currently **hardcoded and scattered** must be
relocated so a single object drives them. Today the same conceptual thing —
"what am I searching for" — lives in at least three disconnected places:

1. `scrape.py` `main()` — a hardcoded `JobFilter(titles=[...], exclude=[...])`
   ("Universal PM/PO filter — no profile dependency").
2. Each scraper's own module constants — `linkedin.py` (`SEARCH_TERMS_LINKEDIN`,
   `LINKEDIN_LOCATIONS`), `indeed.py` (`SEARCH_TERMS_INDEED`, `INDEED_COUNTRIES`),
   `greenhouse.py` (`CRYPTO_WEB3_BOARDS`).
3. The active `SearchProfile` (`pre_filter`, `scoring_context`) — already
   profile-driven, runs post-scrape in `score.py`.

Plus the CV is hardcoded twice in `prepare.py`: the `CV_BULLETS` literal list and
the two `~/Nextcloud/...` `.docx` paths in `_load_cv_bullets()`.

## Decisions locked in

1. **The active `SearchProfile` becomes the single source of truth for search
   inputs.** Every scraper reads its search terms / locations / boards from the
   active profile. Module constants survive only as fallback defaults (used when
   a profile leaves a field empty).
2. **Zero behavior change for the current user.** `UNIFIED_JC`'s new fields are
   populated with the *exact* values that are hardcoded today, so a run produces
   the same search net as before. This is a refactor, not a feature.
3. **No UI in this phase.** No Streamlit changes. The Settings UI that edits
   these fields is Phase 1.
4. **`get_active_profile()` still returns the code object.** Phase 0 does NOT
   make the active profile load from the DB. It only relocates constants into
   that object and serializes them. DB-driven profile loading is Phase 1.
5. **The committed repo must contain no personal CV data.** The real bullets
   move to a local gitignored file; the repo ships a neutral sample.
6. **Scraper enable/disable is already done.** `BaseScraper.is_enabled()`
   already reads `scraper.<name>.enabled` from the `config` table. Do NOT
   reimplement it. Leave it alone.

The guiding idea: after Phase 0, changing *what* the tool searches for is a
matter of editing one profile object's fields — never editing `scrape.py` or a
scraper module.

---

## Prompt 1 — Extend `SearchProfile` with search inputs (profiles.py)

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Add the scrape-time / search-time inputs to the SearchProfile dataclass and
populate UNIFIED_JC with values byte-identical to what is hardcoded today, so
no run behavior changes. Serialize the new fields so the persisted profile is
complete. Do NOT change get_active_profile() — it still returns the code object.

INVESTIGATION FIRST
1. Read profiles.py — the SearchProfile dataclass, to_criteria_dict(), the
   UNIFIED_JC definition, and the ACTIVE_PROFILE / ALL_PROFILES / DEFAULT_PROFILE_ID
   block at the bottom.
2. Read scrape.py — copy the EXACT titles, exclude list, and remote_or_hybrid
   value from the hardcoded JobFilter in main().
3. Read scrapers/linkedin.py — copy SEARCH_TERMS_LINKEDIN and LINKEDIN_LOCATIONS verbatim.
4. Read scrapers/indeed.py — copy SEARCH_TERMS_INDEED and INDEED_COUNTRIES verbatim.
5. Read scrapers/greenhouse.py — note the CRYPTO_WEB3_BOARDS list (do not paste it
   here; you will reference it in Prompt 3).
6. Read storage.py upsert_profile() and get_all_profiles() to confirm criteria is
   stored as a JSON blob built from to_criteria_dict().

CHANGES TO profiles.py

Add these fields to the SearchProfile dataclass (all with empty/default factories
so existing dormant profiles WEB3_REMOTE and CH_HYBRID remain valid without edits):

    # ── Search inputs (relocated from scrape.py + scraper modules) ──────────
    # When a list is empty, the consuming scraper falls back to its module
    # constant. Populate these to make the active profile the source of truth.
    scrape_titles: list[str] = field(default_factory=list)      # broad post-fetch title net (scrape.py JobFilter.titles)
    scrape_exclude: list[str] = field(default_factory=list)     # post-fetch exclusions (scrape.py JobFilter.exclude)
    scrape_remote_or_hybrid: bool = False                       # scrape.py JobFilter.remote_or_hybrid
    search_query_titles: list[str] = field(default_factory=list)  # queries sent to jobspy (LinkedIn/Indeed)
    search_locations: list[str] = field(default_factory=list)     # location display names for jobspy
    greenhouse_boards: list[str] = field(default_factory=list)    # Greenhouse board tokens

Populate UNIFIED_JC with the EXACT current values (copy from the files you read):

    scrape_titles=[
        "product manager", "head of product", "cpo", "vp product",
        "product owner", "product lead", "product engineer", "project manager",
    ],
    scrape_exclude=["junior", "intern", "stage", "apprentice"],
    scrape_remote_or_hybrid=False,
    search_query_titles=[
        "product manager", "product owner", "head of product",
        "product lead", "product director", "senior product",
        "lead product manager",
    ],
    search_locations=[
        "Switzerland", "France", "United Kingdom", "Netherlands", "Spain",
        "Portugal", "Austria", "Belgium", "Ireland", "Italy", "Germany",
        "Czechia", "Hungary", "Türkiye",
    ],
    greenhouse_boards=[],   # leave EMPTY here; Prompt 3 moves the real list in

NOTE on search_locations vs Indeed: LinkedIn uses these display names directly.
Indeed needs lowercase country slugs with "United Kingdom" → "uk". Do NOT add a
separate indeed list — Prompt 3 maps display names to Indeed slugs inside the
Indeed scraper. Confirm that lowercasing this exact list and mapping
"United Kingdom"→"uk" reproduces the current INDEED_COUNTRIES list
["switzerland","france","uk","netherlands","spain","portugal","austria",
"belgium","ireland","italy","czechia","germany","hungary","türkiye"] — it must.

Extend to_criteria_dict() to include all six new fields so upsert_profile()
persists a complete profile.

Add a from-criteria helper for Phase 1 forward-prep (do NOT wire it into
get_active_profile() yet):

    @classmethod
    def from_criteria(cls, id: str, name: str, criteria: dict) -> "SearchProfile":
        """Build a profile from a persisted criteria dict. Forward-prep for the
        Phase-1 Settings UI; not used by get_active_profile() in Phase 0."""
        # Map every key in criteria back onto the dataclass fields, falling back
        # to dataclass defaults for any missing key.

Do NOT touch ACTIVE_PROFILE, ALL_PROFILES, DEFAULT_PROFILE_ID, or
get_active_profile().

DONE WHEN
- `python -c "from profiles import get_active_profile; p=get_active_profile();
  print(len(p.scrape_titles), len(p.search_query_titles), len(p.search_locations))"`
  prints `8 7 14`.
- `python -c "from profiles import get_active_profile; import json;
  print(json.dumps(get_active_profile().to_criteria_dict()))"` runs and the JSON
  contains scrape_titles, scrape_exclude, scrape_remote_or_hybrid,
  search_query_titles, search_locations, greenhouse_boards.
- WEB3_REMOTE and CH_HYBRID still import without error (they use the new defaults).
```

---

## Prompt 2 — Build `scrape.py`'s JobFilter from the active profile

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Replace the hardcoded JobFilter in scrape.py main() with one built from the
active profile's new fields. The resulting JobFilter must be byte-identical to
the current one when running under UNIFIED_JC.

INVESTIGATION FIRST
1. Read scrape.py main() — the hardcoded JobFilter(titles=..., exclude=...,
   remote_or_hybrid=False) block and the comment above it.
2. Confirm models.py JobFilter has fields: titles, exclude, remote_or_hybrid.
3. Note that get_active_profile() is already imported in scrape.py.

CHANGES TO scrape.py
Replace the hardcoded JobFilter construction with:

    profile = get_active_profile()
    job_filter = JobFilter(
        titles=profile.scrape_titles,
        exclude=profile.scrape_exclude,
        remote_or_hybrid=profile.scrape_remote_or_hybrid,
    )

Keep the explanatory comment but update it to say the values now come from the
active profile, not a hardcoded universal filter.

Do NOT change anything else in scrape.py (dedup, DB writes, logging untouched).

DONE WHEN
- A short assertion script confirms equivalence to the pre-refactor values:
    from profiles import get_active_profile
    from models import JobFilter
    p = get_active_profile()
    jf = JobFilter(titles=p.scrape_titles, exclude=p.scrape_exclude,
                   remote_or_hybrid=p.scrape_remote_or_hybrid)
    assert jf.titles == ["product manager","head of product","cpo","vp product",
        "product owner","product lead","product engineer","project manager"]
    assert jf.exclude == ["junior","intern","stage","apprentice"]
    assert jf.remote_or_hybrid is False
    print("scrape.py JobFilter equivalence OK")
- `python scrape.py` still runs end-to-end (a live smoke run is fine; counts will
  vary run-to-run, but the "Scrapers found" line and per-source fetch logs appear
  as before).
```

---

## Prompt 3 — Make scrapers read search config from the active profile

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
linkedin.py, indeed.py, and greenhouse.py must read their search terms /
locations / boards from the active profile, falling back to their existing
module constant only when the profile field is empty. Behavior under UNIFIED_JC
must be identical to today.

INVESTIGATION FIRST
1. Read scrapers/linkedin.py — SEARCH_TERMS_LINKEDIN, LINKEDIN_LOCATIONS, and the
   per-location results_wanted logic (25 for Switzerland, else 15). Leave the
   results_wanted logic alone — it is a scraper internal, not a user search input.
2. Read scrapers/indeed.py — SEARCH_TERMS_INDEED, INDEED_COUNTRIES, and the
   results_wanted logic (50 for switzerland, else 25). Leave results_wanted alone.
3. Read scrapers/greenhouse.py — CRYPTO_WEB3_BOARDS and every place it is
   referenced inside fetch() and the summary prints.
4. Note that none of these currently import from profiles.

PATTERN (apply to all three)
Keep the existing module constant as the fallback default. At the top of fetch(),
resolve the effective list from the active profile:

    from profiles import get_active_profile
    _p = get_active_profile()
    terms = _p.search_query_titles or SEARCH_TERMS_LINKEDIN   # per scraper
    locations = _p.search_locations or LINKEDIN_LOCATIONS     # linkedin only
    boards = _p.greenhouse_boards or CRYPTO_WEB3_BOARDS        # greenhouse only

Then use the resolved local variable everywhere the module constant was used —
do NOT delete the module constants; they remain the fallback.

CHANGES TO scrapers/linkedin.py
- Resolve `terms` from profile.search_query_titles (fallback SEARCH_TERMS_LINKEDIN).
- Resolve `locations` from profile.search_locations (fallback LINKEDIN_LOCATIONS).
- Replace the two for-loops to iterate `terms` and `locations`.

CHANGES TO scrapers/indeed.py
- Resolve `terms` from profile.search_query_titles (fallback SEARCH_TERMS_INDEED).
- Indeed needs country slugs, not display names. Add a mapping helper:

      def _to_indeed_slug(name: str) -> str:
          # Indeed's country_indeed wants lowercase names; "United Kingdom" → "uk".
          special = {"united kingdom": "uk"}
          key = name.strip().lower()
          return special.get(key, key)

  Build the country list from the profile when present:
      raw_locs = get_active_profile().search_locations
      countries = [_to_indeed_slug(n) for n in raw_locs] if raw_locs else INDEED_COUNTRIES
  Then iterate `countries` instead of INDEED_COUNTRIES.
- IMPORTANT: assert this reproduces the current list exactly. With the UNIFIED_JC
  search_locations, [_to_indeed_slug(n) for n in raw_locs] must equal
  INDEED_COUNTRIES. Verify before finishing.

CHANGES TO scrapers/greenhouse.py
- First move the real board list OUT of UNIFIED_JC-empty state: in profiles.py set
  UNIFIED_JC.greenhouse_boards to the CURRENT CRYPTO_WEB3_BOARDS contents (copy the
  full list verbatim from greenhouse.py into the profile field). Keep
  CRYPTO_WEB3_BOARDS defined in greenhouse.py as the fallback.
- In fetch(), resolve `boards = get_active_profile().greenhouse_boards or
  CRYPTO_WEB3_BOARDS` and use `boards` in the loop and in the summary prints
  (replace `len(CRYPTO_WEB3_BOARDS)` with `len(boards)`).

DONE WHEN
- Equivalence assertions pass (run as a script):
    from profiles import get_active_profile
    from scrapers.linkedin import SEARCH_TERMS_LINKEDIN, LINKEDIN_LOCATIONS
    from scrapers.indeed import SEARCH_TERMS_INDEED, INDEED_COUNTRIES, _to_indeed_slug
    from scrapers.greenhouse import CRYPTO_WEB3_BOARDS
    p = get_active_profile()
    assert (p.search_query_titles or SEARCH_TERMS_LINKEDIN) == SEARCH_TERMS_LINKEDIN
    assert (p.search_locations or LINKEDIN_LOCATIONS) == LINKEDIN_LOCATIONS
    assert [_to_indeed_slug(n) for n in p.search_locations] == INDEED_COUNTRIES
    assert (p.greenhouse_boards or CRYPTO_WEB3_BOARDS) == CRYPTO_WEB3_BOARDS
    print("scraper search-config equivalence OK")
- `python -c "from scrapers.greenhouse import GreenhouseScraper"` and the same for
  linkedin/indeed import without error.
```

---

## Prompt 4 — De-personalize the CV in `prepare.py`

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Remove personal CV data from the committed code. The CV path becomes a config
value; the real bullets move to a local gitignored file; the repo ships a neutral
sample. The current user's behavior is preserved because their local file holds
their real bullets.

INVESTIGATION FIRST
1. Read prepare.py — the CV_BULLETS literal list, the _load_cv_bullets() function,
   and the two hardcoded ~/Nextcloud/... docx paths inside it.
2. Confirm storage.py has get_config(key, default) and set_config(key, value).
3. Read .gitignore.

CHANGES — new local data files
- Create data/cv_bullets.txt and paste the CURRENT CV_BULLETS contents into it,
  one bullet per line (no leading "- "). This is the current user's real data and
  must be gitignored.
- Create data/cv_bullets.sample.txt with 4–6 NEUTRAL placeholder bullets (generic
  "Senior Product Manager with N years..." style, no real employer names). This is
  committed so new users have a template.

CHANGES TO .gitignore
Add:
    data/cv_bullets.txt
    data/cv_master.docx

CHANGES TO prepare.py
- Delete the CV_BULLETS literal list. Replace _load_cv_bullets() so it resolves in
  this order:
    1. A docx path from config: db.get_config("cv.master_path"). If set and the
       file exists, extract bullets from it (reuse the existing python-docx
       extraction logic — paragraphs/cells with len > 40, capped at 50).
    2. Else, if data/cv_bullets.txt exists, read it (one bullet per line).
    3. Else, read data/cv_bullets.sample.txt and print a one-line warning that the
       tool is using sample bullets and the user should set cv.master_path or
       populate data/cv_bullets.txt.
  _load_cv_bullets() must take a storage/db handle (or construct one) so it can read
  config — follow how other functions in prepare.py obtain JobStorage.
- Remove the two hardcoded ~/Nextcloud/... paths entirely. The docx path now comes
  ONLY from config.

PRESERVE CURRENT-USER BEHAVIOR
- Set the config so this user's existing CV is used:
    python -c "from storage import JobStorage; db=JobStorage('data/jobs.db'); \
      db.set_config('cv.master_path', '/Users/jeanclaudevd/Nextcloud/Documents/01 Job/00 CV+Motiv/CV/EN/Adresse Suisse/OLD/CV_Generalist_Jérôme_Ceyrac _EN Suisse v0.1.docx')"
  (Confirm the path exists first; if it does not, leave cv.master_path unset and
  rely on data/cv_bullets.txt, which now holds the real bullets.)

DONE WHEN
- grep -rn "Nextcloud" prepare.py returns nothing.
- grep -rn "Powens\|Allianz\|Accenture\|GENIAC\|Vaudoise" prepare.py returns
  nothing (personal data is gone from code).
- prepare.py imports and _load_cv_bullets() returns the real bullets on this
  machine (via config path or data/cv_bullets.txt), and returns the sample bullets
  with a warning on a fresh checkout where neither is present.
```

---

## Acceptance criteria (whole phase)

- [ ] Search inputs (titles, exclude, jobspy terms, locations, greenhouse boards)
      are read from `get_active_profile()`, not from `scrape.py` or scraper modules.
- [ ] All four equivalence assertions in Prompts 1–3 pass — the effective search
      inputs under `UNIFIED_JC` are byte-identical to the pre-refactor constants.
- [ ] `python scrape.py` runs end-to-end with the same "Scrapers found" set and
      per-source logs as before.
- [ ] FELFEL acceptance test still behaves: re-score the FELFEL job
      (`ch.indeed.com/viewjob?jk=f7e16c8a6a3d7af7`, or its row if already in the DB)
      and confirm it lands in its expected band under `UNIFIED_JC`. No scoring code
      changed, so this is a guardrail against accidental pipeline breakage, not a
      recalibration.
- [ ] The committed repo contains no personal CV data: `git grep -n "Nextcloud"`
      and `git grep -n "Powens\|Allianz\|Accenture"` return nothing.
- [ ] `data/cv_bullets.txt` and `data/cv_master.docx` are gitignored;
      `data/cv_bullets.sample.txt` is committed.
- [ ] `git status` shows no unintended changes to `storage.py`, `models.py`
      (other than the JobFilter usage being unchanged), `tracker.py`, or any
      Streamlit view.

## Explicit non-goals (do not build in Phase 0)

- No Streamlit / Settings UI changes — editing these fields in the UI is Phase 1.
- `get_active_profile()` does NOT start loading from the DB — code object stays
  the source. `from_criteria()` is added but left unwired.
- No new scraper enable/disable mechanism — `BaseScraper.is_enabled()` already
  exists; leave it untouched.
- No onboarding wizard, no scoring_context generation — that is Phase 2.
- No changes to the dormant `WEB3_REMOTE` / `CH_HYBRID` profiles beyond inheriting
  the new field defaults.
- Do not unify `scrape_titles` with `pre_filter["title_contains"]`; they are
  different stages (scrape-time net vs pre-LLM filter) and must stay separate.

## One-line summary for the commit

> refactor: relocate scattered search inputs into the active SearchProfile and
> move CV data out of committed code; no behavior change under UNIFIED_JC.
