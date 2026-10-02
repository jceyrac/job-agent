# Feature Specification: Application Lifecycle Tracking + Date-Correct Reports

**Feature Branch**: `030-application-lifecycle`

**Created**: 2026-10-02

**Status**: Ready for plan — clarified 2026-10-02. Mockup (`mockup.html`) approved by Jean Claude on 2026-10-02.

**Input**: User description: "The tracker doesn't manage the life cycle of an application: application date, positive response / interview invite, interviews 1..n, rejection without interview vs rejection after interviews. Add an `interviewing` status with an 'Interviewing' action button wherever status actions exist (Jobs page, Job detail page). Reports must be date-correct: the September report currently shows applications made in August that were rejected in September. The app is not an ORP form clone — ORP is one export view of a general lifecycle."

---

## Clarifications

### Session 2026-10-02

- Q: Ten lifecycle + pipeline actions no longer fit the fixed 8-button bar. Layout? → A: **Contextual action bar** (state-transition pattern; proposed by Claude, confirmed by mockup approval): show only actions valid in the job's derived state; one **primary** action (most likely next step, always first slot); other valid actions as secondary buttons; rare/parking actions (Expired, Not relevant, Withdrawn) in a trailing **⋯** popover. Fixed ordering: primary → secondaries → ⋯.
- Q: Should lifecycle actions ask for a date? → A: **Always** — inline date prompt defaulting to today (2 clicks), for Applied, Interviewing / + Interview, Rejected, Offer, Withdrawn.
- Q: End states beyond Rejected? → A: **Offer + Withdrawn** (I drop out). No accepted/declined sub-states: declining an offer = Withdrawn; an offer rescinded by the employer = Rejected.
- Q: Event store (former OQ-1)? → A: **Reuse `interactions`** (already holds `application_submitted` / `interview` / `decision_received` and feeds contact relationship status). A new table would duplicate the source of truth. Consequence: lifecycle jobs MUST have a `company_id` (resolve/create the company on first lifecycle action). Final sizing from diagnostic D1 at plan time.
- Q: Language of the Applications report? → A: **English only** — all column headers and values (preview and CSV). ORP form equivalents for reference only: Interview held = "Entretien d'embauche"; ORP result `pending` / `hired` / `negative` = "En suspens" / "Engagement" / "Réponse négative".
- Q: ORP result for a withdrawn application? → A: **`negative (withdrawn by me)`** — maps to "Réponse négative" on the ORP form, while stating explicitly that I refused the job / dropped out (what really happened). The Current stage label (`Withdrawn — …`) and the optional reason note keep the detail.
- Q: Reasons for end states? → A: Every rejection (at any stage: applied, interviewing, offer rescinded) can carry an optional **employer's reason**; every withdrawal (including declining an offer) can carry an optional **my reason**. Both are free text, entered in the confirm prompt or added/edited later from the timeline, and shown in the reports. The two are labelled distinctly so the report always says who decided.
- Q: Job detail "Tracking status" selectbox (former OQ-3)? → A: **Keep it** for corrections, routed through the same lifecycle entry point as the buttons (FR-006); lifecycle statuses chosen there open the same date prompt.
- Q: Does `email_monitor.py`'s auto-transition of `rejected` / `interview_scheduled` / `offer` emails (dead for the latter two today) change under spec 030? → A: **No** — email_monitor is unused (never used). `email_monitor.py` is left unchanged and out of scope for 030, and `find_jobs_by_company()` stays as-is (no other caller needs it).
- Q: How are lifecycle dates set when I update statuses in a batch at month end, and how will a future application-monitoring agent set them? → A: **Dates are always explicit, never the click date.** Every event date is chosen manually (or by an agent from the email's received date), can be any past date, and events can be entered in any order — including adding a past event to a job that is already in an end state. The lifecycle entry point is a **programmatic storage API** (not tied to Streamlit) so a future agent can call it; every event records its **source** (`manual` / `migration` / `agent:<name>`) and an optional **source reference** (e.g. email Message-ID) for idempotency. An agent never silently overwrites a manually entered event. Building the agent itself is out of scope (FR-026–FR-031).
- Q: Where do per-event reasons/notes live? (the `interactions` table has no `notes` column — only `subject` / `body_excerpt`) → A: **Add `interactions.notes` (nullable TEXT)** in the spec-030 migration, idempotent (check `PRAGMA table_info(interactions)` before `ALTER TABLE`). Reasons are stored WITHOUT the "Employer:"/"Me:" prefix — the prefix is added at display/export time from the event type. Distinct from `job_tracking.notes`, which stays the job-level note and never feeds the Reason column.

---

## Context (verified against live source, 2026-10-02)

- **Report date bug.** `export_jobs.build_export_rows()` filters on `job_tracking.status IN (...)` AND `date(job_tracking.changed_at) BETWEEN ...`. `changed_at` is the date of the *last* status change. Observed on the live September export (`jobs_2026-09-01_2026-09-30.csv`, 16 rows): 6 rows are applications made in August, rejected in September, dated with the rejection date. Regenerating August would omit them.
- **Notes edits move dates.** `JobStorage.set_status(job_id, status, notes=...)` rewrites `changed_at` even when the status is unchanged (notes-only edit).
- **Half-built lifecycle.** The `interactions` table already supports `application_submitted`, `interview`, `decision_received` (+ `outcome` positive/negative, `occurred_at`, `job_id`). `set_status()` auto-logs via `_auto_log_status_interaction()` using `_STATUS_INTERACTION_MAP`, which already maps `interviewing` and `offer` — but `VALID_STATUSES = {new, queued, ready, applied, rejected, archived, expired}` rejects both, so that code is dead.
- **Auto-log gap.** `interactions.company_id` is `NOT NULL`; `_auto_log_status_interaction()` silently returns when the job has no `company_id`. Auto-logged events always use `now()` (no backdating).
- **Action bar is shared.** `tracker_views/job_helpers.py` defines `ACTIONS`, `ACTION_LABELS`, `ENABLED` (per derived state), `_derive_state()`, `_handle_action()`, `_render_action_bar()`. `_render_action_bar` is called from `tracker_views/jobs.py` (scope `card`) and `tracker_views/job_detail.py` (scope `detail`). One change there covers both pages. `_request_archive()` is the existing inline-prompt pattern (session-state flag + Confirm/Cancel).
- **Streamlit** `>=1.40.0` is pinned: `st.popover` and `st.button(type="primary")` are available (primary already used in `settings.py`, `onboarding.py`).
- **Status lists hard-coded elsewhere** (must all learn the new statuses): `storage.VALID_STATUSES`; `tracker_views/job_detail.py` "Tracking status" selectbox; `tracker_views/jobs.py` status filter; `tracker_views/dashboard.py` `status_order` / `status_icons`; `tracker_views/shared.py` `_ENGAGED_STATUSES` and status labels; `export_jobs.py` `STATUSES_ALL` / `STATUS_TO_RESULTAT`; `scripts/dedupe_jobs.py` and `scripts/dedupe_title_company.py` `NON_NEW_STATUSES`.
- **Status consumers that mean "I applied".** `preference_report.py` and `context_tuner.py` use `status IN ('applied','rejected')` cohorts. `storage.find_jobs_by_company()` (used by the unused `email_monitor.py`) is out of scope and stays as-is.
- `tracker_legacy.py` is legacy — out of scope.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Date-correct application report (Priority: P1)

As the user, when I generate the report for a month, I see exactly the applications I submitted in that month, each with its current outcome, regardless of what happened to it afterwards.

**Why this priority**: This is the bug that blocks the monthly ORP declaration today and makes every other report untrustworthy.

**Independent Test**: On the live DB after backfill, generate September 2026: the 6 August applications rejected in September are absent; generating August 2026 shows them with outcome "Rejected — no interview".

**Acceptance Scenarios**:

1. **Given** a job applied on 2026-08-20 and rejected on 2026-09-04, **When** I generate the Applications report for 09/2026, **Then** the job is not listed.
2. **Given** the same job, **When** I generate 08/2026, **Then** it is listed with application date 20/08/2026, outcome "Rejected — no interview", interviews 0, decision date 04/09/2026.
3. **Given** a job applied on 2026-09-10 whose notes I edit on 2026-10-01, **When** I generate 10/2026, **Then** the job is not listed.

---

### User Story 2 — "Interviewing" action and interview logging (Priority: P1)

As the user, when an application gets a positive response, I click **🎤 Interviewing** — the primary action of any `applied` job, on the job card (Jobs page) and in the Job detail action bar — enter the invite date and/or the interview date (default today; future dates allowed), and the job moves from `applied` to `interviewing`. For each later round I click the primary action, now **🎤 + Interview**; the round number is computed.

**Why this priority**: The missing state the user explicitly asked for; required to distinguish the two kinds of rejection.

**Independent Test**: Take an `applied` job, click Interviewing, confirm with an interview date; the state label shows "Interviewing · round 1 · next 14/10". Log a second interview; it shows "round 2".

**Acceptance Scenarios**:

1. **Given** a job in `applied`, **When** I click 🎤 Interviewing and confirm with invite date 06/10 and interview date 14/10, **Then** status = `interviewing`, one invite event (06/10) and one interview event (14/10) exist, and the state label reads "Interviewing · round 1 · next 14/10".
2. **Given** a job in `interviewing` with 1 interview, **When** I click 🎤 + Interview and confirm 21/10, **Then** a second interview event exists and the label reads "round 2". Status stays `interviewing`.
3. **Given** a job in a pre-application state (`scraped` / `extracted` / `scored` / `queued` / `prepared`), **Then** no Interviewing action is shown (an application must exist first).
4. **Given** I click 🎤 Interviewing, **When** I click Cancel in the inline prompt, **Then** nothing is written.

---

### User Story 3 — End states: rejection with vs without interview, offer, withdrawal (Priority: P1)

As the user, when I record a rejection, the app knows whether it happened before or after interviews, without me choosing a different status. When I get an offer, I record it. When I drop out of a process (including declining an offer), I record a withdrawal.

**Why this priority**: Required by the user's lifecycle and by the ORP "Entretien d'embauche" + "Résultat" fields.

**Independent Test**: Reject one `applied` job and one `interviewing` job with 2 interviews held; labels read "Rejected — no interview" and "Rejected — after 2 interviews". Withdraw from an `interviewing` job with 1 interview held; label reads "Withdrawn — after 1 interview".

**Acceptance Scenarios**:

1. **Given** a job in `applied` with no interview events, **When** I click ❌ Rejected and confirm date 04/09, **Then** status = `rejected`, a negative decision event dated 04/09 exists, and the label reads "Rejected — no interview".
2. **Given** a job in `interviewing` with 2 interviews held, **When** I click ❌ Rejected, **Then** the label reads "Rejected — after 2 interviews".
3. **Given** a job in `interviewing`, **When** I click 🎉 Offer and confirm, **Then** status = `offer` and a positive decision event exists.
4. **Given** a job in `applied` or `interviewing`, **When** I choose ⋯ → 🏳️ Withdrawn and confirm a date and "My reason: salary below range", **Then** status = `withdrawn`, a withdrawal event exists with that reason, and the label reads "Withdrawn — no interview" / "Withdrawn — after N interview(s)".
5. **Given** a job in `offer`, **When** I choose ⋯ → 🏳️ Withdrawn, **Then** label reads "Withdrawn — offer declined". **When** instead I choose ⋯ → ❌ Rejected, **Then** label reads "Rejected — offer withdrawn by employer".
6. **Given** a job in `interviewing`, **When** I click ❌ Rejected and enter "Employer's reason: went with a candidate with more B2B payments experience", **Then** the negative decision event stores that reason, and it appears on the timeline and in the Reason column of both reports.
7. **Given** a `rejected` job recorded without a reason, **When** the feedback arrives later and I edit the decision event in the timeline, **Then** I can add the employer's reason without changing the decision date or the status.
8. **Given** I leave the reason empty, **Then** the rejection / withdrawal is still recorded (reasons are always optional).

---

### User Story 4 — Application timeline on Job detail (Priority: P2)

As the user, on the Job detail page I see a chronological timeline of the application (applied, invite, interviews with round numbers, decision / withdrawal) and can correct the date of any lifecycle event or delete a mistaken one.

**Why this priority**: Mistakes and backdating are routine (logging an interview the day after); without correction the reports inherit errors.

**Independent Test**: Edit the application date of a job from 02/10 to 30/09; it moves from the October report to the September report.

**Acceptance Scenarios**:

1. **Given** a job with 4 lifecycle events, **When** I open Job detail, **Then** the Timeline lists them oldest → newest with type, date, round number for interviews, outcome for decisions; future interviews are marked "scheduled".
2. **Given** a timeline event, **When** I change its date and save, **Then** reports and derived labels reflect the new date immediately.
3. **Given** I delete the only interview event of an `interviewing` job, **Then** the system asks whether to revert status to `applied` (no silent status drift).

---

### User Story 5 — Activity report (Priority: P2)

As the user, I can generate an **Activity** report for a period listing every lifecycle event that occurred in it (invites, interviews, decisions, withdrawals), including for applications made in earlier months — e.g. "interviewing in October for a job applied in September".

**Why this priority**: The pipeline view the user asked for; useful for ORP counsellor meetings; not required for the monthly declaration itself.

**Independent Test**: An application from 25/09 with an interview on 14/10 appears in Activity 10/2026 with "applied 25/09/2026" shown, and does not appear in Applications 10/2026.

**Acceptance Scenarios**:

1. **Given** the case above, **When** I generate Activity for 10/2026, **Then** one row: date 14/10/2026, event "Interview · round 1", company, title, application date 25/09/2026, current stage.
2. **Given** an August application rejected on 04/09, **When** I generate Activity for 09/2026, **Then** one row: "Rejected — no interview", application date shown.

---

### User Story 6 — Backfill of existing applications (Priority: P1, prerequisite of US1)

Existing `applied` / `rejected` jobs get their lifecycle reconstructed from `status_history` so that past months report correctly.

**Independent Test**: After migration, every `applied` / `rejected` job either has an application event, or is listed as "application date missing".

**Acceptance Scenarios**:

1. **Given** a `rejected` job whose `status_history` has `applied` on 2026-08-20 and `rejected` on 2026-09-04, **When** the migration runs, **Then** it has an application event dated 2026-08-20 and a negative decision event dated 2026-09-04.
2. **Given** a `rejected` job with no `applied` row in `status_history`, **Then** no application event is invented; the job is listed in the migration log and surfaces in the Applications report UI under "Application date missing".
3. **Given** a job that already has an auto-logged `application_submitted` interaction, **Then** the migration does not create a duplicate.
4. **Given** the migration has run once, **When** it runs again, **Then** nothing changes (idempotent, recorded in `migrations`).

---

### User Story 7 — Month-end batch update with back-dated events (Priority: P1)

As the user, I usually update statuses at the end of the month. I record each event with the date it really happened (application, invite, interviews, decision, withdrawal), in whatever order I remember them, and the reports come out exactly as if I had logged everything on the day.

**Why this priority**: This is the user's actual working pattern; if back-dating is awkward or out-of-order entry is impossible, the reports are wrong again.

**Independent Test**: On 30/09, for a job still in `new`, record Applied 03/09 then Rejected 18/09 (reason "no fit"). Then add an interview dated 10/09 from the timeline. Result: status `rejected`, label "Rejected — after 1 interview", Applications 09/2026 shows the job with application date 03/09, Activity 09/2026 shows three rows dated 03/09, 10/09, 18/09.

**Acceptance Scenarios**:

1. **Given** a job in `new` on 30/09, **When** I click ✅ Applied and enter 03/09, **Then** the application event is dated 03/09 and the job appears in Applications 09/2026 — the date I clicked plays no role anywhere.
2. **Given** several jobs to update in one session, **When** I confirm a date in one prompt, **Then** the next prompt I open in the same session pre-fills that same date instead of today (I can still change it).
3. **Given** a job already `rejected` (decision 18/09), **When** I use "+ Add past event" on its timeline to add an interview dated 10/09, **Then** the interview is stored, round numbers and labels are recomputed by date ("Rejected — after 1 interview"), and the status stays `rejected`.
4. **Given** a job applied on 03/09, **When** I enter a decision dated 01/09 (before the application), **Then** the save is refused with a message naming the conflicting date.
5. **Given** I enter an interview dated after the job's decision date, **Then** the save is refused (an interview cannot follow the end of the process); a decision, withdrawal or application date in the future is also refused. Only interviews may be in the future.

---

### User Story 8 — Ready for a future application-monitoring agent (Priority: P3)

As the user, I will later add an agent that reads recruiter emails and records lifecycle events with the date each email was received. This spec does not build that agent; it makes sure the lifecycle model can receive its updates safely.

**Independent Test** (via tests, no agent): call the storage API with `source="agent:test"`, a past date and `source_ref="<msg-123@example.com>"`: the event is stored with that date and source; calling again with the same `source_ref` changes nothing; calling it to change the date of a manually entered event is refused.

**Acceptance Scenarios**:

1. **Given** an agent call recording a rejection dated with the email's received date, **Then** the event is stored with that date, `source = agent:<name>`, the email reference, and the same status transition and validations as a manual entry (FR-006, FR-027).
2. **Given** the same email is processed twice, **Then** no duplicate event is created (idempotent on job + event type + source reference).
3. **Given** an event I entered manually, **When** an agent tries to change its date or reason, **Then** the change is refused and logged; manual entries always win.
4. **Given** an event created by an agent, **Then** the timeline shows its source (e.g. "from email") and I can edit or delete it like any other event; once I edit it, it becomes `manual`.

---

### Edge Cases

- Job has no `company_id` (today's auto-log silently skips it): the first lifecycle action MUST resolve or create the company so the event is recorded (FR-004).
- Interview date in the future (scheduled): allowed; shown as "next dd/mm"; counts toward "interviews held" only once its date ≤ today.
- Rejected directly from `queued` / `ready` (never applied): not an application — no Rejected action is offered pre-application (use ⋯ → Not relevant / Expired); status behaviour as today.
- Two interviews on the same day: allowed (two rounds).
- Status changed via the Job detail "Tracking status" selectbox instead of buttons: MUST produce the same events and the same date prompt (single code path).
- Re-clicking 🎤 + Interview by mistake: correctable via Timeline (US4).
- Two inline prompts open on the same card (e.g. Rejected then Withdrawn): only one pending prompt per job at a time; opening another replaces the first.
- Events entered out of chronological order (month-end catch-up): all derived values (round numbers, label, current stage, application date) are computed from event dates, never from insertion order or `created_at`.
- Adding a past event that would change the end state (e.g. a later-dated offer on a rejected job): the system asks before changing the status, as in FR-015.
- Same-day events: an interview and a decision on the same day are allowed; the decision is treated as following the interview.

---

## Requirements *(mandatory)*

### Statuses & lifecycle model

- **FR-001**: The tracking status set MUST add `interviewing`, `offer` and `withdrawn` (`VALID_STATUSES`). Lifecycle transitions: `applied → interviewing | rejected | withdrawn`; `interviewing → interviewing (new round) | offer | rejected | withdrawn`; `offer → withdrawn (offer declined) | rejected (offer rescinded)`. Existing non-lifecycle statuses and transitions are unchanged.
- **FR-002**: Lifecycle facts MUST be stored as dated, job-linked **events** in the `interactions` table — not as per-stage date columns and not as sub-statuses: `application_submitted` (date), `interview_invited` (date, optional), `interview` (date, repeatable, optional notes), `decision_received` (date, outcome positive | negative, optional **employer's reason** for negative decisions), `withdrawn` (date, optional **my reason**). `interview_invited` and `withdrawn` are added to `INTERACTION_TYPES`. Interview notes, the employer's reason (negative decisions) and my reason (withdrawals) are all stored in a new nullable `interactions.notes` column, un-prefixed.
- **FR-003**: The following MUST be **derived** from events, never stored: interview round number (order of interview events by date), interviews held (count with date ≤ today), next scheduled interview, application date (earliest `application_submitted`), decision date/outcome, and the display label: `Applied`, `Interviewing · round N` (+ `· next dd/mm` if scheduled), `Offer`, `Rejected — no interview`, `Rejected — after N interview(s)`, `Rejected — offer withdrawn by employer`, `Withdrawn — no interview`, `Withdrawn — after N interview(s)`, `Withdrawn — offer declined`.
- **FR-004**: Recording a lifecycle event MUST NOT be silently skipped when the job has no `company_id`: the lifecycle entry point resolves the company by normalized name or creates it, then records the event.
- **FR-005**: Lifecycle events MUST carry a user-chosen date (default today, editable; future allowed for interviews only). Auto-logging with `now()` alone is insufficient.
- **FR-006**: Status and events MUST stay consistent through a single storage entry point: a lifecycle action writes the event(s) and the status transition in one transaction and rejects transitions not allowed by FR-001. No UI path writes lifecycle events or lifecycle statuses any other way; `_auto_log_status_interaction()` MUST NOT create competing rows for lifecycle statuses.
- **FR-007**: A notes-only edit MUST NOT change any lifecycle date and MUST NOT affect any report.

### Contextual action bar (Jobs page cards + Job detail page)

- **FR-008**: The shared action bar (`job_helpers._render_action_bar`) MUST become contextual: for the job's derived state it renders only the valid actions, as **[primary] [secondary…] [⋯]**, primary rendered with `type="primary"`, ⋯ as an `st.popover`. Both the Jobs page cards and the Job detail page use it (single change point).
- **FR-009**: Actions per derived state:

  | Derived state | Primary | Secondary | ⋯ menu |
  |---|---|---|---|
  | scraped | 🔍 Extract | ✅ Applied | ⏰ Expired, 🚫 Not relevant |
  | extracted | 🎯 Score | 🚀 Queue, 📝 Prepare, ✅ Applied | ⏰ Expired, 🚫 Not relevant |
  | scored | 🚀 Queue | 📝 Prepare, ✅ Applied | ⏰ Expired, 🚫 Not relevant |
  | queued | 📝 Prepare | ✅ Applied | ⏰ Expired, 🚫 Not relevant |
  | prepared | ✅ Applied | — | ⏰ Expired, 🚫 Not relevant |
  | applied | 🎤 Interviewing | ❌ Rejected | 🏳️ Withdrawn, 🚫 Not relevant |
  | interviewing | 🎤 + Interview | 🎉 Offer, ❌ Rejected | 🏳️ Withdrawn |
  | offer | — | — | 🏳️ Withdrawn (offer declined), ❌ Rejected (offer rescinded) |
  | rejected | — | — | 🚫 Not relevant |
  | withdrawn | — | — | 🚫 Not relevant |
  | archived, expired | — | — | — |

  Pre-application rows reproduce today's `ENABLED` sets; only the primary choice is new.
- **FR-010**: `_derive_state()` MUST return `interviewing`, `offer`, `withdrawn` for those tracking statuses (checked before the `prepared` / `queued` branches, like `applied` today).
- **FR-011**: The state line under the action bar MUST show the derived label (FR-003), e.g. "Interviewing · round 2 · next 21/10", replacing today's raw `State: **…**` caption.
- **FR-012**: Applied, Interviewing / + Interview, Rejected, Offer and Withdrawn MUST open an inline confirm prompt (same pattern as `_request_archive`) with a date input defaulting to today; Confirm writes, Cancel writes nothing. First Interviewing: optional "invite received on" date + optional "interview on" date — at least one required. Rejected: optional free-text "Employer's reason" (any stage, incl. offer rescinded). Withdrawn: optional free-text "My reason" (incl. offer declined). Reasons are never required. Expired and Not relevant keep today's behaviour.

### Job detail

- **FR-013**: The Job detail "Tracking status" selectbox MUST include the new statuses and route lifecycle statuses through the FR-006 entry point with the same date prompt. Behaviour must not diverge from the buttons.
- **FR-014**: Job detail MUST show an **Application timeline** section (only when the job has ≥ 1 lifecycle event) listing events chronologically with date, type, round number, outcome, notes.
- **FR-015**: Each lifecycle event MUST be editable (date, notes; reason for decision and withdrawal events) and deletable from the timeline. Adding or editing a reason alone MUST NOT change the event date or the status. An edit/delete that changes the derived state MUST prompt before changing the status.

### Reports

- **FR-016**: The **Applications** report MUST select rows by **application date** in the period, never by `job_tracking.changed_at`. Each row shows the outcome as of today.
- **FR-017**: The Applications report MUST be correct without any status selection. The current "Statuses" multiselect becomes an optional "Current stage" filter, default = all stages.
- **FR-018**: Applications report columns (preview + CSV): Application date, Company / location, Title, Current stage (derived label), Interviews held, Last event date, Decision date, Reason (employer's reason for rejections prefixed "Employer:", my reason for withdrawals prefixed "Me:", empty otherwise), URL, ID, plus ORP-helper columns "Interview held" (`yes` / `no` = interviews held > 0) and "ORP result" derived from events: no decision and not withdrawn → `pending`; offer → `hired`; negative decision → `negative`; withdrawn → `negative (withdrawn by me)` — the value must make explicit that the candidate refused / dropped out, not the employer. All column headers and values in the preview and the CSV are in English (no French labels). `STATUS_TO_RESULTAT` is replaced by this event-based mapping.
- **FR-019**: Metrics above the preview: Entries, Pending, Interviewing, Offer, Rejected, Withdrawn.
- **FR-020**: A second preset **Activity** MUST list every lifecycle event whose date falls in the period: Event date, Event (label incl. round), Company, Title, Application date, Current stage, Reason (same format as FR-018, on decision and withdrawal rows), URL, ID.
- **FR-021**: Lifecycle jobs with no application date MUST NOT be silently dropped: the Applications report shows a separate collapsed "Application date missing (N)" list linking to each job's timeline.
- **FR-022**: The CLI `export_jobs.py` MUST use the same query functions as the UI.

### Other consumers

- **FR-023**: Everywhere a status list or status-based query means "I applied to this job", `interviewing`, `offer` and `withdrawn` MUST be included where appropriate: at minimum `shared._ENGAGED_STATUSES` and status labels, `dashboard` status order/icons, `jobs.py` status filter, `job_detail` selectbox, dedupe scripts `NON_NEW_STATUSES`. For `preference_report.py` / `context_tuner.py`, include them wherever `applied` is used as a positive-interest signal. The plan MUST enumerate every occurrence (grep) and justify each change or non-change.

### Migration

- **FR-024**: An idempotent migration (recorded in `migrations`) MUST reconstruct events for existing jobs from `status_history`: application date = earliest `applied` row; for `rejected` jobs with a prior `applied` row, a negative decision dated at the `rejected` row. Existing `application_submitted` / `decision_received` interactions for the same job are reused, not duplicated. The same migration adds `interactions.notes`, `interactions.source` and `interactions.source_ref` (nullable TEXT) idempotently — checking `PRAGMA table_info(interactions)` before each `ALTER TABLE` — and a partial unique index on (job_id, type, source_ref) WHERE source_ref IS NOT NULL (FR-031).
- **FR-025**: The migration MUST NOT invent dates. Jobs it cannot date are logged (id, company, title, status) and covered by FR-021.

### Manual dating and future automation

- **FR-026**: Every lifecycle event date MUST be chosen explicitly (prompt, timeline, or API argument). The moment of the click or the API call MUST NOT be used as an event date anywhere; `created_at` is bookkeeping only and never shown in reports.
- **FR-027**: Date validation, applied identically to UI and API writes: application, invite, decision and withdrawal dates MUST NOT be in the future; interview dates may be in the future; no event may be dated before the job's application date; no interview may be dated after the job's decision or withdrawal date. Same-day events are allowed. A refused write names the conflicting event and date.
- **FR-028**: The Job detail timeline MUST offer **"+ Add past event"** for any job with an application event, allowing any lifecycle event type (invite, interview, decision, withdrawal) with any valid date, regardless of the current status. Adding an event never bypasses FR-006: if the derived end state changes, the user is asked before the status changes.
- **FR-029**: Within one tracker session, the date prompt SHOULD pre-fill the last date the user confirmed in that session (fallback: today), to speed up month-end batch entry. The prompt also shows the job's application date and last event date for context.
- **FR-030**: The lifecycle entry point MUST be a plain storage API in `storage.py` (no Streamlit dependency), documented in its docstring, callable by scripts and future agents with: job_id, event type, date, outcome, notes, `source`, optional `source_ref`. Every event stores `source` (`manual` | `migration` | `agent:<name>`) and optional `source_ref` (e.g. email Message-ID) in new nullable columns `interactions.source` / `interactions.source_ref`, added by the FR-024 migration. Existing rows get `source = 'manual'` (auto-logged) or `'migration'` (backfilled).
- **FR-031**: Writes from a non-manual source MUST be idempotent on (job_id, event type, source_ref) and MUST NOT modify or delete an event whose source is `manual`; such attempts are refused and logged. A manual edit of an agent-created event sets its source to `manual`. The timeline shows the source of non-manual events.

### Key Entities

- **Application lifecycle event** (`interactions` row with `job_id`): type (application_submitted | interview_invited | interview | decision_received | withdrawn), date (`occurred_at`), outcome (decisions), optional notes (stored in a nullable `notes` column, un-prefixed — the "Employer:"/"Me:" prefix is added at display/export), source (`manual` | `migration` | `agent:<name>`), optional source reference (e.g. email Message-ID), created-at (bookkeeping only).
- **Tracking status** (existing `job_tracking.status`): coarse current stage, kept consistent with events by FR-006. `job_tracking.notes` is the distinct job-level note and never feeds the report Reason column.
- **Derived application view**: per job — application date, interviews held, next interview, decision date/outcome, display label. Computed, never stored.

---

## Success Criteria *(mandatory)*

- **SC-001**: Regenerating September 2026 on the live DB after migration yields only applications submitted in September; the 6 August applications appear in August with label "Rejected — no interview".
- **SC-002**: Moving a job from Applied to Interviewing with a date takes 2 clicks (primary button + Confirm) from the Jobs page or Job detail.
- **SC-003**: A job card in state `applied` shows at most 2 buttons + the ⋯ menu; no state shows more than 4 buttons + ⋯.
- **SC-004**: Every job in `applied | interviewing | offer | rejected | withdrawn` has a computable derived label; 100 % either have an application date or appear in "Application date missing".
- **SC-005**: Editing notes on any job changes no report output.
- **SC-006**: `python -m pytest tests/` passes, with new tests for: back-dated and out-of-order entry (US7), FR-027 date validation (UI and API paths), FR-031 idempotency and manual-wins rule, report by application date, notes-edit invariance, derived labels (all rejection / withdrawal variants), reasons stored / edited / exported with the correct "Employer:" / "Me:" prefix, round numbering, transitions guarded by FR-001, per-state action sets (FR-009), migration idempotence and no-duplicate.
- **SC-007**: The US7 month-end scenario (Applied 03/09 + Rejected 18/09 entered on 30/09, then an interview 10/09 added afterwards) produces exactly the same reports as if each event had been logged on its day.

---

## Diagnostics to run on LIVE before /speckit.plan (read-only)

The dev DB is empty; these counts size FR-004 and FR-021 and drive the migration design (FR-024/025).

**Owner: Claude Code**, as the first step of `/speckit.plan`, over its existing SSH access to verva. Rules:

- **Read-only.** Open the DB with `mode=ro` (command below). No writes, no `docker compose` up/down/restart, no `git` operations, no file edits on verva. Diagnostics are not development work, so they respect the dev/live separation.
- Run against the `job-tracker` container (DB at `/app/data/jobs.db`, see `docker-compose.yml` / `paths.py`). The image may not ship the `sqlite3` CLI; use Python's `sqlite3` module.
- Record the raw output, the date it was run, and its interpretation in `specs/030-application-lifecycle/research.md`, and use it in `plan.md`. If any count is unexpectedly large, stop and report to Jean Claude before continuing the plan.

```bash
ssh <verva> 'docker exec -i job-tracker python -' <<'EOF'
import sqlite3
c = sqlite3.connect("file:/app/data/jobs.db?mode=ro", uri=True)
Q = {
 "D1": """SELECT t.status, COUNT(*), SUM(j.company_id IS NULL)
          FROM job_tracking t JOIN jobs j ON j.id = t.job_id
          WHERE t.status IN ('applied','rejected') GROUP BY t.status""",
 "D2": """SELECT COUNT(*) FROM job_tracking t
          WHERE t.status = 'rejected' AND NOT EXISTS (
            SELECT 1 FROM status_history h
            WHERE h.job_id = t.job_id AND h.status = 'applied')""",
 "D3": """SELECT t.status, COUNT(*) FROM job_tracking t
          WHERE t.status IN ('applied','rejected') AND NOT EXISTS (
            SELECT 1 FROM interactions i
            WHERE i.job_id = t.job_id AND i.type = 'application_submitted')
          GROUP BY t.status""",
}
for name, q in Q.items():
    print(name, "->", c.execute(q).fetchall())
EOF
```

(`<verva>` = the SSH host alias Claude Code already uses for verva.) The queries, for reference:

```sql
-- D1: lifecycle jobs without company_id (auto-log silently skipped these)
SELECT t.status, COUNT(*) AS n, SUM(j.company_id IS NULL) AS no_company
FROM job_tracking t JOIN jobs j ON j.id = t.job_id
WHERE t.status IN ('applied','rejected') GROUP BY t.status;

-- D2: rejected jobs with no 'applied' row in history (undatable)
SELECT COUNT(*) FROM job_tracking t
WHERE t.status = 'rejected'
  AND NOT EXISTS (SELECT 1 FROM status_history h
                  WHERE h.job_id = t.job_id AND h.status = 'applied');

-- D3: lifecycle jobs without an application_submitted interaction
SELECT t.status, COUNT(*) FROM job_tracking t
WHERE t.status IN ('applied','rejected')
  AND NOT EXISTS (SELECT 1 FROM interactions i
                  WHERE i.job_id = t.job_id AND i.type = 'application_submitted')
GROUP BY t.status;
```

---

## Assumptions

- `rejected` means "rejected by the employer after I applied"; not-relevant jobs use `archived`. A `rejected` job with an `applied` history row is therefore a real application.
- Report periods use local (Europe/Zurich) dates, consistent with existing `_now()` usage.
- Volume is small (< 500 lifecycle jobs): derived values computed on read, no caching.
- ORP-only fields (ORP assignment, occupation rate, postal address split, application channel) are out of scope.

## Non-goals

- No per-stage date columns, no stored sub-statuses, no accepted/declined offer sub-states.
- No ORP form auto-fill, no ORP-only fields.
- No changes to scrapers, scorer, `llm.py`, `cv_agent/`, `tracker_legacy.py`, `email_monitor.py`.
- email_monitor is unused; if reactivated, its status updates must go through the lifecycle entry point (FR-006) — separate spec.
- No calendar integration for interviews (possible later spec).
- No application-monitoring agent (email parsing, automatic event creation) — later spec; 030 only provides the API, provenance columns and rules it will use (FR-030, FR-031).

## Constitutional guardrails

- **Surgical**: changes limited to `storage.py`, `export_jobs.py`, `tracker_views/{job_helpers,jobs,job_detail,reports,dashboard,shared}.py`, dedupe scripts, status-cohort queries justified per FR-023, one migration, tests.
- **DB is sole truth**; the migration runs automatically on live through the normal deploy (`git pull` + `deploy.sh`), never by hand on verva.
- **UI mockup approval required before implementation** (rule since spec 022): `specs/030-application-lifecycle/mockup.html`.
