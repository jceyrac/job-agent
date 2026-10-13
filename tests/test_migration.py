"""
tests/test_migration.py — user-space separation migration tests (spec 039).

Builds a pre-039 "legacy" SQLite file (old-shape tables WITHOUT `user_id`),
opens it through ``JobStorage``, and asserts the one-shot migration:

  * idempotency — a second open is a no-op, recorded exactly once in `migrations`;
  * row counts unchanged across the user-space tables;
  * every user-space row carries `user_id` = the default user;
  * `user_companies` backfill = exactly the non-default companies;
  * single-transaction rollback on an induced mid-migration failure.

The legacy fixture pre-populates companies and pre-records the prior named
migrations so the *only* new work ``_init_db`` performs is
``user_space_separation`` (US1 scenario 4, FR-006/FR-012).
"""
import sqlite3

import pytest

from core.storage import JobStorage, _normalize_company_name
from core.profiles import ALL_PROFILES

USER_SPACE_TABLES = (
    "search_profiles",
    "job_tracking",
    "job_applications",
    "status_history",
    "contacts",
    "interactions",
    "company_status_history",
)

# Companies whose name must NOT collide with the profile denylist (Phase 3.2).
TEST_COMPANIES = {
    "acme":      ("Acme Corp",     "prospect",    None,        0, "unmonitored"),
    "globex":    ("Globex",        "blacklisted", None,        0, "unmonitored"),
    "initech":   ("Initech",       "prospect",    "promising", 0, "unmonitored"),
    "umbrella":  ("Umbrella Corp", "prospect",    None,        1, "watch_ready"),
    "hooli":     ("Hooli",         "prospect",    None,        0, "watching"),
}


def _legacy_schema() -> str:
    """Old-shape (pre-039) tables — no `user_id` anywhere in user space."""
    return """
    CREATE TABLE companies (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        name             TEXT NOT NULL,
        name_normalized  TEXT NOT NULL UNIQUE,
        status           TEXT NOT NULL DEFAULT 'prospect',
        notes            TEXT,
        monitored        BOOLEAN DEFAULT FALSE,
        monitoring_status TEXT NOT NULL DEFAULT 'unmonitored',
        first_seen_at    TEXT NOT NULL,
        last_seen_at     TEXT NOT NULL,
        created_at       TEXT NOT NULL
    );
    CREATE TABLE company_status_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id  INTEGER NOT NULL,
        status      TEXT NOT NULL,
        note        TEXT,
        changed_at  TEXT NOT NULL
    );
    CREATE TABLE jobs (
        id            TEXT PRIMARY KEY,
        title         TEXT NOT NULL,
        company       TEXT,
        company_id    INTEGER,
        url           TEXT,
        source        TEXT,
        location      TEXT,
        posted_date   TEXT,
        description   TEXT,
        first_seen    TEXT NOT NULL,
        last_seen     TEXT NOT NULL
    );
    CREATE TABLE search_profiles (
        id       TEXT PRIMARY KEY,
        name     TEXT,
        criteria TEXT
    );
    CREATE TABLE job_scores (
        job_id     TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        score      INTEGER,
        reason     TEXT,
        scored_by  TEXT,
        scored_at  TEXT,
        PRIMARY KEY (job_id, profile_id)
    );
    CREATE TABLE job_tracking (
        job_id     TEXT PRIMARY KEY,
        status     TEXT NOT NULL DEFAULT 'new',
        notes      TEXT,
        changed_at TEXT
    );
    CREATE TABLE status_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id     TEXT NOT NULL,
        status     TEXT NOT NULL,
        changed_at TEXT NOT NULL
    );
    CREATE TABLE job_applications (
        job_id              TEXT PRIMARY KEY,
        profile_id          TEXT,
        analysis            TEXT,
        cover_letter        TEXT,
        cv_bullets_selected TEXT,
        company_research    TEXT,
        screening_answers   TEXT,
        language            TEXT,
        prepared_by         TEXT,
        prepared_at         TEXT,
        created_at          TEXT
    );
    CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE contacts (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id        INTEGER NOT NULL,
        first_name        TEXT,
        last_name         TEXT,
        full_name         TEXT,
        role_title        TEXT,
        role_family       TEXT,
        seniority         TEXT,
        email             TEXT,
        email_status      TEXT DEFAULT 'unknown',
        email_confidence  REAL,
        linkedin_url      TEXT,
        x_handle          TEXT,
        telegram_handle   TEXT,
        github_handle     TEXT,
        phone             TEXT,
        is_current        INTEGER NOT NULL DEFAULT 1,
        is_unverified     INTEGER NOT NULL DEFAULT 0,
        last_verified_at  TEXT,
        notes             TEXT,
        first_seen_at     TEXT NOT NULL,
        last_seen_at      TEXT NOT NULL,
        created_at        TEXT NOT NULL
    );
    CREATE UNIQUE INDEX idx_contacts_linkedin
        ON contacts (linkedin_url) WHERE linkedin_url IS NOT NULL;
    CREATE TABLE interactions (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id        INTEGER NOT NULL,
        contact_id        INTEGER,
        job_id            TEXT,
        type              TEXT NOT NULL,
        direction         TEXT NOT NULL DEFAULT 'none',
        outcome           TEXT,
        subject           TEXT,
        body_excerpt      TEXT,
        occurred_at       TEXT NOT NULL,
        follow_up_due_at  TEXT,
        created_at        TEXT NOT NULL,
        notes             TEXT,
        source            TEXT,
        source_ref        TEXT
    );
    CREATE UNIQUE INDEX idx_interactions_source_ref
        ON interactions (job_id, type, source_ref) WHERE source_ref IS NOT NULL;
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ran_at TEXT NOT NULL,
        profile_id TEXT,
        jobs_scraped INTEGER,
        jobs_scored INTEGER,
        jobs_above_threshold INTEGER,
        status TEXT,
        error_msg TEXT,
        duration_seconds REAL,
        run_type TEXT DEFAULT 'full'
    );
    CREATE TABLE migrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        applied_at TEXT NOT NULL
    );
    """


