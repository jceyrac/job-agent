# Build `prepare.py` — application prep module for job_agent

## How to use this file

Run from the `job_agent` root:

```bash
claude
> Please read BUILD_PREPARE.md and execute it end-to-end. Ask me before making any architectural choice that isn't already specified here.
```

(Or run via `./claude-deepseek.sh` if you want Claude Code itself to run on DeepSeek inference.)

---

## Context

You're working in `~/AI-Suite/job_agent` — a multi-profile job scraping + scoring pipeline that already exists. The user (Jérôme) built it. Today, scoring runs on **Groq** (`llama-3.3-70b-versatile` / `llama-4-scout`) with **DeepSeek** as fallback. Both are configured in `.env` (`GROQ_APIKEY`, `DEEPSEEK_API_KEY`). The DeepSeek default Jérôme uses today is **`deepseek-v4-pro`** (see `claude-deepseek.sh`). Do not hardcode an older DeepSeek model ID like `deepseek-chat` — if you find one still referenced in `scorer.py`, update it as part of this work after confirming `deepseek-v4-pro` resolves successfully against the API key in `.env` with a tiny test call.

**Gemini has been removed from the stack** — it requires a credit card. Do not reintroduce Gemini under any circumstances, even as a comment or optional code path.

Current pipeline: `scrape.py → score.py --extract → score.py --profile <id> → tracker.py`. Status flows `new → queued → ready → applied → rejected → archived`. We're adding the missing piece: when a job hits `ready`, an LLM drafts the full application package using cheap models, so the user stops spending Claude Sonnet/Opus quota on routine drafting.

## Goal

Build `prepare.py` — a CLI that, given a `job_id` (and optional `profile_id`), generates:

1. **A tailored cover letter** in the JD's language, 250–350 words, no boilerplate.
2. **A CV bullet selection / re-ordering** against the JD — which existing bullets to surface, in what order, and which to omit.
3. **Company research notes** — what the company does, the role's likely stakes, why Jérôme is a fit (grounded only in the JD text — no web fetch in v1).
4. **Drafts for common screening questions**: "Why this company?", "Why are you a fit?", salary expectation framing, notice period framing.

Outputs land in **both**:
- The `job_applications` SQLite table.
- A markdown file at `outputs/applications/<job_id>__<company_slug>__<title_slug>.md`.

## Required reading before you write any code

Read in order:

1. `README.md` — architecture and CLI conventions.
2. `CONTEXT.md` — additional design context the user wrote.
3. `scorer.py` — **this is the pattern to mirror**: Groq → DeepSeek fallback chain, structured prompting, JSON parsing, retry/rate-limit handling, `extracted_by` / `scored_by` tracking. Reuse its primitives rather than reimplementing.
4. `storage.py` — DB layer. Specifically locate the `job_applications` table schema and any existing read/write helpers for it. The README references columns like `application_analysis` and `cover_letter` — confirm the actual schema before designing your inserts.
5. `profiles.py` — how `scoring_context` is structured per profile.
6. `models.py` — `JobPosting` dataclass.
7. `score.py` — CLI patterns: arg parsing, profile loading, idempotency, `--limit`, `--rescore`, `--mock`. Match this style.
8. `tracker.py` — only to confirm whether it already renders `job_applications` content when status is `ready`. If yes, don't touch it.

Then **list the contents of `~/Nextcloud/Documents/01 Job/00 CV+Motiv/CV/EN/Adresse Suisse/`** — Jérôme's CV materials live there. Read 1–2 existing cover letters from that folder (or its subfolders) to learn his voice and tone before drafting any prompts. If there's a master CV in `.docx`, read it via `python-docx` to extract the bullet library. If you can't access the folder via Read, stop and ask the user.

## Design constraints

### Model chain

Groq is primary for everything, DeepSeek is the last resort:

| Priority | Model | Provider | Suggested use |
|----------|-------|----------|---------------|
| 1 | `llama-3.3-70b-versatile` | Groq | Cover letter, screening answers — needs reasoning + voice |
| 2 | `meta-llama/llama-4-scout-17b-16e-instruct` | Groq | Bullet selection, company research notes — lighter task |
| 3 | `deepseek-v4-pro` | DeepSeek | Fallback if all Groq models hit quota |

Reuse the fallback logic already in `scorer.py`. Don't reimplement it; refactor it into a shared helper if needed.

### Language

