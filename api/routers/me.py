"""api/routers/me.py — the token user's space (spec 041, US3).

`read:me` only. Every route reads the bound storage (`get_user_db`), so the
user comes from the token, never a parameter. Scores are user-scoped in
`JobStorage` (`get_user_scores_for_job`) — never filtered in the API (spec 039).
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from core.profiles import SearchProfile
from core.storage import JobStorage

from api.deps import AuthContext, get_user_db, require_scope
from api.serializers import to_model, to_model_list
from api.write_log import log_write
from api import schemas

router = APIRouter()

read_me = require_scope("read:me")
write_me = require_scope("write:me")


def _page(rows: list, limit: int, offset: int) -> tuple[list, int]:
    return rows[offset:offset + limit], len(rows)


@router.get("", response_model=schemas.MeResponse)
def me(
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_me),
) -> schemas.MeResponse:
    return schemas.MeResponse(
        user=to_model(schemas.UserRow, db.get_user()),
        active_profile_id=db.get_active_profile_id(),
        profiles=to_model_list(schemas.Profile, db.get_all_profiles()),
    )


@router.get("/feed", response_model=schemas.FeedEnvelope)
def feed(
    profile_id: str | None = None,
    status: str | None = None,
    min_score: int | None = None,
    exclude_archived: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_me),
) -> schemas.FeedEnvelope:
    pid = profile_id or db.get_active_profile_id()
    rows = db.get_all_for_tracker(pid, exclude_archived=exclude_archived)
    if status is not None:
        rows = [r for r in rows if r.get("status") == status]
    if min_score is not None:
        rows = [r for r in rows if r.get("score") is not None and r.get("score") >= min_score]
    page, total = _page(rows, limit, offset)
    return schemas.FeedEnvelope(
        items=to_model_list(schemas.FeedItem, page),
        total=total, limit=limit, offset=offset,
    )


@router.get("/jobs/{job_id}", response_model=schemas.JobDetailResponse)
def job_detail(
    job_id: str,
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_me),
) -> schemas.JobDetailResponse:
    job = db.get_job_for_prepare(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return schemas.JobDetailResponse(
        job=to_model(schemas.JobPrepare, job),
        scores=to_model_list(schemas.Score, db.get_user_scores_for_job(job_id)),
        lifecycle=to_model(schemas.LifecycleSummary, db.get_lifecycle_summary(job_id)),
        events=to_model_list(schemas.LifecycleEvent, db.get_lifecycle_events(job_id)),
        application=to_model(schemas.Application, db.get_application(job_id)),
    )


@router.get("/stats", response_model=schemas.Stats)
def stats(
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_me),
) -> schemas.Stats:
    return to_model(schemas.Stats, db.get_stats())


@router.get("/dashboard", response_model=schemas.Dashboard)
def dashboard(
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_me),
) -> schemas.Dashboard:
    return to_model(schemas.Dashboard, db.get_dashboard_data())


@router.get("/applications", response_model=schemas.ApplicationEnvelope)
def applications(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_me),
) -> schemas.ApplicationEnvelope:
    page, total = _page(db.get_all_applications(), limit, offset)
    return schemas.ApplicationEnvelope(
        items=to_model_list(schemas.Application, page),
        total=total, limit=limit, offset=offset,
    )


@router.get("/companies", response_model=schemas.CompanyEnvelope)
def companies(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_me),
) -> schemas.CompanyEnvelope:
    page, total = _page(db.get_companies(), limit, offset)
    return schemas.CompanyEnvelope(
        items=to_model_list(schemas.CompanyListItem, page),
        total=total, limit=limit, offset=offset,
    )


@router.get("/companies/{company_id}", response_model=schemas.CompanyDetailResponse)
def company_detail(
    company_id: int,
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_me),
) -> schemas.CompanyDetailResponse:
    company = db.get_company_by_id(company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return schemas.CompanyDetailResponse(
        company=to_model(schemas.CompanyByDetail, company),
        relationship=to_model(
            schemas.RelationshipSummary, db.get_company_relationship_summary(company_id)
        ),
        contacts=to_model_list(schemas.Contact, db.get_company_contacts(company_id)),
        interactions=to_model_list(schemas.Interaction, db.get_company_interactions(company_id)),
    )


@router.get("/contacts", response_model=schemas.ContactEnvelope)
def contacts(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_me),
) -> schemas.ContactEnvelope:
    page, total = _page(db.get_all_contacts(), limit, offset)
    return schemas.ContactEnvelope(
        items=to_model_list(schemas.ContactListItem, page),
        total=total, limit=limit, offset=offset,
    )


@router.get("/contacts/{contact_id}", response_model=schemas.ContactDetailResponse)
def contact_detail(
    contact_id: int,
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_me),
) -> schemas.ContactDetailResponse:
    contact = db.get_contact(contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return schemas.ContactDetailResponse(
        contact=to_model(schemas.Contact, contact),
        interactions=to_model_list(schemas.Interaction, db.get_contact_interactions(contact_id)),
    )


# ── write:me — jobs & lifecycle (spec 042, US1) ──────────────────────────────


# Lifecycle status → the event `record_lifecycle_event` records for it (FR-002:
# byte-identical to the tracker's `_confirm_lifecycle` mapping in job_helpers.py).
_LIFECYCLE_EVENT_FOR_STATUS = {
    "applied": ("application_submitted", None),
    "interviewing": ("interview", None),
    "offer": ("decision_received", "positive"),
    "rejected": ("decision_received", "negative"),
    "withdrawn": ("withdrawn", None),
}


def _require_event(db: JobStorage, job_id: str, event_id: int) -> None:
    """404 unless `event_id` is one of this job's lifecycle events.

    An event that belongs to another job — or another user — is simply absent
    from `get_lifecycle_events(job_id)`, so it maps to 404 here and
    reconciliation never runs on the wrong job.
    """
    if event_id not in {e["id"] for e in db.get_lifecycle_events(job_id)}:
        raise HTTPException(status_code=404, detail="Event not found")


@router.put("/jobs/{job_id}/status", response_model=schemas.JobPrepare)
def set_job_status(
    job_id: str,
    body: schemas.StatusUpdate,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(write_me),
) -> schemas.JobPrepare:
    if db.get_job_for_prepare(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    status = body.status.value
    if status in _LIFECYCLE_EVENT_FOR_STATUS:
        event, outcome = _LIFECYCLE_EVENT_FOR_STATUS[status]
        try:
            db.record_lifecycle_event(
                job_id,
                event=event,
                occurred_at=body.occurred_at,
                new_status=status,
                outcome=outcome,
                notes=body.notes if status in ("rejected", "withdrawn") else None,
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid lifecycle event") from None
    else:
        db.set_status(job_id, status, notes=body.notes)
    log_write(request, auth, resource_id=job_id, status=200)
    return to_model(schemas.JobPrepare, db.get_job_for_prepare(job_id))


@router.put("/jobs/{job_id}/application", response_model=schemas.Application)
def set_application(
    job_id: str,
    body: schemas.ApplicationUpdate,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(write_me),
) -> schemas.Application:
    if db.get_job_for_prepare(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    db.save_application(job_id, body.analysis, body.cover_letter)
    log_write(request, auth, resource_id=job_id, status=200)
    return to_model(schemas.Application, db.get_application(job_id))


@router.post("/jobs/{job_id}/events", response_model=list[schemas.LifecycleEvent])
def add_lifecycle_event(
    job_id: str,
    body: schemas.LifecycleEventCreate,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(write_me),
) -> list[schemas.LifecycleEvent]:
    if db.get_job_for_prepare(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        db.record_lifecycle_event(
            job_id,
            event=body.event.value,
            occurred_at=body.occurred_at,
            new_status=body.new_status.value,
            outcome=body.outcome.value if body.outcome is not None else None,
            notes=body.notes,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid lifecycle event") from None
    log_write(request, auth, resource_id=job_id, status=200)
    return to_model_list(schemas.LifecycleEvent, db.get_lifecycle_events(job_id))


@router.patch("/jobs/{job_id}/events/{event_id}", response_model=list[schemas.LifecycleEvent])
def edit_lifecycle_event(
    job_id: str,
    event_id: int,
    body: schemas.LifecycleEventUpdate,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(write_me),
) -> list[schemas.LifecycleEvent]:
    _require_event(db, job_id, event_id)
    try:
        db.update_lifecycle_event(event_id, occurred_at=body.occurred_at, notes=body.notes)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid event update") from None
    db.reconcile_lifecycle_status(job_id)
    log_write(request, auth, resource_id=event_id, status=200)
    return to_model_list(schemas.LifecycleEvent, db.get_lifecycle_events(job_id))


@router.delete("/jobs/{job_id}/events/{event_id}", status_code=204)
def remove_lifecycle_event(
    job_id: str,
    event_id: int,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(write_me),
) -> Response:
    _require_event(db, job_id, event_id)
    db.delete_lifecycle_event(event_id)
    db.reconcile_lifecycle_status(job_id)
    log_write(request, auth, resource_id=event_id, status=204)
    return Response(status_code=204)


# ── write:me — companies, contacts, interactions (spec 042, US2) ─────────────


@router.patch("/companies/{company_id}", response_model=schemas.CompanyRelationshipResponse)
def update_company_relationship(
    company_id: int,
    body: schemas.CompanyRelationshipUpdate,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(write_me),
) -> schemas.CompanyRelationshipResponse:
    if db.get_company_by_id(company_id) is None:
        raise HTTPException(status_code=404, detail="Company not found")
    try:
        if body.status is not None:
            db.set_company_status(company_id, body.status.value)
        if body.notes is not None:
            db.update_company_fields(company_id, {"notes": body.notes})
        if body.monitored is not None:
            db.set_company_monitored(company_id, body.monitored)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid company update") from None
    log_write(request, auth, resource_id=company_id, status=200)
    return schemas.CompanyRelationshipResponse(
        company=to_model(schemas.CompanyByDetail, db.get_company_by_id(company_id)),
        relationship=to_model(
            schemas.RelationshipSummary, db.get_company_relationship_summary(company_id)
        ),
    )


@router.post("/contacts", response_model=schemas.Contact)
def create_contact(
    body: schemas.ContactCreate,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(write_me),
) -> schemas.Contact:
    if db.get_company_by_id(body.company_id) is None:
        raise HTTPException(status_code=404, detail="Company not found")
    kwargs = {
        "company_id": body.company_id,
        "linkedin_url": body.linkedin_url,
        "email": body.email,
        "first_name": body.first_name,
        "last_name": body.last_name,
        "full_name": body.full_name,
        "role_title": body.role_title,
        "role_family": body.role_family,
        "seniority": body.seniority,
        "email_confidence": body.email_confidence,
        "x_handle": body.x_handle,
        "telegram_handle": body.telegram_handle,
        "github_handle": body.github_handle,
        "phone": body.phone,
        "is_unverified": body.is_unverified,
        "notes": body.notes,
    }
    if body.email_status is not None:
        kwargs["email_status"] = body.email_status
    try:
        new_id = db.upsert_contact(**kwargs)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid contact") from None
    log_write(request, auth, resource_id=new_id, status=200)
    return to_model(schemas.Contact, db.get_contact(new_id))


@router.patch("/contacts/{contact_id}", response_model=schemas.Contact)
def update_contact(
    contact_id: int,
    body: schemas.ContactUpdate,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(write_me),
) -> schemas.Contact:
    if db.get_contact(contact_id) is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        db.update_contact_fields(contact_id, fields)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid contact update") from None
    log_write(request, auth, resource_id=contact_id, status=200)
    return to_model(schemas.Contact, db.get_contact(contact_id))


@router.post(
    "/contacts/{contact_id}/interactions",
    response_model=list[schemas.Interaction],
    status_code=201,
)
def add_contact_interaction(
    contact_id: int,
    body: schemas.InteractionCreate,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(write_me),
) -> list[schemas.Interaction]:
    contact = db.get_contact(contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    try:
        db.log_interaction(
            company_id=contact["company_id"],
            contact_id=contact_id,
            type=body.type.value,
            job_id=body.job_id,
            direction=body.direction.value,
            outcome=body.outcome.value if body.outcome is not None else None,
            subject=body.subject,
            body_excerpt=body.body_excerpt,
            occurred_at=body.occurred_at,
            follow_up_due_at=body.follow_up_due_at,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid interaction") from None
    log_write(request, auth, resource_id=contact_id, status=201)
    return to_model_list(schemas.Interaction, db.get_contact_interactions(contact_id))


@router.post(
    "/companies/{company_id}/interactions",
    response_model=list[schemas.Interaction],
    status_code=201,
)
def add_company_interaction(
    company_id: int,
    body: schemas.InteractionCreate,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(write_me),
) -> list[schemas.Interaction]:
    if db.get_company_by_id(company_id) is None:
        raise HTTPException(status_code=404, detail="Company not found")
    try:
        db.log_interaction(
            company_id=company_id,
            contact_id=None,
            type=body.type.value,
            job_id=body.job_id,
            direction=body.direction.value,
            outcome=body.outcome.value if body.outcome is not None else None,
            subject=body.subject,
            body_excerpt=body.body_excerpt,
            occurred_at=body.occurred_at,
            follow_up_due_at=body.follow_up_due_at,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid interaction") from None
    log_write(request, auth, resource_id=company_id, status=201)
    return to_model_list(schemas.Interaction, db.get_company_interactions(company_id))


# ── write:me — profile (spec 042, US3) ───────────────────────────────────────


def _require_own_profile(db: JobStorage, profile_id: str) -> None:
    """404 unless `profile_id` is an existing profile owned by the token user.

    `get_profile` is user-scoped: a foreign profile (or a missing one) is simply
    absent, so both map to 404 here and no write ever targets another user's row.
    """
    if db.get_profile(profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found")


@router.put("/active-profile", status_code=204)
def set_active_profile(
    body: schemas.ActiveProfileUpdate,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(write_me),
) -> Response:
    _require_own_profile(db, body.profile_id)
    db.set_active_profile_id(body.profile_id)
    log_write(request, auth, resource_id=body.profile_id, status=204)
    return Response(status_code=204)


@router.put("/profiles/{profile_id}", response_model=schemas.Profile)
def upsert_profile(
    profile_id: str,
    body: schemas.ProfileUpdate,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(write_me),
) -> schemas.Profile:
    _require_own_profile(db, profile_id)
    profile = SearchProfile.from_criteria(profile_id, body.name, body.criteria)
    db.upsert_profile(profile)
    log_write(request, auth, resource_id=profile_id, status=200)
    return to_model(schemas.Profile, db.get_profile(profile_id))
