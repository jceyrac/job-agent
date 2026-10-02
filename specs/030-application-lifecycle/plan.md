# Implementation Plan — Application Lifecycle Tracking + Date-Correct Reports

**Branch**: `030-application-lifecycle` | **Date**: 2026-10-02 | **Spec**: `specs/030-application-lifecycle/spec.md`

**Input**: Feature specification `specs/030-application-lifecycle/spec.md` · Research: `research.md` · Mockup: `mockup.html` (approved).

---

## Summary

Replace the tracker's single `applied` state with a real application **lifecycle** (`applied → interviewing → offer | rejected | withdrawn`) stored as dated, job-linked **events** in the existing `interactions` table, and make the Applications report select rows by **application date** (not `job_tracking.changed_at`). The shared action bar becomes contextual, the Job detail page gains an editable timeline, and an Activity report lists lifecycle events per period. One idempotent migration backfills events for the 82 undated live jobs from `status_history`.

**Technical approach**: single storage entry point `record_lifecycle_event()` (FR-006) — a plain `storage.py` API with no Streamlit dependency — writes event(s) + status + history in one transaction, rejects disallowed transitions (FR-001) and invalid dates (FR-027), and records provenance (`source` / `source_ref`) on every event (FR-030/031). Derived values (round, interviews-held, label, application/decision date) are computed on read; `_auto_log_status_interaction` is bypassed for lifecycle statuses. The same API serves manual entry, out-of-order month-end backdating (US7) and a future email agent (US8).

---

## Technical Context

- **Language/Version**: Python 3.11 (`X | Y` unions, dataclasses, f-strings).
- **Primary Dependencies**: Streamlit `>=1.40` (`st.popover`, `st.button(type="primary")`), stdlib `sqlite3`. **No new dependencies.**
- **Storage**: SQLite (WAL) via `JobStorage` only. `data/jobs.db` (dev empty; live on verva via named volume `job_data`).
- **Testing**: `pytest` — `tests/test_storage.py` (in-memory DB).
- **Target Platform**: Streamlit tracker + `export_jobs.py` CLI (Docker on Linux server).
- **Project Type**: Web UI + CLI over SQLite.
- **Performance Goals**: Derived values computed on read, no caching (volume < 500 lifecycle jobs; D1 = 163).
- **Constraints**: Constitution V (surgical diffs); DB access only through `JobStorage`; no LLM in derived/structured fields (Constitution IV).
- **Scale/Scope**: ~163 lifecycle jobs live; 82 need backfill; 9 undatable.

---

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I — Filet large, filtre unique | ✅ | No scraper/scoring logic touched. `get_engaged_job_keys` / `get_jobs_for_scoring` are status-cohort fixes, not fit filtering. |
| II — Deux chemins | ✅ | Pure code change (schema + rules) via dev → git → deploy. No prose payload. |
| III — Profil unique | ✅ | Lifecycle is profile-independent (lives in `job_tracking`/`interactions`); `profile_id` seam untouched. |
| IV — Structure déterministe | ✅ | All derived values (round, count, label, dates) are deterministic SQL/Python, zero LLM. |
| V — Modifications chirurgicales | ✅ | Non-goals explicit; changes limited to the enumerated files; no new deps. |
| VI — Validation empirique | ✅ | SC-001 (September/August regression on live), migration idempotence, tests-first. |
| VII — Sécurité d'abord | ✅ | No new secrets/egress. Diagnostics on live were read-only. |
| VIII — Scrapers | ✅ | Not in scope. |
| IX — Scoring optionnel | ✅ | Not in scope. |
| **Stable core** | ⚠️ justified | `storage.py`, `tracker_views/shared.py` are explicitly in scope for **this** spec (they hold the status model + shared labels). `scrapers/`, `scorer.py`, `llm.py`, `cv_agent/`, `tracker_legacy.py`, `email_monitor.py`, `models.py`, `profiles.py`, `scrape.py`, `main.py` remain untouched. |

No violations requiring Complexity Tracking.

---

## Data model (event model + derived view)

### `interactions` (existing table + 3 columns)

Add three nullable columns (FR-002/FR-024): `notes TEXT`, `source TEXT`, `source_ref TEXT`. Event types used by the lifecycle:

