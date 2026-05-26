# Build plan — Auto-populate company summary + website on extraction

One Claude Code prompt. Self-contained.

Repo root: `/Users/jeanclaudevd/AI-Suite/job_agent`
DB path: `data/jobs.db`

Context:
- Companies are created at scrape time via `storage.upsert_company`, before
  any LLM call has touched the job description. So at creation time we
  only have name + maybe website from the job posting.
- The extraction step (`scorer.extract_job_fields`) already runs an LLM
  call against the full description to populate `industry_sector`,
  `company_country`, `company_size`, etc.
- We can piggy-back on that same LLM call to also produce
  `company_summary` (1-2 sentences about the company) and
  `company_website` (URL if mentioned in the description), then persist
  them on the `companies` row using the existing fill-if-NULL pattern.
- No extra LLM cost. No new scraper logic.

---

## Prompt — Add company summary + website to extraction pipeline

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
1. Extend the extraction LLM call to produce `company_summary` (1-2
   sentences) and `company_website` (URL or null) in addition to the
   existing structured fields.
2. Add a `summary` column on the `companies` table (schema change).
3. After extracting a job, update the linked company row with the new
   fields IF they are currently NULL — preserving manual edits.
4. Surface the summary on the Company detail page and (optionally) as
   a caption on Company list cards.

INVESTIGATION FIRST
1. Read scorer.py EXTRACTION_PROMPT (around line 190) and
   extract_job_fields (around line 613) — confirm current output shape
   and how the result is parsed.
2. Read models.py JobPosting — confirm field list.
3. Read storage.py upsert_company (around line 799) — note the
   fill-if-NULL update pattern for `website` and `careers_url`.
4. Read job_actions.py extract_one — note where update_job_extraction
   is called and where the company_id is available.
5. Run `python -c "import sqlite3; c=sqlite3.connect('data/jobs.db'); \
   print([r[1] for r in c.execute('PRAGMA table_info(companies)')])" `
   to list current columns. Confirm `summary` is NOT yet present.

SCHEMA CHANGE
Add a new column to the companies table. In storage.py, find the
existing `CREATE TABLE companies` schema definition AND any in-place
ALTER TABLE migrations (if storage.py runs migrations at startup, add
this one alongside them; otherwise add an idempotent migration in the
JobStorage __init__).

The column:
    summary TEXT

Pattern for idempotent migration (mirror existing ALTER TABLE adds
inside storage.py):
    try:
        conn.execute("ALTER TABLE companies ADD COLUMN summary TEXT")
    except sqlite3.OperationalError:
        pass  # already added

Run the migration once on connection setup so existing DBs pick it up.

EXTEND THE EXTRACTION PROMPT (scorer.py)
Add two fields to the JSON schema described in EXTRACTION_PROMPT:

  "company_summary": "<1-2 sentences about the company — what they do,
                      market, scale. NULL if description has no info
                      about the company itself>",
  "company_website": "<URL if explicitly mentioned in the description,
                      otherwise null>"

Place them right after `summary` in the schema block. Add a section
near the bottom of the prompt:

  ## Company summary
  Write 1-2 sentences describing the COMPANY (not the role). Cover:
  what they build / sell, their market, and approximate stage if
  inferrable. Ground every claim in the description text — do NOT
  invent facts from training knowledge. If the description doesn't
  describe the company, return null.

  ## Company website
  Look for an explicit URL in the description that points to the
  company's main site (not the job-board listing). Patterns:
  "Learn more at acme.com", "Visit https://acme.com", "Our website:
  www.acme.com", an https:// link inside the company-about section.
  Normalize to "https://<host>". If multiple URLs appear, pick the
  shortest/most generic one (acme.com over acme.com/careers/eng).
  Return null if no clear company URL is mentioned.

EXTEND models.py JobPosting
Add two optional fields:

  company_summary: str | None = None
  company_website: str | None = None

EXTEND scorer.extract_job_fields PARSING
In the parser block that maps JSON result keys to JobPosting attributes,
add:

  job.company_summary = result.get("company_summary")
  job.company_website = result.get("company_website")

Also update `_parse_extraction_result` (if it has a fixed key list) to
include the new keys with safe defaults (None for both — they're
genuinely optional).

NEW STORAGE METHOD — update_company_metadata
Add to storage.py near upsert_company:

  def update_company_metadata(self, company_id: int, *,
                              summary: str | None = None,
                              website: str | None = None) -> None:
      """Fill summary/website on a company row IF the existing values are
      NULL. Preserves manual edits. No-op if both fields are already set
      or the new values are empty."""
      if not summary and not website:
          return
      with self._conn() as conn:
          existing = conn.execute(
              "SELECT summary, website FROM companies WHERE id = ?",
              (company_id,)).fetchone()
          if not existing:
              return
          updates = []
          params = []
          if summary and not existing["summary"]:
              updates.append("summary = ?")
              params.append(summary.strip())
          if website and not existing["website"]:
              updates.append("website = ?")
              params.append(website.strip())
          if updates:
              params.append(company_id)
              conn.execute(
                  f"UPDATE companies SET {', '.join(updates)} WHERE id = ?",
                  params)

