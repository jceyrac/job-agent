# SPEC 021 — LinkedIn Workplace-Type Detection from Raw HTML

> Spec for Claude Code (Dev). Read `scrapers/boards/_jobspy_helpers.py`,
> `scrapers/boards/linkedin.py`, `scorer.py` (`extract_job_fields` +
> EXTRACTION_PROMPT "## Work mode"), and `models.py` before starting.
> Depends on the 020-followon work being in place first (scraper `is_remote`
> split + extraction-prompt "trust-unless-unknown" revision). This spec
> upgrades the `unknown` fallback introduced there, for LinkedIn specifically.
> Dev only — never edit or test on Live/verva.

---

## Context — what triggered this spec

The MANUS "Product Manager" (Eindhoven Area) listing is tagged **Hybrid** on
LinkedIn but was stored as `work_mode="remote"`. Root-caused this session:

- `extract_job_fields()` overwrites the scraper's `work_mode` with the
  DeepSeek result, and the extraction prompt only ever saw
  Title / Company / Location / Base location / Description — never JobSpy's
  raw workplace signal. (Addressed in the 020-followon: the scraper hint is
  now forwarded into the prompt.)
- **Empirical finding (Claude Code, live NL LinkedIn scrape, 50 results):**
  - `work_from_home_type` is **never populated** by JobSpy for LinkedIn —
    all 50 rows returned `None`. The `"hybrid" in wfh` branch is effectively
    dead for LinkedIn.
  - `is_remote` is **noisy**: 11/50 jobs with concrete Dutch city locations
    were flagged `is_remote=True`, driving false `remote` classification.
  - The **cleaned** description text rarely contains the workplace type
    (only 1/5 sampled jobs had "Hybrid" in the stripped text).

The 020-followon split downgrades the ambiguous `is_remote + concrete city`
case to `work_mode="unknown"` (stops the false `remote`), but it **cannot
recover the true `hybrid` / `on-site` value**, because that signal isn't in
any field the scraper currently keeps or the extractor currently sees.

However, LinkedIn **does** render workplace type as a structured element
("Remote" / "Hybrid" / "On-site") on the job page, and JobSpy already fetches
that page (`linkedin_fetch_description=True` in `linkedin.py`). The signal is
present in the **raw HTML** and thrown away during the tag-strip step in
`dataframe_to_postings`. This spec recovers it — mirroring the existing
`_extract_country_from_html` pattern, which already extracts the country
from the same raw HTML before stripping.

---

## Goal

Recover LinkedIn's authoritative workplace-type signal from the **raw
(pre-strip) HTML** at scrape time and use it to set `work_mode`, so hybrid
and on-site LinkedIn jobs are classified correctly instead of falling through
to `unknown`. Once the scraper emits the correct value, the existing
forwarded hint is correct and the extractor trusts it — no extractor change
needed.

---

## Investigation required before implementation

The regex/parse **must not** be written before the real markup is confirmed.
The 1/5 hit rate on cleaned text is a warning: look at the **raw** HTML, not
the stripped text.

1. **Capture raw HTML.** Run a small LinkedIn scrape
   (`linkedin_fetch_description=True`) for a handful of jobs of *known*
   workplace type — include the MANUS listing and at least one confirmed
   Remote and one confirmed On-site — and dump the **unprocessed**
   `row["description"]` (before the `re.sub(r"<[^>]+>", " ", ...)` strip in
   `dataframe_to_postings`). Save these as fixtures.
2. **Locate the encoding.** Identify exactly where LinkedIn puts workplace
   type in that raw HTML. Likely candidates: a job-criteria `<span>`
   ("Remote"/"Hybrid"/"On-site"), an `aria-label`, or a JSON-LD /
   `NEXT_DATA`-style blob. Confirm which one is reliably present before
   writing any matcher.
3. **Quantify hit rate.** Across the sample, how often is the workplace type
   actually in the fetched HTML? If it's absent for a large share of jobs,
   that determines whether this is worth shipping or whether the
   `unknown` fallback should simply stand.
4. **Scope check — Indeed.** Confirm whether the same markup exists for the
   Indeed jobspy source or is LinkedIn-only. If Indeed differs, scope this to
   LinkedIn (`source == "LinkedIn"`), exactly as `_extract_country_from_html`
   is already gated.

