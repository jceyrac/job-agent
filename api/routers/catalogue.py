"""api/routers/catalogue.py — shared-catalogue endpoints (spec 041 US3 / 042 US4).

`read:catalogue`: jobs + company facts, never a user-space field.
`write:catalogue`: shared company facts only — never `user_companies`.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.storage import JobStorage

from api.deps import AuthContext, get_user_db, require_scope
from api.serializers import to_model, to_model_list
from api.write_log import log_write
from api import schemas

router = APIRouter()

read_catalogue = require_scope("read:catalogue")
write_catalogue = require_scope("write:catalogue")


@router.get("/jobs", response_model=schemas.JobListEnvelope)
def list_jobs(
    company_id: int | None = None,
    source: str | None = None,
    posted_after: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_catalogue),
) -> schemas.JobListEnvelope:
    rows = db.get_catalogue_jobs(
        company_id=company_id, source=source, posted_after=posted_after
    )
    total = len(rows)
    page = rows[offset:offset + limit]
    return schemas.JobListEnvelope(
        items=to_model_list(schemas.CatalogueJob, page),
        total=total, limit=limit, offset=offset,
    )


@router.get("/jobs/{job_id}", response_model=schemas.CatalogueJob)
def get_job(
    job_id: str,
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_catalogue),
) -> schemas.CatalogueJob:
    row = db.get_catalogue_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return to_model(schemas.CatalogueJob, row)


@router.get("/companies/{company_id}", response_model=schemas.CompanyFacts)
def get_company(
    company_id: int,
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_catalogue),
) -> schemas.CompanyFacts:
    row = db.get_company_facts(company_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return to_model(schemas.CompanyFacts, row)


@router.patch("/companies/{company_id}", response_model=schemas.CompanyFacts)
def update_company_facts(
    company_id: int,
    body: schemas.CompanyFactUpdate,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(write_catalogue),
) -> schemas.CompanyFacts:
    if db.get_company_facts(company_id) is None:
        raise HTTPException(status_code=404, detail="Company not found")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        db.update_company_fields(company_id, fields)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid company update") from None
    log_write(request, auth, resource_id=company_id, status=200)
    return to_model(schemas.CompanyFacts, db.get_company_facts(company_id))
