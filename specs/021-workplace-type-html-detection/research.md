# Research — LinkedIn Workplace-Type HTML Detection

**Created**: 2026-07-08 | **Plan**: [plan.md](plan.md) | **Status**: ⛔ Closed

## Investigation 1: Capture raw HTML from LinkedIn jobs

**Method**: Ran `jobspy.scrape_jobs(site_name='linkedin', location='Netherlands', results_wanted=30, linkedin_fetch_description=True)`. Saved raw `row["description"]` for the first 10 results as fixtures under `tests/fixtures/`.

**Result**: 30 results obtained. 10 fixtures saved (3,350–8,519 bytes each). Fixtures cover `is_remote=True` and `is_remote=False` jobs, including the MANUS™ "Product Manager" (Eindhoven Area) listing.

## Investigation 2: Locate the workplace type encoding

**Method**: Searched all 10 fixtures for structured workplace type patterns:
- `<span>Remote|Hybrid|On-site</span>` — the most likely LinkedIn pill rendering
- `aria-label="Remote|Hybrid|On-site"`
- `workplaceType` in JSON-LD / schema.org markup
- `job-criteria` / `job-workplace-type` CSS classes
- Any `<span>`, `<div>`, or `<li>` with workplace-related classes

**Result**: **Zero structured workplace elements found. 0/10.** No HTML tags of any kind exist in the fixtures.

Additional discovery: **JobSpy's `description` field for LinkedIn contains no HTML at all.** Tag counts across all 10 fixtures are `0`. The field arrives already stripped — `dataframe_to_postings`'s `re.sub(r"<[^>]+>", " ", description)` is operating on clean text. The `_extract_country_from_html` function (which mirrors the pattern this spec proposed) is therefore also operating on clean text — meaning it only catches country names in plain-text descriptions, not in structured HTML.

## Investigation 3: Quantify hit rate

**Method**: Counted fixtures with ANY workplace mention (hybrid/remote/on-site as standalone words in text).

**Result**: 4/10 (40%) have a loose workplace mention in the description body text. But these are indistinguishable from normal prose usage:

| Fixture | Workplace mentions |
|---------|-------------------|
| skydreams | "hybrid" (1x) |
| Uber | "remote" (1x) |
| BauWatch | "remote" (1x), "on site" (3x) |
| Booking.com | "hybrid" (1x) |

**MANUS has no workplace signal at all.** The word "hybrid" does not appear anywhere in the description. The LinkedIn UI badge "Hybrid + Full-time" is rendered client-side via JavaScript and is not included in the description field that JobSpy fetches.

## Investigation 4: Scope check — Indeed

**Not applicable.** Since the HTML approach is not viable for LinkedIn, the Indeed path was not investigated. Indeed listings have their own workplace type handling via `work_from_home_type` (which IS populated by JobSpy for Indeed, unlike LinkedIn).

## Decision

**Close SPEC 021.** The workplace type signal this spec sought to recover does not exist in the data stream. JobSpy's LinkedIn description field is plain text with no structured metadata.

The `unknown` fallback from the 020-followon (`is_remote + concrete city` → `work_mode="unknown"`, extractor LLM classifies from base location + description) is the correct and only viable behavior given the data available.

### If workplace type accuracy for LinkedIn becomes critical in the future

Options to revisit:
1. **Fix JobSpy upstream** — teach jobspy to extract LinkedIn's workplace badge from the job page DOM (requires changes in the jobspy library, not this repo).
2. **Extractor LLM refinement** — the current approach already has the LLM classify from base location + description. The main weakness (pre-020-followon) was the scraper asserting "remote" off noisy `is_remote` and the LLM trusting that hint. With the hint now `unknown` for these ambiguous cases, the LLM makes an independent judgment — imperfect but no longer anchored to a false signal.
