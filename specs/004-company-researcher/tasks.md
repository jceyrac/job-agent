# Tasks 004 — Company Researcher

## Phase 0 — DB migration
- [x] Read `storage.py` — identify migration pattern
- [x] Add `monitoring_status` column (default `unmonitored`)
- [x] Add `scraping_method`, `research_notes`, `research_confidence`, `researched_at` columns
- [x] Add `get_watch_pending_companies()` to `JobStorage`
- [x] Add `update_company_research(company_id, result)` with status transition logic
- [x] Add `set_monitoring_status(company_id, status)` to `JobStorage`
- [x] Verify migration runs cleanly on existing DB

## Phase 1 — Heuristic detection
- [x] Create `company_researcher.py` with `ResearchResult` dataclass
- [x] `_fetch_page(url)` — requests + curl_cffi fallback, 10s timeout, browser UA
- [x] `_find_careers_link(html, base_url)` — href scan for careers/jobs/join keywords
- [x] `_detect_ats(html, url)` — regex for all 10 ATS signatures
- [x] `research_company(name, url)` — full algorithm steps 1-5
- [x] Smoke: Dfinity → Greenhouse ✅, Impossible Cloud → Lever ✅

## Phase 2 — LLM fallback
- [x] Import `_call_groq_fallback_chain`, `_call_deepseek` from `scorer.py`
- [x] `_classify_with_llm(name, url, html_snippet)` → dict
- [x] LLM cascade: llama-3.1-8b-instant → llama-3.3-70b-versatile → deepseek-chat
- [x] Fallback to method=manual on LLM failure
- [x] JSON parse + validate response fields

## Phase 3 — CLI + batch
- [x] `update_company_from_research(db, company_id, result)` — DB write + status transition
- [x] `research_all_pending(db)` — loop with 1s delay
- [x] CLI: `argparse` with `company_name`, `--url`, `--all`, `--save`
- [x] Formatted table output

## Phase 4 — Smoke tests
- [x] `python company_researcher.py "WalletConnect" --url https://walletconnect.com` → Workable ✅
- [x] `python company_researcher.py "Impossible Cloud" --url https://impossiblecloud.com` → Lever ✅
- [x] `python company_researcher.py "Arrakis Finance" --url https://arrakis.finance` → none ✅
- [x] `--all --save` on watch_pending companies — no crash
- [x] DB: `researched_at` set, `monitoring_status` transitioned correctly
