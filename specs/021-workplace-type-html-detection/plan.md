# Implementation Plan: LinkedIn Workplace-Type HTML Detection

**Branch**: N/A | **Date**: 2026-07-08 | **Spec**: [spec.md](spec.md)

**Status**: ⛔ **CLOSED — approach not viable** (see [research.md](research.md))

## Summary

SPEC 021 proposed recovering LinkedIn's workplace type (Remote/Hybrid/On-site)
from the raw HTML description that JobSpy fetches, by adding an
`_extract_workplace_from_html()` function mirroring the existing
`_extract_country_from_html()` pattern.

Empirical investigation of 10 LinkedIn fixtures from a live Dutch search
**definitively disproves the premise**: JobSpy's `description` field for
LinkedIn contains **clean text only — zero HTML tags, zero structured
metadata, zero workplace type elements**. The signal this spec aimed to
recover does not exist in the data that reaches the scraper.

The `unknown` fallback from the 020-followon (`is_remote + concrete city`
→ `work_mode="unknown"`, letting the extractor LLM classify from base
location + description) is the correct and only viable approach for
LinkedIn given the data JobSpy provides.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: jobspy (LinkedIn scraper), pandas
**Storage**: N/A — no schema changes
**Testing**: pytest, HTML fixture-based unit tests (not applicable given closure)
**Target Platform**: Dev Mac only
**Project Type**: Scraper helper function
**Performance Goals**: N/A
**Constraints**: Raw HTML only — no cleaned-text detection, no new model fields
**Scale/Scope**: LinkedIn-only, gated by `source == "LinkedIn"`

## Constitution Check

*GATE: Not applicable — spec closed before implementation.*

## Investigation Results

See [research.md](research.md) for full details.

Key findings:

1. **JobSpy's LinkedIn description is plain text**, not HTML. All 10 fixtures
   show `tags=0` — no HTML markup whatsoever.

2. **Zero structured workplace elements.** No `<span>Hybrid</span>`, no
   `aria-label`, no JSON-LD, no `job-criteria` markup. 0/10.

3. **4/10 fixtures have loose workplace mentions** (e.g. "remote" or "hybrid"
   in the job description body) — but these are indistinguishable from normal
   prose usage of those words and unreliable for classification.

4. **The MANUS job has NO workplace signal at all** — the word "hybrid"
   doesn't appear anywhere. The LinkedIn UI badge "Hybrid + Full-time" is
   rendered client-side and not included in the description field.

## Decision

**Close this spec.** The `unknown` fallback from the 020-followon is the
correct behavior for LinkedIn jobs where JobSpy doesn't populate
`work_from_home_type`. The workplace type signal this spec sought to recover
is not present in the data stream.

### Alternative considered

If workplace type accuracy for LinkedIn is critical, the options are:
- **Fix JobSpy upstream** — add a field that extracts LinkedIn's workplace
  badge from the job page (requires changes to the jobspy library).
- **LLM-only classification** — the current 020-followon approach already
  does this: when scraper emits `unknown`, the extractor LLM classifies from
  base location + description. It won't always get it right (it may say
  "on-site" when LinkedIn says "hybrid"), but it won't assert a false
  "remote" — which was the original bug.

Neither option is in scope for this spec. The LLM fallback is adequate for
now.
