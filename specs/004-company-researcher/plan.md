# Plan 004 — Company Researcher

## Phase 0 — DB schema migration (storage.py)

Read `storage.py` carefully — identify the existing migration pattern
(backfill guard or migrations table). Follow it exactly.

Add to `companies` table:
- `monitoring_status TEXT NOT NULL DEFAULT 'unmonitored'`
- `scraping_method TEXT`
- `research_notes TEXT`
- `research_confidence TEXT`
- `researched_at TEXT`

Add to `JobStorage`:
- `get_watch_pending_companies()` → list[dict]
- `update_company_research(company_id, result)` → None (+ status transition logic)
- `set_monitoring_status(company_id, status)` → None

## Phase 1 — ResearchResult dataclass + heuristic detection

Create `company_researcher.py`:
1. Define `ResearchResult` dataclass
2. `_fetch_page(url)` — requests with browser User-Agent, curl_cffi fallback on 403, 10s timeout
3. `_find_careers_link(html, base_url)` — BeautifulSoup or regex scan for href keywords
4. `_detect_ats(html, url)` — regex for all ATS signatures, return (provider, slug, board_url)
5. Wire into `research_company(name, url)` — steps 1-5 of algorithm

Manual smoke test: Dfinity → Greenhouse/dfinity, Impossible Cloud → Lever/impossiblecloud.

## Phase 2 — LLM fallback

When heuristics return no ATS or confidence=low:
1. Import `_call_groq_fallback_chain`, `_call_deepseek` from `scorer.py`
2. Build prompt with URL + 500-char HTML snippet
3. Call with `models=["llama-3.1-8b-instant", "llama-3.3-70b-versatile"]`
4. Fallback to `_call_deepseek` if Groq exhausted
5. Parse + validate JSON response
6. On any failure → method=manual, confidence=low, notes="LLM unavailable"

## Phase 3 — CLI + batch runner

1. `update_company_from_research(db, company_id, result)` — writes + transitions status
2. `research_all_pending(db)` — loops `get_watch_pending_companies()`, 1s delay
3. CLI via argparse: positional `company_name`, flags `--url`, `--all`, `--save`
4. Print results as formatted table

## Phase 4 — Smoke tests

```bash
python company_researcher.py "WalletConnect" --url https://walletconnect.com
python company_researcher.py "Impossible Cloud" --url https://impossiblecloud.com
python company_researcher.py "Arrakis Finance" --url https://arrakis.finance
python company_researcher.py --all --save  # requires companies in DB with watch_pending
```

Verify: status transitions correct in DB, notes populated, researched_at set.
