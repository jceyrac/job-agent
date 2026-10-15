"""api/routers/catalogue.py — shared-catalogue read endpoints (spec 041, US3).

`read:catalogue` only: jobs + company facts, never a user-space field.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from core.storage import JobStorage

from api.deps import AuthContext, get_user_db, require_scope
from api.serializers import to_model, to_model_list
from api import schemas

router = APIRouter()

read_catalogue = require_scope("read:catalogue")


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
