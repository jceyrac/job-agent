"""api/schemas.py — explicit pydantic response models (spec 041).

One model per payload, field-for-field over the `JobStorage` dict keys they
serialise. Data fields are typed `Any` on purpose: dates/enums/ids pass through
exactly as `JobStorage` returns them (byte-compatible with the tracker, SC-004)
— no coercion, no reformatting. `extra="forbid"` ensures an accidental new
internal column fails the contract test instead of leaking (US3-4).

Only the list envelope scalars (`total`, `limit`, `offset`) are typed `int` —
they are produced by the API's pagination layer, never read from the DB.
"""
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── Write request enums (spec 042) — values mirror the `JobStorage` constants ──

class JobStatus(str, Enum):
    new = "new"
    queued = "queued"
    ready = "ready"
    applied = "applied"
    interviewing = "interviewing"
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"
    archived = "archived"
    expired = "expired"


class LifecycleEventType(str, Enum):
    application_submitted = "application_submitted"
    interview_invited = "interview_invited"
    interview = "interview"
    decision_received = "decision_received"
    withdrawn = "withdrawn"


class InteractionType(str, Enum):
    discovered_on_posting = "discovered_on_posting"
    discovered_manual = "discovered_manual"
    outreach_sent = "outreach_sent"
    reply_received = "reply_received"
    intro_received = "intro_received"
    call = "call"
    meeting = "meeting"
    application_submitted = "application_submitted"
    interview_invited = "interview_invited"
    interview = "interview"
    decision_received = "decision_received"
    withdrawn = "withdrawn"
    note = "note"


class InteractionDirection(str, Enum):
    inbound = "inbound"
    outbound = "outbound"
    none = "none"


class InteractionOutcome(str, Enum):
    positive = "positive"
    negative = "negative"


class CompanyStatus(str, Enum):
    prospect = "prospect"
    watching = "watching"
    active_outreach = "active_outreach"
    engaged = "engaged"
    dormant = "dormant"
    passed_by_me = "passed_by_me"
    declined_by_them = "declined_by_them"
    blacklisted = "blacklisted"


# ── write request models (spec 042) ───────────────────────────────────────────

# Statuses the tracker routes through `record_lifecycle_event` with a user-chosen
# date (tracker_views/job_helpers.py `_LIFECYCLE_ACTIONS`) — never `set_status`.
LIFECYCLE_STATUSES = frozenset(
    {"applied", "interviewing", "offer", "rejected", "withdrawn"}
)


class StatusUpdate(ApiModel):
    status: JobStatus
    occurred_at: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _require_date_for_lifecycle(self):
        if self.status.value in LIFECYCLE_STATUSES and self.occurred_at is None:
            raise ValueError("occurred_at is required for lifecycle statuses")
        return self


class LifecycleEventCreate(ApiModel):
    event: LifecycleEventType
    occurred_at: str
    new_status: JobStatus
    outcome: InteractionOutcome | None = None
    notes: str | None = None


class LifecycleEventUpdate(ApiModel):
    occurred_at: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.occurred_at is None and self.notes is None:
            raise ValueError("provide at least one of occurred_at or notes")
        return self


# ── write request models — US2 (companies, contacts, interactions) ──────────

class CompanyRelationshipUpdate(ApiModel):
    status: CompanyStatus | None = None
    notes: str | None = None
    monitored: bool | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.status is None and self.notes is None and self.monitored is None:
            raise ValueError("provide at least one of status, notes, or monitored")
        return self


class ContactCreate(ApiModel):
    company_id: int
    linkedin_url: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    role_title: str | None = None
    role_family: str | None = None
    seniority: str | None = None
    email_status: str | None = None
    email_confidence: float | None = None
    x_handle: str | None = None
    telegram_handle: str | None = None
    github_handle: str | None = None
    phone: str | None = None
    is_unverified: bool = False
    notes: str | None = None


class ContactUpdate(ApiModel):
    is_unverified: bool | None = None
    last_verified_at: str | None = None
    is_current: bool | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if all(
            v is None
            for v in (self.is_unverified, self.last_verified_at, self.is_current, self.notes)
        ):
            raise ValueError("provide at least one field")
        return self


class InteractionCreate(ApiModel):
    type: InteractionType
    contact_id: int | None = None
    job_id: str | None = None
    direction: InteractionDirection = InteractionDirection.none
    outcome: InteractionOutcome | None = None
    subject: str | None = None
    body_excerpt: str | None = None
    occurred_at: str | None = None
    follow_up_due_at: str | None = None


