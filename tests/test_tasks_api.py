"""API tests for the task queue (spec 043, US3).

The API *requests and follows* tasks — it never runs them. These tests cover
enqueue + dedup, scope gating (`tasks:run` vs `tasks:run`+`tasks:pipeline`),
user-scoping, the SSE stream vs JSON fallback, and cancel semantics.
"""
from __future__ import annotations

import sqlite3

from api.routers import tasks as tasks_mod


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_second_user(path: str) -> None:
    """Add `user-b` + a profile (`prof-x`) owned by them, for cross-user tests."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        INSERT INTO users (id, display_name, active_profile_id, onboarding_completed_at, created_at)
        VALUES ('user-b', 'Bob', 'prof-x', '2026-01-01', '2026-01-01');
        INSERT INTO search_profiles (id, user_id, name, criteria)
        VALUES ('prof-x', 'user-b', 'Foreign', '{"geo_zones": ["us"]}');
        """
    )
    conn.commit()
    conn.close()


# ── create / enqueue ─────────────────────────────────────────────────────────

def test_create_task_202_and_shape(api_env):
    tok = api_env.make_token(["tasks:run"])
    r = api_env.client.post(
        "/v1/me/tasks", json={"type": "extract", "params": {}}, headers=_auth(tok)
    )
    assert r.status_code == 202
    body = r.json()
    assert body["type"] == "extract"
    assert body["status"] == "queued"
    assert body["params"] == {}
    assert body["id"]
    assert set(body.keys()) == {
        "id", "user_id", "type", "params", "status", "requested_by",
        "cancel_requested", "created_at", "started_at", "finished_at",
        "exit_code", "error",
    }


def test_create_task_dedup_returns_existing(api_env):
    tok = api_env.make_token(["tasks:run"])
    body = {"type": "extract", "params": {}}
    r1 = api_env.client.post("/v1/me/tasks", json=body, headers=_auth(tok))
    r2 = api_env.client.post("/v1/me/tasks", json=body, headers=_auth(tok))
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r2.json()["id"] == r1.json()["id"]
    assert len(api_env.db.list_tasks()[0]) == 1


def test_create_task_score_requires_run_scope(api_env):
    tok = api_env.make_token(["read:me"])  # no tasks:run
    r = api_env.client.post(
        "/v1/me/tasks", json={"type": "score", "params": {"profile_id": "prof-a"}},
        headers=_auth(tok),
    )
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "message": "Insufficient scope"}


def test_create_task_scrape_requires_pipeline_scope(api_env):
    run_only = api_env.make_token(["tasks:run"])
    pipeline = api_env.make_token(["tasks:run", "tasks:pipeline"])
    body = {"type": "scrape", "params": {}}

    r = api_env.client.post("/v1/me/tasks", json=body, headers=_auth(run_only))
    assert r.status_code == 403

    r = api_env.client.post("/v1/me/tasks", json=body, headers=_auth(pipeline))
    assert r.status_code == 202


def test_create_task_scrape_monitored_requires_pipeline_scope(api_env):
    run_only = api_env.make_token(["tasks:run"])
    r = api_env.client.post(
        "/v1/me/tasks", json={"type": "scrape_monitored", "params": {}},
        headers=_auth(run_only),
    )
    assert r.status_code == 403


def test_create_task_score_foreign_profile_404(api_env):
    _seed_second_user(api_env.path)
    tok = api_env.make_token(["tasks:run"])
    r = api_env.client.post(
        "/v1/me/tasks",
        json={"type": "score", "params": {"profile_id": "prof-x"}},  # user-b's profile
        headers=_auth(tok),
    )
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "message": "Profile not found"}


def test_create_task_score_missing_profile_422(api_env):
    tok = api_env.make_token(["tasks:run"])
    r = api_env.client.post(
        "/v1/me/tasks", json={"type": "score", "params": {}}, headers=_auth(tok)
    )
    assert r.status_code == 422


def test_create_task_unknown_type_422(api_env):
    tok = api_env.make_token(["tasks:run"])
    r = api_env.client.post(
        "/v1/me/tasks", json={"type": "bogus", "params": {}}, headers=_auth(tok)
    )
    assert r.status_code == 422


# ── score_job (spec 044) ──────────────────────────────────────────────────────

def test_create_score_job_202_and_command(api_env):
    import sys

    from core.tasks import command_for

    tok = api_env.make_token(["tasks:run"])
    r = api_env.client.post(
        "/v1/me/tasks",
        json={"type": "score_job", "params": {"job_id": "job-1", "profile_id": "prof-a"}},
        headers=_auth(tok),
    )
    assert r.status_code == 202
    body = r.json()
    assert body["type"] == "score_job"
    assert body["status"] == "queued"
    assert body["params"] == {"job_id": "job-1", "profile_id": "prof-a"}

    # The worker command is the single-job entry point, no `tasks:pipeline`.
    assert command_for("score_job", {"job_id": "job-1", "profile_id": "prof-a"}) == [
        sys.executable, "-u", "-m", "core.score", "--job", "job-1", "--profile", "prof-a",
    ]


