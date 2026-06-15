# BUILD — Scoring Feedback Loop

> Spec for Claude Code.  
> Read `preference_report.py`, `profiles.py`, `scorer.py`, `storage.py` before starting.  
> Do NOT modify `storage.py`, `models.py`, `scrape.py`, `scorer.py`, or any scraper.

---

## Context and goal

The job-agent runs in two separate environments:

- **Server (prod):** HPE ProLiant Ubuntu, Docker Compose. Live SQLite DB with real
  behavioral data — every `saved`, `applied`, `rejected`, `archived` status update
  Jean Claude makes in the tracker. This is where signals originate.
- **Mac/VS Code (dev):** codebase, Claude Code, `profiles.py`, `scorer.py`. This is
  where improvements get implemented and deployed via `git push → deploy.sh`.

The feedback loop must bridge both environments. Improvements fall into two distinct
categories with completely different paths:

| Type | Where it lands | Deploy path |
|------|---------------|-------------|
| **Prompt fix** — `scoring_context` prose, tier criteria, band definitions | DB only (server) | `--apply-context` → `--rescore`. Never touches the codebase. |
| **Code fix** — scraper logic, Tier-0 rules, extraction prompt, new denylist entries | `profiles.py` / `scorer.py` / scrapers (dev) | Claude Code implements → `git push` → `deploy.sh` → `--rescore` on server |

The goal of this spec is to close the full loop: gather signals on the server,
classify findings by type, apply prompt fixes in-place, and surface code fixes as
a structured brief that can be pasted directly into a Claude Code session on dev.

---

## The complete workflow

```
┌─────────────────────────────────────────────────────────────┐
│  SERVER                                                      │
│                                                              │
│  1. Weekly cron: scrape + score (main.py)                    │
│  2. Jean Claude reviews jobs in tracker, updates statuses    │
│  3. Trigger: python preference_report.py --full              │
│     → outputs/preference_reports/report_<date>.md           │
│     → outputs/action_items/action_items_<date>.md  [NEW]    │
│  4. Tracker UI (Tailscale): review report + action items     │
│                                                              │
│  ┌──────────────┐         ┌──────────────────────────────┐  │
│  │  PROMPT FIX  │         │  CODE FIX                    │  │
│  │              │         │                              │  │
│  │  --suggest-  │         │  action_items.md contains    │  │
│  │  context     │         │  a Claude Code brief:        │  │
│  │      ↓       │         │  "paste this into dev"       │  │
│  │  review      │         │          ↓                   │  │
│  │  proposal    │         │  copy brief to clipboard     │  │
│  │      ↓       │         │          ↓                   │  │
│  │  --apply-    │         └──────────────────────────────┘  │
│  │  context     │                                           │
│  │      ↓       │         ┌──────────────────────────────┐  │
│  │  --rescore   │         │  DEV (Mac / VS Code)         │  │
│  │              │         │                              │  │
│  └──────────────┘         │  paste brief → Claude Code   │  │
│                           │  implement → git push        │  │
│                           │  deploy.sh pulls on server   │  │
│                           │  --rescore                   │  │
│                           └──────────────────────────────┘  │
│                                                              │
│  5. python score.py --mock --profile unified_jc              │
│     (regression gate — run after any prompt or code change)  │
└─────────────────────────────────────────────────────────────┘
```

No file transfer needed. The tracker (accessible via Tailscale) surfaces everything.
The "bridge" from server to dev is the action items file rendered in the tracker UI —
Jean Claude reads it in the browser and pastes the relevant brief into Claude Code.

---

## Part A — Pre-condition: verify mock test

Before writing any new code, run:

```bash
python score.py --mock --profile unified_jc
```

Confirm all 6 cases are within their expected bands. If any case is out of band, fix
the Tier-0 or scoring path first — the calibration report is only meaningful if the
baseline is correct. The mock test is the regression gate for everything else.

No code changes unless verification fails.

---

## Part B — New module: `context_tuner.py`

Single file at project root. No new dependencies. ~150 lines max.

### Interface

```python
def propose_context_update(
    profile_id: str,
    db: JobStorage,
    *,
    dry_run: bool = False,
) -> str:
    """
    Read calibration signals from the DB, call the LLM once to draft a
    revised scoring_context, write the proposal to outputs/context_proposals/,
    and return the output file path.

    dry_run=True: print the proposal to stdout instead of writing to disk.
    """
```

### Inputs to the LLM call

Assemble a single structured prompt containing:

1. **Current `scoring_context`** — read from DB via `db.get_profile(profile_id)`,
   then `SearchProfile.from_criteria(...)`.

2. **Calibration signals** — same queries as `preference_report.py`:
   - Over-scored: `archived` with `score >= 8` (up to 10 rows: `title`, `company`, `score`, `notes`)
   - Under-scored: `applied` or `rejected` with `score <= 6` (up to 10 rows, same fields)
   - Score distribution: counts per bucket (9-10, 7-8, 5-6, 0-4) for relevant vs. true-negative cohorts
   - Top 5 sector/country patterns from calibration deltas