# ── write request models — US3 (profile) ──────────────────────────────────────

class ActiveProfileUpdate(ApiModel):
    profile_id: str


class ProfileUpdate(ApiModel):
    name: str
    criteria: dict


# ── write request models — US4 (catalogue company facts) ─────────────────────

class CompanyFactUpdate(ApiModel):
    """Fact fields only — `notes`/`status`/`monitored` are absent and rejected as
    unknown fields by `extra="forbid"` (they are user-space, never catalogue)."""
    name: str | None = None
    website: str | None = None
    company_country: str | None = None
    industry_sector: str | None = None
    company_size: str | None = None
    x_handle: str | None = None
    careers_url: str | None = None
    ats_provider: str | None = None
    ats_identifier: str | None = None
    scraping_method: str | None = None
    research_notes: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if all(
            v is None
            for v in (
                self.name, self.website, self.company_country, self.industry_sector,
                self.company_size, self.x_handle, self.careers_url, self.ats_provider,
                self.ats_identifier, self.scraping_method, self.research_notes,
            )
        ):
            raise ValueError("provide at least one company fact field")
        return self


# ── Catalogue ────────────────────────────────────────────────────────────────

class CatalogueJob(ApiModel):
    id: Any
    title: Any
    company: Any
    company_id: Any
    url: Any
    source: Any
    location: Any
    base_location: Any
    posted_date: Any
    description: Any
    first_seen: Any
    last_seen: Any
    extracted_at: Any
    extracted_by: Any
    monitored_company_id: Any
    canonical_url: Any
    norm_title: Any
    norm_company: Any
    summary: Any
    work_mode: Any
    geo_zone: Any
    company_size: Any
    contract_type: Any
    company_country: Any
    industry_sector: Any
    language_required: Any
    country_code: Any


class CompanyFacts(ApiModel):
    id: Any
    name: Any
    website: Any
    careers_url: Any
    company_country: Any
    industry_sector: Any
    company_size: Any
    ats_provider: Any
    ats_identifier: Any
    scraper_id: Any
    first_seen_at: Any
    last_seen_at: Any
    summary: Any
    monitoring_status: Any
    scraping_method: Any
    research_notes: Any
    research_confidence: Any
    researched_at: Any
    x_handle: Any


class JobListEnvelope(ApiModel):
    items: list[CatalogueJob]
    total: int
    limit: int
    offset: int


# ── ingest (spec 044) — submit a posting to the shared catalogue ─────────────

class JobCreate(ApiModel):
    title: str = ""
    company: str = ""
    url: str | None = None
    location: str | None = None
    description: str | None = None
    # `source` is part of `JobPosting.id` for URL-less postings
    # (title::company::source), so the agent passes it to reproduce the same id
    # the local paste path computed. Default "manual" (the URL/manual source).
    source: str = "manual"

    @model_validator(mode="after")
    def _require_title_and_company_without_url(self):
        if not self.url and (not self.title or not self.company):
            raise ValueError("title and company are required when url is empty")
        return self


class JobCreated(ApiModel):
    job_id: str
    created: bool


# ── ingest plan (spec 045, US2) — assembled at request time, no profile ──────

class MonitoredTarget(ApiModel):
    company_id: Any
    name: Any
    ats_provider: Any
    ats_identifier: Any


class BoardSlugs(ApiModel):
    lever: list[str]
    workable: list[str]
    greenhouse_watching: list[str]


class IngestPlan(ApiModel):
    title_families: list[str]
    exclude: list[str]
    remote_or_hybrid: bool
    search_locations: list[str]
    greenhouse_boards: list[str]
    freshness_days: int
    enabled_sources: dict[str, bool]
    monitoring_gates: dict[str, bool]
    monitored_targets: list[MonitoredTarget]
    board_slugs: BoardSlugs


# ── ingest batch + events (spec 045, US3) — the API is the single writer ─────

class BatchPosting(ApiModel):
    """One posting in a batch — the `JobPosting` field set the orchestrator
    collected, minus the computed/dedup fields (id/canonical/norm). Unlike the
    single `JobCreate`, a posting with no URL and an empty title/company is NOT
    rejected here: `ingest_batch` reports it per-posting and keeps going."""
    source: str = "manual"
    title: str = ""
    company: str = ""
    url: str | None = None
    location: str | None = None
    description: str | None = None
    base_location: str | None = None
    posted_date: str | None = None
    monitored_company_id: int | None = None


