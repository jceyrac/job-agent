# Research — Application Lifecycle Tracking + Date-Correct Reports (spec 030)

**Date**: 2026-10-02 · **Owner**: Claude Code · **Source reads**: live source (dev Mac), read-only diagnostics on verva.

---

## 1. Live diagnostics (read-only, run 2026-10-02)

Run against the `job-tracker` container on verva, DB opened `mode=ro` (`/app/data/jobs.db`). No writes, no restarts, no git, no file edits.

```
D1 -> [('applied', 138, 0), ('rejected', 25, 0)]
D2 -> [(9,)]
D3 -> [('applied', 66), ('rejected', 16)]
```

### Interpretation

- **D1** — 163 lifecycle jobs (`applied` 138 + `rejected` 25), **0 with `company_id IS NULL`**. The `interactions.company_id NOT NULL` constraint is satisfiable for every existing lifecycle job, so:
  - The migration backfill can insert events directly (no company synthesis needed).
  - FR-004's "resolve/create company" path in the new entry point is **defensive only** — no live job needs it.
- **D2** — **9 `rejected` jobs have no `applied` row in `status_history`**. These are undatable: the migration MUST NOT invent a date; they are logged and surface under FR-021 "Application date missing".
- **D3** — **82 jobs** (66 applied + 16 rejected) have **no `application_submitted` interaction** and need backfill. The remaining 72 applied + 9 rejected already have one (auto-logged) and must be **reused, not duplicated** (FR-024).

No count is unexpectedly large → no stop-and-report required. Assumption "volume < 500 lifecycle jobs" holds.

---

## 2. FR-023 grep inventory — every status-list / status-cohort occurrence

Each occurrence is marked **CHANGE** (add `interviewing` / `offer` / `withdrawn`, or rework) or **NON-CHANGE** (justified). This is the authoritative list the plan implements against.

| # | Location | What it is | Disposition |
|---|---|---|---|
| 1 | `storage.py:1581` `VALID_STATUSES` | Canonical tracking status set | **CHANGE** — add `interviewing`, `offer`, `withdrawn` (FR-001) |
| 2 | `storage.py:50` `INTERACTION_TYPES` | Allowed interaction types | **CHANGE** — add `interview_invited`, `withdrawn` (FR-002) |
| 3 | `storage.py:2942` `_STATUS_INTERACTION_MAP` | Auto-log on `set_status` (applied/rejected/offer/interviewing) | **NON-CHANGE** — lifecycle statuses route through the new entry point (FR-006), which bypasses this map. No `withdrawn` entry is added (withdrawn is handled by the entry point). The map's `applied`/`rejected` entries become dead once the UI stops calling `set_status` with them. |
| 4 | `storage.py:2949` `_auto_log_status_interaction` | Creates competing event rows | **NON-CHANGE to body** — must simply never be invoked for lifecycle statuses. Enforced by the new entry point not calling it. |
| 5 | `storage.py:1561` `get_engaged_job_keys` | `status IN ('applied','ready','queued','archived')` — "don't re-scrape engaged jobs" (used by `scrape.py`) | **CHANGE** — add `interviewing`, `offer`, `withdrawn` (they mean "I applied", so they must not be re-scraped). |
| 6 | `storage.py:1332,1339` `get_jobs_for_scoring` | rescore/score-candidate exclusion `NOT IN ('rejected','archived','expired')` | **CHANGE** — add `interviewing`, `offer`, `withdrawn` to the exclusion (post-application jobs must not re-enter the scoring pool). |
| 7 | `storage.py:2816` `find_jobs_by_company` | `t.status = 'applied'` (email monitor) | **NON-CHANGE** — `email_monitor.py` is unused/out of scope; `find_jobs_by_company()` stays as-is (no other caller needs it). |
| 8 | `storage.py:2262,2293` `purge_stale_jobs` / `count_purgeable_jobs` | `jt.status = 'new'` | **NON-CHANGE** — new statuses are never `new`, so never purged (correct). |
| 9 | `storage.py:3041,3087` `get_all_jobs_best_score` / `get_all_for_tracker` | `status IS NULL OR status != 'archived'` | **NON-CHANGE** — new statuses are `!= 'archived'`, so they appear (desired). |
| 10 | `storage.py:2591` `get_company_relationship_summary` (contact CRM) | derives `applied`/`interviewing`/`offer` from **contact** interactions | **NON-CHANGE** — this is contact-relationship status, a different concept; already has `interviewing`/`offer`. |
| 11 | `tracker_views/shared.py:63` `_ENGAGED_STATUSES` | stale-job guard (`_is_stale_unengaged`) | **CHANGE** — add `interviewing`, `offer`, `withdrawn` (engaged ≠ stale). |
| 12 | `tracker_views/dashboard.py:103-105` `status_order` / `status_icons` | pipeline metric columns | **CHANGE** — add `interviewing`, `offer`, `withdrawn` (+ icons); widen the 6-column row to 9. |
| 13 | `tracker_views/dashboard.py:136` hot-jobs feed | `status in ("new","ready","queued")` | **NON-CHANGE** — post-application statuses are correctly excluded from "hot jobs". |
| 14 | `tracker_views/jobs.py:190` status filter | sidebar multiselect | **CHANGE** — add `interviewing`, `offer`, `withdrawn`. |
| 15 | `tracker_views/job_detail.py:89` "Tracking status" selectbox | status list | **CHANGE** — add the three statuses; route lifecycle statuses through the FR-006 entry point (FR-013). |
| 16 | `tracker_views/job_helpers.py:75-101` `ACTIONS`/`ACTION_LABELS`/`ENABLED` | action bar | **CHANGE** — becomes contextual (FR-008/FR-009); add `interviewing`/`offer`/`withdrawn` actions. |
| 17 | `export_jobs.py:26-39` `STATUSES_DEFAULT`/`STATUSES_ALL`/`STATUS_TO_RESULTAT` | report status mapping | **CHANGE** — replaced by event-based selection (application date) + derived `ORP result` (FR-016/018). `STATUS_TO_RESULTAT` is removed. |
| 18 | `scripts/dedupe_jobs.py:32`, `scripts/dedupe_title_company.py:27` `NON_NEW_STATUSES` | **dead constant** — real dedup predicate is `job["_status"] != "new"` (lines 46/39) | **NON-CHANGE to logic** (already correct — `!= "new"` covers all three). Update the dead constant to include the new statuses for accuracy (trivial, 2 lines), or delete it. |
| 19 | `preference_report.py` positive-interest cohorts: `98`, `110`, `420`, `462`, `643`, `837`, `855`, `874`, `892` (`'applied','rejected'…`) | "relevant / I applied" signal | **CHANGE** — add `interviewing`, `offer`, `withdrawn` wherever `applied` is a positive-interest signal. `archived` cohorts (`390`,`659`,`824`,`850`,`868`,`886`) are true-negative → **NON-CHANGE**. |
| 20 | `context_tuner.py` cohorts: `64`, `85` (`'applied','rejected'`) | under-scored / relevant signal | **CHANGE** — add the three. `archived` cohorts (`50`,`96`,`111`,`124`) → **NON-CHANGE**. |