def test_create_score_job_dedup_returns_existing(api_env):
    tok = api_env.make_token(["tasks:run"])
    body = {"type": "score_job", "params": {"job_id": "job-1", "profile_id": "prof-a"}}
    r1 = api_env.client.post("/v1/me/tasks", json=body, headers=_auth(tok))
    r2 = api_env.client.post("/v1/me/tasks", json=body, headers=_auth(tok))
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r2.json()["id"] == r1.json()["id"]
    assert len(api_env.db.list_tasks()[0]) == 1


def test_create_score_job_requires_run_scope(api_env):
    tok = api_env.make_token(["read:me"])  # no tasks:run
    r = api_env.client.post(
        "/v1/me/tasks",
        json={"type": "score_job", "params": {"job_id": "job-1", "profile_id": "prof-a"}},
        headers=_auth(tok),
    )
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "message": "Insufficient scope"}


def test_create_score_job_foreign_profile_404(api_env):
    _seed_second_user(api_env.path)
    tok = api_env.make_token(["tasks:run"])
    r = api_env.client.post(
        "/v1/me/tasks",
        json={"type": "score_job", "params": {"job_id": "job-1", "profile_id": "prof-x"}},
        headers=_auth(tok),
    )
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "message": "Profile not found"}


def test_create_score_job_unknown_job_404(api_env):
    tok = api_env.make_token(["tasks:run"])
    r = api_env.client.post(
        "/v1/me/tasks",
        json={"type": "score_job", "params": {"job_id": "nope", "profile_id": "prof-a"}},
        headers=_auth(tok),
    )
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "message": "Job not found"}


def test_create_score_job_missing_params_422(api_env):
    tok = api_env.make_token(["tasks:run"])
    for params in ({}, {"job_id": "job-1"}):
        r = api_env.client.post(
            "/v1/me/tasks", json={"type": "score_job", "params": params}, headers=_auth(tok)
        )
        assert r.status_code == 422


# ── read:me follow ───────────────────────────────────────────────────────────

