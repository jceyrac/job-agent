"""api/schemas.py — explicit pydantic response models (spec 041).

One model per payload, field-for-field over the `JobStorage` dict keys they
serialise. Data fields are typed `Any` on purpose: dates/enums/ids pass through
exactly as `JobStorage` returns them (byte-compatible with the tracker, SC-004)
— no coercion, no reformatting. `extra="forbid"` ensures an accidental new
internal column fails the contract test instead of leaking (US3-4).

Only the list envelope scalars (`total`, `limit`, `offset`) are typed `int` —
they are produced by the API's pagination layer, never read from the DB.
"""
from typing import Any

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