class BatchOutcome(ApiModel):
    job_id: Any   # str for new/existing/dropped_engaged; None for a reported error
    result: Any   # "new" | "existing" | "dropped_engaged" | "error"


class IngestBatchResponse(ApiModel):
    new: int
    existing: int
    dropped_engaged: int
    outcomes: list[BatchOutcome]


class SourceDisabledEvent(ApiModel):
    type: Literal["source_disabled"]
    source: str      # scraper slug, e.g. "linkedin"
    reason: str


class RunEvent(ApiModel):
    type: Literal["run"]
    jobs_scraped: int
    duration_seconds: float
    run_type: str    # "scrape" | "scrape_monitored"


IngestEvent = Annotated[
    Union[SourceDisabledEvent, RunEvent], Field(discriminator="type")
]


class IngestEventResponse(ApiModel):
    ok: bool


# ── read:me — identity / feed ────────────────────────────────────────────────

class Profile(ApiModel):
    id: Any
    name: Any
    criteria: Any


class UserRow(ApiModel):
    id: Any
    display_name: Any
    active_profile_id: Any
    onboarding_completed_at: Any


class MeResponse(ApiModel):
    user: UserRow
    active_profile_id: Any
    profiles: list[Profile]


class FeedItem(ApiModel):
    id: Any
    company_id: Any
    title: Any
    company: Any
    url: Any
    source: Any
    location: Any
    base_location: Any
    posted_date: Any
    description: Any
    first_seen: Any
    last_seen: Any
    extracted_at: Any
    extracted_by: Any
    monitored_company_id: Any
    summary: Any
    work_mode: Any
    geo_zone: Any
    company_size: Any
    contract_type: Any
    company_country: Any
    industry_sector: Any
    language_required: Any
    score: Any
    reason: Any
    scored_by: Any
    country_code: Any
    tracked_status: Any
    status: Any
    notes: Any
    status_changed_at: Any
    score_count: Any


class FeedEnvelope(ApiModel):
    items: list[FeedItem]
    total: int
    limit: int
    offset: int


# ── read:me — job detail ─────────────────────────────────────────────────────

class Score(ApiModel):
    profile_id: Any
    score: Any
    reason: Any
    scored_by: Any
    scored_at: Any


class JobPrepare(ApiModel):
    id: Any
    title: Any
    company: Any
    company_id: Any
    url: Any
    source: Any
    location: Any
    base_location: Any
    posted_date: Any
    description: Any
    first_seen: Any
    last_seen: Any
    extracted_at: Any
    extracted_by: Any
    monitored_company_id: Any
    canonical_url: Any
    norm_title: Any
    norm_company: Any
    summary: Any
    work_mode: Any
    geo_zone: Any
    company_size: Any
    contract_type: Any
    company_country: Any
    industry_sector: Any
    language_required: Any
    country_code: Any
    status: Any


class LifecycleSummary(ApiModel):
    application_date: Any
    stage: Any
    label: Any
    reason: Any
    interviews_held: Any
    round: Any
    next_interview: Any
    decision_outcome: Any
    decision_date: Any
    withdrawn_date: Any
    last_event_date: Any
    orp_result: Any


class LifecycleEvent(ApiModel):
    id: Any
    type: Any
    direction: Any
    outcome: Any
    occurred_at: Any
    created_at: Any
    notes: Any
    source: Any
    source_ref: Any


class Application(ApiModel):
    user_id: Any
    job_id: Any
    profile_id: Any
    analysis: Any
    cover_letter: Any
    cv_bullets_selected: Any
    company_research: Any
    screening_answers: Any
    language: Any
    prepared_by: Any
    prepared_at: Any
    created_at: Any


class ApplicationUpdate(ApiModel):
    """Request body for `PUT /v1/me/jobs/{id}/application` (spec 044)."""
    analysis: str
    cover_letter: str


class JobDetailResponse(ApiModel):
    job: JobPrepare
    scores: list[Score]
    lifecycle: LifecycleSummary
    events: list[LifecycleEvent]
    application: Application | None


# ── read:me — stats / dashboard / applications ───────────────────────────────

class Stats(ApiModel):
    total: Any
    scored: Any
    hot: Any
    solid: Any
    by_status: Any


class Dashboard(ApiModel):
    follow_ups_due_today: Any
    recent_inbound: Any
    unverified_contacts_count: Any
    stale_active_outreach: Any


class ApplicationEnvelope(ApiModel):
    items: list[Application]
    total: int
    limit: int
    offset: int


# ── read:me — companies ──────────────────────────────────────────────────────