3. **Few-shot anchors** — call `export_few_shot_anchors(profile_id)` from
   `preference_report.py`, include verbatim (max 2000 chars, truncate with `...` if needed).

4. **Instruction** — tell the LLM:
   - Return ONLY the revised `scoring_context` text, no preamble, no markdown wrapper
   - Preserve the overall structure and length of the existing context
   - Adjust tone, scoring band definitions, or tier criteria only where calibration
     signals clearly support it
   - Do not invent criteria not supported by the signal data
   - The output will be stored verbatim in the DB as the new `scoring_context`

### LLM call details

- Use the same Groq client pattern as `scorer.py` (read `scorer.py` first to get the
  exact client initialisation — do not guess)
- Model: `llama-3.3-70b-versatile`
- `max_tokens`: 3000
- No retry — this is human-triggered; if it fails, the user re-runs it

### Output file: `outputs/context_proposals/proposal_<date>.md`

```markdown
# Context Proposal — <date>
Profile: unified_jc

## Calibration signals used
- Over-scored: N jobs
- Under-scored: N jobs
- Positive anchors: N | Negative anchors: N

## Proposed scoring_context

<raw LLM output verbatim>

---
*Prompt fix — server-side only. No code changes needed.*
*To apply:  python preference_report.py --apply-context outputs/context_proposals/proposal_<date>.md*
*To verify: python score.py --mock --profile unified_jc*
*To discard: delete this file.*
```

---

## Part C — New function: `generate_action_items()` in `preference_report.py`

This is the bridge from server signals to dev implementation. It reads the preference
report data and outputs a structured Markdown file that classifies every finding by
type (prompt fix vs. code fix) and includes a ready-to-paste Claude Code brief for
code fixes.

### Function signature

```python
def generate_action_items(
    profile_id: str,
    db_conn,          # already-open sqlite3 connection (read-only)
    output_dir: str = "outputs/action_items",
) -> str:
    """Generate action_items_<date>.md and return the output path."""
```

Call this at the end of `generate_report()` so both files are always produced
together. Also expose it via `--action-items` flag for standalone use.

### What the function classifies

Read the same data already computed during report generation (no extra queries).
Classify each finding into one of two buckets:

**Prompt fixes** (resolved on server, no code change):
- Scoring band miscalibration (over-scored archived jobs, under-scored applied jobs)
- `scoring_context` tier criteria that don't match observed behavior
- Few-shot anchor refresh (anchors exist but are stale — last generated > 14 days ago)

**Code fixes** (must be implemented in dev, then deployed):
- New denylist candidates (companies with ≥ 3 archived, 0 applied — add to `denylisted_companies` in `profiles.py`)
- New excluded sectors (≥ 5 archived in sector, 0 applied — add to `excluded_sectors` in `profiles.py`)
- Scraper quality issues (sources with 0% strong match rate over last N runs — flag for removal or deprioritisation)
- Tier-0 filter gaps (jobs that passed Tier-0 but should have been blocked — rule missing from `scorer.py`)
- New `banned_countries` candidates (countries with ≥ 3 archived, 0 applied)

### Output format

```markdown
# Action Items — <date>
Profile: unified_jc | Generated from: preference_report_<date>.md

---

## Prompt fixes (apply on server, no deploy needed)

### [P1] Scoring band miscalibration
<finding description with specific job examples>
→ Run: python preference_report.py --suggest-context --profile unified_jc
→ Review proposal, then: python preference_report.py --apply-context <path>
→ Verify: python score.py --mock --profile unified_jc

### [P2] Few-shot anchors stale (last generated: <date>)
→ Run: python preference_report.py --anchors --profile unified_jc
→ Paste updated anchors into scoring_context, then --apply-context

---

## Code fixes (implement in dev → git push → deploy)

### [C1] Denylist candidates
The following companies had ≥ 3 archived jobs and 0 applied. Add to
`denylisted_companies` in `profiles.py`:
- CompanyA (5 archived)
- CompanyB (3 archived)

### [C2] Sector exclusions
The following sectors had ≥ 5 archived jobs and 0 applied. Add to
`excluded_sectors` in `profiles.py`:
- sector_name (N archived)

### [C3] Source quality
The following scrapers have returned 0 strong matches (score ≥ 8) in the
last 30 days. Consider disabling or deprioritising:
- SourceName: N jobs scraped, 0 strong matches, 0 applied

---

## Claude Code brief
<!-- Paste this block into a Claude Code session on dev to implement all code fixes -->

```
Read prompts/BUILD_feedback_loop.md for context.

Implement the following changes from the latest action items report
(outputs/action_items/action_items_<date>.md on the server):

**profiles.py — denylisted_companies** (add):
- "CompanyA"
- "CompanyB"

**profiles.py — excluded_sectors** (add):
- "sector_name"

**scrapers/<name>.py — disable** (set ENABLED = False):
- Reason: 0 strong matches in 30 days

After implementing, run: python score.py --mock --profile unified_jc
Expected: all 6 cases in their bands.
```
<!-- End of Claude Code brief -->
```

The Claude Code brief section is the literal text that gets copied and pasted into
a Claude Code session on the dev Mac. It must be self-contained — no references to
files that only exist on the server, no context that Claude Code can't read from
the codebase itself.