def _build_legacy_db(path: str) -> None:
    """Create a pre-039 DB with one row per user-space table + test companies."""
    conn = sqlite3.connect(path)
    conn.executescript(_legacy_schema())

    # Prior named migrations, so they don't re-run and pollute counts.
    for name in ("backfill_lifecycle_events",
                 "backfill_monitoring_status_from_monitored",
                 "seed_greenhouse_boards"):
        conn.execute(
            "INSERT INTO migrations (name, applied_at) VALUES (?, '2026-01-01T00:00:00+00:00')",
            (name,),
        )

    # config: active profile + onboarding (picked up by _ensure_default_user).
    conn.execute("INSERT INTO config (key, value) VALUES ('active_profile_id', 'profile_1')")
    conn.execute("INSERT INTO config (key, value) VALUES ('onboarding_complete', 'true')")

    # Phase 3.2 pre-empt: create every denylisted company as already-blacklisted,
    # so the unguarded blacklist migration is a genuine no-op.
    for profile in ALL_PROFILES.values():
        for company_name in getattr(profile, "denylisted_companies", []) or []:
            norm = _normalize_company_name(company_name)
            if not norm:
                continue
            conn.execute(
                """INSERT OR IGNORE INTO companies
                   (name, name_normalized, status, first_seen_at, last_seen_at, created_at)
                   VALUES (?, ?, 'blacklisted', '2026-01-01', '2026-01-01', '2026-01-01')""",
                (company_name, norm),
            )

    # Test companies (Acme = default, others = non-default relationships).
    ids: dict[str, int] = {}
    for key, (name, status, notes, monitored, monitoring_status) in TEST_COMPANIES.items():
        cur = conn.execute(
            """INSERT INTO companies
               (name, name_normalized, status, notes, monitored, monitoring_status,
                first_seen_at, last_seen_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, '2026-01-01', '2026-01-01', '2026-01-01')""",
            (name, name.lower().replace(" ", "_"), status, notes,
             monitored, monitoring_status),
        )
        ids[key] = cur.lastrowid

    # jobs + search_profiles (catalogue/owned rows referenced below).
    conn.execute(
        "INSERT INTO jobs (id, title, company, company_id, source, first_seen, last_seen) "
        "VALUES ('job-1', 'Backend Engineer', 'Acme Corp', ?, 'test', '2026-01-01', '2026-01-01')",
        (ids["acme"],),
    )
    conn.execute(
        "INSERT INTO search_profiles (id, name, criteria) VALUES ('profile_1', 'Primary', '{}')"
    )

    # job_tracking + status_history + job_applications (old shapes).
    conn.execute(
        "INSERT INTO job_tracking (job_id, status, notes, changed_at) "
        "VALUES ('job-1', 'applied', 'note', '2026-02-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO status_history (job_id, status, changed_at) "
        "VALUES ('job-1', 'applied', '2026-02-01T00:00:00+00:00')"
    )
    conn.execute(
        """INSERT INTO job_applications
           (job_id, profile_id, analysis, cover_letter, created_at)
           VALUES ('job-1', 'profile_1', 'analysis', 'letter', '2026-02-01T00:00:00+00:00')"""
    )

    # company_status_history (old shape, blacklisted decision on Globex).
    conn.execute(
        "INSERT INTO company_status_history (company_id, status, note, changed_at) "
        "VALUES (?, 'blacklisted', 'denied', '2026-02-01T00:00:00+00:00')",
        (ids["globex"],),
    )

    # contacts + interactions (old shapes).
    conn.execute(
        """INSERT INTO contacts
           (company_id, first_name, last_name, linkedin_url, first_seen_at, last_seen_at, created_at)
           VALUES (?, 'Jane', 'Doe', 'https://linkedin.com/in/jane',
                   '2026-02-01', '2026-02-01', '2026-02-01')""",
        (ids["acme"],),
    )
    conn.execute(
        """INSERT INTO interactions
           (company_id, job_id, type, direction, occurred_at, created_at, source, source_ref)
           VALUES (?, 'job-1', 'application_submitted', 'outbound',
                   '2026-02-01T00:00:00+00:00', '2026-02-01T00:00:00+00:00', 'manual', 'r1')""",
        (ids["acme"],),
    )

    conn.commit()
    conn.close()


