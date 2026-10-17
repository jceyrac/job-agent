"""api/routers/tasks.py — request and follow tasks (spec 043, US3).

Clients *request* work here — nothing runs it. `POST /tasks` enqueues and never
spawns a process; the worker (a separate container) is the only runner. Progress
is read from `task_events`, either as a live SSE stream or as paginated JSON.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from core.storage import JobStorage

from api.deps import AuthContext, get_current_user, get_user_db, require_scope
from api.serializers import to_model, to_model_list
from api.write_log import log_write
from api import schemas

router = APIRouter()

read_me = require_scope("read:me")

_TERMINAL = {"succeeded", "failed", "cancelled"}
_SSE_IDLE_SECONDS = 15.0   # heartbeat cadence (spec US3-3, ≤ 15 s)
_SSE_POLL = 0.5            # event-read cadence


def _require_task_scopes(ttype: str, scopes: list[str]) -> None:
    required = {"tasks:run"}
    if ttype in ("scrape", "scrape_monitored"):
        required.add("tasks:pipeline")
    if not required.issubset(set(scopes)):
        raise HTTPException(status_code=403, detail="Insufficient scope")


def _sse_lines(task_id: str, db: JobStorage, after_seq: int,
               idle_seconds: float, poll: float):
    """Yield SSE frames: ``line`` events as they land, a ``: ping`` comment when
    idle for ``idle_seconds``, and a final ``status`` event at a terminal state."""
    last = after_seq
    idle_since = time.monotonic()
    while True:
        events = db.get_task_events(task_id, after_seq=last)
        if events:
            idle_since = time.monotonic()
            for e in events:
                last = e["seq"]
                yield f"event: line\nid: {last}\ndata: {e['line']}\n\n"
            continue

        current = db.get_task(task_id)
        status = current["status"] if current else "failed"
        if status in _TERMINAL:
            yield f"event: status\ndata: {status}\n\n"
            return
        if time.monotonic() - idle_since >= idle_seconds:
            yield ": ping\n\n"
            idle_since = time.monotonic()
        time.sleep(poll)


def _sse_stream(task_id: str, db: JobStorage, after_seq: int) -> StreamingResponse:
    return StreamingResponse(
        _sse_lines(task_id, db, after_seq, _SSE_IDLE_SECONDS, _SSE_POLL),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("", response_model=schemas.Task, status_code=202)
def create_task(
    body: schemas.TaskCreate,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(get_current_user),
) -> schemas.Task:
    _require_task_scopes(body.type.value, auth.scopes)
    try:
        task = db.enqueue_task(body.type.value, body.params, requested_by=auth.token_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return to_model(schemas.Task, task)


@router.get("", response_model=schemas.TaskListEnvelope)
def list_tasks(
    status: str | None = None,
    type: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_me),
) -> schemas.TaskListEnvelope:
    rows, total = db.list_tasks(status=status, type=type, limit=limit, offset=offset)
    return schemas.TaskListEnvelope(
        items=to_model_list(schemas.Task, rows),
        total=total, limit=limit, offset=offset,
    )


@router.get("/{task_id}", response_model=schemas.Task)
def get_task(
    task_id: str,
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_me),
) -> schemas.Task:
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return to_model(schemas.Task, task)


@router.get("/{task_id}/events")
def task_events(
    task_id: str,
    request: Request,
    after_seq: int = Query(0, ge=0),
    limit: int = Query(2000, ge=1, le=2000),
    db: JobStorage = Depends(get_user_db),
    _: AuthContext = Depends(read_me),
):
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    accept = request.headers.get("accept", "")
    if "text/event-stream" in accept:
        last_event_id = int(request.headers.get("last-event-id", 0) or 0)
        return _sse_stream(task_id, db, after_seq=last_event_id)

    events = db.get_task_events(task_id, after_seq=after_seq, limit=limit)
    return schemas.TaskEventEnvelope(
        items=to_model_list(schemas.TaskEvent, events),
        after_seq=events[-1]["seq"] if events else after_seq,
        task_status=db.get_task(task_id)["status"],
    )


@router.post("/{task_id}/cancel", response_model=schemas.Task)
def cancel_task(
    task_id: str,
    request: Request,
    db: JobStorage = Depends(get_user_db),
    auth: AuthContext = Depends(get_current_user),
) -> schemas.Task:
    if "tasks:run" not in auth.scopes:
        raise HTTPException(status_code=403, detail="Insufficient scope")
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    status = task["status"]
    if status in _TERMINAL:
        raise HTTPException(status_code=409, detail="Task already finished")
    if status == "queued":
        db.mark_cancelled(task_id)
    elif status == "running":
        db.request_cancel(task_id)
    log_write(request, auth, resource_id=task_id, status=200)
    return to_model(schemas.Task, db.get_task(task_id))