HOOK INTO THE EXTRACTION FLOW

In job_actions.extract_one, after the existing
`db.update_job_extraction(result.id, {...})` block and before
`_discover_contacts(...)`, add:

  company_id = row.get("company_id")
  if company_id is not None:
      db.update_company_metadata(
          company_id,
          summary=getattr(result, "company_summary", None),
          website=getattr(result, "company_website", None),
      )

ALSO PATCH score.py's INLINE EXTRACTION LOOPS

score.py has two extraction loops (the dedicated `_run_extraction` and
the Phase 1 block in `main()` for unextracted survivors). If Prompt 3
of the earlier refactor already routed both to extract_one, no further
change is needed. Otherwise, add the same update_company_metadata call
right after the corresponding update_job_extraction call.

UI — COMPANY DETAIL PAGE

In tracker_views/company_detail.py, render the summary right after the
title block and before the meta chips:

  if company.get("summary"):
      st.markdown(f"_{company['summary']}_")

The italics make it visually distinct from operational status.

For the website chip already present in the meta line:
  if company.get("website"):
      meta.append(f"[🌐 {company['website']}]({company['website']})")

…keep it as-is. The newly-extracted website will populate via the
existing flow.

UI — COMPANY LIST CARD (OPTIONAL POLISH)

In tracker_views/companies.py inside the card-rendering loop, below the
existing meta caption and ABOVE the last_interaction caption, add a
truncated summary preview:

  summary = (c.get("summary") or "").strip()
  if summary:
      st.caption(summary[:160] + "…" if len(summary) > 160 else summary)

Keep it short — the list view is dense; the detail page is where to see
the full sentence.

EXTEND THE SHARED COMPANIES LOADER

In tracker_views/shared.py, the load_companies wrapper calls
get_db().get_companies(...). Make sure get_companies returns the new
`summary` column. Inspect the SELECT statement in storage.get_companies
(around line 1796) and add `c.summary` to the column list. Verify
load_company_by_id similarly includes summary (it uses SELECT * so
should be automatic, but confirm).

CONSTRAINTS
- Don't backfill historical companies in this prompt. Existing companies
  will populate naturally as new jobs from them get scraped + extracted.
  A separate backfill prompt can come later if needed.
- Don't add a UI text-area to edit the company summary in this prompt —
  manual edit support is out of scope. The fill-if-NULL semantics already
  protect any direct DB edit the user makes.
- Keep the extraction prompt change minimal — adding two fields, not
  rewriting the whole prompt. Models can regress on the existing fields
  when the prompt grows too much.
- The company_website extracted should pass a basic sanity check:
  starts with http(s)://, no spaces. If it doesn't, drop it (treat as
  null) rather than save garbage.

VERIFY
1. Schema: `python -c "import sqlite3; c=sqlite3.connect('data/jobs.db'); \
   print([r[1] for r in c.execute('PRAGMA table_info(companies)')])" `
   includes 'summary'.

2. Backfill check (no data should change yet):
   `python -c "import sqlite3; c=sqlite3.connect('data/jobs.db'); \
   print(c.execute('SELECT COUNT(*) FROM companies WHERE summary IS NOT NULL').fetchone())"`
   should print 0 immediately after the migration.

3. Pick an unextracted job (extracted_at IS NULL) and run:
     python -c "from job_actions import extract_one; \
     print(extract_one('SOME_JOB_ID'))"
   Then query the linked company:
     SELECT id, name, summary, website FROM companies
     WHERE id = <company_id of that job>
   summary should be populated, website may or may not be (depends on
   description content).

4. Re-extract a job from a company that ALREADY has a summary — the
   company row should NOT change (fill-if-NULL).

5. streamlit: open a company detail page where the new summary has
   landed — see the italic sentence at the top.

6. streamlit: open the Companies list — cards for companies with
   summaries show a one-line preview; the others render unchanged.
```

---

## Run notes

Independent of the v2 preference report and the companies-filters prompt. Safe to run in any order. The schema change is idempotent — re-running the prompt is harmless.

Optional follow-up if you want full backfill across the existing 1,445 companies in one pass: write a small `backfill_company_metadata.py` script that iterates over companies missing a summary, picks their most recent extracted job, and uses that job's description as input to a fresh extraction call. Cheap on Groq's free tier; one rate-limited pass and you're caught up.