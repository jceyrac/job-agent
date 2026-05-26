# Build plan — Preference report (Layer 3 starter)

Two Claude Code prompts. Prompt 1 ships the analysis script; Prompt 2 wires the report into the Streamlit tracker so you don't need a terminal to read it. Prompt 2 is optional — defer until you've seen Prompt 1's output and confirmed the signal is useful.

Repo root: `/Users/jeanclaudevd/AI-Suite/job_agent`
DB path: `data/jobs.db`

**Cohort definitions (load-bearing — don't deviate)**

- **Relevant** = `job_tracking.status IN ('applied', 'rejected')`
  Reason: the user *applied* to these jobs. A rejection from the employer is still a positive relevance signal — they thought the job was worth pursuing.
- **Not relevant** = `job_tracking.status = 'archived'`
  Reason: the user explicitly marked these as not worth applying to.
- **Ignore** = everything else (`new`, `queued`, `ready`, `saved`, NULL) — no decision yet, not signal.

Today's totals (verify first thing in the prompt):
- applied: 72
- rejected: 11 → relevant cohort: 83
- archived: 348 → not-relevant cohort: 348

---

## Prompt 1 — Preference report script

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Create a new file `preference_report.py` at the repo root that generates a
markdown analysis of the user's apply/archive behavior. The report compares
the "relevant" cohort (applied + rejected) against the "not relevant"
cohort (archived) and surfaces the gaps between the LLM scoring and the
user's actual decisions.

This script is pure data analysis. NO LLM calls. NO DB writes. Read-only.

COHORTS (DON'T DEVIATE)
- relevant      = job_tracking.status IN ('applied', 'rejected')
- not_relevant  = job_tracking.status = 'archived'
- ignored (everything else) = no decision yet

OUTPUT
A markdown file at `outputs/preference_reports/preference_report_{YYYY-MM-DD}.md`
with these sections, in order:

  1. Header
     - Generation date
     - Cohort sizes (relevant, not_relevant)
     - DB totals (jobs, scored, prepared)
     - Profiles analyzed

  2. Score distribution by cohort (PER PROFILE)
     For each profile in ALL_PROFILES, a small markdown table:
       Score bucket | Relevant | Not relevant | Calibration
       ------------ | -------- | ------------ | -----------
       9-10         | N        | M            | flag if M > N
       7-8          | ...      | ...          |
       5-6          | ...      | ...          |
       0-4          | ...      | ...          |
     Calibration column flags rows where the LLM is mis-aligned:
       - "🚨 over-scored" when not_relevant count exceeds relevant count
         in the 7+ bucket
       - "👀 under-scored" when relevant has meaningful count in the 0-4
         or 5-6 bucket

  3. Categorical breakdowns
     For each of: industry_sector, work_mode, geo_zone, company_country,
     language_required, company_size — render a table showing:
       Value | Relevant | Not relevant | Relevant share
     Sort by combined count desc, limit to top 10 rows per dimension.
     Flag dimensions where the over-/under-score skew is sharp:
       - "Healthcare: 47 not_relevant / 0 relevant — strong negative signal"
       - "Web3: 24 relevant / 18 not_relevant — strong positive signal"

  4. Company hotspots
     - Companies with ≥ 2 jobs in relevant cohort → ALLOWLIST CANDIDATES.
       Print as a markdown table: company | relevant_count | not_relevant_count.
     - Companies with ≥ 3 jobs in not_relevant cohort AND 0 in relevant
       → DENYLIST CANDIDATES.
     - Cross-list any companies that appear in BOTH (i.e. you've applied
       and archived multiple times) — flag them as ambiguous.

  5. Calibration deltas (the most actionable section)
     - "Over-scored": jobs where LLM score ≥ 8 but you archived. Show the
       top 10 by score, including title, company, score, archive note.
       This list tells you what the LLM keeps recommending that you reject.
     - "Under-scored": jobs where LLM score ≤ 6 but you applied or were
       rejected. Show all of them (likely small list). Each row tells you
       what made you apply despite a low score — useful for tuning.

  6. Archive note themes
     Pull job_tracking.notes for status='archived' WHERE notes IS NOT NULL.
     Count word frequency (lowercase, strip punctuation, drop stopwords:
     english + french — "the", "a", "le", "la", etc.). Print the top 30
     words with counts. This shows recurring archive reasons in your own
     words ("location", "salary", "junior", "consultancy", etc.).

  7. Title keyword frequency deltas
     For job titles in each cohort, count single-word frequencies after
     lowercasing and dropping stopwords. For each word that appears at
     least 5 times TOTAL across both cohorts, compute:
        score = (rel_freq + 1) / (not_rel_freq + 1)
        normalized for cohort size.
     Show top 20 with score > 1.5 (positive — appears more in relevant)
     and top 20 with score < 0.67 (negative — appears more in archived).
     Skip TF-IDF, skip n-grams — keep it simple.

  8. Suggested edits to profiles.py
     A bulleted summary based on what sections 2-7 revealed. Examples:
       - "Add to excluded_sectors: healthcare (47 archives, 0 applies)"
       - "Add to denylisted_companies: <Company X, Y, Z>"
       - "Consider tightening geo_zone extraction — 78% of archived
          'global_remote' jobs are outside CET"
     Pure suggestions; the user manually decides whether to apply them.

IMPLEMENTATION

Create `preference_report.py` at the repo root. Use:
  - sqlite3 (stdlib) for queries
  - collections.Counter for frequency analysis
  - datetime for timestamps
  - os for paths
  - re for tokenization
  - argparse for CLI

Do NOT use pandas. Stdlib only — keeps the dependency surface small and
the script easy to run on any future server.

CLI:
  python preference_report.py
    → analyze all profiles, write to default output location

  python preference_report.py --profile unified_jc
    → analyze only one profile

  python preference_report.py --output path/to/report.md
    → override output path

  python preference_report.py --print
    → also print the report to stdout

SQL HINTS

Cohort query (parameterize cohort_status_list as a tuple):
  SELECT j.id, j.title, j.company, j.company_id, j.url,
         t.status, t.notes,
         COALESCE(c.industry_sector, 'other') AS industry_sector,
         COALESCE(j.work_mode, 'unknown')     AS work_mode,
         COALESCE(j.geo_zone, 'unknown')      AS geo_zone,
         COALESCE(c.company_country, 'unknown') AS company_country,
         COALESCE(j.language_required, 'unknown') AS language_required,
         COALESCE(c.company_size, 'unknown')  AS company_size
    FROM jobs j
    JOIN job_tracking t ON j.id = t.job_id
    LEFT JOIN companies c ON j.company_id = c.id
   WHERE t.status IN (?, ?, ...)

Per-profile score query:
  SELECT j.id, t.status, s.score, s.reason
    FROM jobs j
    JOIN job_tracking t ON j.id = t.job_id
    JOIN job_scores s   ON j.id = s.job_id
   WHERE s.profile_id = ?
     AND t.status IN ('applied', 'rejected', 'archived')

For section 5, sort by score DESC for the over-scored list and ASC for the
under-scored list.

STOPWORDS
Include a hard-coded list of english + french stopwords in the file (top 100
or so for each). Keep them upper- and lower-cased dedupe-safe. Example:
  STOPWORDS = {
    # english
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on",
    "by", "as", "at", "from", "is", "are", "be", "was", "were", "this",
    "that", "you", "your", "we", "our", "they", "their", "it", "its",
    "i", "me", "my", "us", "but", "not", "no", "if", "so", "do", "did",
    "have", "has", "had", "will", "would", "can", "could", "should", "more",
    "most", "such", "than", "then", "there", "here", "when", "where", "who",
    "what", "which", "how", "why", "all", "any", "some", "other", "into",
    "about", "over", "under", "out", "up", "down", "very", "just", "also",
    "or", "vs", "etc",
    # french
    "le", "la", "les", "un", "une", "des", "du", "de", "à", "au", "aux",
    "et", "ou", "mais", "donc", "or", "ni", "car", "que", "qui", "dont",
    "où", "ce", "cette", "ces", "se", "son", "sa", "ses", "leur", "leurs",
    "nous", "vous", "ils", "elles", "il", "elle", "on", "je", "tu", "moi",
    "toi", "lui", "eux", "y", "en", "ne", "pas", "plus", "moins", "très",
    "peu", "trop", "déjà", "encore", "aussi", "même", "tout", "tous",
    "toute", "toutes", "avec", "sans", "pour", "par", "sur", "sous",
    "dans", "entre", "vers", "chez", "depuis", "pendant", "avant", "après",
    "comme", "afin", "ainsi",
  }

CONSTRAINTS
- New file only — do NOT touch storage.py, score.py, scorer.py, profiles.py,
  or any tracker_views file. This is meant to be a standalone analysis tool.
- Read-only DB access. Use `conn = sqlite3.connect(DB_PATH); conn.row_factory
  = sqlite3.Row; conn.execute("PRAGMA query_only = 1")` to be defensive.
- If the cohort is empty for a profile (e.g. the user hasn't applied to
  anything scored against ch_hybrid), don't crash — emit "no data" and skip
  the profile's section.
- Numbers in tables are integers — no floats with trailing decimals like
  "23.0".
- Keep the script under 500 lines. If it grows, you're doing too much.

VERIFY
1. `python preference_report.py` runs without errors and writes a file to
   `outputs/preference_reports/preference_report_YYYY-MM-DD.md`.
2. The header reports cohort sizes that match this query:
     sqlite3 data/jobs.db "
       SELECT status, COUNT(*) FROM job_tracking GROUP BY status"
   relevant should equal applied + rejected; not_relevant should equal
   archived.
3. Section 2 (score distribution) renders one table per profile with all
   four buckets present (zeros are fine if a bucket is empty).
4. Section 4 (company hotspots) shows companies you recognize from your
   actual job search history.
5. Section 5 (calibration deltas) — open the markdown in any viewer and
   sanity-check 3 rows: each over-scored job has a high LLM score but
   status='archived'; each under-scored job has a low score but status
   in ('applied', 'rejected').
6. Section 8 (suggestions) is non-empty.
7. `python preference_report.py --profile unified_jc --print` prints only
   the unified_jc section to stdout.
```

---

## Prompt 2 — Optional follow-up: Streamlit "Preferences" page

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent

GOAL
Add a "Preferences" page to the multi-page Streamlit tracker so the user
can view the latest preference report (and regenerate it) without leaving
the browser. Defer this until the markdown report from Prompt 1 has been
reviewed and found useful.

PREREQUISITE
preference_report.py exists at the repo root and writes to
outputs/preference_reports/.

DESIGN
- New file: tracker_views/preferences.py
- Registered in tracker.py as a regular sidebar page (NOT a detail page —
  this one belongs in the nav).
- The page shows:
  1. A "Regenerate" button that runs the script in-process (import
     preference_report; preference_report.run() or similar — refactor
     the CLI entry point into a callable if needed).
  2. A profile selectbox (matches the existing active-profile pattern).
  3. The most recent report rendered as st.markdown.
  4. A list of older reports under an expander so the user can compare.

IMPLEMENTATION

Refactor preference_report.py so the work happens in a `generate_report(
profile_id: str | None, output_dir: str | None) -> str` function that
returns the path of the written file. Keep `main()` as the CLI entry point
that calls generate_report.

In tracker_views/preferences.py:

    import os, glob
    import streamlit as st
    from preference_report import generate_report
    from tracker_views.shared import ensure_db

    REPORT_DIR = "outputs/preference_reports"

    def _list_reports() -> list[str]:
        if not os.path.isdir(REPORT_DIR):
            return []
        return sorted(glob.glob(os.path.join(REPORT_DIR, "*.md")),
                      reverse=True)

    def render():
        ensure_db()
        st.title("📈 Preferences")

        with st.sidebar:
            st.markdown("### Generate")
            profile_choice = st.selectbox(
                "Profile", ["All"] + list_known_profiles(),
                key="pref_profile",
            )
            if st.button("Regenerate report"):
                with st.spinner("Analyzing apply/archive behavior…"):
                    path = generate_report(
                        profile_id=None if profile_choice == "All"
                        else profile_choice,
                        output_dir=REPORT_DIR,
                    )
                st.success(f"Wrote {os.path.basename(path)}")
                st.cache_data.clear()
                st.rerun()

        reports = _list_reports()
        if not reports:
            st.info("No reports yet. Click Regenerate in the sidebar.")
            return

        st.caption(f"Showing latest report: {os.path.basename(reports[0])}")
        with open(reports[0]) as f:
            st.markdown(f.read())

        if len(reports) > 1:
            with st.expander(f"Older reports ({len(reports) - 1})"):
                for path in reports[1:]:
                    label = os.path.basename(path)
                    if st.button(f"View {label}", key=f"v_{label}"):
                        st.session_state["pref_selected"] = path
                        st.rerun()

        # Show selected older report if any
        sel = st.session_state.get("pref_selected")
        if sel and sel != reports[0]:
            st.divider()
            st.caption(f"Viewing: {os.path.basename(sel)}")
            with open(sel) as f:
                st.markdown(f.read())

    from tracker_views.shared import is_active_page
    if is_active_page(__file__):
        render()

In tracker.py, add the page to the list (between Settings and the detail
pages):

    st.Page("tracker_views/preferences.py", title="Preferences",
            icon="📈"),

CONSTRAINTS
- generate_report must NOT print to stdout when called from Streamlit — if
  the existing CLI uses print(), gate it behind a `verbose` arg.
- Reports can grow over time. Don't try to cache them — disk reads are
  fast and st.markdown handles them fine up to ~1 MB.

VERIFY
1. streamlit run tracker.py — Preferences appears in the sidebar nav.
2. Open it — if no reports yet, the empty state shows.
3. Click Regenerate — spinner shows for a few seconds, then the report
   renders inline.
4. Regenerate a second time — older report appears under the expander.
```

---

## Run order
1 first. Read its output. If the patterns are interesting, run 2. If they
aren't, no harm done — you've still learned something about whether the
agent vision is worth investing in.
