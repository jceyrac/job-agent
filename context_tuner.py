"""context_tuner.py — LLM-driven scoring_context proposal from calibration signals.

Read-only DB access. One LLM call. Writes a proposal Markdown file for human
review before any change is applied to the DB.
"""

import os
from datetime import date

from paths import DB_PATH
import llm
from storage import JobStorage


OUTPUT_DIR = "outputs/context_proposals"


def propose_context_update(
    profile_id: str,
    db: JobStorage,
    *,
    dry_run: bool = False,
) -> str:
    """Read calibration signals from the DB, call the LLM once to draft a
    revised scoring_context, write the proposal to outputs/context_proposals/,
    and return the output file path.

    dry_run=True: print the proposal to stdout instead of writing to disk.
    """
    from profiles import SearchProfile
    from preference_report import export_few_shot_anchors

    # ── 1. Current scoring_context from DB ─────────────────────────────────
    row = db.get_profile(profile_id)
    if not row:
        raise ValueError(f"Profile '{profile_id}' not found in DB")
    profile = SearchProfile.from_criteria(row["id"], row["name"], row["criteria"])
    current_context = profile.scoring_context.strip()

    # ── 2. Calibration signals ────────────────────────────────────────────
    with db._conn() as conn:
        conn.row_factory = None  # plain tuples for this block

        # Over-scored: archived with score >= 8, excluding Tier-0 cleanups
        over_rows = conn.execute(
            """SELECT j.title, j.company, s.score, t.notes
                 FROM jobs j
                 JOIN job_tracking t ON j.id = t.job_id
                 JOIN job_scores s   ON j.id = s.job_id
                WHERE t.status = 'archived' AND s.score >= 8
                  AND s.profile_id = ?
                  AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)
                ORDER BY s.score DESC
                LIMIT 10""",
            (profile_id,),
        ).fetchall()

        # Under-scored: applied or rejected with score <= 6
        under_rows = conn.execute(
            """SELECT j.title, j.company, s.score, t.status
                 FROM jobs j
                 JOIN job_tracking t ON j.id = t.job_id
                 JOIN job_scores s   ON j.id = s.job_id
                WHERE t.status IN ('applied', 'rejected') AND s.score <= 6
                  AND s.profile_id = ?
                ORDER BY s.score ASC
                LIMIT 10""",
            (profile_id,),
        ).fetchall()

        # Score distribution per bucket for relevant vs true-negative cohorts
        buckets = [
            ("9-10", "s.score >= 9"),
            ("7-8",  "s.score BETWEEN 7 AND 8"),
            ("5-6",  "s.score BETWEEN 5 AND 6"),
            ("0-4",  "s.score <= 4"),
        ]
        dist_lines: list[str] = []
        for label, cond in buckets:
            # relevant = applied + rejected (non-Tier-0)
            rel = conn.execute(
                f"""SELECT COUNT(*) FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id
                     JOIN job_scores s ON j.id = s.job_id
                    WHERE t.status IN ('applied', 'rejected')
                      AND s.profile_id = ?
                      AND {cond}
                      AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)""",
                (profile_id,),
            ).fetchone()[0]
            # true negative = archived (non-Tier-0)
            tn = conn.execute(
                f"""SELECT COUNT(*) FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id
                     JOIN job_scores s ON j.id = s.job_id
                    WHERE t.status = 'archived'
                      AND s.profile_id = ?
                      AND {cond}
                      AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)""",
                (profile_id,),
            ).fetchone()[0]
            dist_lines.append(f"  {label}: {rel} relevant / {tn} true-negative")

        # Top 5 sector patterns from over-scored archived jobs
        sector_over = conn.execute(
            """SELECT COALESCE(c.industry_sector, 'other'), COUNT(*) AS n
                 FROM jobs j
                 JOIN job_tracking t ON j.id = t.job_id
                 JOIN job_scores s ON j.id = s.job_id
                 LEFT JOIN companies c ON j.company_id = c.id
                WHERE t.status = 'archived' AND s.score >= 8
                  AND s.profile_id = ?
                  AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)
                GROUP BY 1 ORDER BY n DESC LIMIT 5""",
            (profile_id,),
        ).fetchall()

        # Top 5 country patterns from over-scored archived jobs
        country_over = conn.execute(
            """SELECT COALESCE(j.company_country, 'unknown'), COUNT(*) AS n
                 FROM jobs j
                 JOIN job_tracking t ON j.id = t.job_id
                 JOIN job_scores s ON j.id = s.job_id
                WHERE t.status = 'archived' AND s.score >= 8
                  AND s.profile_id = ?
                  AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)
                GROUP BY 1 ORDER BY n DESC LIMIT 5""",
            (profile_id,),
        ).fetchall()

    # ── 3. Few-shot anchors ────────────────────────────────────────────────
    anchors = export_few_shot_anchors(profile_id)
    if len(anchors) > 2000:
        anchors = anchors[:1997] + "..."

    # ── 4. Build the prompt ────────────────────────────────────────────────
    over_block = "\n".join(
        f"- [{r[2]}/10] {r[0]} · {r[1]}" + (f" — {r[3][:60]}" if r[3] else "")
        for r in over_rows
    ) if over_rows else "(none)"

    under_block = "\n".join(
        f"- [{r[2]}/10] {r[0]} · {r[1]} ({r[3]})"
        for r in under_rows
    ) if under_rows else "(none)"

    dist_block = "\n".join(dist_lines)

    sector_block = "\n".join(
        f"  - {sector}: {n} over-scored archived" for sector, n in sector_over
    ) if sector_over else "  (none)"

    country_block = "\n".join(
        f"  - {country}: {n} over-scored archived" for country, n in country_over
    ) if country_over else "  (none)"

    prompt = f"""You are tuning the scoring rubric for a job-matching LLM pipeline.
Below is the current scoring_context, calibration signals from real
user behavior, and few-shot anchors. Your task is to revise the
scoring_context to better align with observed behavior.

## Current scoring_context
```
{current_context}
```

## Calibration signals

### Over-scored jobs (archived despite high LLM score — the user passed)
{over_block}

### Under-scored jobs (applied/rejected despite low LLM score — the user wanted)
{under_block}

### Score distribution (relevant = applied/rejected, true-neg = archived)
{dist_block}

### Top sectors in over-scored archived jobs
{sector_block}

### Top countries in over-scored archived jobs
{country_block}

## Few-shot anchors (examples of real user decisions)
```
{anchors}
```

## Instructions
Return ONLY the revised scoring_context text — no preamble, no markdown
wrapper, no code fences. The output will be stored verbatim in the DB.

1. Preserve the overall structure and approximate length of the existing
   context.
2. Adjust tone, scoring band definitions, tier criteria, caps, and hard
   exclusions ONLY where the calibration signals clearly support a change.
3. If over-scored jobs cluster in a specific sector or country, consider
   whether the scoring_context should be more cautious about that pattern.
4. If under-scored jobs share a pattern the context undervalues, adjust
   upward.
5. Do NOT invent new criteria unsupported by the signal data.
6. If the signals are weak or contradictory, return the current
   scoring_context unchanged with a note at the top: "<!-- No changes:
   calibration data insufficient -->".
7. Keep the same format: # section headers, bullet points, scoring bands
   table, industry fit hierarchy, geography rules.
"""

    # ── 5. Call the LLM ────────────────────────────────────────────────────
    try:
        messages = [
            {"role": "system", "content": "You are an expert at tuning LLM scoring rubrics for job matching pipelines. Return ONLY the revised scoring_context text — no preamble, no markdown wrapper, no code fences."},
            {"role": "user", "content": prompt},
        ]
        proposal_text = llm.call(messages, json_mode=False, max_tokens=3000, sleep_after=0).strip()
    except Exception as e:
        raise Exception(f"LLM call failed: {e}") from e

    # Strip code fences if the LLM wrapped it anyway
    if proposal_text.startswith("```"):
        lines = proposal_text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        proposal_text = "\n".join(lines).strip()

    # ── 6. Write proposal file ─────────────────────────────────────────────
    today = date.today().isoformat()
    proposal_path = os.path.join(OUTPUT_DIR, f"proposal_{today}.md")

    over_count = len(over_rows)
    under_count = len(under_rows)
    # Count anchors
    pos_anchors = anchors.count("\n- [") if "\n## Strong Matches" in anchors else 0
    neg_anchors = anchors.count("\n- [") - pos_anchors if pos_anchors else 0

    md = f"""# Context Proposal — {today}
Profile: {profile_id}

## Calibration signals used
- Over-scored: {over_count} jobs
- Under-scored: {under_count} jobs
- Positive anchors: ~{pos_anchors} | Negative anchors: ~{neg_anchors}

## Proposed scoring_context

{proposal_text}

---
*Prompt fix — server-side only. No code changes needed.*
*To apply:  python preference_report.py --apply-context {proposal_path}*
*To verify: python score.py --mock --profile {profile_id}*
*To discard: delete this file.*
"""

    if dry_run:
        print(md)
    else:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(proposal_path, "w") as f:
            f.write(md)

    return proposal_path