def test_get_task(api_env):
    tok = api_env.make_token(["read:me"])
    task = api_env.db.enqueue_task("extract", {})
    r = api_env.client.get(f"/v1/me/tasks/{task['id']}", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json() == api_env.db.get_task(task["id"])


def test_get_task_foreign_404(api_env):
    _seed_second_user(api_env.path)
    foreign = api_env.make_token(["tasks:run"], user_id="user-b")
    # Create a task owned by user-b via the API.
    r = api_env.client.post(
        "/v1/me/tasks", json={"type": "extract", "params": {}}, headers=_auth(foreign)
    )
    foreign_id = r.json()["id"]

    # user-a cannot read it.
    tok = api_env.make_token(["read:me"])
    r = api_env.client.get(f"/v1/me/tasks/{foreign_id}", headers=_auth(tok))
    assert r.status_code == 404


def test_list_tasks_scoped_and_filtered(api_env):
    tok = api_env.make_token(["read:me"])
    api_env.db.enqueue_task("extract", {})
    api_env.db.enqueue_task("extract", {"profile_id": "prof-a"})
    api_env.db.enqueue_task("score", {"profile_id": "prof-a"})

    r = api_env.client.get("/v1/me/tasks", headers=_auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert body["limit"] == 100 and body["offset"] == 0

    r = api_env.client.get(
        "/v1/me/tasks", params={"type": "extract"}, headers=_auth(tok)
    )
    assert r.json()["total"] == 2
    assert all(i["type"] == "extract" for i in r.json()["items"])


def test_list_tasks_foreign_not_visible(api_env):
    _seed_second_user(api_env.path)
    foreign = api_env.make_token(["tasks:run"], user_id="user-b")
    api_env.client.post(
        "/v1/me/tasks", json={"type": "extract", "params": {}}, headers=_auth(foreign)
    )
    tok = api_env.make_token(["read:me"])
    r = api_env.client.get("/v1/me/tasks", headers=_auth(tok))
    assert r.json()["total"] == 0


# ── events: JSON fallback ────────────────────────────────────────────────────

def test_task_events_json_fallback(api_env):
    tok = api_env.make_token(["read:me"])
    task = api_env.db.enqueue_task("extract", {})
    api_env.db.append_task_events(task["id"], ["one", "two", "three"])

    r = api_env.client.get(
        f"/v1/me/tasks/{task['id']}/events", headers=_auth(tok)
    )
    assert r.status_code == 200
    body = r.json()
    assert [e["line"] for e in body["items"]] == ["one", "two", "three"]
    assert body["after_seq"] == 3
    assert body["task_status"] == "queued"


def test_task_events_json_resume_by_after_seq(api_env):
    tok = api_env.make_token(["read:me"])
    task = api_env.db.enqueue_task("extract", {})
    api_env.db.append_task_events(task["id"], ["one", "two", "three"])

    r = api_env.client.get(
        f"/v1/me/tasks/{task['id']}/events", params={"after_seq": 1}, headers=_auth(tok)
    )
    body = r.json()
    assert [e["line"] for e in body["items"]] == ["two", "three"]
    assert body["after_seq"] == 3


def test_task_events_foreign_404(api_env):
    _seed_second_user(api_env.path)
    foreign = api_env.make_token(["tasks:run"], user_id="user-b")
    r = api_env.client.post(
        "/v1/me/tasks", json={"type": "extract", "params": {}}, headers=_auth(foreign)
    )
    foreign_id = r.json()["id"]
    tok = api_env.make_token(["read:me"])
    r = api_env.client.get(f"/v1/me/tasks/{foreign_id}/events", headers=_auth(tok))
    assert r.status_code == 404


def test_task_events_sse_foreign_404(api_env):
    """SSE mode enforces the same user-scoped ownership check as the JSON fallback."""
    _seed_second_user(api_env.path)
    foreign = api_env.make_token(["tasks:run"], user_id="user-b")
    r = api_env.client.post(
        "/v1/me/tasks", json={"type": "extract", "params": {}}, headers=_auth(foreign)
    )
    foreign_id = r.json()["id"]
    tok = api_env.make_token(["read:me"])
    r = api_env.client.get(
        f"/v1/me/tasks/{foreign_id}/events",
        headers={**_auth(tok), "Accept": "text/event-stream"},
    )
    assert r.status_code == 404


# ── events: SSE stream ───────────────────────────────────────────────────────

def test_sse_stream_lines_then_terminal_status(api_env):
    tok = api_env.make_token(["read:me"])
    task = api_env.db.enqueue_task("extract", {})
    api_env.db.append_task_events(task["id"], ["hello", "world"])
    api_env.db.finish_task(task["id"], "succeeded", exit_code=0)

    headers = {**_auth(tok), "Accept": "text/event-stream"}
    r = api_env.client.get(f"/v1/me/tasks/{task['id']}/events", headers=headers)
    assert r.status_code == 200
    body = r.text
    assert "event: line" in body
    assert "id: 1\ndata: hello" in body
    assert "id: 2\ndata: world" in body
    assert "event: status\ndata: succeeded" in body


def test_sse_heartbeat_comment_within_idle_window(api_env):
    # Drive the frame generator directly (sync) so a non-terminating stream can
    # be stopped deterministically: a queued (non-terminal) task idles and pings.
    task = api_env.db.enqueue_task("extract", {})
    gen = tasks_mod._sse_lines(task["id"], api_env.db, 0, idle_seconds=0.01, poll=0.005)
    lines = []
    for line in gen:
        lines.append(line)
        if line == ": ping\n\n":
            break
    assert ": ping\n\n" in lines


# ── cancel ───────────────────────────────────────────────────────────────────

def test_cancel_queued(api_env):
    tok = api_env.make_token(["tasks:run"])
    task = api_env.db.enqueue_task("extract", {})
    r = api_env.client.post(f"/v1/me/tasks/{task['id']}/cancel", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    assert api_env.db.get_task(task["id"])["status"] == "cancelled"


def test_cancel_running_sets_flag(api_env):
    tok = api_env.make_token(["tasks:run"])
    api_env.db.enqueue_task("extract", {})
    claimed = api_env.db.claim_next_task()  # queued → running

    r = api_env.client.post(f"/v1/me/tasks/{claimed['id']}/cancel", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["status"] == "running"
    assert r.json()["cancel_requested"] == 1


def test_cancel_terminal_409(api_env):
    tok = api_env.make_token(["tasks:run"])
    task = api_env.db.enqueue_task("extract", {})
    api_env.db.finish_task(task["id"], "succeeded", exit_code=0)

    r = api_env.client.post(f"/v1/me/tasks/{task['id']}/cancel", headers=_auth(tok))
    assert r.status_code == 409


def test_cancel_requires_run_scope(api_env):
    tok = api_env.make_token(["read:me"])
    task = api_env.db.enqueue_task("extract", {})
    r = api_env.client.post(f"/v1/me/tasks/{task['id']}/cancel", headers=_auth(tok))
    assert r.status_code == 403


def test_cancel_foreign_404(api_env):
    _seed_second_user(api_env.path)
    foreign = api_env.make_token(["tasks:run"], user_id="user-b")
    r = api_env.client.post(
        "/v1/me/tasks", json={"type": "extract", "params": {}}, headers=_auth(foreign)
    )
    foreign_id = r.json()["id"]
    tok = api_env.make_token(["tasks:run"])
    r = api_env.client.post(f"/v1/me/tasks/{foreign_id}/cancel", headers=_auth(tok))
    assert r.status_code == 404