**Key findings worth calling out:**

- `NON_NEW_STATUSES` in both dedupe scripts is **unused dead code** — the actual protection is `_status != "new"`, which already treats `interviewing`/`offer`/`withdrawn` as "non-new" (protected from dedup). No functional change required there.
- `email_monitor` / `find_jobs_by_company` is fully out of scope (confirmed in Clarifications).
- The notes-only `changed_at` rewrite in `set_status` (Context §"Notes edits move dates") becomes **irrelevant** once reports key off event dates, not `job_tracking.changed_at`. `status_history` is already append-only on true status changes, so backfill dates are safe. → no `set_status` body change strictly required for report correctness (see plan §FR-007).

The new FR-026–FR-031 (explicit dates, date validation, provenance columns `source`/`source_ref`, agent-ready API, "+ Add past event") affect the lifecycle **model and the storage entry point**, not any status-list cohort — so this FR-023 inventory is unchanged by them. They are covered in the plan's data-model, migration and storage sections.

---

## 3. Design decisions (from clarification + code)

| Decision | Choice | Rationale |
|---|---|---|
| Event store | Reuse `interactions` (add `notes`, `source`, `source_ref` columns) | Single source of truth already partially built (`application_submitted`/`interview`/`decision_received`). New table would duplicate it. |
| Reasons storage | `interactions.notes`, un-prefixed | Prefix ("Employer:"/"Me:") added at display/export from event type. Distinct from `job_tracking.notes`. |
| Derived values | Computed on read from events (never stored) | Constitution IV — deterministic, no LLM; small volume (<500) so no caching. |
| Status/event consistency | One storage entry point `record_lifecycle_event` (new) | FR-006 — single transaction, transition-guarded, bypasses `_auto_log_status_interaction`. |
| Report date source | `occurred_at` of `application_submitted` (via backfill) | Fixes the August/September bug (US1). |
| Migration | Idempotent `_migrate_application_lifecycle(conn)` + `backfill_lifecycle_events` migrations row | FR-024/025; adds `notes`/`source`/`source_ref` via `PRAGMA table_info` guard + partial unique index on `(job_id,type,source_ref)`; backfilled rows `source='migration'`, pre-existing auto-logged rows `source='manual'`. |
| Provenance & idempotency | `interactions.source` (`manual`/`migration`/`agent:<name>`) + optional `source_ref`, partial unique index | FR-030/031; non-manual writes idempotent on `(job_id,type,source_ref)`; manual wins; a manual edit of an agent event flips its `source` to `manual`. |
| Date validation | Explicit dates on every write; no click/call-time fallback; FR-027 rules on all paths | FR-026/027; identical for UI and API; future dates allowed for interviews only; refused writes name the conflict. |
