# Build plan — Preference report v2 (Tier-0 awareness + under-scoring section)

One Claude Code prompt. Patches the existing `preference_report.py` so the next run produces decision-grade output.

Repo root: `/Users/jeanclaudevd/AI-Suite/job_agent`
DB path: `data/jobs.db`

Why v2:
- The v1 archive cohort lumps together "user clicked archive on a job they
  cared about" (real rejection signal) with "user cleaned up an already-
  Tier-0-rejected job from the inbox" (filter cleanup, not preference
  signal). On the current DB, 131 of 348 archived jobs were Tier-0
  rejected with score 0-3 — so 38% of the "negative cohort" was noise.
- v1's suggestions list re-recommends rules the profile already has
  (e.g. "add government to excluded_sectors" when it's already there).
- v1 misses the **biggest** finding in your data: **27 jobs you applied
  to were Tier-0 rejected by the LLM** — the filter is too aggressive
  on roles you genuinely want.

---

## Prompt — Patch preference_report.py for Tier-0 awareness

```
Repo: /Users/jeanclaudevd/AI-Suite/job_agent
File: preference_report.py (already exists from prior build)

GOAL
Three substantive changes:

1. Split the not-relevant cohort into "true negative" (real rejection
   signal) and "filter cleanup" (Tier-0 rejected, user just clicked
   archive). Categorical breakdowns and calibration sections should use
   only "true negative".

2. Add a NEW section titled "LLM under-scored — jobs you applied to
   despite Tier-0 rejection". This is the most actionable insight in
   the dataset and v1 doesn't surface it.

3. Cross-reference all suggestions against the CURRENT profile state so
   we don't re-recommend rules already in profiles.py.

INVESTIGATION FIRST
1. Open preference_report.py and locate:
   - The cohort definition (where applied/rejected/archived are selected)
   - The categorical breakdowns block
   - The calibration deltas block
   - The "suggested edits" block at the bottom
2. Run a one-off check to confirm scored_by encoding for Tier-0:
     python -c "import sqlite3; c=sqlite3.connect('data/jobs.db'); \
     print(c.execute(\"SELECT DISTINCT scored_by FROM job_scores \
     WHERE scored_by LIKE 'tier_0%' LIMIT 5\").fetchall())"
   Expect to see exactly 'tier_0' (no suffix). If a Tier-0 reason is
   ever appended (e.g. 'tier_0:work_mode'), make the filter robust by
   using `scored_by LIKE 'tier_0%'`.

NEW COHORT DEFINITIONS

Replace the existing two-cohort split with four buckets, all keyed by
the analyzed profile_id:

  true_positive    = status IN ('applied','rejected')
                     AND (scored_by IS NULL OR scored_by NOT LIKE 'tier_0%')
                     OR (status IN ('applied','rejected')
                         AND scored_by LIKE 'tier_0%'
                         AND score > 3)
                     -- user applied, LLM didn't Tier-0-reject

  llm_under_scored = status IN ('applied','rejected')
                     AND scored_by LIKE 'tier_0%'
                     AND score <= 3
                     -- user applied DESPITE the Tier-0 filter saying no

  true_negative    = status = 'archived'
                     AND (scored_by IS NULL
                          OR NOT (scored_by LIKE 'tier_0%' AND score <= 3))
                     -- user archived a job that the filter did NOT
                     -- already reject

  filter_cleanup   = status = 'archived'
                     AND scored_by LIKE 'tier_0%' AND score <= 3
                     -- user archived a job the filter already rejected;
                     -- this is inbox tidying, not preference signal

The "relevant" cohort for categorical breakdowns becomes:
  true_positive ∪ llm_under_scored  (i.e. all user-positive decisions)

The "not relevant" cohort for categorical breakdowns becomes:
  true_negative  (NOT filter_cleanup)

Filter_cleanup is reported in the header for transparency but is excluded
from categorical analysis.

UPDATED HEADER

The header section should now show:

  - Relevant decisions: <true_positive + llm_under_scored>
    - Genuine apply/reject: <true_positive>
    - Applied despite Tier-0 reject: <llm_under_scored>  ← if > 0, flag
  - Negative decisions: <true_negative>
  - Filter cleanup (excluded from analysis): <filter_cleanup>

NEW SECTION — "LLM UNDER-SCORED"

Insert this section between current sections 4 and 5 (after Company
hotspots, before Calibration deltas):

  ## 🚨 LLM under-scored — applied despite Tier-0 rejection

  Jobs where the Tier-0 filter rejected (score ≤ 3) but you still applied
  or got rejected. These are the strongest "your filter is wrong" signal
  in the dataset.

  Total: N jobs.

  Then a markdown table:
  | Title | Company | Score | Tier-0 reason | Status |
  |---|---|---|---|---|
  | … | … | 1 | language: german | applied |
  ...

  The "Tier-0 reason" column comes from the job_scores.reason field,
  truncated to ~50 chars. Tier-0 reasons typically follow the pattern
  "<rule>: <value>" (e.g. "language: german", "sector: pharma",
  "country: not in allowlist", "work_mode: onsite"). Extract the rule
  prefix before the colon for grouping.

  After the table, a small breakdown:
  **By rule:**
  - work_mode: 12 jobs   ← if largest, that rule is over-aggressive
  - language: 8 jobs
  - sector: 5 jobs
  - country: 2 jobs

  Then a recommendation line:
  "Consider loosening the <top rule> filter — it's blocking jobs you
  actually want. Review the table above and decide whether to remove
  specific values from the rule's blocklist."

CROSS-REFERENCE SUGGESTIONS AGAINST CURRENT PROFILE STATE

In the "Suggested edits to profiles.py" section, before emitting any
suggestion, look up the relevant field on the current SearchProfile and
SKIP suggestions for values that are already present.

  from profiles import ALL_PROFILES

  current = ALL_PROFILES.get(profile_id)
  if current is None:
      # fall back to no-cross-reference mode
      already_excluded_sectors = set()
      already_banned_countries = set()
      already_denylisted = set()
  else:
      already_excluded_sectors = set(current.excluded_sectors or [])
      already_banned_countries = set(current.banned_countries or [])
      already_denylisted = set(current.denylisted_companies or [])

Then for each suggestion, filter:
  - Don't suggest a sector already in already_excluded_sectors.
  - Don't suggest a country already in already_banned_countries.
  - Don't suggest a company already in already_denylisted.

When a suggestion is filtered out because it's already present, add a
quiet note at the END of the suggestions section:
  "*(Already in profile: <list of skipped items>)*"

This keeps the user informed without polluting the actionable list.

ALSO — SOFTEN DENYLIST SUGGESTIONS WHEN COMPANY IS IN A TARGET SECTOR

For company denylist candidates, check the company's industry_sector
(from the companies table). If the sector is NOT in
current.excluded_sectors (i.e. it's a sector the user IS targeting),
downgrade the suggestion from "denylist" to "review":

  "Review (don't auto-denylist): Crypto.com (4 archives) — web3_crypto
   is a target sector. Likely role-specific rather than company-wide.
   Inspect titles before adding to denylist."

The threshold for emitting "Denylist" (no review qualifier):
  - archive_count >= 3 AND apply_count == 0
    AND company's sector is in already_excluded_sectors
    OR company's sector is 'other' / 'unknown'

For everything else use the "Review" framing.

CONSTRAINTS
- All four cohorts must be mutually exclusive and exhaustive for the
  subset of jobs that have a tracking status in
  ('applied','rejected','archived'). Add a defensive assertion:
      assert tp + us + tn + fc == count_of_tracked_jobs_in_three_statuses
- Don't touch the SQL for the title-keyword frequency section or the
  archive-note section — those still work on the broader cohort and
  give useful signal.
- Output filename pattern stays the same:
  outputs/preference_reports/preference_report_{YYYY-MM-DD}.md
  (overwrites today's file when re-run).
- If preference_report.py was split into multiple files in v1, apply
  the changes to whichever module(s) own the cohort definitions and
  the suggestions block.

VERIFY
1. `python preference_report.py --profile unified_jc` runs without error.
2. Header shows the four-cohort breakdown. On today's DB, expect roughly:
     Genuine apply/reject: ~56
     Applied despite Tier-0 reject: ~27   ← key new metric
     Negative decisions: ~217
     Filter cleanup: ~131
3. The new "LLM under-scored" section appears between Company hotspots
   and Calibration deltas, with a populated table and a per-rule breakdown.
4. The Suggested-edits section no longer includes:
     - government, pharma, retail (already in excluded_sectors)
     - Singapore, Taiwan (already in banned_countries)
   It should have a small "*(Already in profile: ...)*" footnote listing
   the skipped items.
5. Crypto.com / Coinbase / Binance / Mercor (web3 sector) appear under
   "Review (don't auto-denylist)" rather than under direct denylist
   suggestions. Swiss Federal Administration (sector probably 'government'
   which IS excluded) stays in the direct denylist.
6. Run also against web3_remote and ch_hybrid — no regressions, sections
   render cleanly even if cohort counts are small.
```

---

## What to expect from the next report run

The post-v2 report on your current DB should:

- Surface **~27 jobs you applied to despite Tier-0 rejection** in a dedicated section, with a per-rule breakdown that tells you which Tier-0 rule is too aggressive. That's the lead worth investigating.
- Remove the noise suggestions for rules already in `unified_jc` (government / pharma / retail / Singapore / Taiwan).
- Downgrade web3-sector denylist candidates (Crypto.com, Coinbase, Binance, Mercor) to "review" — you decide per-role, not per-company.
- Leave the genuinely useful suggestions intact (e.g. companies that don't fit any target sector and you've archived 3+ times).

If the under-scoring breakdown points overwhelmingly to one Tier-0 rule (likely `work_mode` or `country`), the next move is a focused review of that rule rather than blanket profile edits.
