# Spec 004 — Company Researcher

## Goal

Build `company_researcher.py` — a standalone module that, given a company name and
optional website URL, automatically discovers:

1. Whether a careers page exists
2. Which ATS platform is used (Greenhouse, Lever, Workable, Ashby, custom…)
3. The exact board URL / slug to monitor
4. A recommended `scraping_method` for integration into the pipeline

The researcher feeds the `companies` table in the DB, updating `ats_provider`,
`careers_url`, and a new `scraping_method` field.

It is callable from the CLI, from Claude Code, and eventually from a Streamlit button
in the Companies view.

---

## LLM Model Strategy

The task has two components with very different complexity:

**Step 1 — Detection (heuristic, no LLM needed)**
Fetch the careers page, look for known ATS signatures in HTML/URLs. Pure Python
pattern matching. This handles ~70% of cases.

**Step 2 — Classification (LLM, only for ambiguous cases)**
When heuristics fail (custom site, 404, JS-rendered, multiple candidates), invoke
an LLM to classify and summarize what it found.

LLM cascade — cheapest first:
1. `llama-3.1-8b-instant` via Groq (free, fast)
2. `llama-3.3-70b-versatile` via Groq (free, smarter)
3. `deepseek-chat` via DeepSeek API (cheap, fallback)
4. Opus — explicitly NOT used. If all above fail, mark `scraping_method = "manual"`

This mirrors the existing `scorer.py` pattern (`EVALUATION_MODELS` → `EXTRACTION_MODELS`
→ `_call_deepseek`).

---

## ATS Detection Signatures

Pattern matching on the fetched HTML/redirect URL:

| ATS | URL pattern | Notes |
|---|---|---|
| Greenhouse | `boards.greenhouse.io/{slug}` | Extract slug from URL |
| Lever | `jobs.lever.co/{slug}` | Extract slug from URL |
| Workable | `apply.workable.com/{slug}` | Extract slug from URL |
| Ashby | `jobs.ashbyhq.com/{slug}` | Extract slug from URL |
| Teamtailor | `{company}.teamtailor.com/jobs` | Domain-based |
| Recruitee | `{company}.recruitee.com` | Domain-based |
| Join.com | `join.com/companies/{slug}` | Used by Bity |
| BambooHR | `{company}.bamboohr.com/careers` | Domain-based |
| Rippling | `ats.rippling.com/{slug}` | |
| SmartRecruiters | `careers.smartrecruiters.com/{slug}` | |
| LinkedIn only | No dedicated careers page, jobs only on linkedin.com | Set method = "jobspy" |
| Custom | None of the above match | Set method = "custom_html" |
| None | No careers page found, 404 | Set method = "none" |

---

## Output — ResearchResult dataclass

```python
@dataclass
class ResearchResult:
    company_name: str
    careers_url: str | None        # canonical careers page URL
    ats_provider: str | None       # "greenhouse" | "lever" | "workable" | "ashby" | "teamtailor" | "linkedin" | "custom" | None
    ats_board_slug: str | None     # slug extracted from ATS URL (e.g. "impossiblecloud")
    ats_board_url: str | None      # full ATS board URL
    scraping_method: str           # "greenhouse" | "lever" | "workable" | "ashby" | "jobspy" | "custom_html" | "manual" | "none"
    detection_method: str          # "heuristic" | "llm:groq/{model}" | "llm:deepseek" | "manual"
    notes: str | None              # free-text observations (from LLM or heuristic)
    confidence: str                # "high" | "medium" | "low"
```

---

## Module interface

```python
# Single company
result: ResearchResult = research_company("Impossible Cloud", "https://impossiblecloud.com")

# Batch — all unresearched companies in DB
results: list[ResearchResult] = research_all_unresearched(db: JobStorage)

# Update DB from result
update_company_from_research(db: JobStorage, company_id: int, result: ResearchResult)
```

---

## Algorithm

For each company:

```
1. Fetch homepage (requests + curl_cffi fallback on 403)
2. Look for <a href> containing "careers", "jobs", "work-with-us", "join-us", "hiring"
3. If found → fetch careers URL
4. Scan HTML + redirect URL for ATS signatures (regex on known patterns)
5. If ATS detected → high confidence, return result
6. If no clear ATS but page exists → invoke LLM with page URL + HTML snippet (500 chars)
7. If LLM unavailable → mark confidence=low, method=manual
8. If homepage 404 or no careers link found → method=none
```

**LLM prompt (Step 6):**
```
Given this information about a company's careers page, determine:
1. What ATS platform (if any) is being used
2. The board URL or slug if detectable
3. How to best scrape this company's jobs

Company: {name}
Careers URL: {url}
Page HTML snippet: {html[:500]}

Return ONLY JSON:
{
  "ats_provider": "<name or null>",
  "ats_board_slug": "<slug or null>",
  "ats_board_url": "<full URL or null>",
  "scraping_method": "<greenhouse|lever|workable|ashby|jobspy|custom_html|manual|none>",
  "notes": "<one sentence>",
  "confidence": "<high|medium|low>"
}
```

---

## CLI usage

```bash
# Research a single company (prints result, updates DB if --save)
python company_researcher.py "Impossible Cloud" --url https://impossiblecloud.com --save

# Research all companies in DB with no ats_provider set
python company_researcher.py --all --save

# Dry run (no DB write)
python company_researcher.py --all
```

---

## DB changes required

Add to `companies` table (migration in `storage.py`):

```sql
ALTER TABLE companies ADD COLUMN scraping_method TEXT;
ALTER TABLE companies ADD COLUMN research_notes TEXT;
ALTER TABLE companies ADD COLUMN research_confidence TEXT;
ALTER TABLE companies ADD COLUMN researched_at TEXT;
```

Add to `JobStorage`:
```python
def update_company_research(self, company_id: int, result: ResearchResult) -> None: ...
def get_unresearched_companies(self) -> list[dict]: ...  # WHERE researched_at IS NULL
```

---

## Files to create/modify

- **Create** `company_researcher.py` at project root
- **Modify** `storage.py` — add 4 columns + migration + 2 methods
- **Do NOT** modify `scorer.py`, `scrape.py`, `main.py`, any scraper

---

## Non-goals

- No Streamlit UI (Streamlit button is a future stretch — call the module directly for now)
- No parallelism (sequential, polite delays between requests)
- No JavaScript rendering (Playwright/Selenium out of scope — mark as custom_html if JS-only)
- No Opus usage under any circumstance
- Do not modify any existing scraper
