# Data Model — Single DB Access Path (spec 037)

The single gateway `core/storage.py` (`JobStorage`) gains the named methods below. Each
replaces a raw-SQL site with **identical statements and transaction boundaries** (FR-004);
the verbatim SQL for every site is in [`inventory.md`](inventory.md). `_conn()` commits on
successful exit and rolls back on exception, so a `with self._conn()` block is one transaction —
the bulk-verify and multi-statement sites preserve their boundary by keeping their statements in
one block.

## 1. JobStorage method surface

### 1.1 New methods

| Method (signature) | Replaces | Purpose |
|---|---|---|
| `get_database_stats() -> dict` | `dashboard.py:115`, `settings.py:780` | Profile-independent catalogue counts: `total_jobs`, `total_scores` (`COUNT(job_scores)`), `distinct_scored` (`COUNT(DISTINCT job_id)`), `total_companies`, `total_contacts`, `unverified_contacts`, `total_interactions`. One shared method (FR-003); dashboard uses the 6 it needs, settings all 7. |
| `get_contacts_by_role_family() -> list[tuple[str, int]]` | `settings.py:798` | `SELECT role_family, COUNT(*) FROM contacts GROUP BY role_family ORDER BY COUNT(*) DESC`. |
| `update_contact_fields(contact_id: int, fields: dict) -> None` | `contact_detail.py:77,86,97,134` | Generic allowlisted contact updater mirroring `update_company_fields`. Allowlist `{is_unverified, last_verified_at, is_current, notes}`. `:77` → `{"is_unverified": 0, "last_verified_at": now}`; `:86` → `{"is_unverified": 1}`; `:97` → `{"is_current": 0}`; `:134` → `{"notes": new_notes}`. |
| `bulk_mark_contacts_verified(contact_ids: list[int], last_verified_at: str) -> None` | `contacts.py:81` | One `with self._conn()` block looping `UPDATE contacts SET is_unverified = 0, last_verified_at = ? WHERE id = ?` — preserves the single-transaction boundary. |
| `get_tracking_status(job_id: str) -> str \| None` | `job_detail.py:67` | `SELECT status FROM job_tracking WHERE job_id = ?`; returns `None` when no row (view keeps its `or "new"` fallback). |
| `get_contacts_discovered_for_job(job_id: str) -> list[dict]` | `job_detail.py:340` | `SELECT DISTINCT ct.* FROM contacts ct JOIN interactions i ON i.contact_id = ct.id WHERE i.job_id = ? AND i.type = 'discovered_on_posting' ORDER BY ct.last_seen_at DESC`. |
| `get_calibration_signals(profile_id: str) -> dict` | `context_tuner.py:41` | All 8 SELECTs from the one `_conn()` block → a structured dict: `over_scored`, `under_scored` (top-10 rows each), `score_buckets` (the 4 hard-coded `cond`s × applied/archived counts), `top_sectors`, `top_countries`. SQL moved verbatim; `context_tuner.py` only builds the prompt. |
| `get_best_score_profile_id(job_id: str) -> str \| None` | `prepare.py:552` | `SELECT profile_id FROM job_scores WHERE job_id = ? AND score IS NOT NULL ORDER BY score DESC LIMIT 1`. |
| `delete_scores_for_profile(profile_id: str) -> int` | `score.py:175` | `DELETE FROM job_scores WHERE profile_id = ?`; returns `cursor.rowcount`. Does **not** touch `search_profiles` (unlike `delete_profile`). |
| `get_jobs_to_score(profile_id: str, rescore: bool = False) -> list[dict]` | `score.py:317` | The two-branch rescore/normal SELECT from `_get_jobs_to_score`, verbatim. **Note:** compare against `get_jobs_for_scoring` (which adds freshness/blacklist/company-join filters); keep this dedicated method unless the two are proven row-identical for score.py's call sites (FR-004). |
| `get_export_seed_companies() -> list[dict]` | `export_seed.py:20` | The wide `companies` SELECT with the `monitoring_status != 'unmonitored' OR ats_provider IS NOT NULL OR scraper_id IS NOT NULL` filter. |
| `get_jobs_missing_description() -> list[dict]` | `backfill_descriptions.py:74` | `SELECT id, url, source FROM jobs WHERE description IS NULL ORDER BY source, last_seen DESC`. |
| `set_job_description(job_id: str, description: str) -> None` | `backfill_descriptions.py:74` | `UPDATE jobs SET description = ? WHERE id = ?` (one call per fetched description — each commit is independent, matching today). |

**Preference-report cluster** (`core/preference_report.py` is a whole raw-sqlite3 module; `:51` is
only its `_connect()` factory). Each query becomes a read-only `JobStorage` method; the module
keeps its report-shaping logic. `_section_header`'s two counts reuse `get_database_stats()`.