| `type` | `direction` | `outcome` | `occurred_at` | `notes` | `source` |
|---|---|---|---|---|---|
| `application_submitted` | `outbound` | — | application date | — | `manual` / `migration` |
| `interview_invited` *(new)* | `inbound` | — | invite date (optional) | — | `manual` / `agent:<name>` |
| `interview` | `none` | — | interview date (repeatable) | optional interview notes | `manual` / `agent:<name>` |
| `decision_received` | `inbound` | `positive` / `negative` | decision date | optional employer's reason (negative) | `manual` / `agent:<name>` |
| `withdrawn` *(new)* | `none` | — | withdrawal date | optional my reason | `manual` / `agent:<name>` |

`INTERACTION_TYPES` += `interview_invited`, `withdrawn`.

**Provenance (FR-030/031)**: `source` ∈ `manual` | `migration` | `agent:<name>`; `source_ref` is optional (e.g. an email Message-ID). A partial unique index on `(job_id, type, source_ref) WHERE source_ref IS NOT NULL` makes non-manual writes idempotent. Existing auto-logged rows are set `source='manual'`; backfilled rows `source='migration'`. `occurred_at` is always the user-chosen (or agent-supplied) event date — `created_at` stays bookkeeping-only and is never shown in reports (FR-026).

### Derived view (never stored — FR-003)

Per job, from its events (ordered by `occurred_at`, then `id`):

- **application date** = earliest `application_submitted.occurred_at`.
- **interviews held** = count of `interview` with `date(occurred_at) <= today`.
- **next interview** = earliest `interview` with `date(occurred_at) > today`.
- **round N** = 1-based order of `interview` events by date.
- **decision date/outcome** = most recent `decision_received`.
- **label** (canonical, no icons): `Applied` · `Interviewing · round N` (+ `· next dd/mm`) · `Offer` · `Rejected — no interview` · `Rejected — after N interview(s)` · `Rejected — offer withdrawn by employer` · `Withdrawn — no interview` · `Withdrawn — after N interview(s)` · `Withdrawn — offer declined`.
- **Reason** (display/export only): `decision_received.negative` → `Employer: <notes>`; `withdrawn` → `Me: <notes>`; else empty.

---

## Migration design (FR-024 / FR-025)

New method `_migrate_application_lifecycle(self, conn)`, called in the migration block **after** `_migrate_monitored_companies(conn)` (`storage.py` ~line 866), inside the existing `__init__`/schema path. Two idempotent parts:

**Part A — columns + index (no `migrations` row; PRAGMA-guarded, matches existing column-add pattern):**

```python
cols = {row[1] for row in conn.execute("PRAGMA table_info(interactions)").fetchall()}
for col in ("notes", "source", "source_ref"):
    if col not in cols:
        conn.execute(f"ALTER TABLE interactions ADD COLUMN {col} TEXT")
conn.execute(
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_interactions_source_ref "
    "ON interactions(job_id, type, source_ref) WHERE source_ref IS NOT NULL"
)
```

Also add `notes TEXT`, `source TEXT`, `source_ref TEXT` to the base `interactions` `CREATE TABLE` (both the `SCHEMA` string and the inline "if not exists" block) so fresh DBs match.

**Part B — backfill (guarded by `migrations` row `backfill_lifecycle_events`):**

1. **Application events** — for each job with `job_tracking.status IN ('applied','rejected')` and **no** existing `application_submitted` interaction, take `MIN(status_history.changed_at)` where `status='applied'`:
   - non-NULL → `INSERT` `application_submitted` (`company_id`, `job_id`, `type`, `direction='outbound'`, `occurred_at=applied_at`, `source='migration'`).
   - NULL → **undatable**: do not invent a date; append `(id, company, title, status)` to the migration log (FR-025) → surfaces under FR-021.
2. **Decision events** — for each job with `status='rejected'`, a prior `applied` history row, and **no** existing `decision_received`, `INSERT` `decision_received` (`direction='inbound'`, `outcome='negative'`, `occurred_at = MIN(changed_at) where status='rejected'`, `source='migration'`).
3. **Provenance backfill** — pre-existing auto-logged `application_submitted` / `decision_received` / `interview` rows (the ones D3 shows already exist) get `source='manual'` where `source IS NULL`.
4. Existing `application_submitted`/`decision_received` rows are **reused, never duplicated** (the `NOT EXISTS` guards + the `migrations` gate).
5. `self._record_migration(conn, "backfill_lifecycle_events")` once complete.

D1 guarantees no `company_id IS NULL` among these jobs, so no company synthesis is needed in the migration. The migration runs automatically on live via the normal deploy; it is never run by hand on verva.

---

## Storage changes (single entry point — FR-004/005/006)

