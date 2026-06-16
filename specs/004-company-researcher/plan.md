# Plan 004 — Company Researcher

## Phase 0 — DB schema migration (storage.py)

Add 4 columns to `companies` table with a `COUNT(*) = 0` guard or dedicated migration:
`scraping_method`, `research_notes`, `research_confidence`, `researched_at`.

Add `update_company_research()` and `get_unresearched_companies()` to `JobStorage`.

Read `storage.py` carefully before editing — follow existing migration pattern.

## Phase 1 — ResearchResult dataclass + heuristic detection

Create `company_researcher.py`:
1. Define `ResearchResult` dataclass
2. Implement `_fetch_page(url)` — requests with User-Agent, curl_cffi fallback on 403
3. Implement `_find_careers_link(html, base_url)` — look for href with careers/jobs keywords
4. Implement `_detect_ats(html, url)` — regex scan for all ATS signatures
5. Unit-test with a few known companies (Dfinity → Greenhouse, Impossible Cloud → Lever)

## Phase 2 — LLM fallback (Groq → DeepSeek)

When heuristics return confidence=low or no ATS detected:
1. Build LLM prompt with URL + HTML snippet
2. Call `llama-3.1-8b-instant` via Groq first (cheapest)
3. Fallback to `llama-3.3-70b-versatile` if 429 or bad JSON
4. Fallback to `deepseek-chat` if all Groq exhausted
5. If all fail → mark confidence=low, method=manual, notes="LLM unavailable"

Use the `_call_groq` / `_call_deepseek` pattern from `scorer.py` directly — import them.

## Phase 3 — CLI + DB update

1. `research_company(name, url)` → ResearchResult (no DB side effects)
2. `update_company_from_research(db, company_id, result)` → writes to DB
3. `research_all_unresearched(db)` → loops `get_unresearched_companies()`, 1s delay between
4. CLI: `argparse` with `--all`, `--save`, `--url` flags, prints result table

## Phase 4 — Smoke test

```bash
python company_researcher.py "WalletConnect" --url https://walletconnect.com --save
python company_researcher.py "Impossible Cloud" --url https://impossiblecloud.com --save
python company_researcher.py "Arrakis Finance" --url https://arrakis.finance --save
```

Expected: WalletConnect → Workable, Impossible Cloud → Lever, Arrakis → none/custom_html.
Verify DB updated correctly.
