"""Shared fixtures for the spec 041 API tests (contract / auth / isolation).

One temp DB file, migrated once and seeded with a single user (`user-a`) plus a
small catalogue/space dataset, behind a `TestClient` whose `app.state.db_path`
points at that file — so auth (`get_db`) and data (`get_user_db`) share the same
storage. Each test file builds on `api_env`; none touches the live `data/jobs.db`.
"""
from __future__ import annotations

import io
import logging
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.logging_config import configure_logging
from api.main import create_app
from core.storage import JobStorage

USER_A = "user-a"

_SEED_SQL = """
INSERT INTO users (id, display_name, active_profile_id, onboarding_completed_at, created_at)
VALUES ('user-a', 'Alice', 'prof-a', '2026-01-01', '2026-01-01');

INSERT INTO companies (name, name_normalized, status, first_seen_at, last_seen_at, created_at)
VALUES ('Alpha', 'alpha', 'prospect', '2026-01-01', '2026-01-01', '2026-01-01');
INSERT INTO companies (name, name_normalized, status, first_seen_at, last_seen_at, created_at)
VALUES ('Beta', 'beta', 'prospect', '2026-01-01', '2026-01-01', '2026-01-01');

INSERT INTO jobs (id, title, company, company_id, source, location, first_seen, last_seen)
VALUES ('job-1', 'Backend', 'Alpha', 1, 'board', 'Zurich', '2026-01-01', '2026-01-02');
INSERT INTO jobs (id, title, company, company_id, source, location, first_seen, last_seen)
VALUES ('job-2', 'Frontend', 'Beta', 2, 'board', 'Bern', '2026-01-01', '2026-01-03');

INSERT INTO search_profiles (id, user_id, name, criteria)
VALUES ('prof-a', 'user-a', 'Active', '{"geo_zones": ["ch"]}');
INSERT INTO search_profiles (id, user_id, name, criteria)
VALUES ('prof-b', 'user-a', 'Other', '{"geo_zones": ["de"]}');

INSERT INTO job_scores (job_id, profile_id, score, reason, scored_by, scored_at)
VALUES ('job-1', 'prof-a', 8, 'good fit', 'llm', '2026-01-02');

INSERT INTO job_tracking (user_id, job_id, status, notes, changed_at)
VALUES ('user-a', 'job-1', 'applied', 'tracked note', '2026-01-03');

INSERT INTO status_history (user_id, job_id, status, changed_at)
VALUES ('user-a', 'job-1', 'applied', '2026-01-03');

INSERT INTO job_applications
  (user_id, job_id, profile_id, analysis, cover_letter, language, prepared_by, prepared_at, created_at)
VALUES ('user-a', 'job-1', 'prof-a', 'analysis', 'cover', 'en', 'me', '2026-01-03', '2026-01-03');

INSERT INTO contacts (user_id, company_id, first_name, last_name, full_name, role_title,
                      first_seen_at, last_seen_at, created_at)
VALUES ('user-a', 1, 'John', 'Doe', 'John Doe', 'Recruiter', '2026-01-01', '2026-01-01', '2026-01-01');

INSERT INTO interactions (user_id, company_id, contact_id, job_id, type, direction,
                          outcome, occurred_at, created_at)
VALUES ('user-a', 1, 1, 'job-1', 'application_submitted', 'outbound', NULL, '2026-01-03', '2026-01-03');
"""


def _seed(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_SEED_SQL)
    conn.commit()
    conn.close()


@pytest.fixture
def api_env(tmp_path):
    """Temp file DB + TestClient + a bound `JobStorage` + a token factory."""
    path = str(tmp_path / "spec041.db")
    JobStorage(path)  # run the one-shot migration (also creates the default user)
    _seed(path)

    app = create_app()
    app.state.db_path = path
    client = TestClient(app)

    db = JobStorage(path, user_id=USER_A)

    def make_token(scopes, *, user_id=USER_A, expires_at=None):
        return db.create_api_token("test", scopes, user_id=user_id, expires_at=expires_at)

    return SimpleNamespace(path=path, client=client, db=db, make_token=make_token)


@pytest.fixture
def api_log_stream():
    """Capture the configured `api.*` stdout handler's stream (the "wiring").

    Swaps the handler's stream for a StringIO, yields it, and restores the
    original on teardown — so a test can assert a write / failed-auth line lands
    on the configured handler, not just that the record was created.
    """
    configure_logging()
    handler = next(
        h for h in logging.getLogger("api").handlers
        if isinstance(h, logging.StreamHandler)
    )
    buf = io.StringIO()
    original = handler.stream
    handler.stream = buf
    try:
        yield buf
    finally:
        handler.stream = original