New method **`record_lifecycle_event(job_id, *, event, occurred_at, new_status, outcome=None, notes=None, source="manual", source_ref=None)`** — a plain `storage.py` API (no Streamlit import), documented in its docstring (FR-030), callable by the UI, scripts, and future agents:

1. **Date validation** (FR-027, identical UI/API): application, invite, decision and withdrawal dates MUST NOT be in the future; no event before the job's application date; no interview after the job's decision/withdrawal date; same-day allowed. A refused write raises naming the conflicting event + date.
2. Validate the transition against FR-001's allowed map (`applied → interviewing|rejected|withdrawn`; `interviewing → interviewing(+round)|offer|rejected|withdrawn`; `offer → withdrawn|rejected`). Reject otherwise.
3. **Idempotency + manual-wins** (FR-031): when `source != "manual"` and `source_ref` is set, a duplicate `(job_id, type, source_ref)` is a no-op (the partial unique index enforces it); a non-manual write never modifies or deletes an event whose `source == 'manual'` (refused + logged).
4. Resolve/create `company_id` by normalized name when the job has none (FR-004 — defensive; D1 shows none live).
5. In **one** `self._conn()` transaction: insert the event row(s) (`occurred_at` = user date, `notes` un-prefixed, `source`, `source_ref`), update `job_tracking.status`, append `status_history`.
6. Does **not** call `_auto_log_status_interaction()` (FR-006 — no competing rows).

Special cases handled inside (FR-012): first **Interviewing** writes `interview_invited` (optional) + `interview` (optional, ≥1 date required); **+ Interview** writes one `interview`; **Rejected** writes `decision_received(negative, notes=employer reason)`; **Withdrawn** writes `withdrawn(notes=my reason)`.

`record_lifecycle_event` also backs **"+ Add past event"** (FR-028): any event type, any valid date, in any order, regardless of current status — subject to the same FR-001 + FR-027 guards; when the derived end state would change, the caller prompts before the status flips (FR-015). Out-of-order month-end entry (US7) falls out naturally because every derived value keys off event dates, never insertion order or `created_at`.

Read helpers (also in `storage.py`, deterministic):

- `get_lifecycle_events(job_id) -> list[dict]` — chronological events for the timeline (FR-014), including `source`/`source_ref` so non-manual events can be shown (FR-031).
- `get_lifecycle_summary(job_id) -> dict` — derived view above (FR-003), single source for labels, reports, and the action bar.
- `update_lifecycle_event(event_id, *, occurred_at=None, notes=None)` / `delete_lifecycle_event(event_id)` — timeline edit/delete; FR-031 applies (a manual edit of an agent-created event flips its `source` to `manual`; non-manual edit/delete of a manual event is refused); a state-affecting change prompts before flipping status (FR-015).
- Report query functions shared by UI + CLI (FR-022): `build_application_rows(...)` (by application date), `build_activity_rows(...)` (by event date), `application_date_missing(...)`.

`VALID_STATUSES` += `interviewing`, `offer`, `withdrawn`. `set_status` and `_auto_log_status_interaction` are **left unchanged** — no production caller will pass lifecycle statuses to `set_status` after the UI reroute (callers today: `job_helpers` applied/rejected, `job_detail` selectbox — both rerouted; `email_monitor` out of scope; `cv_agent` uses archived/ready only).

---

## File-by-file change map

