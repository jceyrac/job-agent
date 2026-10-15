"""api/routers/me.py — the token user's space (spec 041, US3).

`read:me` only. Every route reads the bound storage (`get_user_db`), so the
user comes from the token, never a parameter. Scores are user-scoped in
`JobStorage` (`get_user_scores_for_job`) — never filtered in the API (spec 039).
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from core.storage import JobStorage

from api.deps import AuthContext, get_user_db, require_scope
from api.serializers import to_model, to_model_list
from api import schemas

router = APIRouter()

read_me = require_scope("read:me")


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