Cover letter and screening answers must match `jobs.language_required` (already extracted by `score.py --extract`). Realistic values: `en`, `fr`, `de`, `it`. If `language_required` is `NULL`, default to `en`. Pass language as an explicit constraint in the prompt.

### Idempotency

Re-running `python prepare.py --job <id>` must not duplicate rows. Use upsert semantics, same shape as `job_scores`. Re-running without `--redo` should be a no-op for already-prepared jobs.

### CLI surface (mirror `score.py`)

```
python prepare.py --job <job_id>                    # auto-pick profile from job's existing scores
python prepare.py --job <job_id> --profile <id>     # explicit profile
python prepare.py --ready                           # prepare all jobs in status=ready, not yet prepared
python prepare.py --ready --limit 5
python prepare.py --job <id> --redo                 # overwrite existing application
python prepare.py --job <id> --mock                 # dry-run: print to stdout, no DB writes, no file writes
```

### Persistence

Write to `job_applications` with at minimum:

- `job_id` (FK)
- `profile_id` (the profile whose `scoring_context` was used)
- `cover_letter` (text)
- `cv_bullets_selected` (JSON: `{"bullets":[{"text","rationale"}],"omit":[…]}`)
- `company_research` (text)
- `screening_answers` (JSON: `{"why_company","why_fit","salary","notice_period"}`)
- `language` (text, ISO 2-letter)
- `prepared_by` (model name string, e.g. `llama-3.3-70b-versatile` or `tier_fallback:deepseek-chat`)
- `prepared_at` (ISO timestamp)

If the existing table is missing columns, add a migration script following the pattern of `migrate_single_status.py` / `migrate_profile_independent_tracking.py`. Migrations must be idempotent (check column existence before adding).

### Markdown output

`outputs/applications/<job_id>__<company_slug>__<title_slug>.md`. Slugify aggressively (ASCII, lowercase, dashes). Header should contain: job URL, company, title, language, model used, generated-at timestamp. Body in this order: cover letter, screening answers, CV bullets (selected + omitted with rationale), company research.

## Prompt design (for the LLM calls)

Pass to the model:

- The **full job description** (`jobs.description`).
- The **profile's `scoring_context`**.
- The **JD's extracted fields**: `industry_sector`, `work_mode`, `geo_zone`, `company_size`, `contract_type`, `summary`.
- The **2–3 sample cover letters** loaded from Jérôme's Nextcloud CV folder, as style anchors.
- The **CV bullet library** if extractable from the master CV.

For the cover letter prompt: target 250–350 words, conversational, concrete relevance to the JD. Explicitly forbid the phrases `"I am excited to apply"`, `"passionate about"`, `"dynamic team"`, `"results-driven"`, `"hit the ground running"`. Demand at least two specific references to the JD's stated responsibilities or stack.

For screening answers: each 80–150 words, conversational, specific. Same anti-cliché list applies.

For bullet selection: return strict JSON `{"bullets":[{"text":"…","rationale":"matches X requirement from JD"}],"omit":[{"text":"…","reason":"…"}]}`. Validate the JSON before persisting; on parse failure, retry once with a "respond in valid JSON only" reminder, then fall back to the next model in the chain.

## Out of scope (v1)

- No web scraping or company-website lookups. Company research is grounded in the JD text only.
- No new providers. Groq + DeepSeek only. **No Gemini.**
- No UI changes unless `tracker.py` is broken.
- No auto-submission. Output is read by Jérôme, edited, then submitted manually.

## Verification before you finish

1. Run `python prepare.py --job <some_id> --mock` against a real job from `data/jobs.db` and inspect the cover letter — does it sound like Jérôme (based on the style anchors)? Does it reference the JD specifically? If it sounds generic, iterate on the prompt before committing.
2. Run `python tests/test_storage.py`. Must still pass.
3. Add a unit test in `tests/test_prepare.py` covering: upsert idempotency, language fallback to `en` when `language_required` is NULL, and JSON-parse retry on the bullet-selection model.
4. Run `python prepare.py --ready --limit 2` end-to-end. Confirm both DB rows and markdown files were written.
5. Print a final summary: rows written, models used (with fallback counts), markdown files created, language distribution.

## When you're done

Commit on a feature branch `feat/prepare-applications` with a single tidy commit. Don't merge to `main` — Jérôme will review the diff. Update `README.md` with a "Prepare applications" section in the same style as the existing "Score unscored jobs" section.
