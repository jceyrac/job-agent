# Tasks 004 — Company Researcher

## Phase 0 — DB migration
- [ ] Read `storage.py` — find existing migration pattern
- [ ] Add columns: `scraping_method`, `research_notes`, `research_confidence`, `researched_at` to companies table
- [ ] Add `update_company_research(company_id, result)` to `JobStorage`
- [ ] Add `get_unresearched_companies()` to `JobStorage` (WHERE researched_at IS NULL)

## Phase 1 — Heuristic detection
- [ ] Create `company_researcher.py` with `ResearchResult` dataclass
- [ ] Implement `_fetch_page(url)` — requests + curl_cffi 403 fallback, 10s timeout
- [ ] Implement `_find_careers_link(html, base_url)` — href scan for careers/jobs/join keywords
- [ ] Implement `_detect_ats(html, url)` — regex for all 10+ ATS signatures
- [ ] Implement `research_company(name, url)` — orchestrates steps 1-4 of algorithm
- [ ] Test heuristic: Dfinity → Greenhouse/dfinity, Impossible Cloud → Lever/impossiblecloud

## Phase 2 — LLM fallback
- [ ] Import `_call_groq`, `_call_deepseek` from `scorer.py`
- [ ] Build LLM prompt with URL + 500-char HTML snippet
- [ ] Implement `_classify_with_llm(name, url, html_snippet)` → dict
- [ ] LLM cascade: llama-3.1-8b-instant → llama-3.3-70b-versatile → deepseek-chat
- [ ] On LLM failure → confidence=low, method=manual
- [ ] Parse + validate LLM JSON response

## Phase 3 — CLI + batch
- [ ] Implement `update_company_from_research(db, company_id, result)`
- [ ] Implement `research_all_unresearched(db)` with 1s inter-request delay
- [ ] CLI with argparse: positional `company_name`, `--url`, `--all`, `--save`
- [ ] Print results as table (name | ats | method | confidence)

## Phase 4 — Smoke tests
- [ ] `python company_researcher.py "WalletConnect" --url https://walletconnect.com` → Workable ✅
- [ ] `python company_researcher.py "Impossible Cloud" --url https://impossiblecloud.com` → Lever ✅
- [ ] `python company_researcher.py "Arrakis Finance" --url https://arrakis.finance` → none/custom ✅
- [ ] `python company_researcher.py --all --save` on full watchlist (29 companies) — no crash
- [ ] Verify `researched_at` set in DB after `--save`