| Method | Replaces |
|---|---|
| `get_cohort_jobs(statuses: list[str]) -> list[dict]` | `_get_cohort_jobs` |
| `get_applications_count() -> int` | `_get_applications_count` |
| `get_classified_jobs(profile_id: str) -> list[dict]` | `_get_classified_jobs` |
| `get_company_sector(company_name: str) -> str \| None` | `_get_company_sector` |
| `get_calibration_deltas(profile_id: str) -> dict` | `_section_calibration_deltas` (over/under-scored) |
| `get_archived_notes() -> list[str]` | `_section_archive_themes` |
| `get_few_shot_anchors(profile_id: str) -> dict` | `export_few_shot_anchors` (positive + negative anchors) |
| `get_action_item_candidates(profile_id: str) -> dict` | `generate_action_items` (the ~5 candidate queries) |

`PRAGMA query_only = 1` is dropped — all migrated statements are `SELECT`s, so the read-only
guard is redundant under `JobStorage` (no behaviour change).

### 1.2 Reused methods (no change)

- `set_company_monitored(company_id, monitored)` — replaces `settings.py:488` and `:509` exactly.
- `update_company_fields(company_id, fields)` — replaces `company_detail.py:280` and
  `forms.py:52` after the allowlist change below.

### 1.3 Allowlist change

- Add `"notes"` to `_ALLOWED_COMPANY_FIELDS` (`core/storage.py:2650–2654`). `update_company_fields`
  is a pure allowlisted-column `UPDATE` (verified) with no side effects, so
  `update_company_fields(company_id, {"notes": notes})` is behaviourally identical to the raw
  `UPDATE companies SET notes = ? WHERE id = ?`.

---

## 2. LLM provider-residue map (step 2c)

Only `core/llm.py` may name a provider. Edits below remove every provider name outside it; the
guard test (FR-009) then enforces it. `core/llm.py` itself is unchanged (its `DEEPSEEK_API_KEY`
fallback and `https://api.deepseek.com/v1` default stay — inside the one permitted file).

| File | Edit |
|---|---|
| `core/email_monitor.py` | **Delete** (`git rm`). Unimportable (missing `groq`), unused. |
| `core/scorer.py:567,576,610,621,654,656,660,886,906` | `DeepSeek` → `LLM` in docstrings/prints. |
| `core/scorer.py:609,653,885` | `if "DEEPSEEK_API_KEY" in msg or "not set" in msg:` → `"LLM_API_KEY"` (still matches `llm.call()`'s message). |
| `core/scorer.py:707` | Comment "…authority over Groq inference" → reword (drop `Groq`). |
| `core/prepare.py:268` | `Call DeepSeek for preparation tasks.` → `Call the LLM …`. |
| `core/prepare.py:279` | `return raw, "deepseek-chat"` → `return raw, llm.MODEL`. |
| `core/prepare.py:734` | `Fallback calls (DeepSeek): {…}` → provider-agnostic wording. |
| `core/company_researcher.py:251` | `# DeepSeek` comment → remove/reword. |
| `core/cv_agent/cli.py:242` | `… (set LLM_API_KEY / DEEPSEEK_API_KEY)` → `… (set LLM_API_KEY)`. |
| `tracker_views/settings.py:21` | `("DEEPSEEK_API_KEY", …)` → `("LLM_API_KEY", "LLM API key (LLM extraction + scoring)", True)`. |
| `tracker_views/onboarding.py:435–459` | Groq wizard block → `if not llm.is_configured():`; prompt asks for `LLM_API_KEY`, writes via `shared.set_secret`. |
| `.env.example` | Rewrite: document `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`; drop `GROQ_API_KEY` + `GEMINI_API_KEY` + Hydroxide block. |
| `docker-compose.yml` | Remove the `email-monitor` service. |
| `scripts/deploy.sh:37` | `docker compose build tracker agent email-monitor` → `docker compose build tracker agent`. |
| `core/storage.py:3445` | `find_jobs_by_company` docstring — drop "Used by email_monitor.py" (method stays). |
| `CLAUDE.md:137` | Infrastructure section — drop the `email-monitor` clause. |
| `README.md:69,164,290` / `docs/freelance-sourcing-map.md` / `docs/restore-procedure.md:101,103` / `docs/migration-checklist.md:92` | Remove/update `email-monitor` references. |
| `docs/history.md` | Append a `core/email_monitor.py` row (purpose, last commit containing it, `git show <sha>:core/email_monitor.py`). |

**Not edited**: `pyproject.toml` (package discovery, no explicit module list), `scripts/` dev
tools (`rewrite_core_imports.py`, etc. — historical, outside the guard scan), `docs/roadmap-api.md`
(its `:66` is the step's own plan description), and `specs/`/`prompts/` (historical reference).
