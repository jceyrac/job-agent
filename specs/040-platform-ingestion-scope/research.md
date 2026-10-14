# Research — spec 040 (platform ingestion scope)

Phase 0 findings. Every source location cited in the spec was re-verified against
`cd14148` on 2026-10-14. Decisions are marked **Decision**; the single parity decision
(§1) was resolved by the operator (third option, 2026-10-14).

---

## 1. Resolution: no backfill — byte-identical parity + an expected-change check

**Decision (operator's third option, accepted 2026-10-14).** Drop FR-007's backfill.
The one-shot migration only initialises `config.ingestion_scope`; it writes **no**
`job_scores` rows and leaves `jobs.filtered_non_product` untouched (kept unused until
step 10). Parity on staging is therefore **byte-identical** to the pre-step image —
exactly as for step 3, no itemised delta.

The flagged jobs are not lost: they still have no score row, so the next
`core.score --profile <active>` run picks them up and scores them via the new Tier 0
title rule (score 1, `scored_by=tier_0`, no LLM). The migration itself makes no score
change, so the "before scoring" fingerprint is identical; the expected change is
observed *after* the first scoring run.

**Expected-change check (replaces the old FR-007/SC-001 delta):**
1. Before scoring on staging, compute
   `N = COUNT(jobs WHERE filtered_non_product = TRUE AND NOT EXISTS (job_scores row for
   the active profile) AND within the freshness window)`.
2. After `core.score --profile <active>` on staging, assert exactly `N` new `job_scores`
   rows with `scored_by = 'tier_0'` and reason `filtered: title not in profile` exist,
   and **no other score changed** (the rest of the fingerprint diffs to empty).

**A caveat this plan uncovered (see §9):** the premise "flagged jobs are re-selected on
every scoring run" is **not** true today, because `core/score.py` also puts
`title_contains` / `exclude_title_contains` (derived from `profile.job_titles`) into the
scoring SQL pre-filter. That pre-filter excludes non-matching-title jobs *before* the
scoring loop, so the old gate (`score.py:443`) is dead code and flagged jobs are never
re-selected. FR-006 therefore also removes those two pre-filter clauses — the Tier 0
title rule is then the sole title judgement and the expected-change check yields
`N > 0`.

**Alternatives rejected.** (a) The original FR-007 backfill breaks byte-parity (the
itemised `+N` delta). (b) A flag-gated/deferred backfill splits one migration into a
schema step + a manual prose step. Both add a moving part for no benefit: the scoring
run converges the flagged set naturally once the pre-filter clause is gone.

---

## 2. There are two profile-driven title filters at broad ingestion today

**Finding.** In `_run_broad_scrape` (`core/scrape.py:328`):

1. `JobFilterEngine.apply` (`core/filters.py:35–38`) already hard-filters on
   `job_filter.titles` (substring) and `job_filter.exclude` (word-boundary).
2. `core/scrape.py:379` then re-checks `title_matches_profile(job.title,
   profile.job_titles)` before `save_unscored` — **redundant** given (1), and never
   triggered for a job that passed (1) with a non-empty title list.

**Decision.** The platform scope feeds `JobFilter(titles=…, exclude=…,
remote_or_hybrid=…)` exactly as the profile does today; `scrape.py:379` is deleted.
`JobFilterEngine.apply` becomes the *only* ingestion title filter (spec US1 scenario
3). The title-family filter on query-driven boards stays as the coarse scoping the
constitution already permits; company-keyed Greenhouse uses the same
`title_families`.

---

## 3. The monitored scrape has no title filter at ingestion (and that is where the flag comes from)

**Finding.** `core/scrape.py:257` calls `scraper.fetch(None)` (no `JobFilter`) for
monitoring, so every monitored opening enters the catalogue. The title gate is applied
later, at scoring (`core/score.py:442–448`), writing `filtered_non_product` and
skipping. This is the only writer of the flag (verified: `set_job_filtered_non_product`
is called from exactly one place).

**Decision.** Monitored ingestion is unchanged (wide net per Constitution VIII — a
monitored company's full list is intended). Title judgement for those jobs now happens
in `evaluate_for_profile` (Tier 0) for **every** job, so the `core/score.py` gate and
`set_job_filtered_non_product` are removed.

---

## 4. `filtered_non_product` is currently inert in the UI

**Finding.** `grep` shows the flag is *written* (`core/score.py:445`) and *selected*
(`storage.py:1717`, `storage.py:2737`) but **never read** by `tracker_views/`, the
tracker feed (`get_all_for_tracker`), the digest (`get_digest`), or the stats. Its
visible effect is purely "no score row → shows as unscored / excluded from the
digest".

**Decision.** No feed/dashboard/stats query needs to change to "derive visibility from
the Tier 0 row" — a score-1 row already flows through `get_all_for_tracker` /
`get_digest` (score < threshold) / `get_stats` naturally. The work is only: remove the
*write* path (`set_job_filtered_non_product`) and drop the `SELECT j.filtered_non_product`
columns from `get_jobs_for_scoring` / `get_job_for_prepare` (no consumer reads them),
while the column itself and the `JobPosting.filtered_non_product` field stay until step
10 (expand/contract). FR-008's guard test ensures no runtime code reads or writes it.

---

## 5. Which profile fields map to the scope

**Finding.** `core/scrape.py:332–334` reads `profile.job_titles`,
`profile.title_exclude`, `profile.scrape_remote_or_hybrid`. **Important:** the seed
profile (`core/profiles.py:385`) sets `scrape_remote_or_hybrid=False`, distinct from
the unrelated `remote_or_hybrid=True` (a post-scoring digest field, line 31). The
scope must copy **`scrape_remote_or_hybrid`**, not `remote_or_hybrid`, or day-one
ingestion would silently flip from "no remote/hybrid location filter" to "exclude
fully on-site".

**Decision.** `ingestion_scope = {title_families: job_titles, exclude: title_exclude,
remote_or_hybrid: scrape_remote_or_hybrid}`. The migration reads the active profile's
`search_profiles.criteria` JSON (resolved via `config.active_profile_id` →
`DEFAULT_PROFILE_ID`), falling back to the `core/profiles` seed defaults for a fresh
install (no profile row). Names match FR-001 exactly.

---

## 6. Matching semantics to reproduce in the Tier 0 rule

**Finding.**
- Include: `title_matches_profile` (`core/title_gate.py`) — case-insensitive
  **substring**; empty list → open.
- Exclude: word-boundary regex `\b{word}\b` on lowercased text (today applied to
  title+company+location+description+tags in `JobFilterEngine`; the Tier 0 rule
  applies it to **title only**, per US2's "excluded title word (<word>)").

**Decision.** Add a small `title_excluded(title, words) -> str | None` helper (returns
the first offending word) to `core/title_gate.py`, mirroring the existing regex. The
Tier 0 rule runs **first** in `evaluate_for_profile`, before the denylist rule, per
"new first Tier 0 rule". Both branches return `score=1`,
`scored_by="tier_0"`, `comp_flag` passed through (it is already computed at the top of
`evaluate_for_profile`, `scorer.py:779–784`).

---

## 7. Migration insertion point and recording

**Finding.** Migrations run in `JobStorage._init_db` (`core/storage.py:939–950`) and
each records via `_record_migration(conn, name)` guarded by `_migration_applied`. The
user-space seam (`_migrate_user_space_separation`) runs last, so the active profile and
`search_profiles.criteria` are resolvable after it.

**Decision.** Add `_migrate_ingestion_scope(conn)` **after**
`_migrate_user_space_separation`, recording a single name `ingestion_scope` (seed the
`config` key). No `job_scores` write, no `backfill_title_tier0` migration (see §1).
The Tier 0 title rows are produced by the normal scoring path, not by a migration.

## 8. Fresh-install / empty-scope edge cases

**Finding.** `title_matches_profile` is open on empty titles; `JobFilterEngine` skips
the title filter when `titles` is empty; a query-driven board with no terms needs a
fallback query. `get_freshness_days` (`storage.py:3397`) is the typed-accessor
template: coerce absent/malformed input to a safe default.

**Decision.** `get_ingestion_scope()` returns
`{"title_families": [], "exclude": [], "remote_or_hybrid": False}` when the key is
absent or malformed (open ingestion, Constitution IX). `set_ingestion_scope` validates
types and rejects non-list/non-bool fields. Empty `title_families` logs a warning in
`core/scrape.py` (the edge case in the spec); each query-driven board's behaviour with
empty terms is checked in `quickstart.md` §3.

---

## 9. The scoring pre-filter is a *second* title gate (and must go with the first)

**Finding.** `core/score.py:376–383` builds `effective_pre_filter` from the profile:
`title_contains = profile.job_titles` and `exclude_title_contains =
profile.title_exclude`. `get_jobs_for_scoring` (`core/storage.py:1743–1754`) turns
those into `LOWER(j.title) LIKE '%…%'` clauses **before** the freshness window. So a
job whose title does not match the profile is filtered out of the scoring set entirely
and never reaches `evaluate_for_profile`. The monitored-only gate at
`core/score.py:443–448` is therefore unreachable — it can only see jobs that already
matched `job_titles`, so `title_matches_profile(…)` is always true there and the flag
is never written in the current path (it is a historical artifact).

**Decision.** FR-006 removes **both** title clauses from `effective_pre_filter`
(`title_contains` and `exclude_title_contains`), leaving only the location/remote
clauses, so `get_jobs_for_scoring` selects every fresh job regardless of title. Title
judgement then happens *only* in the new first Tier 0 rule — which is what makes the
expected-change check (§1) possible and makes the rule actually fire. This is
consistent with the whole point of the step: ingestion (and now the scoring *selection*)
must not filter on the profile's titles.

---

## Open items resolved

- Q1 (scope editor): CLI only (`scripts/ingestion_scope.py`) + read-only Settings
  note — no mockup (confirmed in spec Clarifications).
- Q2 (initial value): copy active profile's `job_titles` / `title_exclude` /
  `scrape_remote_or_hybrid`; fresh install → seed defaults (§5).
- Parity semantics: resolved in §1 (operator's third option — no backfill, byte-identical
  parity, expected-change check after the first scoring run).
