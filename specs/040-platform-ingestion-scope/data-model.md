# Data Model — spec 040 (platform ingestion scope)

No new tables. The feature adds one `config` key, one `job_scores` row shape, and a
one-shot migration. `jobs.filtered_non_product` stays as an unused column
(expand/contract).

---

## 1. `config.ingestion_scope` — the platform scope (FR-001, FR-002)

Stored in the existing `config` key-value store (`key TEXT PRIMARY KEY, value TEXT`),
value is a JSON string.

```json
{
  "title_families": ["product manager", "head of product", "…"],
  "exclude":        ["junior", "intern", "…"],
  "remote_or_hybrid": false
}
```

| Field | Type | Semantics | Source on day one |
|-------|------|-----------|-------------------|
| `title_families` | `list[str]` | Title terms for query-driven boards and the coarse family filter; substring match. Empty = open ingestion. | active profile `job_titles` |
| `exclude` | `list[str]` | Word-boundary exclusion words. | active profile `title_exclude` |
| `remote_or_hybrid` | `bool` | Exclude fully on-site jobs (location-based, `JobFilterEngine`). | active profile `scrape_remote_or_hybrid` (**not** `remote_or_hybrid`) |

**Rules:**
- `get_ingestion_scope()` never raises: absent/malformed JSON or wrong types → the
  safe default `{"title_families": [], "exclude": [], "remote_or_hybrid": False}`
  (open net, Constitution IX). Coercion follows the `get_freshness_days` template.
- `set_ingestion_scope(title_families, exclude, remote_or_hybrid)` validates types
  (lists of `str`, `bool`) and writes JSON via `set_config`.
- Platform-level: not keyed by `user_id` (unlike `active_profile_id` /
  `onboarding_complete`, which are aliased to the `users` row).

**Relationships:** read by `core/scrape.py` (broad scrape `JobFilter`) and
`core/scrapers/greenhouse.py` (family filter). Never read by the scorer — scoring
reads the **profile**, not the scope (the whole point of the separation).

---

## 2. `job_scores` — the Tier 0 title row (FR-005)

Existing table, `PRIMARY KEY (job_id, profile_id)`. A title-filtered job now gets a
deterministic row instead of a catalogue flag.

| Column | Value |
|--------|-------|
| `job_id` | the job |
| `profile_id` | the scoring profile (user space via profile) |
| `score` | `1` |
| `reason` | `filtered: title not in profile` **or** `filtered: excluded title word (<word>)` |
| `scored_by` | `tier_0` |
| `scored_at` | now |
| `country_code`, `comp_flag` | `comp_flag` passed through from `evaluate_for_profile`; `country_code` `None` |

**Lifecycle:** produced by the new first Tier 0 rule in `evaluate_for_profile`
(applies to **every** job, not just monitored ones), **not** by a migration. The
currently-flagged jobs (`filtered_non_product = TRUE`, no score row) are scored by the
next `core.score --profile` run — reason `filtered: title not in profile` (the flag was
only ever set for a title mismatch). Flagged jobs outside the freshness window stay
unscored until `--rescore` or a re-scrape (spec edge case).

**Existing scores preserved:** the primary key `(job_id, profile_id)` means a job
already scored keeps its row; `save_scored` upserts by that key (US2 scenario 3).

---

## 3. `jobs.filtered_non_product` — kept, unused (FR-007, expand/contract)

- No runtime code reads or writes it after this step (`set_job_filtered_non_product`
  removed; the `SELECT j.filtered_non_product` columns dropped from
  `get_jobs_for_scoring` (`storage.py:1717`) and `get_job_for_prepare`
  (`storage.py:2737`), since no consumer read them).
- The column itself and the `JobPosting.filtered_non_product` dataclass field
  (`core/models.py:39`) are kept for round-trip compatibility until dropped at step 10.
- The scoring SQL pre-filter's title clauses (`title_contains` / `exclude_title_contains`
  in `core/score.py`) are removed so the Tier 0 rule is the sole title judgement;
  `get_jobs_for_scoring` then selects every fresh job regardless of title.

**State transition** (per job):
```
ingested (no title judgement) → score time:
  title matches profile   → normal Tier 0/Tier 1 scoring
  title not in profile    → score 1, reason "filtered: title not in profile"
  title has exclude word  → score 1, reason "filtered: excluded title word (<word>)"
```

## 4. What does **not** change

- `runs.profile_id` (non-goal — scrape runs still log the active profile id).
- `jobs.country_code` / `geo_zone` (pre-existing Constitution IV gap, non-goal).
- The `config` table, `users`, `user_companies`, `job_tracking`, `search_profiles` schemas.