| File | Change |
|---|---|
| `storage.py` | `INTERACTION_TYPES` += 2; `VALID_STATUSES` += 3; add `notes`/`source`/`source_ref` to `interactions` CREATE TABLE (×2) + partial unique index; new `_migrate_application_lifecycle()` + call; new `record_lifecycle_event()` (FR-026/027/028/030/031) + read/derived helpers; `get_engaged_job_keys` + `get_jobs_for_scoring` cohorts (FR-023 #5/#6). |
| `export_jobs.py` | Replace `build_export_rows`/`STATUS_TO_RESULTAT` with event-based Applications + Activity builders (FR-016–020); English columns; Reason with `Employer:`/`Me:` prefix; `ORP result` derived. |
| `tracker_views/job_helpers.py` | Contextual action bar (FR-008/009/010/011/012): per-state primary/secondary/⋯; new `interviewing`/`offer`/`withdrawn` actions with inline date+reason prompts; derived label caption; session-sticky date pre-fill + application/last-event context in the prompt (FR-029). |
| `tracker_views/job_detail.py` | Selectbox += new statuses routed through entry point (FR-013); Application timeline section (FR-014/015) with "+ Add past event" (FR-028) and source badges for non-manual events (FR-031). |
| `tracker_views/reports.py` | Two presets (Applications / Activity); "Current stage" filter default all; metrics Entries/Pending/Interviewing/Offer/Rejected/Withdrawn (FR-019); "Application date missing" list (FR-021). |
| `tracker_views/dashboard.py` | `status_order`/`status_icons` += 3; widen pipeline row 6→9 columns. |
| `tracker_views/shared.py` | `_ENGAGED_STATUSES` += 3 (FR-023 #11). |
| `tracker_views/jobs.py` | Status filter += 3 (FR-023 #14). |
| `scripts/dedupe_jobs.py` / `dedupe_title_company.py` | Update dead `NON_NEW_STATUSES` constant += 3 (logic already correct via `!= "new"`). |
| `preference_report.py` / `context_tuner.py` | Add the three statuses to every `'applied'`-as-positive-interest cohort (FR-023 #19/#20); leave `archived` true-negative cohorts. |
| `tests/test_storage.py` | New unit tests (below), all in-memory. |

**Untouched**: `models.py`, `profiles.py`, `scrape.py`, `scorer.py`, `main.py`, `llm.py`, `cv_agent/`, `scrapers/`, `tracker_legacy.py`, `email_monitor.py`, `migrate_*.py`.

---

## Testing (tests-first, per SC-006)

New tests in `tests/test_storage.py` (in-memory DB), written **before** implementation:

1. **Report by application date** — a job applied 08-20/rejected 09-04 appears in August (label `Rejected — no interview`, decision date 04/09) and is absent from September (US1).
2. **Notes-edit invariance** — editing `job_tracking.notes` (and editing a reason alone) changes no report output and no event date (FR-007 / SC-005).
3. **Derived labels** — all rejection/withdrawal variants: no interview / after N / offer withdrawn / offer declined (FR-003).
4. **Reasons stored/edited/exported** with the correct `Employer:`/`Me:` prefix; stored un-prefixed (FR-018).
5. **Round numbering** — 1st and 2nd interview derive `round 1` / `round 2`; future interviews don't count as "held" (FR-003).
6. **Transition guards** — FR-001 rejects disallowed transitions (e.g. `scraped → interviewing`, `rejected → offer`).
7. **Per-state action sets** — FR-009 table (primary/secondary/⋯) matches for each derived state.
8. **Migration idempotence + no-duplicate** — run twice → no change; jobs with a pre-existing `application_submitted` don't get a second; undatable jobs are logged not dated; `source` set to `manual`/`migration` correctly (FR-024/025/030).
9. **Back-dated & out-of-order entry (US7 / SC-007)** — enter Applied 03/09 + Rejected 18/09 on 30/09, then add an interview 10/09 via "+ Add past event"; the Applications/Activity output equals the day-logged equivalent (status `rejected`, label "Rejected — after 1 interview").
10. **FR-027 date validation (UI + API paths)** — future application/decision/withdrawal refused; future interview allowed; event before application refused; interview after decision refused; a refused write names the conflicting event + date.
11. **FR-031 provenance & manual-wins** — agent write with `source_ref` is idempotent (duplicate → no-op); non-manual edit/delete of a manual event refused + logged; a manual edit of an agent event flips `source` to `manual`.

Run `python -m pytest tests/` before declaring done.

---

## Deployment

Normal path, nothing by hand on verva:

1. `git push` on dev Mac (branch `030-application-lifecycle` → PR/merge to `main`).
2. `git pull` + `scripts/deploy.sh` (or `docker compose build tracker` + `up -d tracker`) on verva.
3. Migration runs automatically on first storage init; verify SC-001 (September/August) against live after deploy.

---

## Rollback / risk

- **Column + events are additive**; `job_tracking`/`status_history` semantics unchanged for non-lifecycle statuses. Worst-case rollback = revert code; the new `interactions.notes` column and backfilled events are inert to the old report path.
- **Report behavior change is intentional and gated** by the mockup + SC-001.

---

## Open items (resolved in tasks, not blocking)

- Exact derived-label icon mapping (presentation) is finalized in `job_helpers`/`reports` during implementation; the canonical text labels are fixed here.
- `NON_NEW_STATUSES`: update-vs-delete is a trivial call left to the tasks step (recommend update for accuracy).
- FR-029 session-sticky date pre-fill is a `SHOULD` (best-effort UI convenience): the exact session-state key (`st.session_state` per spec convention) is fixed in `job_helpers` during implementation; no test is required beyond the prompt defaulting to today on first use.
