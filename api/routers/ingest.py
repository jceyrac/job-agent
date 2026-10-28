"""api/routers/ingest.py — ingestion endpoints (spec 045, US2/US3).

`GET /v1/ingest/plan` returns everything an ingest run needs — scope, freshness
window, enabled sources, monitoring gates, monitored targets and board slugs —
with no profile read and no user-space data. `POST /v1/ingest/batch` is the
single writer for collected postings; `POST /v1/ingest/events` persists
source auto-disables and scrape-only run records. All gated by the `ingest`
scope (the platform principal). No SQL here: every read/write goes through
`JobStorage`.
"""
from fastapi import APIRouter, Depends, Request

from core.scrapers import discover_scrapers
from core.storage import JobStorage

from api.deps import AuthContext, get_user_db, ingest
from api.write_log import log_write
from api import schemas

router = APIRouter()


def _enabled_sources(db: JobStorage) -> dict[str, bool]:
    """{slug: enabled} from `scraper.<slug>.enabled` (absent → the class flag)."""
    enabled: dict[str, bool] = {}
    for cls in discover_scrapers():
        val = db.get_config(cls.enabled_config_key())
        enabled[cls.slug()] = cls.ENABLED if val is None else val.lower() == "true"
    return enabled


def _monitoring_gates(db: JobStorage) -> dict[str, bool]:
    """{provider: enabled} from `monitoring.ats.<provider>.enabled` (default on)."""
    providers = sorted({
        r["ats_provider"] for r in db.get_monitored_companies()
        if r.get("ats_provider")
    })
    gates: dict[str, bool] = {}
    for provider in providers:
        val = db.get_config(f"monitoring.ats.{provider}.enabled")
        gates[provider] = val is None or val.lower() != "false"
    return gates


def _monitored_targets(db: JobStorage) -> list[schemas.MonitoredTarget]:
    return [
        schemas.MonitoredTarget(
            company_id=r["id"],
            name=r["name"],
            ats_provider=r.get("ats_provider"),
            ats_identifier=r.get("ats_identifier"),
        )
        for r in db.get_monitored_companies()
    ]


def _watching_slugs(db: JobStorage, method: str) -> list[str]:
    rows = db.get_watching_companies_by_method(method)
    return list(dict.fromkeys(r["ats_identifier"] for r in rows if r.get("ats_identifier")))


@router.get("/plan", response_model=schemas.IngestPlan)
def get_plan(
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(ingest),
) -> schemas.IngestPlan:
    scope = db.get_ingestion_scope()
    return schemas.IngestPlan(
        title_families=scope["title_families"],
        exclude=scope["exclude"],
        remote_or_hybrid=scope["remote_or_hybrid"],
        search_locations=scope["search_locations"],
        greenhouse_boards=scope["greenhouse_boards"],
        freshness_days=db.get_freshness_days(),
        enabled_sources=_enabled_sources(db),
        monitoring_gates=_monitoring_gates(db),
        monitored_targets=_monitored_targets(db),
        board_slugs=schemas.BoardSlugs(
            lever=_watching_slugs(db, "lever"),
            workable=_watching_slugs(db, "workable"),
            greenhouse_watching=_watching_slugs(db, "greenhouse"),
        ),
    )


@router.post("/batch", response_model=schemas.IngestBatchResponse)
def submit_batch(
    body: list[schemas.BatchPosting],
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(ingest),
) -> schemas.IngestBatchResponse:
    result = db.ingest_batch([p.model_dump() for p in body])
    log_write(request, auth, status=200)
    return schemas.IngestBatchResponse(**result)


@router.post("/events", response_model=schemas.IngestEventResponse)
def submit_events(
    body: schemas.IngestEvent,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(ingest),
) -> schemas.IngestEventResponse:
    if isinstance(body, schemas.SourceDisabledEvent):
        db.set_config(f"scraper.{body.source}.enabled", "false")
        db.set_config(f"scraper.{body.source}.disabled_reason", body.reason)
        log_write(request, auth, resource_id=body.source, status=200)
    else:
        # A scrape-only run record: no profile (the platform principal has none).
        db.log_run(
            profile_id="platform",
            jobs_scraped=body.jobs_scraped,
            jobs_scored=0,
            jobs_above_threshold=0,
            status="scraped",
            duration_seconds=body.duration_seconds,
            run_type=body.run_type,
        )
        log_write(request, auth, resource_id=body.run_type, status=200)
    return schemas.IngestEventResponse(ok=True)
