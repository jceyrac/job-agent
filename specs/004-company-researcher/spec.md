# Spec 004 — Company Researcher

## Goal

Build `company_researcher.py` — a standalone module that, given a company name and
optional website URL, automatically discovers:

1. Whether a careers page exists
2. Which ATS platform is used (Greenhouse, Lever, Workable, Ashby, custom…)
3. The exact board URL / slug to monitor
4. A recommended `scraping_method` for integration into the pipeline

The researcher is triggered on companies with `monitoring_status = 'watch_pending'`
and transitions them to `watch_ready` (ATS/scraper found) or leaves them at
`watch_pending` with notes when nothing actionable is found.

It is callable from the CLI, from Claude Code, and eventually from a Streamlit button
in the Companies view (spec 005).

---

## LLM Model Strategy

The task has two components with very different complexity:

**Step 1 — Detection (heuristic, no LLM needed)**
Fetch the careers page, look for known ATS signatures in HTML/URLs. Pure Python
pattern matching. This handles ~70% of cases.

**Step 2 — Classification (LLM, only for ambiguous cases)**
When heuristics fail (custom site, 404, JS-rendered, multiple candidates), invoke
an LLM to classify and summarize what was found.

LLM cascade — cheapest first:
1. `llama-3.1-8b-instant` via Groq (free, fast)
2. `llama-3.3-70b-versatile` via Groq (free, smarter)
3. `deepseek-chat` via DeepSeek API (cheap, fallback)
4. Opus — explicitly NOT used. If all above fail, mark `scraping_method = "manual"`,
   leave `monitoring_status = 'watch_pending'`, add a note.

Import `_call_groq_fallback_chain` and `_call_deepseek` directly from `scorer.py`.
Do not duplicate the retry/backoff logic.

---

## DB changes (storage.py)

### New columns on `companies` table

```sql
ALTER TABLE companies ADD COLUMN monitoring_status TEXT NOT NULL DEFAULT 'unmonitored';
ALTER TABLE companies ADD COLUMN scraping_method TEXT;
ALTER TABLE companies ADD COLUMN research_notes TEXT;
ALTER TABLE companies ADD COLUMN research_confidence TEXT;
ALTER TABLE companies ADD COLUMN researched_at TEXT;
```

Follow the existing migration pattern in `storage.py` (backfill guard on `COUNT(*)`
or dedicated `migrations` table — use whichever pattern is already in the codebase).

### Status enum (enforced in Python, not SQL)

```
unmonitored   — default; no plan to monitor
watch_pending — user wants monitoring; research not yet done or ATS not yet found
watch_ready   — ATS/scraper identified, not yet active
watching      — active monitoring (cron processes these)
```

### New methods on JobStorage

```python
def get_watch_pending_companies(self) -> list[dict]:
    """Companies with monitoring_status = 'watch_pending'."""

def update_company_research(self, company_id: int, result: "ResearchResult") -> None:
    """Write research fields and transition status to watch_ready if actionable."""

def set_monitoring_status(self, company_id: int, status: str) -> None:
    """Generic status setter — used by UI and CLI."""
```

`update_company_research` transitions status as follows:
- `scraping_method` in `("greenhouse", "lever", "workable", "ashby", "custom_html")` → `watch_ready`
- `scraping_method` in `("manual", "none", "jobspy")` → stay `watch_pending`
  (jobspy is already covered by broad scrape; manual means no automated solution)

---

## ATS Detection Signatures

Pattern matching on fetched HTML + redirect URL:

| ATS | URL pattern | Slug extraction |
|---|---|---|
| Greenhouse | `boards.greenhouse.io/{slug}` | path segment after domain |
| Lever | `jobs.lever.co/{slug}` | path segment after domain |
| Workable | `apply.workable.com/{slug}` | path segment after domain |
| Ashby | `jobs.ashbyhq.com/{slug}` | path segment after domain |
| Teamtailor | `{company}.teamtailor.com` | subdomain |
| Recruitee | `{company}.recruitee.com` | subdomain |
| Join.com | `join.com/companies/{slug}` | path segment |
| BambooHR | `{company}.bamboohr.com/careers` | subdomain |
| SmartRecruiters | `careers.smartrecruiters.com/{slug}` | path segment |
| LinkedIn only | No careers page; jobs only on linkedin.com | → method = `jobspy` |
| Custom | None of the above | → method = `custom_html` |
| None | No careers page, 404 | → method = `none` |

---

## ResearchResult dataclass

```python
@dataclass
class ResearchResult:
    company_name: str
    careers_url: str | None
    ats_provider: str | None       # "greenhouse" | "lever" | "workable" | "ashby" | "teamtailor" | "linkedin" | "custom" | None
    ats_board_slug: str | None     # slug for known ATS (e.g. "impossiblecloud")
    ats_board_url: str | None      # full ATS board URL
    scraping_method: str           # "greenhouse" | "lever" | "workable" | "ashby" | "jobspy" | "custom_html" | "manual" | "none"
    detection_method: str          # "heuristic" | "llm:groq/{model}" | "llm:deepseek" | "manual"
    notes: str | None
    confidence: str                # "high" | "medium" | "low"
```

---

## Public interface

```python
# Single company — no DB side effects
result: ResearchResult = research_company(name: str, url: str | None = None) -> ResearchResult

# Batch — all watch_pending companies in DB
results: list[ResearchResult] = research_all_pending(db: JobStorage) -> list[ResearchResult]

# Write result to DB + transition status
update_company_from_research(db: JobStorage, company_id: int, result: ResearchResult) -> None
```

---

## Algorithm (per company)

```
1. If url given: fetch homepage. Else: try https://{name}.com as guess.
2. Look for <a href> containing "careers", "jobs", "work-with-us", "join", "hiring"
3. If careers link found: fetch careers URL, follow redirects
4. Scan final URL + HTML for ATS signatures (regex)
5. If ATS match: confidence=high, return result immediately
6. If no ATS but page exists: invoke LLM with URL + HTML snippet (500 chars)
7. If LLM unavailable or no signal: confidence=low, method=manual, notes explain
8. If homepage 404 or no careers link: method=none, confidence=high
```

---

## LLM prompt (Step 6)

```
Given this careers page, determine the ATS platform and how to scrape it.

Company: {name}
Careers URL: {url}
HTML snippet: {html[:500]}

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

## CLI

```bash
# Single company — dry run (no DB write)
python company_researcher.py "Impossible Cloud" --url https://impossiblecloud.com

# Single company — write to DB
python company_researcher.py "Impossible Cloud" --url https://impossiblecloud.com --save

# Batch: all watch_pending companies in DB
python company_researcher.py --all --save

# Dry run batch (prints table, no DB write)
python company_researcher.py --all
```

Output format (table):
```
Company               | ATS         | Slug            | Method       | Confidence
Impossible Cloud      | lever       | impossiblecloud | lever        | high
WalletConnect         | workable    | walletconnect   | workable     | high
Arrakis Finance       | none        | —               | none         | high
Bitcoin Suisse        | custom      | —               | custom_html  | medium
```

---

## Files to create/modify

- **Create** `company_researcher.py` at project root
- **Modify** `storage.py` — add columns + migration + 3 new methods
- Do NOT modify `scorer.py`, `scrape.py`, `main.py`, any scraper

---

## Non-goals

- No Streamlit UI (button in Companies view is spec 005)
- No parallelism — sequential, 1s delay between requests
- No JavaScript rendering — mark JS-only sites as `custom_html`, note in `research_notes`
- No Opus under any circumstance
- Do not modify any existing scraper