---

## Part D — CLI additions to `preference_report.py`

Add three flags to the existing `argparse` parser. Existing behaviour unchanged.

### `--full`
Runs `generate_report()` + `generate_action_items()` together. Preferred for weekly use.
```
python preference_report.py --full [--profile <id>]
```

### `--action-items`
Runs `generate_action_items()` standalone (reads most recent report data from DB).
```
python preference_report.py --action-items [--profile <id>]
```

### `--suggest-context`
```
python preference_report.py --suggest-context [--profile <id>] [--dry-run]
```
Calls `context_tuner.propose_context_update(profile_id, db, dry_run=args.dry_run)`.
Standalone — does not generate the full report.

### `--apply-context <path>`
```
python preference_report.py --apply-context outputs/context_proposals/proposal_<date>.md
```
1. Reads the proposal file
2. Extracts the block between `## Proposed scoring_context` and the `---` separator
3. Loads the current profile from DB
4. Sets `profile.scoring_context` to the extracted block
5. Calls `db.upsert_profile(profile)`
6. Prints: `✅ scoring_context updated for 'unified_jc'. Run: python score.py --mock --profile unified_jc`

Does NOT call the LLM. Does NOT modify `profiles.py`.

---

## Part E — Tracker UI: `tracker_views/preferences.py`

The page already exists with a basic report viewer. Extend it — do not rewrite it.

Add a **"Feedback loop"** expander (collapsed by default) after the existing report
viewer. The expander contains three columns:

**Column 1 — Status**
```
Last report:   <date or "never">
Last proposal: <date or "none">
Last actions:  <date or "none">
```
Dates are read from the most recent file in each output directory.

**Column 2 — Generate**
- Button: **"Run full analysis"** → calls `generate_report()` + `generate_action_items()`
  with a spinner, then `st.success("Report and action items written.")` with both paths.
- Button: **"Suggest context update"** → calls `context_tuner.propose_context_update()`
  with a spinner, then `st.success("Proposal written to <path>. Review before applying.")`.

**Column 3 — Review**
- Button: **"View action items"** → reads latest `outputs/action_items/*.md` and renders
  with `st.markdown()` in a full-width expander below.
- Button: **"View context proposal"** → reads latest `outputs/context_proposals/*.md`
  and renders with `st.markdown()` in a full-width expander below.

**No "Apply context" button in the UI.** Applying is CLI-only to force conscious review.
The rendered proposal includes the CLI command to run — that's sufficient.

---

## Done-when assertions

- [ ] `python score.py --mock --profile unified_jc` — all 6 cases in expected bands (pre-condition)
- [ ] `python preference_report.py --full` — produces both `preference_report_<date>.md`
      and `action_items_<date>.md` in their respective output directories
- [ ] `action_items_<date>.md` contains a non-empty "Claude Code brief" section when
      there is at least one code-fix finding (denylist candidates, sector exclusions, or
      source quality issues)
- [ ] `action_items_<date>.md` contains only prompt-fix items (or "No prompt fixes found")
      when calibration deltas are within normal range
- [ ] `python preference_report.py --suggest-context --dry-run` — prints a plausible
      revised context without error, even with empty calibration data
- [ ] `python preference_report.py --apply-context <path>` — updates DB; verify with:
      `sqlite3 data/jobs.db "SELECT json_extract(criteria, '$.scoring_context') FROM search_profiles WHERE id='unified_jc'" | head -3`
- [ ] After `--apply-context`, mock test still passes (prompt fix didn't break Tier-0)
- [ ] Tracker "Feedback loop" expander renders without crashing on a populated DB
- [ ] "View action items" renders the latest file in the tracker UI

---

## Non-goals (explicit out-of-scope)

- No automatic application of context updates or code changes — human review always required
- No file transfer between server and dev — the tracker UI (Tailscale) is the bridge
- No versioning or rollback beyond git
- No A/B testing across profiles
- No new DB tables — all signals read from existing tables
- No scheduled/cron execution — the feedback loop is human-triggered weekly
- No changes to `scorer.py`, `storage.py`, `models.py`, or `profiles.py` structure

---

## Regression test (mandatory after any change)

```bash
python score.py --mock --profile unified_jc
```

Run this after every prompt fix (`--apply-context`) and after every code fix deployment.
All 6 cases must remain within their expected bands. If any case falls out of band,
the change introduced a regression — revert or fix before proceeding.

---

## File map

| File | Action |
|------|--------|
| `context_tuner.py` | **CREATE** — new module (~150 lines) |
| `preference_report.py` | **MODIFY** — add `generate_action_items()` + `--full`, `--action-items`, `--suggest-context`, `--apply-context` flags |
| `tracker_views/preferences.py` | **MODIFY** — extend existing page with Feedback loop expander |
| `score.py` | **DO NOT MODIFY** |
| `storage.py` | **DO NOT MODIFY** |
| `profiles.py` | **DO NOT MODIFY** |
| `scorer.py` | **DO NOT MODIFY** |
| Any scraper | **DO NOT MODIFY** |