Report findings before implementing. If the signal turns out not to be
reliably present in the raw HTML, stop and recommend closing this spec in
favour of the `unknown` fallback.

---

## Changes

### 1. `scrapers/boards/_jobspy_helpers.py`

- Add `_extract_workplace_from_html(html: str) -> str | None`, mirroring
  `_extract_country_from_html`. Returns exactly one of `"remote"`,
  `"hybrid"`, `"on-site"`, or `None`. Pattern to be finalised from the
  Investigation output — do not hardcode before step 2.
- In `dataframe_to_postings`, compute the HTML workplace type from
  `raw_description` **before** stripping (same place `html_country` is
  computed), LinkedIn-gated like the country extraction.
- Rework the `work_mode` block so precedence is, highest authority first:
  1. **HTML workplace type** (LinkedIn's own field) — if present, it wins.
  2. `"hybrid" in wfh` (kept for Indeed / any future source that populates
     `work_from_home_type`).
  3. `not raw_loc or "remote" in raw_loc.lower()` → `remote`.
  4. `is_remote` **with a concrete location** → `unknown`
     (the 020-followon behaviour — unchanged).
  5. else → `on-site`.
  The HTML signal specifically **overrides** the noisy `is_remote` flag.
- When the HTML type is `hybrid`, keep appending `"(Hybrid)"` to the
  `location` string as today, for digest/UI consistency.

### 2. `scorer.py` — no change

The 020-followon already forwards `Source-detected work mode` into the
extraction prompt and instructs the model to trust it unless the description
explicitly contradicts. Once the scraper emits the correct `hybrid`/`on-site`,
the hint is correct. Do **not** add a second workplace-detection path in the
extractor — one source of truth.

---

## Non-goals

- **No filtering at scrape time.** This is classification / normalisation of
  an existing field, not a gate — nothing is dropped. Constitution intact
  (scraper = wide net; scorer = all filtering).
- No change to `scorer.py` Tier-0, `_geography_for_mode`, or
  `evaluate_for_profile`.
- No detection based on the cleaned/stripped description text — raw HTML only.
- No generalisation beyond LinkedIn unless Investigation step 4 shows Indeed
  uses identical markup.
- No new model field and no new DB column — `work_mode` already exists.

---

## Acceptance criteria

- [ ] `_extract_workplace_from_html` returns `"hybrid"` for the MANUS raw-HTML
      fixture, and the correct value for the confirmed Remote and On-site
      fixtures.
- [ ] End-to-end (scrape → extract → store), the MANUS Product Manager
      (Eindhoven) listing resolves to `work_mode="hybrid"`.
- [ ] Of the 11/50 NL jobs previously false-flagged `remote`: those that are
      actually hybrid/on-site now classify correctly; genuine remotes stay
      `remote`; any with no HTML signal fall through to `unknown` (not a
      crash).
- [ ] Detection hit-rate across the fixture sample is documented in
      `research.md`.
- [ ] FELFEL regression (`https://ch.indeed.com/viewjob?jk=f7e16c8a6a3d7af7`)
      still scores 6–8 for `ch_hybrid` / `unified_jc` and 2–4 for
      `web3_remote` — unaffected (Indeed source, scoring logic untouched).
- [ ] New unit test over saved raw-HTML fixtures covering hybrid / remote /
      on-site / absent.

---

## Testing plan (Dev)

1. From a live LinkedIn scrape, save raw-HTML fixtures for known-type jobs
   (incl. MANUS) under `tests/fixtures/`.
2. Unit-test `_extract_workplace_from_html` over those fixtures.
3. Re-extract the MANUS job end-to-end; assert stored `work_mode == "hybrid"`.
4. Re-run the FELFEL regression across `unified_jc` / `ch_hybrid` and
   `web3_remote`.
5. Spot-check the NL false-remote set from the 020-followon investigation.

---

## Sequencing

Depends on the 020-followon (scraper `is_remote` split + prompt revision)
already merged — this spec upgrades the LinkedIn branch of that fallback.
Run `/speckit.plan` (reading the live source files above) then
`/speckit.tasks` before implementing. Dev Mac only; deploy to verva via the
normal `git pull + deploy.sh` path once validated.