def _table_counts(path: str) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in USER_SPACE_TABLES
        }
    finally:
        conn.close()


def _user_id_null_counts(path: str) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        out: dict[str, int] = {}
        for table in USER_SPACE_TABLES:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "user_id" not in cols:
                out[table] = -1  # column absent
            else:
                out[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL"
                ).fetchone()[0]
        return out
    finally:
        conn.close()


def _default_user_id(path: str) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT value FROM config WHERE key = 'default_user_id'"
        ).fetchone()[0]
    finally:
        conn.close()


# ── US1 scenario 4 / FR-006: one-shot migration on a legacy DB ────────────────

def test_legacy_migration_rekeys_and_backfills(tmp_path):
    path = str(tmp_path / "legacy.db")
    _build_legacy_db(path)
    before = _table_counts(path)

    db = JobStorage(path)

    # Migration recorded exactly once.
    conn = sqlite3.connect(path)
    names = [r[0] for r in conn.execute("SELECT name FROM migrations").fetchall()]
    assert names.count("user_space_separation") == 1
    conn.close()

    # Row counts unchanged for every user-space table.
    assert _table_counts(path) == before

    # Every user-space row has user_id = the default user.
    uid = db.user_id
    assert uid == _default_user_id(path)
    nulls = _user_id_null_counts(path)
    for table in USER_SPACE_TABLES:
        assert nulls[table] == 0, f"{table} has NULL user_id rows"

    # job_tracking / job_applications rekeyed to PK (user_id, job_id).
    conn = sqlite3.connect(path)
    for table in ("job_tracking", "job_applications"):
        pks = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
               if r[5] > 0]  # r[5] is pk ordinal
        assert pks == ["user_id", "job_id"], f"{table} PK not rekeyed: {pks}"
    conn.close()


def test_user_companies_backfill_exactly_non_default(tmp_path):
    path = str(tmp_path / "legacy.db")
    _build_legacy_db(path)
    JobStorage(path)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT c.name_normalized, uc.status, uc.notes, uc.monitored "
        "FROM user_companies uc JOIN companies c ON c.id = uc.company_id "
        "ORDER BY c.name_normalized"
    ).fetchall()
    conn.close()

    by_norm = {r["name_normalized"]: r for r in rows}

    # Default company (Acme) must NOT get a user_companies row.
    assert "acme_corp" not in by_norm
    # Non-default relationships must each get one row (the profile denylist
    # companies are also blacklisted, so they legitimately appear too).
    assert {"globex", "initech", "umbrella_corp", "hooli"} <= set(by_norm)
    assert by_norm["globex"]["status"] == "blacklisted"
    assert by_norm["initech"]["notes"] == "promising"
    assert by_norm["umbrella_corp"]["monitored"] == 1
    # 'watching' is revalued to 'watch_ready' and captured as monitored=1.
    assert by_norm["hooli"]["monitored"] == 1


def test_monitoring_status_revalued_from_watching(tmp_path):
    path = str(tmp_path / "legacy.db")
    _build_legacy_db(path)
    JobStorage(path)

    conn = sqlite3.connect(path)
    status = conn.execute(
        "SELECT monitoring_status FROM companies WHERE name_normalized = 'hooli'"
    ).fetchone()[0]
    conn.close()
    assert status == "watch_ready"


def test_migration_idempotent_second_open_is_noop(tmp_path):
    path = str(tmp_path / "legacy.db")
    _build_legacy_db(path)
    JobStorage(path)
    after_first = _table_counts(path)

    # Second open must be a no-op.
    JobStorage(path)
    assert _table_counts(path) == after_first

    conn = sqlite3.connect(path)
    names = [r[0] for r in conn.execute("SELECT name FROM migrations").fetchall()]
    assert names.count("user_space_separation") == 1
    conn.close()


def test_migration_rolls_back_on_failure(tmp_path, monkeypatch):
    path = str(tmp_path / "legacy.db")
    _build_legacy_db(path)

    def _boom(self, conn, uid):
        raise RuntimeError("induced mid-migration failure")

    monkeypatch.setattr(JobStorage, "_backfill_user_companies", _boom)

    with pytest.raises(RuntimeError):
        JobStorage(path)

    # Nothing from user_space_separation may have half-committed.
    conn = sqlite3.connect(path)
    names = [r[0] for r in conn.execute("SELECT name FROM migrations").fetchall()]
    assert "user_space_separation" not in names
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    has_default = conn.execute(
        "SELECT COUNT(*) FROM config WHERE key = 'default_user_id'"
    ).fetchone()[0]
    assert has_default == 0
    # job_tracking rekey (DROP/RENAME) was rolled back — still old shape.
    track_cols = {r[1] for r in conn.execute("PRAGMA table_info(job_tracking)").fetchall()}
    assert "user_id" not in track_cols
    conn.close()