class CompanyListItem(ApiModel):
    id: Any
    name: Any
    website: Any
    status: Any
    notes: Any
    company_country: Any
    industry_sector: Any
    company_size: Any
    first_seen_at: Any
    last_seen_at: Any
    summary: Any
    ats_provider: Any
    ats_identifier: Any
    scraper_id: Any
    monitored: Any
    monitoring_status: Any
    scraping_method: Any
    research_notes: Any
    research_confidence: Any
    researched_at: Any
    job_count: Any
    contact_count: Any
    interaction_count: Any
    last_interaction_at: Any


class CompanyEnvelope(ApiModel):
    items: list[CompanyListItem]
    total: int
    limit: int
    offset: int


class CompanyByDetail(ApiModel):
    id: Any
    name: Any
    website: Any
    status: Any
    notes: Any
    company_country: Any
    industry_sector: Any
    company_size: Any
    careers_url: Any
    ats_provider: Any
    ats_identifier: Any
    scraper_id: Any
    monitored: Any
    first_seen_at: Any
    last_seen_at: Any
    summary: Any
    monitoring_status: Any
    scraping_method: Any
    research_notes: Any
    research_confidence: Any
    researched_at: Any
    x_handle: Any
    job_count: Any
    contact_count: Any
    interaction_count: Any
    last_interaction_at: Any


class RelationshipSummary(ApiModel):
    company_id: Any
    total_contacts: Any
    by_status: Any
    last_interaction_at: Any


class CompanyDetailResponse(ApiModel):
    company: CompanyByDetail
    relationship: RelationshipSummary
    contacts: list["Contact"]
    interactions: list["Interaction"]


class CompanyRelationshipResponse(ApiModel):
    company: CompanyByDetail
    relationship: RelationshipSummary


# ── read:me — contacts ───────────────────────────────────────────────────────

class Contact(ApiModel):
    id: Any
    user_id: Any
    company_id: Any
    first_name: Any
    last_name: Any
    full_name: Any
    role_title: Any
    role_family: Any
    seniority: Any
    email: Any
    email_status: Any
    email_confidence: Any
    linkedin_url: Any
    x_handle: Any
    telegram_handle: Any
    github_handle: Any
    phone: Any
    is_current: Any
    is_unverified: Any
    last_verified_at: Any
    notes: Any
    first_seen_at: Any
    last_seen_at: Any
    created_at: Any


class ContactListItem(ApiModel):
    id: Any
    first_name: Any
    last_name: Any
    full_name: Any
    role_title: Any
    role_family: Any
    seniority: Any
    email: Any
    email_status: Any
    email_confidence: Any
    linkedin_url: Any
    x_handle: Any
    telegram_handle: Any
    github_handle: Any
    phone: Any
    is_current: Any
    is_unverified: Any
    notes: Any
    first_seen_at: Any
    last_seen_at: Any
    created_at: Any
    company_id: Any
    company_name: Any
    company_status: Any
    last_interaction_at: Any


class ContactEnvelope(ApiModel):
    items: list[ContactListItem]
    total: int
    limit: int
    offset: int


class ContactDetailResponse(ApiModel):
    contact: Contact
    interactions: list["Interaction"]


class Interaction(ApiModel):
    id: Any
    user_id: Any
    company_id: Any
    contact_id: Any
    job_id: Any
    type: Any
    direction: Any
    outcome: Any
    subject: Any
    body_excerpt: Any
    occurred_at: Any
    follow_up_due_at: Any
    created_at: Any
    notes: Any
    source: Any
    source_ref: Any


# ── tasks (spec 043) ──────────────────────────────────────────────────────────

class TaskType(str, Enum):
    scrape = "scrape"
    scrape_monitored = "scrape_monitored"
    extract = "extract"
    score = "score"
    score_job = "score_job"


class ScoreJobParams(ApiModel):
    """Params for the `score_job` task (spec 044)."""
    job_id: str
    profile_id: str


class TaskCreate(ApiModel):
    type: TaskType
    params: dict


class Task(ApiModel):
    id: Any
    user_id: Any
    type: Any
    params: Any
    status: Any
    requested_by: Any
    cancel_requested: Any
    created_at: Any
    started_at: Any
    finished_at: Any
    exit_code: Any
    error: Any


class TaskEvent(ApiModel):
    seq: Any
    ts: Any
    line: Any


class TaskListEnvelope(ApiModel):
    items: list[Task]
    total: int
    limit: int
    offset: int


class TaskEventEnvelope(ApiModel):
    items: list[TaskEvent]
    after_seq: int
    task_status: Any
