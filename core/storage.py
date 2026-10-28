"""
storage.py — SQLite persistence layer for job_agent
=====================================================
Schéma 3 tables :
  - jobs          : données brutes, jamais rescorées, partagées entre profils
  - search_profiles : définition des profils (criteria JSON)
  - job_scores    : scoring par job × profil, statut de suivi

Usage typique dans main.py :
    db = JobStorage()
    db.upsert_profile(profile)
    new, cached = db.split_new_cached(all_jobs, profile.id)
    # scorer uniquement `new`
    db.save_scored(job, result, profile.id)
    db.touch_many([j["id"] for j in cached])
"""

import hashlib
import json
import logging
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

from core.paths import DB_PATH
from core.tasks import (
    EVENT_RETENTION_DAYS,
    EVENT_WINDOW,
    TASK_RETENTION_DAYS,
    TASK_TYPES,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Company name normalization
# ---------------------------------------------------------------------------

COMPANY_STATUSES = frozenset({
    "prospect", "watching", "active_outreach", "engaged",
    "dormant", "passed_by_me", "declined_by_them", "blacklisted",
})

CONTACT_ROLE_FAMILIES = frozenset({
    "recruiter", "hiring_manager", "founder", "leadership",
    "product", "peer", "other",
})
CONTACT_SENIORITIES = frozenset({
    "IC", "manager", "director", "vp", "cxo", "unknown",
})
CONTACT_EMAIL_STATUSES = frozenset({
    "unknown", "pattern_guessed", "verified", "bounced", "role_account",
})
INTERACTION_TYPES = frozenset({
    "discovered_on_posting", "discovered_manual",
    "outreach_sent", "reply_received", "intro_received",
    "call", "meeting",
    "application_submitted", "interview_invited", "interview",
    "decision_received", "withdrawn",
    "note",
})
INTERACTION_DIRECTIONS = frozenset({"inbound", "outbound", "none"})
INTERACTION_OUTCOMES = frozenset({"positive", "negative"})

# Scopes a v1 API token may carry (spec 041/042/043). Read + write, catalogue vs
# user, plus the task queue (tasks:run / tasks:pipeline).
API_TOKEN_SCOPES = frozenset({
    "read:catalogue", "read:me", "write:me", "write:catalogue",
    "tasks:run", "tasks:pipeline", "ingest",
})

_LEGAL_SUFFIXES = {
    "ag", "gmbh", "sa", "sarl", "inc", "ltd",
    "llc", "llp", "plc", "bv", "nv", "oy", "ab", "as",
    "corp", "corporation", "co", "holding", "holdings",
    "group", "the", "companies",
}
_GENERIC_SUFFIXES = {
    "labs", "lab", "software", "technologies", "technology", "tech",
    "solutions", "systems", "services",
}


def _normalize_company_name(name: str | None) -> str:
    """Normalize a company name for dedup: lowercase, strip legal/generic suffixes
    and punctuation, collapse whitespace."""
    if not name:
        return ""
    # Lowercase and strip punctuation, then split into words
    n = name.lower().strip()
    n = re.sub(r"[^a-z0-9\s]", " ", n)
    words = n.split()
    # Strip "the" from the beginning (common legal prefix)
    if words and words[0] == "the":
        words.pop(0)
    # Strip trailing legal/generic suffixes
    while words and words[-1] in _LEGAL_SUFFIXES | _GENERIC_SUFFIXES:
        words.pop()
    return " ".join(words)


# ---------------------------------------------------------------------------
# URL normalization for intra-source dedup
# ---------------------------------------------------------------------------

def normalize_url(url: str, source: str) -> str:
    """Derive a canonical URL for dedup purposes.

    LinkedIn:  Extract numeric id from /jobs/view/XXXXXXX, strip tracking params.
    Indeed:    Extract jk param regardless of country TLD.
    All other: Passthrough — returned unchanged.
    """
    if not url:
        return url
    source_lower = source.lower() if source else ""
    if source_lower == "linkedin":
        match = re.search(r"/jobs/view/(\d+)", url)
        if match:
            return f"https://www.linkedin.com/jobs/view/{match.group(1)}"
    elif source_lower == "indeed":
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        jk = qs.get("jk", [None])[0]
        if jk:
            return f"https://www.indeed.com/viewjob?jk={jk}"
    return url


def normalize_title(title: str) -> str:
    """Normalize a job title for dedup: lowercase, strip punctuation, collapse whitespace."""
    if not title:
        return ""
    return " ".join(re.sub(r"[^\w\s]", "", title.lower()).split())


def normalize_company(company: str) -> str:
    """Normalize a company name for dedup: lowercase, collapse whitespace only.

    Does NOT strip legal suffixes (Inc/GmbH/SA/Ltd) — that enters cross-source
    matching territory, which needs its own false-positive review.
    """
    if not company:
        return ""
    return " ".join(company.lower().split())


# ---------------------------------------------------------------------------
# Schéma
# ---------------------------------------------------------------------------

SCHEMA = """
-- Company registry — canonical company records with status tracking
CREATE TABLE IF NOT EXISTS companies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    name_normalized TEXT NOT NULL UNIQUE,
    website         TEXT,
    careers_url     TEXT,
    status          TEXT NOT NULL DEFAULT 'prospect',
    notes           TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companies_status ON companies (status);
CREATE INDEX IF NOT EXISTS idx_companies_name_norm ON companies (name_normalized);

-- Company status change history — append-only (user-space: one user's relationship)
CREATE TABLE IF NOT EXISTS company_status_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    company_id  INTEGER NOT NULL,
    status      TEXT NOT NULL,
    note        TEXT,
    changed_at  TEXT NOT NULL,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);
CREATE INDEX IF NOT EXISTS idx_company_status_history ON company_status_history (company_id, changed_at DESC);
-- idx_company_status_history_user (user_id) is created in _rebuild_per_user_indexes
-- after the user_id column exists (legacy DBs add it during migration).

-- Users — the owners of every user-space row (one row today; the API seam)
CREATE TABLE IF NOT EXISTS users (
    id                      TEXT PRIMARY KEY,
    display_name            TEXT,
    active_profile_id       TEXT,
    onboarding_completed_at TEXT,
    is_service              INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL
);

-- User–company relationship — status/notes/watch decision for one user + company
CREATE TABLE IF NOT EXISTS user_companies (
    user_id            TEXT NOT NULL,
    company_id         INTEGER NOT NULL,
    status             TEXT NOT NULL DEFAULT 'prospect',
    notes              TEXT,
    monitored          INTEGER NOT NULL DEFAULT 0,
    watch_requested_at TEXT,
    created_at         TEXT NOT NULL,
    PRIMARY KEY (user_id, company_id),
    FOREIGN KEY (company_id) REFERENCES companies(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Données brutes des offres — une ligne par job, partagée entre profils
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    company       TEXT,
    company_id    INTEGER,
    url           TEXT,
    canonical_url TEXT,
    norm_title    TEXT,
    norm_company  TEXT,
    source        TEXT,
    location      TEXT,
    base_location TEXT,
    posted_date   TEXT,
    description   TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

-- Profils de recherche
CREATE TABLE IF NOT EXISTS search_profiles (
    id       TEXT PRIMARY KEY,
    user_id  TEXT NOT NULL,
    name     TEXT,
    criteria TEXT   -- JSON sérialisé (geo_zones, work_modes, etc.)
);

-- Scoring par job × profil (pure scoring — no pipeline state)
CREATE TABLE IF NOT EXISTS job_scores (
    job_id        TEXT NOT NULL,
    profile_id    TEXT NOT NULL,
    score         INTEGER,
    reason        TEXT,
    summary       TEXT,
    work_mode     TEXT,
    geo_zone      TEXT,
    company_size  TEXT,
    contract_type TEXT,
    scored_by     TEXT,
    scored_at     TEXT,
    PRIMARY KEY (job_id, profile_id),
    FOREIGN KEY (job_id)     REFERENCES jobs(id),
    FOREIGN KEY (profile_id) REFERENCES search_profiles(id)
);

-- Pipeline status — one row per (user, job), user-space
CREATE TABLE IF NOT EXISTS job_tracking (
    user_id    TEXT NOT NULL,
    job_id     TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'new',
    notes      TEXT,
    changed_at TEXT,
    PRIMARY KEY (user_id, job_id)
);

-- Status change history — one row per transition, append-only (user-space)
CREATE TABLE IF NOT EXISTS status_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    job_id     TEXT NOT NULL,
    status     TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE INDEX IF NOT EXISTS idx_status_history_job ON status_history (job_id, changed_at DESC);
-- idx_status_history_user (user_id) is created in _rebuild_per_user_indexes after
-- the user_id column exists (legacy DBs add it during migration).

-- Application content — one row per (user, job), user-space
CREATE TABLE IF NOT EXISTS job_applications (
    user_id             TEXT NOT NULL,
    job_id              TEXT NOT NULL,
    profile_id          TEXT,
    analysis            TEXT,
    cover_letter        TEXT,
    cv_bullets_selected  TEXT,
    company_research     TEXT,
    screening_answers    TEXT,
    language             TEXT,
    prepared_by          TEXT,
    prepared_at          TEXT,
    created_at           TEXT,
    PRIMARY KEY (user_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_last_seen  ON jobs (last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_company_id ON jobs (company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs (first_seen);
-- idx_jobs_title_company created in migration below (depends on norm_company/norm_title columns)
CREATE INDEX IF NOT EXISTS idx_scores_profile  ON job_scores (profile_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_job      ON job_scores (job_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_tracking_status ON job_tracking (status);

-- Key-value config store (active_profile_id, etc.)
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Contacts — people discovered at companies (recruiters, hiring managers, etc.)
CREATE TABLE IF NOT EXISTS contacts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT NOT NULL,
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
    created_at        TEXT NOT NULL,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);
CREATE INDEX IF NOT EXISTS idx_contacts_company    ON contacts (company_id);
CREATE INDEX IF NOT EXISTS idx_contacts_email      ON contacts (email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contacts_unverified ON contacts (is_unverified) WHERE is_unverified = 1;
-- idx_contacts_linkedin and idx_contacts_user (user_id) are created in
-- _rebuild_per_user_indexes after the user_id column exists (legacy DBs add it
-- during migration).

-- Interactions — timeline of touchpoints with companies/contacts/jobs (user-space)
CREATE TABLE IF NOT EXISTS interactions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT NOT NULL,
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
    source_ref        TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    FOREIGN KEY (job_id)     REFERENCES jobs(id)
);
CREATE INDEX IF NOT EXISTS idx_interactions_company  ON interactions (company_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_interactions_contact  ON interactions (contact_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_interactions_job      ON interactions (job_id);
CREATE INDEX IF NOT EXISTS idx_interactions_followup ON interactions (follow_up_due_at)
    WHERE follow_up_due_at IS NOT NULL;
-- idx_interactions_user (user_id) is created in _rebuild_per_user_indexes after
-- the user_id column exists (legacy DBs add it during migration).
-- idx_interactions_source_ref is created in _migrate_application_lifecycle after
-- the source_ref column exists (the SCHEMA must not reference it: on an existing
-- DB the column is added by migration, after executescript has already run).

-- Pipeline run log — one row per main.py invocation
CREATE TABLE IF NOT EXISTS runs (
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

-- API bearer tokens — scoped read access (spec 041). User space: owner = user_id.
-- Raw token never stored; only its SHA-256 hex digest.
CREATE TABLE IF NOT EXISTS api_tokens (
    id           TEXT PRIMARY KEY,           -- uuid4().hex (32 chars)
    user_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,       -- sha256(raw).hexdigest()
    scopes       TEXT NOT NULL,              -- JSON list, e.g. ["read:catalogue"]
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT,
    expires_at   TEXT                        -- FR-013; NULL = no expiry
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id);

-- Task queue + worker (spec 043) — one row per requested long-running task.
-- params is canonical JSON (sorted keys) so identical requests dedup.
CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,       -- uuid4().hex
    user_id          TEXT NOT NULL,          -- requester (owner)
    type             TEXT NOT NULL,          -- scrape|scrape_monitored|extract|score
    params           TEXT NOT NULL,          -- canonical JSON: {} or {"profile_id": "…"}
    status           TEXT NOT NULL DEFAULT 'queued',
    requested_by     TEXT,                   -- "tracker" or an API token id
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    exit_code        INTEGER,
    error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_user   ON tasks (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_active_dedup
    ON tasks (user_id, type, params) WHERE status IN ('queued', 'running');

-- One stdout/stderr line per task, monotonic seq per task (spec 043).
CREATE TABLE IF NOT EXISTS task_events (
    task_id TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    ts      TEXT NOT NULL,
    line    TEXT NOT NULL,
    PRIMARY KEY (task_id, seq),
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- Schema migrations tracking — idempotent, applied exactly once
CREATE TABLE IF NOT EXISTS migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# ── Tiny config helpers for migrations (conn-level, no JobStorage needed) ────

def _config_bool(conn, key: str) -> bool:
    return conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone() is not None


def _set_config(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
    )


# JobStorage
# ---------------------------------------------------------------------------

class ProfileNotFoundError(Exception):
    """A ``profile_id`` belongs to another user (spec 041 isolation).

    Raised by profile-scoped ``JobStorage`` methods instead of silently returning
    (or writing) another user's data. The API maps it to 404. A profile id with
    no row at all is *not* this error — those return empty results, so the app
    still works on a fresh install before any profile is seeded (Constitution IX).
    """


class JobNotFoundError(Exception):
    """A ``job_id`` has no row in the shared catalogue (spec 044 ``score_job``).

    Raised by ``enqueue_task`` when a ``score_job`` names a job that does not
    exist, so the API maps it to 404 before enqueuing a doomed task.
    """


# A purge older than this triggers a health-check warning. Kept here so both
# `/api/health` and `scripts/health_check.py` share the same threshold.
PURGE_STALE_DAYS = 3


class JobStorage:

    def __init__(self, db_path: str = DB_PATH, user_id: str | None = None):
        self.db_path = db_path
        # For :memory: databases each new connection is a separate empty DB,
        # so we keep a single persistent connection for the lifetime of this object.
        self._memory_conn: sqlite3.Connection | None = None
        if db_path == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:")
            self._memory_conn.row_factory = sqlite3.Row
            self._memory_conn.execute("PRAGMA journal_mode=WAL")
            self._memory_conn.execute("PRAGMA foreign_keys=ON")
        self._init_db()
        # Bind to a user after init — the migration/fresh init guarantees
        # `config.default_user_id` exists by now.
        self.user_id: str = user_id if user_id is not None else self._resolve_default_user_id()

    def _resolve_default_user_id(self) -> str:
        uid = self.get_config("default_user_id")
        if uid:
            return uid
        with self._conn() as conn:
            return self._ensure_default_user(conn)

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def _init_db(self):
        import os
        os.makedirs(
            os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else ".",
            exist_ok=True,
        )
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            # Migrations — add columns introduced after initial schema
            jobs_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "base_location" not in jobs_cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN base_location TEXT")
                logger.info("[Storage] Migration: added base_location column to jobs")

            # URL canonicalization for intra-source dedup (LinkedIn/Indeed)
            if "canonical_url" not in jobs_cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN canonical_url TEXT")
                logger.info("[Storage] Migration: added canonical_url column to jobs")

            # Title + company normalization for intra-source dedup
            if "norm_title" not in jobs_cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN norm_title TEXT")
                logger.info("[Storage] Migration: added norm_title column to jobs")
            if "norm_company" not in jobs_cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN norm_company TEXT")
                logger.info("[Storage] Migration: added norm_company column to jobs")

            # Index for title+company dedup lookup — create unconditionally
            # on upgrade since it's idempotent (IF NOT EXISTS).
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_title_company "
                "ON jobs (source, norm_company, norm_title)"
            )

            # Phase 1e — move structured fields from job_scores to jobs
            _p1e_cols = [
                ("summary",            "TEXT"),
                ("work_mode",          "TEXT"),
                ("geo_zone",           "TEXT"),
                ("company_size",       "TEXT"),
                ("contract_type",      "TEXT"),
                ("company_country",    "TEXT"),
                ("industry_sector",    "TEXT"),
                ("language_required",  "TEXT"),
                ("extracted_at",       "TEXT"),
                ("extracted_by",       "TEXT"),
            ]
            need_backfill = False
            for col, defn in _p1e_cols:
                if col not in jobs_cols:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {defn}")
                    logger.info(f"[Storage] Phase 1e migration: added {col} column to jobs")
                    if col != "extracted_at":
                        need_backfill = True

            if need_backfill:
                # Check source columns exist on job_scores before backfilling
                _p1e_src_cols = {row[1] for row in
                                 conn.execute("PRAGMA table_info(job_scores)").fetchall()}
                if "company_country" not in _p1e_src_cols:
                    # Columns already removed by Phase 1e.4 cleanup — nothing to backfill
                    pass
                else:
                    # Backfill structured fields from job_scores to jobs
                    src_rows = conn.execute("""
                        SELECT job_id, summary, work_mode, geo_zone,
                           company_size, contract_type,
                           company_country, industry_sector, language_required,
                           scored_at
                    FROM (
                        SELECT job_id, summary, work_mode, geo_zone,
                               company_size, contract_type,
                               company_country, industry_sector, language_required,
                               scored_at,
                               ROW_NUMBER() OVER (
                                   PARTITION BY job_id ORDER BY scored_at DESC
                               ) AS rn
                        FROM job_scores
                        WHERE score IS NOT NULL
                          AND (summary IS NOT NULL OR work_mode IS NOT NULL
                               OR company_country IS NOT NULL)
                    ) WHERE rn = 1
                """).fetchall()
                    backfilled = 0
                    for row in src_rows:
                        conn.execute(
                            """UPDATE jobs SET
                                   summary = ?, work_mode = ?, geo_zone = ?,
                                   company_size = ?, contract_type = ?,
                                   company_country = ?, industry_sector = ?,
                                   language_required = ?, extracted_at = ?
                             WHERE id = ? AND extracted_at IS NULL""",
                            (row["summary"], row["work_mode"], row["geo_zone"],
                             row["company_size"], row["contract_type"],
                             row["company_country"], row["industry_sector"],
                             row["language_required"], row["scored_at"],
                             row["job_id"]),
                        )
                        backfilled += 1
                    if backfilled:
                        logger.info(
                            f"[Storage] Phase 1e migration: backfilled {backfilled} jobs "
                            f"from job_scores"
                        )

            # Phase 1e.4 — drop redundant columns from job_scores (now on jobs table)
            score_cols = {row[1] for row in conn.execute("PRAGMA table_info(job_scores)").fetchall()}
            for col in ("summary", "work_mode", "geo_zone", "company_size",
                        "contract_type", "company_country", "industry_sector",
                        "language_required"):
                if col in score_cols:
                    conn.execute(f"ALTER TABLE job_scores DROP COLUMN {col}")
                    logger.info(f"[Storage] Phase 1e.4 cleanup: dropped {col} from job_scores")

            # Phase 6 — country_code on job_scores (per-profile country anchor)
            score_cols = {row[1] for row in conn.execute("PRAGMA table_info(job_scores)").fetchall()}
            if "country_code" not in score_cols:
                conn.execute("ALTER TABLE job_scores ADD COLUMN country_code TEXT")
                logger.info("[Storage] Phase 6: added country_code to job_scores")

            # Phase 7 — duration_seconds on runs table
            runs_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
            if "duration_seconds" not in runs_cols:
                conn.execute("ALTER TABLE runs ADD COLUMN duration_seconds REAL")
                logger.info("[Storage] Phase 7: added duration_seconds to runs")

            # Phase 8 — comp_flag on job_scores table
            scores_cols = {row[1] for row in conn.execute("PRAGMA table_info(job_scores)").fetchall()}
            if "comp_flag" not in scores_cols:
                conn.execute("ALTER TABLE job_scores ADD COLUMN comp_flag INTEGER DEFAULT 0")
                logger.info("[Storage] Phase 8: added comp_flag to job_scores")

            # Phase 6 — country_code on jobs table (profile-independent extraction)
            job_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "country_code" not in job_cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN country_code TEXT")
                logger.info("[Storage] Phase 6: added country_code to jobs")
            # One-shot: re-extract recent jobs (last 7 days) that were extracted before country_code existed
            if "country_code" not in job_cols:
                reset = conn.execute(
                    "UPDATE jobs SET extracted_at = NULL WHERE country_code IS NULL AND last_seen >= date('now', '-7 days')"
                ).rowcount
                if reset:
                    logger.info(f"[Storage] Phase 6: cleared extracted_at on {reset} recent jobs for country_code re-extraction")

            # Phase 2 — prepare.py: add application prep columns to job_applications
            app_cols = {row[1] for row in conn.execute("PRAGMA table_info(job_applications)").fetchall()}
            _p2_cols = [
                ("profile_id",          "TEXT"),
                ("cv_bullets_selected",  "TEXT"),
                ("company_research",     "TEXT"),
                ("screening_answers",    "TEXT"),
                ("language",             "TEXT"),
                ("prepared_by",          "TEXT"),
                ("prepared_at",          "TEXT"),
            ]
            for col, defn in _p2_cols:
                if col not in app_cols:
                    conn.execute(f"ALTER TABLE job_applications ADD COLUMN {col} {defn}")
                    logger.info(f"[Storage] Phase 2 migration: added {col} to job_applications")

            # Phase 2.1 — backfill status_history from existing job_tracking rows
            # Only runs once when status_history is empty (fresh DB / first migration)
            hist_count = conn.execute("SELECT COUNT(*) FROM status_history").fetchone()[0]
            if hist_count == 0:
                backfill_count = conn.execute("""
                    INSERT OR IGNORE INTO status_history (job_id, status, changed_at)
                    SELECT t.job_id, t.status, COALESCE(a.prepared_at, datetime('now'))
                    FROM job_tracking t
                    LEFT JOIN job_applications a ON t.job_id = a.job_id
                """).rowcount
                if backfill_count:
                    logger.info(
                        f"[Storage] Phase 2.1 backfill: {backfill_count} status_history rows"
                    )

            # Phase 2.2 — add changed_at to job_tracking (denormalized for UI perf)
            track_cols = {row[1] for row in conn.execute("PRAGMA table_info(job_tracking)").fetchall()}
            if "changed_at" not in track_cols:
                conn.execute("ALTER TABLE job_tracking ADD COLUMN changed_at TEXT")
                logger.info("[Storage] Phase 2.2 migration: added changed_at to job_tracking")
                # Backfill from status_history
                conn.execute("""
                    UPDATE job_tracking SET changed_at = (
                        SELECT MAX(changed_at) FROM status_history
                        WHERE status_history.job_id = job_tracking.job_id
                    )
                """)
                logger.info("[Storage] Phase 2.2 backfill: populated changed_at from status_history")

            # Phase 3 — companies table, company_id FK, data backfill, blacklist migration
            if "company_id" not in jobs_cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN company_id INTEGER REFERENCES companies(id)")
                logger.info("[Storage] Phase 3 migration: added company_id column to jobs")

            # Phase 3.1 — backfill companies table from existing jobs
            # Only runs when companies table is empty (idempotent guard)
            company_count = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
            if company_count == 0 and conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] > 0:
                # Upsert distinct (company, source) pairs, using aggregate timestamps
                distinct_pairs = conn.execute("""
                    SELECT company, source,
                           MIN(first_seen) AS first_seen,
                           MAX(last_seen)  AS last_seen
                    FROM jobs
                    WHERE company IS NOT NULL AND company != ''
                    GROUP BY company, source
                """).fetchall()
                company_ids: dict[str, int] = {}  # name_normalized → id
                created = 0
                for row in distinct_pairs:
                    name_norm = _normalize_company_name(row["company"])
                    if not name_norm:
                        continue
                    if name_norm not in company_ids:
                        now = _now()
                        conn.execute(
                            """INSERT INTO companies
                               (name, name_normalized, status,
                                first_seen_at, last_seen_at, created_at)
                               VALUES (?, ?, 'prospect', ?, ?, ?)
                               ON CONFLICT(name_normalized) DO UPDATE SET
                                   name = excluded.name,
                                   last_seen_at = MAX(companies.last_seen_at, excluded.last_seen_at)""",
                            (row["company"], name_norm,
                             row["first_seen"], row["last_seen"], now))
                        cid = conn.execute(
                            "SELECT id FROM companies WHERE name_normalized = ?",
                            (name_norm,)).fetchone()["id"]
                        company_ids[name_norm] = cid
                        created += 1

                if created:
                    logger.info(
                        f"[Storage] Phase 3.1 backfill: {created} companies created "
                        f"from {len(distinct_pairs)} distinct (company, source) pairs"
                    )

                # Link jobs to companies
                linked = 0
                job_rows = conn.execute(
                    "SELECT id, company FROM jobs WHERE company IS NOT NULL AND company != ''"
                ).fetchall()
                for jrow in job_rows:
                    name_norm = _normalize_company_name(jrow["company"])
                    cid = company_ids.get(name_norm)
                    if cid:
                        conn.execute(
                            "UPDATE jobs SET company_id = ? WHERE id = ?",
                            (cid, jrow["id"]))
                        linked += 1
                if linked:
                    logger.info(
                        f"[Storage] Phase 3.1 backfill: linked {linked} jobs to companies"
                    )

            # Phase 3.2 — blacklist migration from profiles
            company_count = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
            if company_count > 0:
                from core.profiles import ALL_PROFILES
                hist_cols = {r[1] for r in conn.execute(
                    "PRAGMA table_info(company_status_history)").fetchall()}
                has_user_id = "user_id" in hist_cols
                uid = self._ensure_default_user(conn) if has_user_id else None
                bl_created = 0
                for profile in ALL_PROFILES.values():
                    denylist = getattr(profile, "denylisted_companies", []) or []
                    for company_name in denylist:
                        name_norm = _normalize_company_name(company_name)
                        if not name_norm:
                            continue
                        now = _now()
                        existing = conn.execute(
                            "SELECT id, status FROM companies WHERE name_normalized = ?",
                            (name_norm,)).fetchone()
                        if existing is None:
                            conn.execute(
                                """INSERT INTO companies
                                   (name, name_normalized, status,
                                    first_seen_at, last_seen_at, created_at)
                                   VALUES (?, ?, 'blacklisted', ?, ?, ?)""",
                                (company_name, name_norm, now, now, now))
                            cid = conn.execute(
                                "SELECT id FROM companies WHERE name_normalized = ?",
                                (name_norm,)).fetchone()["id"]
                            if has_user_id:
                                conn.execute(
                                    """INSERT INTO company_status_history
                                       (user_id, company_id, status, note, changed_at)
                                       VALUES (?, ?, 'blacklisted', ?, ?)""",
                                    (uid, cid, "migrated from profiles.py denylist", now))
                            else:
                                conn.execute(
                                    """INSERT INTO company_status_history
                                       (company_id, status, note, changed_at)
                                       VALUES (?, 'blacklisted', ?, ?)""",
                                    (cid, "migrated from profiles.py denylist", now))
                            bl_created += 1
                        elif existing["status"] != "blacklisted":
                            conn.execute(
                                "UPDATE companies SET status = 'blacklisted' WHERE id = ?",
                                (existing["id"],))
                            if has_user_id:
                                conn.execute(
                                    """INSERT INTO company_status_history
                                       (user_id, company_id, status, note, changed_at)
                                       VALUES (?, ?, 'blacklisted', ?, ?)""",
                                    (uid, existing["id"], "migrated from profiles.py denylist", now))
                            else:
                                conn.execute(
                                    """INSERT INTO company_status_history
                                       (company_id, status, note, changed_at)
                                       VALUES (?, 'blacklisted', ?, ?)""",
                                    (existing["id"], "migrated from profiles.py denylist", now))
                            bl_created += 1
                if bl_created:
                    logger.info(
                        f"[Storage] Phase 3.2 blacklist migration: {bl_created} companies blacklisted"
                    )

            # Phase 4 — move company-level fields from jobs to companies
            companies_cols = {row[1] for row in conn.execute("PRAGMA table_info(companies)").fetchall()}
            if "company_country" not in companies_cols:
                conn.execute("ALTER TABLE companies ADD COLUMN company_country TEXT")
                conn.execute("ALTER TABLE companies ADD COLUMN industry_sector TEXT")
                conn.execute("ALTER TABLE companies ADD COLUMN company_size TEXT")
                conn.execute("ALTER TABLE companies ADD COLUMN enriched_at TEXT")
                conn.execute("ALTER TABLE companies ADD COLUMN enriched_by TEXT")
                logger.info("[Storage] Phase 4 migration: added enrichment columns to companies")

                # Backfill from existing jobs — for each company, pick the most
                # recent non-null values across its jobs (tiebreak by extracted_at).
                backfill_rows = conn.execute("""
                    SELECT j.company_id,
                           j.company_country, j.industry_sector, j.company_size,
                           j.extracted_at, j.extracted_by
                    FROM jobs j
                    WHERE j.company_id IS NOT NULL
                      AND (j.company_country IS NOT NULL
                           OR j.industry_sector IS NOT NULL
                           OR j.company_size IS NOT NULL)
                    ORDER BY j.company_id, j.extracted_at DESC
                """).fetchall()

                company_best: dict[int, dict] = {}
                for row in backfill_rows:
                    cid = row["company_id"]
                    if cid not in company_best:
                        company_best[cid] = {
                            "company_country": None,
                            "industry_sector": None,
                            "company_size": None,
                            "enriched_at": None,
                            "enriched_by": None,
                        }
                    entry = company_best[cid]
                    _val = row["company_country"]
                    if entry["company_country"] is None and _val and _val != "unknown":
                        entry["company_country"] = _val
                    _val = row["industry_sector"]
                    if entry["industry_sector"] is None and _val and _val != "other":
                        entry["industry_sector"] = _val
                    _val = row["company_size"]
                    if entry["company_size"] is None and _val and _val != "unknown":
                        entry["company_size"] = _val
                    if entry["enriched_at"] is None and row["extracted_at"]:
                        entry["enriched_at"] = row["extracted_at"]
                    if entry["enriched_by"] is None and row["extracted_by"]:
                        entry["enriched_by"] = row["extracted_by"]

                backfilled = 0
                for cid, fields in company_best.items():
                    conn.execute(
                        """UPDATE companies SET
                               company_country = ?, industry_sector = ?,
                               company_size = ?, enriched_at = ?, enriched_by = ?
                         WHERE id = ?""",
                        (fields["company_country"], fields["industry_sector"],
                         fields["company_size"], fields["enriched_at"],
                         fields["enriched_by"], cid))
                    backfilled += 1

                if backfilled:
                    logger.info(
                        f"[Storage] Phase 4 backfill: {backfilled} companies enriched "
                        f"from existing job data"
                    )

            # Phase 4.1 — company summary (extracted from job descriptions)
            cur_cols = {row[1] for row in conn.execute("PRAGMA table_info(companies)").fetchall()}
            if "summary" not in cur_cols:
                try:
                    conn.execute("ALTER TABLE companies ADD COLUMN summary TEXT")
                    logger.info("[Storage] Phase 4.1 migration: added summary column to companies")
                except sqlite3.OperationalError:
                    pass  # already added

            # Phase 5 — drop company-level columns from jobs (SQLite 3.35+)
            # macOS ships SQLite < 3.35, so DROP COLUMN is unsupported there.
            # The columns are dead weight (data was migrated to companies table);
            # log once, then skip future attempts.
            if not _config_bool(conn, "migration.phase5.attempted"):
                jobs_cols_current = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
                phase5_cols = {"company_country", "industry_sector", "company_size"}
                remaining = phase5_cols & jobs_cols_current
                if remaining:
                    try:
                        # Try dropping one column to check SQLite version support
                        test_col = next(iter(remaining))
                        conn.execute(f"ALTER TABLE jobs DROP COLUMN {test_col}")
                        logger.info(f"[Storage] Phase 5 cleanup: dropped {test_col} from jobs")
                        # Drop any others that remain
                        for col in remaining - {test_col}:
                            if col in {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}:
                                try:
                                    conn.execute(f"ALTER TABLE jobs DROP COLUMN {col}")
                                    logger.info(f"[Storage] Phase 5 cleanup: dropped {col} from jobs")
                                except sqlite3.OperationalError:
                                    pass
                    except sqlite3.OperationalError:
                        logger.info(
                            f"[Storage] Phase 5: columns {', '.join(sorted(remaining))} "
                            f"left on jobs table (SQLite < 3.35, harmless dead weight)"
                        )
                _set_config(conn, "migration.phase5.attempted", "true")

            # Phase 6 — contacts + interactions tables
            existing_tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "contacts" not in existing_tables:
                conn.execute("""
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
                        created_at        TEXT NOT NULL,
                        FOREIGN KEY (company_id) REFERENCES companies(id)
                    )
                """)
                conn.execute("""
                    CREATE UNIQUE INDEX idx_contacts_linkedin
                        ON contacts (linkedin_url) WHERE linkedin_url IS NOT NULL
                """)
                conn.execute("CREATE INDEX idx_contacts_company ON contacts (company_id)")
                conn.execute("""
                    CREATE INDEX idx_contacts_email ON contacts (email) WHERE email IS NOT NULL
                """)
                conn.execute("""
                    CREATE INDEX idx_contacts_unverified ON contacts (is_unverified)
                        WHERE is_unverified = 1
                """)
                logger.info("[Storage] Phase 6 migration: created contacts table")
            if "interactions" not in existing_tables:
                conn.execute("""
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
                        source_ref        TEXT,
                        FOREIGN KEY (company_id) REFERENCES companies(id),
                        FOREIGN KEY (contact_id) REFERENCES contacts(id),
                        FOREIGN KEY (job_id)     REFERENCES jobs(id)
                    )
                """)
                conn.execute("""
                    CREATE INDEX idx_interactions_company
                        ON interactions (company_id, occurred_at DESC)
                """)
                conn.execute("""
                    CREATE INDEX idx_interactions_contact
                        ON interactions (contact_id, occurred_at DESC)
                """)
                conn.execute("CREATE INDEX idx_interactions_job ON interactions (job_id)")
                conn.execute("""
                    CREATE INDEX idx_interactions_followup ON interactions (follow_up_due_at)
                        WHERE follow_up_due_at IS NOT NULL
                """)
                logger.info("[Storage] Phase 6 migration: created interactions table")

            # ── Company Researcher — migration (must run before Monitored,
            #   because _seed_greenhouse_boards needs monitoring_status column) ──
            self._migrate_company_researcher(conn)

            # ── Monitored Companies — Phase A: data model ──
            self._migrate_monitored_companies(conn)

            # ── Application Lifecycle — columns + event backfill (spec 030) ──
            self._migrate_application_lifecycle(conn)

            # ── User-space separation — user_id seam + companies split (spec 039) ──
            self._migrate_user_space_separation(conn)

            # ── Platform ingestion scope — title judgement moves to scoring (spec 040) ──
            self._migrate_ingestion_scope(conn)

            # ── Scope extension — search_locations/greenhouse_boards (spec 045) ──
            self._migrate_ingestion_scope_locations(conn)

            # ── API read tokens — api_tokens table already created by SCHEMA (spec 041) ──
            self._migrate_api_tokens(conn)

            # ── Task queue — tasks/task_events tables already created by SCHEMA (spec 043) ──
            self._migrate_tasks(conn)

            # ── Platform principal — users.is_service + 'platform' row (spec 045) ──
            self._migrate_platform_principal(conn)

        logger.debug(f"[Storage] DB ready at {self.db_path}")

    def _migration_applied(self, conn, name: str) -> bool:
        """Check if a named migration has already been applied."""
        row = conn.execute(
            "SELECT 1 FROM migrations WHERE name = ?", (name,)
        ).fetchone()
        return row is not None

    def _record_migration(self, conn, name: str) -> None:
        conn.execute(
            "INSERT INTO migrations (name, applied_at) VALUES (?, ?)",
            (name, _now()),
        )

    def _migrate_api_tokens(self, conn) -> None:
        """Record the api_tokens migration name (the table itself is idempotent in SCHEMA)."""
        if self._migration_applied(conn, "api_tokens"):
            return
        self._record_migration(conn, "api_tokens")

    def _migrate_tasks(self, conn) -> None:
        """Record the tasks migration name (the tables are idempotent in SCHEMA)."""
        if self._migration_applied(conn, "tasks"):
            return
        self._record_migration(conn, "tasks")

    def _migrate_platform_principal(self, conn) -> None:
        """Add `users.is_service` and seed the platform principal (spec 045).

        Idempotent by construction (no `migrations` row): the column is added
        only when missing and the `platform` row only when absent. The platform
        principal owns ingest-only tokens and never reads/writes user space.
        """
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "is_service" not in cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN is_service INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("[Storage] Platform principal: added users.is_service")

        row = conn.execute("SELECT 1 FROM users WHERE id = 'platform'").fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO users
                   (id, display_name, active_profile_id, onboarding_completed_at,
                    is_service, created_at)
                   VALUES ('platform', 'Platform ingestion', NULL, NULL, 1, ?)""",
                (_now(),),
            )
            logger.info("[Storage] Platform principal: seeded 'platform'")

    def _migrate_application_lifecycle(self, conn) -> None:
        """Add provenance/notes columns to interactions and backfill lifecycle events.

        Part A (idempotent, not recorded in `migrations`): add `notes`, `source`,
        `source_ref` columns and the partial unique index enforcing idempotency.
        Part B (recorded as `backfill_lifecycle_events`): reconstruct
        `application_submitted` / `decision_received` events for existing
        applied/rejected jobs from `status_history`.
        """
        cols = {row[1] for row in conn.execute("PRAGMA table_info(interactions)").fetchall()}
        for col in ("notes", "source", "source_ref"):
            if col not in cols:
                conn.execute(f"ALTER TABLE interactions ADD COLUMN {col} TEXT")
                logger.info(f"[Storage] Lifecycle migration: added {col} to interactions")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_interactions_source_ref "
            "ON interactions (job_id, type, source_ref) WHERE source_ref IS NOT NULL"
        )

        # Provenance backfill for pre-existing rows (idempotent by construction).
        conn.execute("UPDATE interactions SET source = 'manual' WHERE source IS NULL")

        if self._migration_applied(conn, "backfill_lifecycle_events"):
            return

        has_user_id = "user_id" in cols
        uid = self._ensure_default_user(conn) if has_user_id else None

        # Application events: earliest `applied` history row for applied/rejected
        # jobs without an existing application_submitted interaction.
        rows = conn.execute("""
            SELECT j.id AS job_id, j.company_id, j.title, j.company, t.status,
                   (SELECT MIN(h.changed_at) FROM status_history h
                     WHERE h.job_id = j.id AND h.status = 'applied') AS applied_at
            FROM jobs j
            JOIN job_tracking t ON j.id = t.job_id
            WHERE t.status IN ('applied', 'rejected')
              AND NOT EXISTS (
                  SELECT 1 FROM interactions i
                  WHERE i.job_id = j.id AND i.type = 'application_submitted')
        """).fetchall()

        undatable = []
        backfilled_app = 0
        for r in rows:
            if not r["applied_at"]:
                undatable.append((r["job_id"], r["company"], r["title"], r["status"]))
                continue
            if has_user_id:
                conn.execute(
                    """INSERT INTO interactions
                       (user_id, company_id, job_id, type, direction, occurred_at, created_at, source)
                       VALUES (?, ?, ?, 'application_submitted', 'outbound', ?, ?, 'migration')""",
                    (uid, r["company_id"], r["job_id"], r["applied_at"], _now()),
                )
            else:
                conn.execute(
                    """INSERT INTO interactions
                       (company_id, job_id, type, direction, occurred_at, created_at, source)
                       VALUES (?, ?, 'application_submitted', 'outbound', ?, ?, 'migration')""",
                    (r["company_id"], r["job_id"], r["applied_at"], _now()),
                )
            backfilled_app += 1

        # Decision events: negative decision for rejected jobs with a prior applied row.
        dec_rows = conn.execute("""
            SELECT j.id AS job_id, j.company_id,
                   (SELECT MIN(h.changed_at) FROM status_history h
                     WHERE h.job_id = j.id AND h.status = 'rejected') AS rejected_at
            FROM jobs j
            JOIN job_tracking t ON j.id = t.job_id
            WHERE t.status = 'rejected'
              AND EXISTS (
                  SELECT 1 FROM status_history h
                  WHERE h.job_id = j.id AND h.status = 'applied')
              AND NOT EXISTS (
                  SELECT 1 FROM interactions i
                  WHERE i.job_id = j.id AND i.type = 'decision_received')
        """).fetchall()
        backfilled_dec = 0
        for r in dec_rows:
            if not r["rejected_at"]:
                continue
            if has_user_id:
                conn.execute(
                    """INSERT INTO interactions
                       (user_id, company_id, job_id, type, direction, outcome, occurred_at, created_at, source)
                       VALUES (?, ?, ?, 'decision_received', 'inbound', 'negative', ?, ?, 'migration')""",
                    (uid, r["company_id"], r["job_id"], r["rejected_at"], _now()),
                )
            else:
                conn.execute(
                    """INSERT INTO interactions
                       (company_id, job_id, type, direction, outcome, occurred_at, created_at, source)
                       VALUES (?, ?, 'decision_received', 'inbound', 'negative', ?, ?, 'migration')""",
                    (r["company_id"], r["job_id"], r["rejected_at"], _now()),
                )
            backfilled_dec += 1

        if undatable:
            logger.info(
                f"[Storage] Lifecycle backfill: {len(undatable)} undatable jobs "
                f"(no 'applied' history) — see application_date_missing(): {undatable}"
            )
        if backfilled_app or backfilled_dec:
            logger.info(
                f"[Storage] Lifecycle backfill: {backfilled_app} application + "
                f"{backfilled_dec} decision events created"
            )

        self._record_migration(conn, "backfill_lifecycle_events")

    # ------------------------------------------------------------------
    # Spec 039 — shared catalogue vs user space (roadmap step 3)
    # ------------------------------------------------------------------

    def _migrate_user_space_separation(self, conn) -> None:
        """Add the `user_id` seam and split `companies` into facts (catalogue)
        vs user relationship (`user_companies`).

        Runs inside the `_init_db` transaction (a failure rolls back the whole
        open). Recorded as `user_space_separation`, so it runs exactly once.
        The `users`/`user_companies` tables themselves come from SCHEMA; this
        migration creates the default user and rekeys/backfills existing data.
        """
        if self._migration_applied(conn, "user_space_separation"):
            return

        uid = self._ensure_default_user(conn)
        self._rekey_job_tracking(conn, uid)
        self._rekey_job_applications(conn, uid)
        for table in ("search_profiles", "status_history", "contacts",
                      "interactions", "company_status_history"):
            self._add_user_id_column(conn, table, uid)
        self._backfill_user_companies(conn, uid)
        self._revalue_monitoring_status(conn)
        self._rebuild_per_user_indexes(conn)

        self._record_migration(conn, "user_space_separation")
        logger.info("[Storage] user_space_separation: default user %s bound", uid)

    def _migrate_ingestion_scope(self, conn) -> None:
        """Seed `config.ingestion_scope` from the active profile (spec 040).

        Runs once (recorded as `ingestion_scope`). Ingestion is a platform
        parameter (Constitution I): the scope is copied from the profile's
        title fields exactly once; afterwards it is edited only via
        `scripts/ingestion_scope.py`. Writes **no** `job_scores` rows — the
        currently-flagged jobs converge on the next scoring run (FR-007).
        """
        if self._migration_applied(conn, "ingestion_scope"):
            return

        from core.profiles import ACTIVE_PROFILE, SearchProfile

        uid = self._ensure_default_user(conn)
        row = conn.execute(
            "SELECT active_profile_id FROM users WHERE id = ?", (uid,)
        ).fetchone()
        profile_id = row["active_profile_id"] if row and row["active_profile_id"] else None

        profile = None
        if profile_id:
            prof = conn.execute(
                "SELECT name, criteria FROM search_profiles WHERE id = ? AND user_id = ?",
                (profile_id, uid),
            ).fetchone()
            if prof:
                try:
                    criteria = json.loads(prof["criteria"]) if prof["criteria"] else {}
                except (TypeError, ValueError):
                    criteria = {}
                profile = SearchProfile.from_criteria(profile_id, prof["name"], criteria)

        if profile is None:
            profile = ACTIVE_PROFILE

        scope = {
            "title_families": list(profile.job_titles or []),
            "exclude": list(profile.title_exclude or []),
            "remote_or_hybrid": bool(profile.scrape_remote_or_hybrid),
        }
        conn.execute(
            "INSERT INTO config (key, value) VALUES ('ingestion_scope', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(scope),),
        )
        self._record_migration(conn, "ingestion_scope")
        logger.info("[Storage] ingestion_scope: seeded from profile %s", profile_id)

    def _migrate_ingestion_scope_locations(self, conn) -> None:
        """Seed scope `search_locations` / `greenhouse_boards` from the active
        profile once (spec 045, Constitution I). Recorded as
        `ingestion_scope_locations`.

        Runs after `_migrate_ingestion_scope`, so the spec-040 keys already
        exist; only the two new keys are copied (`setdefault` preserves a value
        already written by the prose path)."""
        if self._migration_applied(conn, "ingestion_scope_locations"):
            return

        from core.profiles import ACTIVE_PROFILE, SearchProfile

        uid = self._ensure_default_user(conn)
        row = conn.execute(
            "SELECT active_profile_id FROM users WHERE id = ?", (uid,)
        ).fetchone()
        profile_id = row["active_profile_id"] if row and row["active_profile_id"] else None

        profile = None
        if profile_id:
            prof = conn.execute(
                "SELECT name, criteria FROM search_profiles WHERE id = ? AND user_id = ?",
                (profile_id, uid),
            ).fetchone()
            if prof:
                try:
                    criteria = json.loads(prof["criteria"]) if prof["criteria"] else {}
                except (TypeError, ValueError):
                    criteria = {}
                profile = SearchProfile.from_criteria(profile_id, prof["name"], criteria)

        if profile is None:
            profile = ACTIVE_PROFILE

        raw = conn.execute(
            "SELECT value FROM config WHERE key = 'ingestion_scope'"
        ).fetchone()
        scope = {}
        if raw and raw["value"]:
            try:
                scope = json.loads(raw["value"])
            except (TypeError, ValueError):
                scope = {}
        if not isinstance(scope, dict):
            scope = {}
        scope.setdefault("search_locations", list(profile.effective_search_locations()))
        scope.setdefault("greenhouse_boards", list(profile.greenhouse_boards or []))

        conn.execute(
            "INSERT INTO config (key, value) VALUES ('ingestion_scope', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(scope),),
        )
        self._record_migration(conn, "ingestion_scope_locations")
        logger.info(
            "[Storage] ingestion_scope_locations: seeded locations/boards from profile %s",
            profile_id,
        )

    def _ensure_default_user(self, conn) -> str:
        """Create the single default user if absent; return its id.

        Idempotent: if `config.default_user_id` is set, return it. The
        per-user settings are copied out of `config` (their final home is the
        `users` row; the old keys are left dead, cleaned at step 10).
        """
        row = conn.execute(
            "SELECT value FROM config WHERE key = 'default_user_id'"
        ).fetchone()
        if row and row["value"]:
            return row["value"]

        from core.profiles import DEFAULT_PROFILE_ID

        uid = uuid.uuid4().hex
        active = conn.execute(
            "SELECT value FROM config WHERE key = 'active_profile_id'"
        ).fetchone()
        onboarded = conn.execute(
            "SELECT value FROM config WHERE key = 'onboarding_complete'"
        ).fetchone()
        now = _now()
        conn.execute(
            """INSERT INTO users
               (id, display_name, active_profile_id, onboarding_completed_at, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (uid, "Jean Claude",
             active["value"] if active and active["value"] else DEFAULT_PROFILE_ID,
             now if onboarded and onboarded["value"] == "true" else None,
             now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('default_user_id', ?)",
            (uid,),
        )
        return uid

    def _rekey_job_tracking(self, conn, uid: str) -> None:
        """Rebuild job_tracking with PRIMARY KEY (user_id, job_id)."""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(job_tracking)")}
        if "user_id" in cols:
            return
        conn.execute("""
            CREATE TABLE job_tracking_new (
                user_id    TEXT NOT NULL,
                job_id     TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'new',
                notes      TEXT,
                changed_at TEXT,
                PRIMARY KEY (user_id, job_id)
            )
        """)
        conn.execute(
            """INSERT INTO job_tracking_new (user_id, job_id, status, notes, changed_at)
               SELECT ?, job_id, status, notes, changed_at FROM job_tracking""",
            (uid,),
        )
        conn.execute("DROP TABLE job_tracking")
        conn.execute("ALTER TABLE job_tracking_new RENAME TO job_tracking")
        conn.execute("CREATE INDEX idx_tracking_status ON job_tracking (status)")
        logger.info("[Storage] user_space_separation: rekeyed job_tracking")

    def _rekey_job_applications(self, conn, uid: str) -> None:
        """Rebuild job_applications with PRIMARY KEY (user_id, job_id)."""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(job_applications)")}
        if "user_id" in cols:
            return
        conn.execute("""
            CREATE TABLE job_applications_new (
                user_id             TEXT NOT NULL,
                job_id              TEXT NOT NULL,
                profile_id          TEXT,
                analysis            TEXT,
                cover_letter        TEXT,
                cv_bullets_selected  TEXT,
                company_research     TEXT,
                screening_answers    TEXT,
                language             TEXT,
                prepared_by          TEXT,
                prepared_at          TEXT,
                created_at           TEXT,
                PRIMARY KEY (user_id, job_id)
            )
        """)
        conn.execute(
            """INSERT INTO job_applications_new
               (user_id, job_id, profile_id, analysis, cover_letter,
                cv_bullets_selected, company_research, screening_answers,
                language, prepared_by, prepared_at, created_at)
               SELECT ?, job_id, profile_id, analysis, cover_letter,
                      cv_bullets_selected, company_research, screening_answers,
                      language, prepared_by, prepared_at, created_at
               FROM job_applications""",
            (uid,),
        )
        conn.execute("DROP TABLE job_applications")
        conn.execute("ALTER TABLE job_applications_new RENAME TO job_applications")
        logger.info("[Storage] user_space_separation: rekeyed job_applications")

    def _add_user_id_column(self, conn, table: str, uid: str) -> None:
        """Add + backfill `user_id` on a simple user-space table (nullable on
        migrated DBs; NOT NULL on fresh installs via SCHEMA)."""
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "user_id" in cols:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")
        conn.execute(f"UPDATE {table} SET user_id = ?", (uid,))
        logger.info("[Storage] user_space_separation: added user_id to %s", table)

    def _backfill_user_companies(self, conn, uid: str) -> None:
        """Copy non-default company relationships into `user_companies`.

        A row is created only when the user has a decision beyond the default
        (status != 'prospect', non-empty notes, or watched). "No row" remains
        the default relationship on every read.
        """
        now = _now()
        conn.execute(
            """INSERT INTO user_companies
               (user_id, company_id, status, notes, monitored, watch_requested_at, created_at)
               SELECT ?, id, status, notes,
                      CASE WHEN (monitored IS NOT NULL AND monitored)
                                 OR monitoring_status = 'watching' THEN 1 ELSE 0 END,
                      CASE WHEN (monitored IS NOT NULL AND monitored)
                                 OR monitoring_status = 'watching' THEN ? ELSE NULL END,
                      ?
               FROM companies
               WHERE status != 'prospect'
                  OR (notes IS NOT NULL AND trim(notes) != '')
                  OR (monitored IS NOT NULL AND monitored)
                  OR monitoring_status = 'watching'""",
            (uid, now, now),
        )
        logger.info("[Storage] user_space_separation: backfilled user_companies")

    def _revalue_monitoring_status(self, conn) -> None:
        """Drop the mixed `watching` value: it conflated the user's request with
        the platform's capability. Every `watching` row was capable, so it
        becomes `watch_ready`; the request lives on user_companies.monitored."""
        updated = conn.execute(
            "UPDATE companies SET monitoring_status = 'watch_ready' "
            "WHERE monitoring_status = 'watching'"
        ).rowcount
        if updated:
            logger.info(
                f"[Storage] user_space_separation: {updated} companies "
                f"monitoring_status 'watching' -> 'watch_ready'"
            )

    def _rebuild_per_user_indexes(self, conn) -> None:
        """Widen the two unique indexes to per-user scope and ensure user_id
        lookups are indexed."""
        conn.execute("DROP INDEX IF EXISTS idx_contacts_linkedin")
        conn.execute(
            "CREATE UNIQUE INDEX idx_contacts_linkedin "
            "ON contacts (user_id, linkedin_url) WHERE linkedin_url IS NOT NULL"
        )
        conn.execute("DROP INDEX IF EXISTS idx_interactions_source_ref")
        conn.execute(
            "CREATE UNIQUE INDEX idx_interactions_source_ref "
            "ON interactions (user_id, job_id, type, source_ref) "
            "WHERE source_ref IS NOT NULL"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_user ON contacts (user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_interactions_user ON interactions (user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status_history_user ON status_history (user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_company_status_history_user ON company_status_history (user_id)")

    def _migrate_monitored_companies(self, conn) -> None:
        """Phase A: extend companies, jobs, runs for monitored companies feature."""

        # --- companies: ATS / monitoring columns ---
        companies_cols = {row[1] for row in conn.execute("PRAGMA table_info(companies)").fetchall()}
        # Ensure base columns exist (for DBs created before these were in the schema)
        for col, defn in [
            ("website",       "TEXT"),
            ("careers_url",   "TEXT"),
            ("notes",         "TEXT"),
            ("summary",       "TEXT"),
            ("company_country", "TEXT"),
            ("industry_sector", "TEXT"),
            ("company_size",  "TEXT"),
            ("enriched_at",   "TEXT"),
            ("enriched_by",   "TEXT"),
        ]:
            if col not in companies_cols:
                try:
                    conn.execute(f"ALTER TABLE companies ADD COLUMN {col} {defn}")
                    companies_cols.add(col)
                except sqlite3.OperationalError:
                    pass  # column already exists (race with earlier migration)
        for col, defn in [
            ("ats_provider",  "TEXT"),
            ("ats_identifier", "TEXT"),
            ("scraper_id",    "TEXT"),
            ("monitored",     "BOOLEAN DEFAULT FALSE"),
            ("detected_at",   "TEXT"),
        ]:
            if col not in companies_cols:
                conn.execute(f"ALTER TABLE companies ADD COLUMN {col} {defn}")
                logger.info(f"[Storage] Monitored: added {col} to companies")

        # --- jobs: monitored_company_id FK + filtered_non_product ---
        jobs_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "monitored_company_id" not in jobs_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN monitored_company_id INTEGER REFERENCES companies(id)")
            logger.info("[Storage] Monitored: added monitored_company_id to jobs")
        if "filtered_non_product" not in jobs_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN filtered_non_product BOOLEAN DEFAULT FALSE")
            logger.info("[Storage] Monitored: added filtered_non_product to jobs")

        # --- runs: run_type ---
        runs_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        if "run_type" not in runs_cols:
            conn.execute("ALTER TABLE runs ADD COLUMN run_type TEXT DEFAULT 'full'")
            logger.info("[Storage] Monitored: added run_type to runs")

        # --- Seed Greenhouse boards ---
        if not self._migration_applied(conn, "seed_greenhouse_boards"):
            self._seed_greenhouse_boards(conn)
            self._record_migration(conn, "seed_greenhouse_boards")

    def _migrate_company_researcher(self, conn) -> None:
        """Add company researcher columns to companies table."""
        companies_cols = {row[1] for row in
                          conn.execute("PRAGMA table_info(companies)").fetchall()}
        for col, defn in [
            ("monitoring_status",   "TEXT NOT NULL DEFAULT 'unmonitored'"),
            ("scraping_method",     "TEXT"),
            ("research_notes",      "TEXT"),
            ("research_confidence", "TEXT"),
            ("researched_at",       "TEXT"),
            ("x_handle",            "TEXT"),
        ]:
            if col not in companies_cols:
                conn.execute(f"ALTER TABLE companies ADD COLUMN {col} {defn}")
                logger.info(f"[Storage] Company Researcher: added {col} to companies")

        # Backfill: set monitoring_status = 'watching' for legacy monitored companies
        if ("monitored" in companies_cols
                and not self._migration_applied(conn, "backfill_monitoring_status_from_monitored")):
            updated = conn.execute(
                """UPDATE companies SET monitoring_status = 'watching'
                   WHERE monitored = TRUE
                     AND (monitoring_status IS NULL OR monitoring_status = 'unmonitored')"""
            ).rowcount
            if updated:
                logger.info(
                    f"[Storage] Monitoring: backfilled {updated} companies "
                    f"from monitored=TRUE to monitoring_status='watching'"
                )
            self._record_migration(conn, "backfill_monitoring_status_from_monitored")

    def _seed_greenhouse_boards(self, conn) -> None:
        """Seed ~30 Greenhouse boards from the current CRYPTO_WEB3_BOARDS constant."""
        # Import here to avoid circular dependency at module load
        try:
            from core.scrapers.greenhouse import CRYPTO_WEB3_BOARDS
        except ImportError:
            import importlib
            mod = importlib.import_module("core.scrapers.greenhouse")
            CRYPTO_WEB3_BOARDS = getattr(mod, "CRYPTO_WEB3_BOARDS", [])

        if not CRYPTO_WEB3_BOARDS:
            return

        seeded = 0
        now = _now()
        for token in CRYPTO_WEB3_BOARDS:
            # Derive a display name from the token: "coinbase" → "Coinbase", "avalabs" → "Ava Labs"
            name = token.replace("-", " ").replace("_", " ").title()
            name_norm = _normalize_company_name(name)
            if not name_norm:
                continue
            existing = conn.execute(
                "SELECT id FROM companies WHERE name_normalized = ?", (name_norm,)
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE companies SET
                           ats_provider = 'greenhouse',
                           ats_identifier = ?,
                           monitored = TRUE,
                           monitoring_status = 'watching',
                           last_seen_at = ?
                       WHERE id = ?""",
                    (token, now, existing["id"]))
            else:
                conn.execute(
                    """INSERT INTO companies
                       (name, name_normalized, careers_url,
                        ats_provider, ats_identifier, monitored,
                        monitoring_status,
                        status, first_seen_at, last_seen_at, created_at, detected_at)
                       VALUES (?, ?, ?, 'greenhouse', ?, TRUE, 'watching',
                               'prospect', ?, ?, ?, ?)""",
                    (name, name_norm,
                     f"https://boards.greenhouse.io/{token}",
                     token, now, now, now, now))
            seeded += 1

        if seeded:
            logger.info(f"[Storage] Monitored: seeded {seeded} Greenhouse boards as companies")

    @contextmanager
    def _conn(self):
        if self._memory_conn is not None:
            # Shared in-memory connection — no close, commit manually
            try:
                yield self._memory_conn
                self._memory_conn.commit()
            except Exception:
                self._memory_conn.rollback()
                raise
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _require_profile(self, conn, profile_id: str) -> None:
        """Guard profile-scoped methods against a cross-user leak (spec 041).

        A ``profile_id`` that belongs to the bound user is allowed. A profile
        that exists under a *different* user raises ``ProfileNotFoundError``
        (the API maps it to 404). A ``profile_id`` with no row at all is
        **not** an error — the caller proceeds and returns empty results, so the
        app still works on a fresh install before any profile is seeded
        (Constitution IX).
        """
        row = conn.execute(
            "SELECT user_id FROM search_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if row is not None and row[0] != self.user_id:
            raise ProfileNotFoundError(profile_id)

    # ------------------------------------------------------------------
    # Task queue + worker (spec 043)
    # ------------------------------------------------------------------

    def _task_row(self, row):
        """Decode a ``tasks`` row: parse the canonical params JSON back to a dict."""
        if row is None:
            return None
        d = dict(row)
        params = d.get("params")
        d["params"] = json.loads(params) if params else {}
        return d

    def _canonical_params(self, params: dict | None) -> str:
        return json.dumps(params or {}, sort_keys=True, separators=(",", ":"))

    def enqueue_task(self, type: str, params: dict | None = None,
                     requested_by: str | None = None) -> dict | None:
        """Validate + insert a task; on an active-dedup conflict return the existing task.

        The partial unique index ``idx_tasks_active_dedup`` makes the dedup
        race-free: an identical ``(user_id, type, params)`` already queued or
        running raises ``IntegrityError`` and we return that row instead of a
        new one (spec FR-001 / US1-4).
        """
        if type not in TASK_TYPES:
            raise ValueError(f"unknown task type: {type}")
        params = params or {}
        if type == "score" and not params.get("profile_id"):
            raise ValueError("score requires a profile_id")
        if type == "score_job" and (not params.get("job_id") or not params.get("profile_id")):
            raise ValueError("score_job requires job_id and profile_id")
        params_canonical = self._canonical_params(params)
        task_id = uuid.uuid4().hex
        with self._conn() as conn:
            if type in ("score", "score_job"):
                self._require_profile(conn, params["profile_id"])
            if type == "score_job" and conn.execute(
                "SELECT 1 FROM jobs WHERE id = ?", (params["job_id"],)
            ).fetchone() is None:
                raise JobNotFoundError(params["job_id"])
            try:
                conn.execute(
                    "INSERT INTO tasks (id, user_id, type, params, status, requested_by, created_at) "
                    "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                    (task_id, self.user_id, type, params_canonical, requested_by, _now()),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE user_id = ? AND type = ? AND params = ? "
                    "AND status IN ('queued', 'running')",
                    (self.user_id, type, params_canonical),
                ).fetchone()
                return self._task_row(row)
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict | None:
        """User-scoped: a foreign or absent task reads as ``None`` (spec 039)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, self.user_id),
            ).fetchone()
        return self._task_row(row)

    def list_tasks(self, status: str | None = None, type: str | None = None,
                   limit: int = 100, offset: int = 0):
        """User-scoped, newest first. Returns ``(rows, total)``."""
        where = ["user_id = ?"]
        args = [self.user_id]
        if status is not None:
            where.append("status = ?")
            args.append(status)
        if type is not None:
            where.append("type = ?")
            args.append(type)
        where_sql = " AND ".join(where)
        with self._conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM tasks WHERE {where_sql}", args
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE {where_sql} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                args + [limit, offset],
            ).fetchall()
        return [self._task_row(r) for r in rows], total

    def get_active_task(self, type: str, params: dict | None = None) -> dict | None:
        """The dedup row for a ``(type, params)`` key (tracker resume-after-reload)."""
        params_canonical = self._canonical_params(params)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE user_id = ? AND type = ? AND params = ? "
                "AND status IN ('queued', 'running')",
                (self.user_id, type, params_canonical),
            ).fetchone()
        return self._task_row(row)

    def has_queued_task(self) -> bool:
        """Cheap read for the worker's idle poll — True when a task is waiting.

        Lets an idle worker skip the flock and the holder write entirely, so it
        writes only its heartbeat between tasks.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM tasks WHERE status = 'queued' LIMIT 1"
            ).fetchone()
        return row is not None

    def claim_next_task(self) -> dict | None:
        """Atomically claim the oldest queued task (spec FR-003).

        Global across users: the worker is the single consumer for every user's
        queue. ``RETURNING`` (SQLite ≥ 3.35) makes claim + fetch one statement.
        """
        with self._conn() as conn:
            row = conn.execute(
                "UPDATE tasks SET status = 'running', started_at = ?, cancel_requested = 0 "
                "WHERE id = (SELECT id FROM tasks WHERE status = 'queued' "
                "ORDER BY created_at, id LIMIT 1) RETURNING *",
                (_now(),),
            ).fetchone()
        return self._task_row(row)

    def append_task_events(self, task_id: str, lines: list[str]) -> None:
        """Insert a batch of output lines with sequential ``seq``, then trim to
        the last ``EVENT_WINDOW`` events (spec R6)."""
        if not lines:
            return
        with self._conn() as conn:
            next_seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM task_events WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0] + 1
            ts = _now()
            conn.executemany(
                "INSERT INTO task_events (task_id, seq, ts, line) VALUES (?, ?, ?, ?)",
                [(task_id, next_seq + i, ts, line) for i, line in enumerate(lines)],
            )
            conn.execute(
                "DELETE FROM task_events WHERE task_id = ? AND seq <= ("
                "SELECT seq FROM task_events WHERE task_id = ? "
                "ORDER BY seq DESC LIMIT 1 OFFSET ?)",
                (task_id, task_id, EVENT_WINDOW),
            )

    def get_task_events(self, task_id: str, after_seq: int = 0, limit: int | None = None) -> list:
        """Lines with ``seq > after_seq``, ascending (spec R5 resumability)."""
        sql = "SELECT seq, ts, line FROM task_events WHERE task_id = ? AND seq > ? ORDER BY seq ASC"
        args = [task_id, after_seq]
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def finish_task(self, task_id: str, status: str,
                    exit_code: int | None = None, error: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, finished_at = ?, exit_code = ?, error = ? WHERE id = ?",
                (status, _now(), exit_code, error, task_id),
            )

    def mark_cancelled(self, task_id: str) -> None:
        """``queued`` → ``cancelled`` (user-scoped)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'cancelled', finished_at = ? "
                "WHERE id = ? AND user_id = ? AND status = 'queued'",
                (_now(), task_id, self.user_id),
            )

    def request_cancel(self, task_id: str) -> None:
        """Set ``cancel_requested = 1`` on a running task (user-scoped)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET cancel_requested = 1 "
                "WHERE id = ? AND user_id = ? AND status = 'running'",
                (task_id, self.user_id),
            )

    def is_cancel_requested(self, task_id: str) -> bool:
        """Polled by the worker between output reads."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def recover_interrupted(self) -> None:
        """``running`` → ``failed`` (``error="interrupted"``) at worker startup (US1-6)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'failed', error = 'interrupted', finished_at = ? "
                "WHERE status = 'running'",
                (_now(),),
            )

    def get_worker_heartbeat(self) -> str | None:
        return self.get_config("worker.heartbeat")

    def set_worker_heartbeat(self, ts: str) -> None:
        self.set_config("worker.heartbeat", ts)

    def get_pipeline_lock_holder(self) -> str | None:
        return self.get_config("pipeline.lock_holder")

    def set_pipeline_lock_holder(self, label: str | None) -> None:
        if self.get_pipeline_lock_holder() == label:
            return
        if label is None:
            with self._conn() as conn:
                conn.execute("DELETE FROM config WHERE key = 'pipeline.lock_holder'")
        else:
            self.set_config("pipeline.lock_holder", label)

    def prune_task_history(self, event_days: int = EVENT_RETENTION_DAYS,
                           task_days: int = TASK_RETENTION_DAYS) -> None:
        """Idle-tick cleanup (spec R6): drop finished tasks' events older than
        ``event_days`` and finished task rows older than ``task_days``."""
        finished = ("succeeded", "failed", "cancelled")
        event_cutoff = (datetime.now(timezone.utc) - timedelta(days=event_days)).isoformat()
        task_cutoff = (datetime.now(timezone.utc) - timedelta(days=task_days)).isoformat()
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM task_events WHERE task_id IN "
                "(SELECT id FROM tasks WHERE status IN (?, ?, ?) AND finished_at < ?)",
                (*finished, event_cutoff),
            )
            conn.execute(
                "DELETE FROM tasks WHERE status IN (?, ?, ?) AND finished_at < ?",
                (*finished, task_cutoff),
            )

    # ------------------------------------------------------------------
    # Gestion des profils
    # ------------------------------------------------------------------

    def upsert_profile(self, profile) -> None:
        """Insère ou met à jour un profil de recherche."""
        criteria = json.dumps(profile.to_criteria_dict())
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO search_profiles (id, user_id, name, criteria) VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name = excluded.name, criteria = excluded.criteria""",
                (profile.id, self.user_id, profile.name, criteria),
            )

    def get_profile(self, profile_id: str) -> dict | None:
        """Return a single profile row with parsed criteria, or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, name, criteria FROM search_profiles WHERE id = ? AND user_id = ?",
                (profile_id, self.user_id),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["criteria"] = json.loads(d["criteria"]) if d.get("criteria") else {}
        return d

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------

    def get_score_result(self, job_id: str, profile_id: str) -> Optional[dict]:
        """Retourne le dict de scoring pour ce job+profil, None si absent."""
        with self._conn() as conn:
            self._require_profile(conn, profile_id)
            row = conn.execute(
                """SELECT s.score, s.reason, s.scored_by,
                          COALESCE(j.summary, '') AS summary,
                          COALESCE(j.work_mode, 'unknown') AS work_mode,
                          COALESCE(j.geo_zone, 'unknown') AS geo_zone,
                          COALESCE(c.company_size, 'unknown') AS company_size,
                          COALESCE(j.contract_type, 'unknown') AS contract_type,
                          COALESCE(c.company_country, 'unknown') AS company_country,
                          COALESCE(c.industry_sector, 'other') AS industry_sector,
                          COALESCE(j.language_required, 'unknown') AS language_required,
                          s.country_code AS country_code
                   FROM job_scores s
                   JOIN jobs j ON j.id = s.job_id
                   LEFT JOIN companies c ON j.company_id = c.id
                   WHERE s.job_id = ? AND s.profile_id = ?""",
                (job_id, profile_id),
            ).fetchone()
            if row is None or row["score"] is None:
                return None
            return dict(row)

    # ------------------------------------------------------------------
    # split_new_cached — point d'entrée principal dans main.py
    # ------------------------------------------------------------------

    def split_new_cached(self, jobs: list, profile_id: str) -> tuple[list, list]:
        """
        Sépare une liste de JobPosting en :
          - new_jobs    : jamais scorés pour ce profil → à scorer
          - cached_jobs : déjà scorés pour ce profil → dict fusionné job+score

        Un job peut être nouveau pour un profil et caché pour un autre.
        Les jobs connus sans score (échec précédent) sont remis en new_jobs.
        """
        with self._conn() as conn:
            self._require_profile(conn, profile_id)
        new_jobs = []
        cached_jobs = []

        for job in jobs:
            score_data = self.get_score_result(job.id, profile_id)
            if score_data is not None:
                merged = job.to_json() if hasattr(job, "to_json") else vars(job)
                merged.update(score_data)
                cached_jobs.append(merged)
            else:
                new_jobs.append(job)

        logger.info(
            f"[Storage:{profile_id}] {len(new_jobs)} nouveaux · "
            f"{len(cached_jobs)} en cache"
        )
        return new_jobs, cached_jobs

    # ------------------------------------------------------------------
    # Écriture
    # ------------------------------------------------------------------

    def upsert_company(self, name: str, *,
                        website: str | None = None,
                        careers_url: str | None = None) -> int:
        """Insert or update a company, returning its id. Deduplicates by normalized name."""
        name_norm = _normalize_company_name(name)
        if not name_norm:
            raise ValueError(f"Company name normalizes to empty: {name!r}")
        now = _now()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id, website, careers_url FROM companies WHERE name_normalized = ?",
                (name_norm,)).fetchone()
            if existing:
                # Update last_seen_at, optionally fill website/careers_url if currently NULL
                updates = []
                params = []
                if website and not existing["website"]:
                    updates.append("website = ?")
                    params.append(website)
                if careers_url and not existing["careers_url"]:
                    updates.append("careers_url = ?")
                    params.append(careers_url)
                if updates:
                    updates.append("last_seen_at = ?")
                    params.append(now)
                    params.append(existing["id"])
                    conn.execute(
                        f"UPDATE companies SET {', '.join(updates)} WHERE id = ?",
                        params)
                else:
                    conn.execute(
                        "UPDATE companies SET last_seen_at = ? WHERE id = ?",
                        (now, existing["id"]))
                return existing["id"]
            else:
                conn.execute(
                    """INSERT INTO companies
                       (name, name_normalized, website, careers_url,
                        first_seen_at, last_seen_at, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (name, name_norm, website, careers_url, now, now, now))
                return conn.execute(
                    "SELECT id FROM companies WHERE name_normalized = ?",
                    (name_norm,)).fetchone()["id"]

    def update_company_metadata(self, company_id: int, *,
                                summary: str | None = None,
                                website: str | None = None) -> None:
        """Fill summary/website on a company row IF the existing values are NULL.

        Preserves manual edits. No-op if both fields are already set or the new
        values are empty.
        """
        if not summary and not website:
            return
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT summary, website FROM companies WHERE id = ?",
                (company_id,)).fetchone()
            if not existing:
                return
            updates = []
            params = []
            if summary and not existing["summary"]:
                updates.append("summary = ?")
                params.append(summary.strip())
            if website and not existing["website"]:
                updates.append("website = ?")
                params.append(website.strip())
            if updates:
                params.append(company_id)
                conn.execute(
                    f"UPDATE companies SET {', '.join(updates)} WHERE id = ?",
                    params)

    def set_company_status(self, company_id: int, status: str,
                           note: str | None = None) -> None:
        """Update the bound user's relationship status with a company and record it."""
        if status not in COMPANY_STATUSES:
            raise ValueError(f"Invalid company status: {status!r}. Valid: {COMPANY_STATUSES}")
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO user_companies (user_id, company_id, status, created_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, company_id) DO UPDATE SET status = excluded.status""",
                (self.user_id, company_id, status, now),
            )
            conn.execute(
                """INSERT INTO company_status_history
                   (user_id, company_id, status, note, changed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (self.user_id, company_id, status, note, now),
            )

    def _upsert_job_raw(self, job, conn, now: str, company_id: int | None = None,
                         monitored_company_id: int | None = None) -> str:
        """Insère ou met à jour la table jobs (données brutes uniquement).

        Uses canonical_url for dedup: if an existing row has the same
        canonical_url (but a different id — legacy rows from before URL
        normalization was introduced), the existing row's id is reused so
        ON CONFLICT fires correctly and no duplicate is created.

        Also checks (source, norm_company, norm_title) as a fallback — catches
        duplicates where the same job gets different URLs (LinkedIn issues new
        numeric IDs on every scrape; Indeed issues different jk per country).

        Returns the effective job id used for the upsert (may differ from
        job.id when dedup maps to an existing row).
        """
        effective_id = job.id
        canonical = getattr(job, "canonical_url", None)
        source = getattr(job, "source", None)
        norm_title = getattr(job, "norm_title", None)
        norm_company = getattr(job, "norm_company", None)

        if canonical and source:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE canonical_url = ? AND source = ?",
                (canonical, source),
            ).fetchone()
            if existing:
                effective_id = existing["id"]

        # Fallback: match on (source, norm_company, norm_title) when URLs differ
        if effective_id == job.id and source and norm_company and norm_title:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE source = ? AND norm_company = ? AND norm_title = ?",
                (source, norm_company, norm_title),
            ).fetchone()
            if existing:
                effective_id = existing["id"]

        conn.execute(
            """INSERT INTO jobs (
                   id, title, company, company_id, url, canonical_url, norm_title, norm_company,
                   source, location, base_location,
                   posted_date, description, first_seen, last_seen, monitored_company_id
               ) VALUES (
                   :id, :title, :company, :company_id, :url, :canonical_url, :norm_title, :norm_company,
                   :source, :location, :base_location,
                   :posted_date, :description, :now, :now, :monitored_company_id
               )
               ON CONFLICT(id) DO UPDATE SET
                   last_seen  = excluded.last_seen,
                   base_location = excluded.base_location,
                   norm_title = COALESCE(jobs.norm_title, excluded.norm_title),
                   norm_company = COALESCE(jobs.norm_company, excluded.norm_company),
                   canonical_url = COALESCE(jobs.canonical_url, excluded.canonical_url),
                   company_id = COALESCE(jobs.company_id, excluded.company_id),
                   monitored_company_id = COALESCE(jobs.monitored_company_id, excluded.monitored_company_id)""",
            {
                "id":            effective_id,
                "title":         job.title,
                "company":       getattr(job, "company", None),
                "company_id":    company_id,
                "url":           getattr(job, "url", None),
                "canonical_url": canonical,
                "norm_title":    norm_title,
                "norm_company":  norm_company,
                "source":        source,
                "location":      getattr(job, "location", None),
                "base_location": getattr(job, "base_location", None),
                "posted_date":   str(getattr(job, "posted_date", "") or ""),
                "description":   getattr(job, "description", None),
                "now":           now,
                "monitored_company_id": monitored_company_id,
            },
        )
        return effective_id

    def get_jobs_for_scoring(self, profile_id: str,
                             pre_filter: dict | None = None,
                             rescore: bool = False) -> list[dict]:
        """
        Returns jobs that need scoring for this profile.
        Applies optional SQL pre-filter before returning.
        Always excludes jobs with status='rejected'.
        Company-level fields joined from companies table (Phase 4).
        """
        clauses = []
        params: list = [self.user_id, profile_id, self.user_id]  # uc + profile_id + job_tracking

        _job_columns = (
            "j.id, j.title, j.company, j.company_id, j.url, j.source,"
            "j.location, j.base_location, j.posted_date, j.description,"
            "j.first_seen, j.last_seen, j.summary, j.work_mode, j.geo_zone,"
            "j.contract_type, j.language_required, j.extracted_at, j.extracted_by,"
            "j.country_code, j.monitored_company_id,"
            "j.canonical_url, j.norm_title, j.norm_company"
        )
        _company_fields = (
            "COALESCE(c.company_country, 'unknown') AS company_country,"
            "COALESCE(c.industry_sector, 'other') AS industry_sector,"
            "COALESCE(c.company_size, 'unknown') AS company_size"
        )
        if rescore:
            base = (f"SELECT {_job_columns}, {_company_fields} FROM jobs j"
                    " LEFT JOIN companies c ON j.company_id = c.id"
                    " LEFT JOIN user_companies uc ON uc.company_id = c.id AND uc.user_id = ?"
                    " LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?"
                    " LEFT JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?")
            clauses.append("(t.status IS NULL OR t.status NOT IN ('rejected', 'archived', 'expired', 'interviewing', 'offer', 'withdrawn'))")
        else:
            base = (f"SELECT {_job_columns}, {_company_fields} FROM jobs j"
                    " LEFT JOIN companies c ON j.company_id = c.id"
                    " LEFT JOIN user_companies uc ON uc.company_id = c.id AND uc.user_id = ?"
                    " LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?"
                    " LEFT JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?")
            clauses.append("(s.job_id IS NULL OR s.score IS NULL)")
            clauses.append("(t.status IS NULL OR t.status NOT IN ('rejected', 'archived', 'expired', 'interviewing', 'offer', 'withdrawn'))")
        clauses.append("(c.id IS NULL OR COALESCE(uc.status, 'prospect') != 'blacklisted')")

        if pre_filter:
            title_kws = pre_filter.get("title_contains", [])
            if title_kws:
                or_parts = " OR ".join("LOWER(j.title) LIKE ?" for _ in title_kws)
                clauses.append(f"({or_parts})")
                params += [f"%{kw.lower()}%" for kw in title_kws]

            excl_title = pre_filter.get("exclude_title_contains", [])
            if excl_title:
                not_parts = " AND ".join("LOWER(j.title) NOT LIKE ?" for _ in excl_title)
                clauses.append(f"({not_parts})")
                params += [f"%{kw.lower()}%" for kw in excl_title]

        days = self.get_freshness_days()
        clauses.append(
            f"(j.posted_date >= date('now', '-{days} days') OR j.posted_date IS NULL OR j.posted_date = '')"
        )
        where = " AND ".join(clauses)
        query = f"{base} WHERE {where}"

        with self._conn() as conn:
            self._require_profile(conn, profile_id)
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_jobs_for_extraction(self) -> list[dict]:
        """Returns jobs where extraction has not run (extracted_at IS NULL)."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT j.* FROM jobs j
                WHERE j.extracted_at IS NULL
                ORDER BY j.last_seen DESC
            """).fetchall()
        return [dict(r) for r in rows]

    def update_company_enrichment(self, company_id: int, fields: dict,
                                   enriched_by: str | None = None,
                                   _conn: sqlite3.Connection | None = None) -> bool:
        """Write company-level fields to the companies table.

        Only updates fields that are currently NULL — never overwrites.
        Returns True if at least one field was written.
        """
        def _do_update(conn):
            current = conn.execute(
                """SELECT company_country, industry_sector, company_size
                   FROM companies WHERE id = ?""",
                (company_id,)).fetchone()
            if current is None:
                return False

            updates = []
            params = []
            for col in ("company_country", "industry_sector", "company_size"):
                val = fields.get(col)
                current_val = current[col]
                # Only write non-trivial values when the column is currently
                # empty (NULL, '', or semantically empty like 'unknown'/'other').
                is_empty = not current_val or current_val in ("unknown", "other")
                if val and val not in ("unknown", "other") and is_empty:
                    updates.append(f"{col} = ?")
                    params.append(val)

            if not updates:
                return False

            updates.append("enriched_at = ?")
            params.append(_now())
            updates.append("enriched_by = ?")
            params.append(enriched_by or fields.get("extracted_by"))
            params.append(company_id)

            conn.execute(
                f"UPDATE companies SET {', '.join(updates)} WHERE id = ?",
                params)
            return True

        if _conn is not None:
            return _do_update(_conn)
        with self._conn() as conn:
            return _do_update(conn)

    def update_job_extraction(self, job_id: str, fields: dict,
                               extracted_at: str | None = None) -> None:
        """Writes job-level extraction fields to the jobs table.

        Company-level fields (company_country, industry_sector, company_size)
        are written to the companies table via update_company_enrichment.

        Args:
            job_id: the job to update.
            fields: dict with keys company_country, industry_sector,
                    language_required, work_mode, geo_zone, company_size,
                    contract_type, summary, extracted_by.
            extracted_at: ISO timestamp. If None, uses current UTC time.
        """
        ts = extracted_at or _now()
        with self._conn() as conn:
            conn.execute(
                """UPDATE jobs SET
                       language_required = ?,
                       work_mode = ?,
                       geo_zone = ?,
                       country_code = ?,
                       contract_type = ?,
                       summary = ?,
                       extracted_at = ?,
                       extracted_by = ?
                 WHERE id = ?""",
                (
                    fields.get("language_required", "unknown"),
                    fields.get("work_mode", "unknown"),
                    fields.get("geo_zone", "unknown"),
                    fields.get("country_code"),
                    fields.get("contract_type", "unknown"),
                    fields.get("summary", ""),
                    ts,
                    fields.get("extracted_by"),
                    job_id,
                ),
            )
            # Write company-level fields to companies table
            row = conn.execute(
                "SELECT company_id FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row and row["company_id"]:
                self.update_company_enrichment(
                    row["company_id"], fields,
                    enriched_by=fields.get("extracted_by"), _conn=conn)

    def _update_job_extraction_fields(self, job_id: str, score_result: dict, conn, now: str,
                                       company_id: int | None = None) -> None:
        """Write job-level structured fields to jobs table, company fields to companies."""
        extracted_by = score_result.get("scored_by")
        conn.execute(
            """UPDATE jobs SET
                   summary = ?, work_mode = ?, geo_zone = ?,
                   country_code = ?, contract_type = ?, language_required = ?,
                   extracted_at = ?, extracted_by = ?
             WHERE id = ?""",
            (
                score_result.get("summary"),
                score_result.get("work_mode", "unknown"),
                score_result.get("geo_zone", "unknown"),
                score_result.get("country_code"),
                score_result.get("contract_type", "unknown"),
                score_result.get("language_required", "unknown"),
                now,
                extracted_by,
                job_id,
            ),
        )
        # Write company-level fields to companies table (reuse conn)
        if not company_id:
            row = conn.execute(
                "SELECT company_id FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row:
                company_id = row["company_id"]
        if company_id:
            self.update_company_enrichment(
                company_id, score_result,
                enriched_by=extracted_by, _conn=conn)

    def save_scored(self, job, score_result: dict, profile_id: str,
                    company_id: int | None = None,
                    monitored_company_id: int | None = None) -> None:
        """
        Sauvegarde un job avec son score pour un profil donné.
        Après Phase 1e.4: only writes profile-dependent columns to job_scores.
        """
        now = _now()
        with self._conn() as conn:
            self._require_profile(conn, profile_id)
            effective_id = self._upsert_job_raw(job, conn, now, company_id=company_id,
                                                monitored_company_id=monitored_company_id)
            conn.execute(
                """INSERT INTO job_scores (
                       job_id, profile_id,
                       score, reason, scored_by, scored_at, country_code, comp_flag
                   ) VALUES (
                       ?, ?, ?, ?, ?, ?, ?, ?
                   )
                   ON CONFLICT(job_id, profile_id) DO UPDATE SET
                       score        = excluded.score,
                       reason       = excluded.reason,
                       scored_by    = excluded.scored_by,
                       scored_at    = excluded.scored_at,
                       country_code = excluded.country_code,
                       comp_flag    = excluded.comp_flag""",
                (effective_id, profile_id, score_result.get("score"),
                 score_result.get("reason"), score_result.get("scored_by", "unknown"),
                 now, score_result.get("country_code"),
                 score_result.get("comp_flag", 0)),
            )
            # Also write structured fields to jobs table (Phase 1e)
            self._update_job_extraction_fields(effective_id, score_result, conn, now,
                                               company_id=company_id)

    def save_unscored(self, job, company_id: int | None = None,
                       monitored_company_id: int | None = None) -> None:
        """
        Enregistre un job sans score (échec scorer persistant).
        Le job sera retenté au prochain run (absent de job_scores).
        """
        now = _now()
        with self._conn() as conn:
            self._upsert_job_raw(job, conn, now, company_id=company_id,
                                monitored_company_id=monitored_company_id)

    def get_engaged_job_keys(self) -> list[dict]:
        """Return (title, company, id) for jobs with status in {applied, ready, queued, archived}.

        Used by scrape.py dedupe_against_db to avoid re-scraping jobs the user
        has already engaged with.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT j.id, j.title, j.company
                   FROM jobs j
                   JOIN job_tracking t ON j.id = t.job_id
                   WHERE t.status IN ('applied', 'ready', 'queued', 'archived',
                                      'interviewing', 'offer', 'withdrawn')
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def touch_many(self, job_ids: list[str]) -> None:
        """Met à jour last_seen pour signaler que les jobs sont toujours actifs."""
        if not job_ids:
            return
        now = _now()
        with self._conn() as conn:
            conn.executemany(
                "UPDATE jobs SET last_seen = ? WHERE id = ?",
                [(now, jid) for jid in job_ids],
            )

    # ------------------------------------------------------------------
    # Tracker — mise à jour de statut
    # ------------------------------------------------------------------

    VALID_STATUSES = {
        "new", "queued", "ready", "applied", "interviewing", "offer",
        "rejected", "withdrawn", "archived", "expired",
    }

    def set_status(self, job_id: str, status: str, notes: str = None) -> None:
        if status not in self.VALID_STATUSES:
            raise ValueError(f"Statut invalide : {status!r}. Valides : {self.VALID_STATUSES}")
        now = _now()
        with self._conn() as conn:
            # Read current status to avoid no-op history entries
            old = conn.execute(
                "SELECT status FROM job_tracking WHERE job_id = ? AND user_id = ?",
                (job_id, self.user_id),
            ).fetchone()
            old_status = old["status"] if old else None

            if notes is not None:
                conn.execute(
                    """INSERT INTO job_tracking (user_id, job_id, status, notes, changed_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, job_id) DO UPDATE SET
                           status = excluded.status,
                           notes = excluded.notes,
                           changed_at = excluded.changed_at""",
                    (self.user_id, job_id, status, notes, now),
                )
            else:
                conn.execute(
                    """INSERT INTO job_tracking (user_id, job_id, status, changed_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(user_id, job_id) DO UPDATE SET
                           status = excluded.status,
                           changed_at = excluded.changed_at""",
                    (self.user_id, job_id, status, now),
                )
            # Only log to history if status actually changed (prevents runaway duplicates)
            if old_status != status:
                conn.execute(
                    "INSERT INTO status_history (user_id, job_id, status, changed_at) VALUES (?, ?, ?, ?)",
                    (self.user_id, job_id, status, now),
                )
                self._auto_log_status_interaction(job_id, status, _conn=conn)

    # ------------------------------------------------------------------
    # Application lifecycle (spec 030) — single entry point + derived view
    # ------------------------------------------------------------------

    _LIFECYCLE_EVENT_TYPES = frozenset({
        "application_submitted", "interview_invited", "interview",
        "decision_received", "withdrawn",
    })

    _LIFECYCLE_DIRECTION = {
        "application_submitted": "outbound",
        "interview_invited": "inbound",
        "interview": "none",
        "decision_received": "inbound",
        "withdrawn": "none",
    }

    # Current derived status -> allowed next statuses (FR-001).
    _LIFECYCLE_TRANSITIONS = {
        None: {"applied"},
        "new": {"applied"},
        "queued": {"applied"},
        "ready": {"applied"},
        "applied": {"interviewing", "offer", "rejected", "withdrawn"},
        "interviewing": {"interviewing", "offer", "rejected", "withdrawn"},
        "offer": {"withdrawn", "rejected"},
        "rejected": set(),
        "withdrawn": set(),
        "archived": set(),
        "expired": set(),
    }

    # Event -> status it implies when the write changes state (FR-012 consistency).
    _STATUS_BY_EVENT = {
        "application_submitted": "applied",
        "interview_invited": "interviewing",
        "interview": "interviewing",
        "withdrawn": "withdrawn",
    }

    @staticmethod
    def _implied_status(event: str, outcome: str | None) -> str:
        if event == "decision_received":
            return "offer" if outcome == "positive" else "rejected"
        return JobStorage._STATUS_BY_EVENT[event]

    def _resolve_job_company_id(self, job_id: str) -> int:
        """Return a job's company_id, resolving/creating one from jobs.company if absent (FR-004)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT company_id, company FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown job id: {job_id!r}")
        if row["company_id"] is not None:
            return row["company_id"]
        if not row["company"]:
            raise ValueError(f"Job {job_id!r} has no company_id and no company name")
        cid = self.upsert_company(name=row["company"])
        with self._conn() as conn:
            conn.execute("UPDATE jobs SET company_id = ? WHERE id = ?", (cid, job_id))
        return cid

    def _validate_lifecycle_date(self, conn, job_id: str, event: str,
                                 occurred_at: str, exclude_id=None) -> None:
        """Enforce FR-027 on a pending event write (read from `conn`)."""
        d = _date_prefix(occurred_at)
        if not d:
            raise ValueError("occurred_at is required")
        today = date.today().isoformat()

        if event != "interview" and d > today:
            raise ValueError(f"{event} date {d} is in the future (today {today})")

        excl = " AND id != ?" if exclude_id is not None else ""
        params: list = [job_id, self.user_id]
        if exclude_id is not None:
            params.append(exclude_id)

        app_row = conn.execute(
            f"SELECT MIN(occurred_at) AS d FROM interactions "
            f"WHERE job_id = ? AND user_id = ? AND type = 'application_submitted'{excl}",
            params,
        ).fetchone()
        app_date = _date_prefix(app_row["d"]) if app_row and app_row["d"] else ""
        if event != "application_submitted" and app_date and d < app_date:
            raise ValueError(f"Event {event} date {d} is before the application date {app_date}")

        if event == "interview":
            term_row = conn.execute(
                f"SELECT MIN(occurred_at) AS d FROM interactions "
                f"WHERE job_id = ? AND user_id = ? AND type IN ('decision_received', 'withdrawn'){excl}",
                params,
            ).fetchone()
            term_date = _date_prefix(term_row["d"]) if term_row and term_row["d"] else ""
            if term_date and d > term_date:
                raise ValueError(f"Interview date {d} is after the terminal event date {term_date}")

    def record_lifecycle_event(self, job_id, *, event, occurred_at, new_status,
                               outcome=None, notes=None, source="manual",
                               source_ref=None) -> None:
        """Record one lifecycle event and update the job's tracking status atomically.

        This is the single write path for the application lifecycle (FR-006). It is
        a plain ``storage.py`` API with **no Streamlit dependency**, callable by the
        UI, CLI scripts, and a future email agent alike (FR-030).

        Contract for agents (US8):
          * ``event`` — one of ``application_submitted`` | ``interview_invited`` |
            ``interview`` | ``decision_received`` | ``withdrawn``.
          * ``occurred_at`` — the event date (ISO ``YYYY-MM-DD`` or full timestamp),
            always explicit; the call time is never used as the event date (FR-026).
          * ``new_status`` — the resulting ``job_tracking.status`` (``applied`` |
            ``interviewing`` | ``offer`` | ``rejected`` | ``withdrawn``). A value equal
            to the current status records a back-dated/out-of-order event without
            flipping state (US7); otherwise the transition must be allowed by FR-001
            and implied by the event.
          * ``outcome`` — required ``positive``/``negative`` for ``decision_received``.
          * ``notes`` — un-prefixed free text (employer's reason on a rejection, my
            reason on a withdrawal). The ``Employer:``/``Me:`` prefix is added only at
            display/export (FR-002/018).
          * ``source`` — ``manual`` | ``migration`` | ``agent:<name>``.
          * ``source_ref`` — optional external id (e.g. email Message-ID) for idempotency.

        Guarantees: FR-001 transition guard, FR-027 date validation, FR-031 idempotency
        (non-manual writes with a ``source_ref`` are no-ops when repeated), FR-004 company
        resolution. Raises ``ValueError`` on any refused write, naming the conflict.
        """
        if event not in self._LIFECYCLE_EVENT_TYPES:
            raise ValueError(
                f"Invalid lifecycle event: {event!r}. Valid: {sorted(self._LIFECYCLE_EVENT_TYPES)}"
            )
        if new_status not in self.VALID_STATUSES:
            raise ValueError(f"Invalid status: {new_status!r}. Valid: {sorted(self.VALID_STATUSES)}")
        if event == "decision_received":
            if outcome not in ("positive", "negative"):
                raise ValueError(
                    f"decision_received requires outcome 'positive' or 'negative', got {outcome!r}"
                )
        else:
            outcome = None

        direction = self._LIFECYCLE_DIRECTION[event]
        now = _now()

        # FR-031 idempotency: a non-manual write carrying the same source_ref is a no-op.
        if source != "manual" and source_ref is not None:
            with self._conn() as conn:
                dup = conn.execute(
                    "SELECT id FROM interactions WHERE user_id = ? AND job_id = ? AND type = ? AND source_ref = ?",
                    (self.user_id, job_id, event, source_ref),
                ).fetchone()
            if dup:
                return

        # FR-004: resolve/create company before writing (defensive — D1 shows none live).
        company_id = self._resolve_job_company_id(job_id)

        with self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM job_tracking WHERE job_id = ? AND user_id = ?",
                (job_id, self.user_id),
            ).fetchone()
            current_status = row["status"] if row else None

            if new_status != current_status:
                allowed = self._LIFECYCLE_TRANSITIONS.get(current_status, set())
                if new_status not in allowed:
                    raise ValueError(
                        f"Disallowed transition: {current_status or 'none'} -> {new_status} (event {event!r})"
                    )
                implied = self._implied_status(event, outcome)
                if new_status != implied:
                    raise ValueError(
                        f"Event {event!r} implies status {implied!r}, not {new_status!r}"
                    )

            self._validate_lifecycle_date(conn, job_id, event, occurred_at)

            conn.execute(
                """INSERT INTO interactions
                   (user_id, company_id, job_id, type, direction, outcome,
                    occurred_at, created_at, notes, source, source_ref)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.user_id, company_id, job_id, event, direction, outcome,
                 occurred_at, now, notes, source, source_ref),
            )

            if current_status != new_status:
                conn.execute(
                    "INSERT INTO status_history (user_id, job_id, status, changed_at) VALUES (?, ?, ?, ?)",
                    (self.user_id, job_id, new_status, now),
                )
            conn.execute(
                """INSERT INTO job_tracking (user_id, job_id, status, changed_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, job_id) DO UPDATE SET
                       status = excluded.status, changed_at = excluded.changed_at""",
                (self.user_id, job_id, new_status, now),
            )

    def get_lifecycle_events(self, job_id: str) -> list[dict]:
        """Return the job's lifecycle events in chronological order (FR-014)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, type, direction, outcome, occurred_at, created_at,
                          notes, source, source_ref
                   FROM interactions
                   WHERE job_id = ? AND user_id = ?
                     AND type IN ('application_submitted','interview_invited',
                                  'interview','decision_received','withdrawn')
                   ORDER BY occurred_at ASC, id ASC""",
                (job_id, self.user_id),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_lifecycle_summary(self, job_id: str) -> dict:
        """Compute the derived lifecycle view (FR-003) from events — never stored."""
        events = self.get_lifecycle_events(job_id)
        today = date.today().isoformat()

        application_date = None
        decision = None
        withdrawn = None
        interviews = []
        invites = []

        for e in events:
            t = e["type"]
            if t == "application_submitted" and application_date is None:
                application_date = _date_prefix(e["occurred_at"])
            elif t == "interview_invited":
                invites.append(e)
            elif t == "interview":
                interviews.append(e)
            elif t == "decision_received":
                decision = e
            elif t == "withdrawn":
                withdrawn = e

        interviews_held = sum(1 for i in interviews if _date_prefix(i["occurred_at"]) <= today)
        next_interview = next(
            (i for i in interviews if _date_prefix(i["occurred_at"]) > today), None
        )
        offer_received = any(
            e["type"] == "decision_received" and e["outcome"] == "positive" for e in events
        )

        stage = ""
        if withdrawn is not None:
            stage = "Withdrawn"
        elif decision is not None:
            stage = "Offer" if decision.get("outcome") == "positive" else "Rejected"
        elif interviews or invites:
            stage = "Interviewing"
        elif application_date:
            stage = "Applied"

        label = ""
        reason = ""
        if withdrawn is not None:
            wd_notes = withdrawn.get("notes") or ""
            if offer_received:
                label = "Withdrawn — offer declined"
            elif interviews_held == 0:
                label = "Withdrawn — no interview"
            else:
                label = f"Withdrawn — after {interviews_held} interview" + ("s" if interviews_held != 1 else "")
            reason = f"Me: {wd_notes}" if wd_notes else ""
        elif decision is not None:
            if decision.get("outcome") == "positive":
                label = "Offer"
            elif offer_received:
                label = "Rejected — offer withdrawn by employer"
            elif interviews_held == 0:
                label = "Rejected — no interview"
            else:
                label = f"Rejected — after {interviews_held} interview" + ("s" if interviews_held != 1 else "")
            d_notes = decision.get("notes") or ""
            if decision.get("outcome") == "negative" and d_notes:
                reason = f"Employer: {d_notes}"
        elif interviews:
            label = f"Interviewing · round {len(interviews)}"
            if next_interview is not None:
                label += f" · next {_fmt_dm(next_interview['occurred_at'])}"
        elif invites:
            label = "Interviewing"
        elif application_date:
            label = "Applied"

        if decision is None and withdrawn is None:
            orp_result = "pending"
        elif withdrawn is not None:
            orp_result = "negative (withdrawn by me)"
        elif decision.get("outcome") == "positive":
            orp_result = "hired"
        else:
            orp_result = "negative"

        return {
            "application_date": application_date,
            "stage": stage,
            "label": label,
            "reason": reason,
            "interviews_held": interviews_held,
            "round": len(interviews) if interviews else None,
            "next_interview": _date_prefix(next_interview["occurred_at"]) if next_interview else None,
            "decision_outcome": decision.get("outcome") if decision else None,
            "decision_date": _date_prefix(decision["occurred_at"]) if decision else None,
            "withdrawn_date": _date_prefix(withdrawn["occurred_at"]) if withdrawn else None,
            "last_event_date": _date_prefix(events[-1]["occurred_at"]) if events else None,
            "orp_result": orp_result,
        }

    def update_lifecycle_event(self, event_id, *, occurred_at=None, notes=None,
                               source="manual") -> None:
        """Edit a lifecycle event's date and/or notes (manual timeline edit — FR-015).

        FR-031: a non-manual caller may not edit a manual event; a manual edit of an
        agent-created event flips its ``source`` to ``manual``.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT job_id, type, source FROM interactions WHERE id = ? AND user_id = ?",
                (event_id, self.user_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown interaction id: {event_id}")
            if row["source"] == "manual" and source != "manual":
                logger.warning(
                    f"[Storage] Refused non-manual edit of manual interaction {event_id}"
                )
                raise ValueError(f"Cannot modify manual event {event_id} from source {source!r}")

            if occurred_at is not None:
                self._validate_lifecycle_date(
                    conn, row["job_id"], row["type"], occurred_at, exclude_id=event_id
                )

            new_source = "manual" if source == "manual" else row["source"]
            sets = ["source = ?"]
            params: list = [new_source]
            if occurred_at is not None:
                sets.append("occurred_at = ?")
                params.append(occurred_at)
            if notes is not None:
                sets.append("notes = ?")
                params.append(notes)
            params.append(event_id)
            conn.execute(
                f"UPDATE interactions SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
                params + [self.user_id],
            )

    def delete_lifecycle_event(self, event_id, *, source="manual") -> None:
        """Delete a lifecycle event from the timeline (FR-015). FR-031 applies."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT source FROM interactions WHERE id = ? AND user_id = ?",
                (event_id, self.user_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown interaction id: {event_id}")
            if row["source"] == "manual" and source != "manual":
                logger.warning(
                    f"[Storage] Refused non-manual delete of manual interaction {event_id}"
                )
                raise ValueError(f"Cannot delete manual event {event_id} from source {source!r}")
            conn.execute(
                "DELETE FROM interactions WHERE id = ? AND user_id = ?",
                (event_id, self.user_id),
            )

    def reconcile_lifecycle_status(self, job_id: str) -> str | None:
        """Reconcile ``job_tracking.status`` with the derived stage after an edit/delete.

        After a timeline edit or delete the tracking status can drift from what the
        events imply (e.g. deleting the only interview should revert ``interviewing``
        to ``applied``). This recomputes the derived stage and updates the status when
        it differs (FR-015 — the caller prompts before a state-affecting change).

        A job left with no application event is left untouched (we can't guess the
        pre-application state). Returns the new status, or ``None`` when unchanged.
        """
        summary = self.get_lifecycle_summary(job_id)
        stage = summary.get("stage") or ""
        if not stage:
            return None
        new_status = stage.lower()
        now = _now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM job_tracking WHERE job_id = ? AND user_id = ?",
                (job_id, self.user_id),
            ).fetchone()
            if row is None:
                return None
            if row["status"] == new_status:
                return new_status
            conn.execute(
                "INSERT INTO status_history (user_id, job_id, status, changed_at) VALUES (?, ?, ?, ?)",
                (self.user_id, job_id, new_status, now),
            )
            conn.execute(
                "UPDATE job_tracking SET status = ?, changed_at = ? WHERE job_id = ? AND user_id = ?",
                (new_status, now, job_id, self.user_id),
            )
        return new_status

    def _get_job_basics(self, job_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, title, company, location, url FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def _application_row(self, job: dict, app_date: str, summary: dict) -> dict:
        company = job.get("company") or ""
        location = job.get("location") or ""
        company_location = f"{company} — {location}" if location else company
        interviews_held = summary["interviews_held"]
        return {
            "Application date": app_date,
            "Company / location": company_location,
            "Title": job.get("title") or "",
            "Current stage": summary["label"],
            "Interviews held": interviews_held,
            "Last event date": summary["last_event_date"] or "",
            "Decision date": summary["decision_date"] or "",
            "Reason": summary["reason"],
            "URL": job.get("url") or "",
            "ID": job.get("id"),
            "Interview held": "yes" if interviews_held > 0 else "no",
            "ORP result": summary["orp_result"],
        }

    def build_application_rows(self, date_from: str, date_to: str, *,
                               current_stage: str | None = None) -> list[dict]:
        """Applications whose application date falls in [date_from, date_to] (FR-016).

        Returns one row per application with derived current stage, reason, and the
        ORP-helper columns (FR-018/022). Optional ``current_stage`` filters on the
        coarse derived stage (Applied / Interviewing / Offer / Rejected / Withdrawn).
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT i.job_id, MIN(i.occurred_at) AS app_date
                   FROM interactions i
                   WHERE i.user_id = ?
                     AND i.type = 'application_submitted'
                     AND date(i.occurred_at) BETWEEN ? AND ?
                   GROUP BY i.job_id
                   ORDER BY app_date ASC""",
                (self.user_id, date_from, date_to),
            ).fetchall()

        out = []
        for r in rows:
            job_id = r["job_id"]
            summary = self.get_lifecycle_summary(job_id)
            if current_stage and summary["stage"] != current_stage:
                continue
            job = self._get_job_basics(job_id)
            if job is None:
                continue
            out.append(self._application_row(job, _date_prefix(r["app_date"]), summary))
        return out

    def build_activity_rows(self, date_from: str, date_to: str) -> list[dict]:
        """Every lifecycle event dated in [date_from, date_to] (FR-020)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT i.id, i.job_id, i.type, i.outcome, i.occurred_at, i.notes,
                          j.company, j.title, j.url
                   FROM interactions i
                   JOIN jobs j ON i.job_id = j.id
                   WHERE i.user_id = ?
                     AND i.type IN ('application_submitted','interview_invited',
                                    'interview','decision_received','withdrawn')
                     AND date(i.occurred_at) BETWEEN ? AND ?
                   ORDER BY i.occurred_at ASC, i.id ASC""",
                (self.user_id, date_from, date_to),
            ).fetchall()

        # Precompute per-event labels (round + as-of decision/withdrawal labels).
        label_by_id: dict[int, str] = {}
        for job_id in {r["job_id"] for r in rows}:
            events = self.get_lifecycle_events(job_id)
            for i, e in enumerate(events):
                label_by_id[e["id"]] = _lifecycle_event_label(events, i)

        out = []
        for r in rows:
            summary = self.get_lifecycle_summary(r["job_id"])
            reason = ""
            if r["type"] == "decision_received" and r["outcome"] == "negative" and r["notes"]:
                reason = f"Employer: {r['notes']}"
            elif r["type"] == "withdrawn" and r["notes"]:
                reason = f"Me: {r['notes']}"
            out.append({
                "Event date": _date_prefix(r["occurred_at"]),
                "Event": label_by_id.get(r["id"], ""),
                "Company": r["company"] or "",
                "Title": r["title"] or "",
                "Application date": summary["application_date"] or "",
                "Current stage": summary["label"],
                "Reason": reason,
                "URL": r["url"] or "",
                "ID": r["job_id"],
            })
        return out

    def application_date_missing(self) -> list[dict]:
        """Jobs in a lifecycle status with no application event (FR-021)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT j.id, j.company, j.title, t.status
                   FROM jobs j
                   JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                   WHERE t.status IN ('applied','interviewing','offer','rejected','withdrawn')
                     AND NOT EXISTS (
                         SELECT 1 FROM interactions i
                         WHERE i.job_id = j.id AND i.user_id = t.user_id
                           AND i.type = 'application_submitted')
                   ORDER BY t.status, j.company""",
                (self.user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Requêtes pour digest / tracker
    # ------------------------------------------------------------------

    def get_digest(self, profile_id: str, min_score: int = 5, status: str = "new") -> list[dict]:
        with self._conn() as conn:
            self._require_profile(conn, profile_id)
            query = """
                SELECT j.id, j.company_id, j.title, j.company, j.url, j.source, j.location,
                       j.base_location, j.posted_date, j.description,
                       j.first_seen, j.last_seen, j.extracted_at, j.extracted_by,
                       COALESCE(j.summary, '') AS summary,
                       COALESCE(j.work_mode, 'unknown') AS work_mode,
                       COALESCE(j.geo_zone, 'unknown') AS geo_zone,
                       COALESCE(c.company_size, 'unknown') AS company_size,
                       COALESCE(j.contract_type, 'unknown') AS contract_type,
                       COALESCE(c.company_country, 'unknown') AS company_country,
                       COALESCE(c.industry_sector, 'other') AS industry_sector,
                       COALESCE(j.language_required, 'unknown') AS language_required,
                       s.score, s.reason, s.scored_by, s.scored_at,
                       s.country_code AS country_code,
                       s.comp_flag AS comp_flag,
                       COALESCE(t.status, 'new') AS status, t.notes,
                       t.changed_at AS status_changed_at
                FROM jobs j
                JOIN job_scores s ON j.id = s.job_id
                LEFT JOIN companies c ON j.company_id = c.id
                LEFT JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                WHERE s.profile_id = ? AND s.score >= ?
            """
            params: list = [self.user_id, profile_id, min_score]
            if status:
                query += " AND COALESCE(t.status, 'new') = ?"
                params.append(status)
            query += " ORDER BY s.score DESC, j.last_seen DESC"
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self, profile_id: str | None = None) -> dict:
        """Quick stats for logging/display.

        Pass ``profile_id`` to restrict to one profile; ``None`` aggregates across
        the bound user's profiles (``job_scores`` is user space — spec 041).
        """
        with self._conn() as conn:
            total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            if profile_id is None:
                # scored/hot/solid count only the bound user's profiles: job_scores
                # is keyed by profile_id, and search_profiles maps profile → user.
                scored = conn.execute(
                    """SELECT COUNT(DISTINCT s.job_id)
                       FROM job_scores s
                       JOIN search_profiles p ON p.id = s.profile_id AND p.user_id = ?""",
                    (self.user_id,),
                ).fetchone()[0]
                hot = conn.execute(
                    """SELECT COUNT(DISTINCT s.job_id)
                       FROM job_scores s
                       JOIN search_profiles p ON p.id = s.profile_id AND p.user_id = ?
                       WHERE s.score >= 9""",
                    (self.user_id,),
                ).fetchone()[0]
                solid = conn.execute(
                    """SELECT COUNT(DISTINCT s.job_id)
                       FROM job_scores s
                       JOIN search_profiles p ON p.id = s.profile_id AND p.user_id = ?
                       WHERE s.score BETWEEN 7 AND 8""",
                    (self.user_id,),
                ).fetchone()[0]
                by_status = dict(
                    conn.execute(
                        """SELECT COALESCE(t.status, 'new'), COUNT(*)
                           FROM jobs j
                           LEFT JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                           GROUP BY COALESCE(t.status, 'new')""",
                        (self.user_id,),
                    ).fetchall()
                )
            else:
                self._require_profile(conn, profile_id)
                scored = conn.execute(
                    "SELECT COUNT(*) FROM job_scores WHERE profile_id = ?", (profile_id,)
                ).fetchone()[0]
                hot = conn.execute(
                    "SELECT COUNT(*) FROM job_scores WHERE profile_id = ? AND score >= 9",
                    (profile_id,),
                ).fetchone()[0]
                solid = conn.execute(
                    "SELECT COUNT(*) FROM job_scores WHERE profile_id = ? AND score BETWEEN 7 AND 8",
                    (profile_id,),
                ).fetchone()[0]
                by_status = dict(
                    conn.execute(
                        """SELECT COALESCE(t.status, 'new'), COUNT(*)
                           FROM job_scores s
                           LEFT JOIN job_tracking t ON s.job_id = t.job_id AND t.user_id = ?
                           WHERE s.profile_id = ? GROUP BY COALESCE(t.status, 'new')""",
                        (self.user_id, profile_id),
                    ).fetchall()
                )
        return {
            "total":     total_jobs,
            "scored":    scored,
            "hot":       hot,
            "solid":     solid,
            "by_status": by_status,
        }

    def count_blacklisted_jobs(self) -> int:
        """Count jobs linked to blacklisted companies — for pre-filter reporting."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COUNT(*) FROM jobs j
                   JOIN companies c ON j.company_id = c.id
                   LEFT JOIN user_companies uc ON uc.company_id = c.id AND uc.user_id = ?
                   WHERE COALESCE(uc.status, 'prospect') = 'blacklisted'""",
                (self.user_id,),
            ).fetchone()
            return row[0] if row else 0

    # ------------------------------------------------------------------
    # Application content (job_applications table, profile-independent)
    # ------------------------------------------------------------------

    def get_application(self, job_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM job_applications WHERE job_id = ? AND user_id = ?",
                (job_id, self.user_id),
            ).fetchone()
            return dict(row) if row else None

    def get_all_applications(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM job_applications WHERE user_id = ?",
                (self.user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_application(self, job_id: str, analysis: str, cover_letter: str) -> None:
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO job_applications (user_id, job_id, analysis, cover_letter, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, job_id) DO UPDATE SET
                       analysis = excluded.analysis,
                       cover_letter = excluded.cover_letter""",
                (self.user_id, job_id, analysis, cover_letter, now),
            )
            conn.execute(
                """INSERT INTO job_tracking (user_id, job_id, status) VALUES (?, ?, 'ready')
                   ON CONFLICT(user_id, job_id) DO UPDATE SET status = 'ready'""",
                (self.user_id, job_id),
            )

    def save_prepared_application(self, job_id: str, profile_id: str,
                                  cover_letter: str,
                                  cv_bullets_selected: dict | None,
                                  company_research: dict | None,
                                  screening_answers: dict | None,
                                  language: str,
                                  prepared_by: str) -> None:
        """Upsert a full application package from prepare.py."""
        now = _now()
        with self._conn() as conn:
            self._require_profile(conn, profile_id)
            conn.execute(
                """INSERT INTO job_applications (
                       user_id, job_id, profile_id, cover_letter,
                       cv_bullets_selected, company_research, screening_answers,
                       language, prepared_by, prepared_at, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, job_id) DO UPDATE SET
                       profile_id         = excluded.profile_id,
                       cover_letter        = excluded.cover_letter,
                       cv_bullets_selected  = excluded.cv_bullets_selected,
                       company_research     = excluded.company_research,
                       screening_answers    = excluded.screening_answers,
                       language             = excluded.language,
                       prepared_by          = excluded.prepared_by,
                       prepared_at          = excluded.prepared_at""",
                (self.user_id, job_id, profile_id, cover_letter,
                 json.dumps(cv_bullets_selected, ensure_ascii=False) if cv_bullets_selected else None,
                 json.dumps(company_research, ensure_ascii=False) if company_research else None,
                 json.dumps(screening_answers, ensure_ascii=False) if screening_answers else None,
                 language, prepared_by, now, now),
            )
            conn.execute(
                """INSERT INTO job_tracking (user_id, job_id, status) VALUES (?, ?, 'ready')
                   ON CONFLICT(user_id, job_id) DO UPDATE SET status = 'ready'""",
                (self.user_id, job_id),
            )

    def get_job_for_prepare(self, job_id: str) -> dict | None:
        """Fetch a single job with all extracted fields for preparation."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT j.id, j.title, j.company, j.company_id, j.url, j.source,
                          j.location, j.base_location, j.posted_date, j.description,
                          j.first_seen, j.last_seen, j.extracted_at, j.extracted_by,
                          j.monitored_company_id,
                          j.canonical_url, j.norm_title, j.norm_company,
                          COALESCE(j.summary, '') AS summary,
                          COALESCE(j.work_mode, 'unknown') AS work_mode,
                          COALESCE(j.geo_zone, 'unknown') AS geo_zone,
                          COALESCE(c.company_size, 'unknown') AS company_size,
                          COALESCE(j.contract_type, 'unknown') AS contract_type,
                          COALESCE(c.company_country, 'unknown') AS company_country,
                          COALESCE(c.industry_sector, 'other') AS industry_sector,
                          COALESCE(j.language_required, 'unknown') AS language_required,
                          j.country_code,
                          COALESCE(t.status, 'new') AS status
                   FROM jobs j
                   LEFT JOIN companies c ON j.company_id = c.id
                   LEFT JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                   WHERE j.id = ?""",
                (self.user_id, job_id),
            ).fetchone()
            return dict(row) if row else None

    def get_queued_jobs_unprepared(self) -> list[dict]:
        """Jobs with status='queued' that have no prepared application yet."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT j.* FROM jobs j
                   JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                   LEFT JOIN job_applications a ON j.id = a.job_id AND a.user_id = t.user_id
                   WHERE t.status = 'queued'
                     AND (a.job_id IS NULL OR a.prepared_at IS NULL)
                   ORDER BY j.last_seen DESC""",
                (self.user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_queued_jobs(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT j.id, j.title, j.company, j.url, j.description
                   FROM jobs j
                   JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                   WHERE t.status = 'queued'
                   ORDER BY j.last_seen DESC""",
                (self.user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Monitored Companies — queries and mutations
    # ------------------------------------------------------------------

    def set_company_monitored(self, company_id: int, monitored: bool) -> None:
        """Toggle the bound user's watch request on a company (user_companies).

        The platform capability lives on ``companies.monitoring_status`` and is
        untouched here (FR-005)."""
        now = _now()
        with self._conn() as conn:
            if monitored:
                conn.execute(
                    """INSERT INTO user_companies
                       (user_id, company_id, status, monitored, watch_requested_at, created_at)
                       VALUES (?, ?, 'prospect', 1, ?, ?)
                       ON CONFLICT(user_id, company_id) DO UPDATE SET
                           monitored = 1,
                           watch_requested_at = COALESCE(user_companies.watch_requested_at, excluded.watch_requested_at)""",
                    (self.user_id, company_id, now, now),
                )
            else:
                conn.execute(
                    """INSERT INTO user_companies
                       (user_id, company_id, status, monitored, created_at)
                       VALUES (?, ?, 'prospect', 0, ?)
                       ON CONFLICT(user_id, company_id) DO UPDATE SET monitored = 0""",
                    (self.user_id, company_id, now),
                )

    def get_monitored_companies(self) -> list[dict]:
        """Return companies watched by any user and ready to scrape (union, FR-010)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT c.id, c.name, c.ats_provider, c.ats_identifier, c.scraper_id,
                          c.careers_url, c.detected_at, c.monitoring_status
                   FROM companies c
                   JOIN user_companies uc ON uc.company_id = c.id
                   WHERE uc.monitored = 1
                     AND c.monitoring_status = 'watch_ready'
                   ORDER BY c.name"""
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_monitorable_companies(self) -> list[dict]:
        """Return all companies that have a scraping source (ATS or dedicated scraper),
        regardless of monitored state.  Used by Settings UI so paused companies
        remain visible — fixes the pause/resume disappearing-row bug.

        ``monitored`` comes from the bound user's ``user_companies`` row
        (Constitution X), not the legacy ``companies.monitored`` column."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT c.id, c.name, c.ats_provider, c.ats_identifier, c.scraper_id,
                          c.careers_url, COALESCE(uc.monitored, 0) AS monitored, c.detected_at
                   FROM companies c
                   LEFT JOIN user_companies uc ON uc.company_id = c.id AND uc.user_id = ?
                   WHERE c.ats_provider IS NOT NULL OR c.scraper_id IS NOT NULL
                   ORDER BY c.name""",
                (self.user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def is_company_scrapable(self, company_id: int) -> bool:
        """A company is scrapable if it has an ATS provider or a dedicated scraper."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT ats_provider, scraper_id FROM companies WHERE id = ?",
                (company_id,),
            ).fetchone()
        if not row:
            return False
        return bool(row["ats_provider"] or row["scraper_id"])

    def get_companies_by_ats_provider(self, provider: str) -> list[dict]:
        """Return watched companies for a specific ATS provider (union, FR-010)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT c.id, c.name, c.ats_identifier, c.scraper_id
                   FROM companies c
                   JOIN user_companies uc ON uc.company_id = c.id
                   WHERE uc.monitored = 1 AND c.ats_provider = ?
                   ORDER BY c.name""",
                (provider,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_watching_companies_by_method(self, scraping_method: str) -> list[dict]:
        """Return watched, ready companies with the given scraping_method (union, FR-010).
        Does NOT return legacy companies with scraping_method=NULL.
        Logs a warning for watched companies that have a NULL ats_identifier.
        """
        with self._conn() as conn:
            # Warn about watched companies with NULL ats_identifier
            null_ids = conn.execute(
                """SELECT c.name FROM companies c
                   JOIN user_companies uc ON uc.company_id = c.id
                   WHERE uc.monitored = 1
                     AND c.monitoring_status = 'watch_ready'
                     AND c.scraping_method = ?
                     AND c.ats_identifier IS NULL""",
                (scraping_method,),
            ).fetchall()
            for row in null_ids:
                logger.warning(
                    "[%s] watched company '%s' has NULL ats_identifier — skipped",
                    scraping_method, row["name"])

            rows = conn.execute(
                """SELECT c.id, c.name, c.ats_identifier, c.ats_provider, c.careers_url
                   FROM companies c
                   JOIN user_companies uc ON uc.company_id = c.id
                   WHERE uc.monitored = 1
                     AND c.monitoring_status = 'watch_ready'
                     AND c.scraping_method = ?
                     AND c.ats_identifier IS NOT NULL""",
                (scraping_method,),
            ).fetchall()
            return [dict(r) for r in rows]

    def set_job_monitored_company(self, job_id: str, company_id: int) -> None:
        """Record that a job came from a monitored company (set at scrape time)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET monitored_company_id = ? WHERE id = ?",
                (company_id, job_id),
            )

    def upsert_company_from_detection(self, name: str, careers_url: str,
                                       ats_provider: str,
                                       ats_identifier: str) -> int:
        """Create or update a company from ATS detection. Returns company id."""
        name_norm = _normalize_company_name(name)
        if not name_norm:
            raise ValueError(f"Company name normalizes to empty: {name!r}")
        now = _now()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM companies WHERE name_normalized = ?",
                (name_norm,),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE companies SET
                           careers_url = COALESCE(NULLIF(?, ''), companies.careers_url),
                           ats_provider = ?,
                           ats_identifier = ?,
                           detected_at = ?,
                           last_seen_at = ?
                       WHERE id = ?""",
                    (careers_url, ats_provider, ats_identifier, now, now, existing["id"]),
                )
                return existing["id"]
            else:
                conn.execute(
                    """INSERT INTO companies
                       (name, name_normalized, website, careers_url,
                        ats_provider, ats_identifier,
                        first_seen_at, last_seen_at, created_at, detected_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (name, name_norm, None, careers_url,
                     ats_provider, ats_identifier,
                     now, now, now, now),
                )
                return conn.execute(
                    "SELECT id FROM companies WHERE name_normalized = ?",
                    (name_norm,),
                ).fetchone()["id"]

    def get_monitored_company_for_job(self, job_id: str) -> dict | None:
        """Return the monitored company info for a job, if any."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT c.id, c.name
                   FROM jobs j
                   JOIN companies c ON j.monitored_company_id = c.id
                   WHERE j.id = ?""",
                (job_id,),
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Company Researcher — watch_pending → watch_ready pipeline
    # ------------------------------------------------------------------

    VALID_MONITORING_STATUSES = frozenset({
        "unmonitored", "watch_pending", "watch_ready", "watching",
        "watch_unsuitable",  # investigated, no viable monitoring path
    })

    _ALLOWED_COMPANY_FIELDS = frozenset({
        "name", "website", "company_country", "industry_sector", "company_size",
        "x_handle", "careers_url", "ats_provider", "ats_identifier",
        "scraping_method", "research_notes", "notes",
    })

    def update_company_fields(self, company_id: int, fields: dict) -> None:
        """Update arbitrary company fields by column name.

        Only columns present in `fields` are updated — others untouched.
        Raises ValueError if an unknown column name is passed.
        ``notes`` is user-space (user_companies) — the rest are company facts.
        """
        invalid = set(fields) - self._ALLOWED_COMPANY_FIELDS
        if invalid:
            raise ValueError(f"Unknown company fields: {invalid}")
        if not fields:
            return
        notes = fields.get("notes")
        fact_fields = {k: v for k, v in fields.items() if k != "notes"}
        if fact_fields:
            cols = ", ".join(f"{k} = ?" for k in fact_fields)
            vals = list(fact_fields.values()) + [company_id]
            with self._conn() as conn:
                conn.execute(f"UPDATE companies SET {cols} WHERE id = ?", vals)
        if notes is not None:
            now = _now()
            with self._conn() as conn:
                conn.execute(
                    """INSERT INTO user_companies (user_id, company_id, status, notes, created_at)
                       VALUES (?, ?, 'prospect', ?, ?)
                       ON CONFLICT(user_id, company_id) DO UPDATE SET notes = excluded.notes""",
                    (self.user_id, company_id, notes, now),
                )

    def set_monitoring_status(self, company_id: int, status: str) -> None:
        if status not in self.VALID_MONITORING_STATUSES:
            raise ValueError(
                f"Invalid monitoring status: {status!r}. "
                f"Valid: {self.VALID_MONITORING_STATUSES}"
            )
        # `watching` is the legacy mixed value (request + capability). After the
        # split the request lives on user_companies and the capability is
        # `watch_ready`; map it so the legacy call keeps working (FR-005).
        capability = "watch_ready" if status == "watching" else status
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "UPDATE companies SET monitoring_status = ? WHERE id = ?",
                (capability, company_id),
            )
            if status == "watching":
                conn.execute(
                    """INSERT INTO user_companies
                       (user_id, company_id, status, monitored, watch_requested_at, created_at)
                       VALUES (?, ?, 'prospect', 1, ?, ?)
                       ON CONFLICT(user_id, company_id) DO UPDATE SET
                           monitored = 1,
                           watch_requested_at = COALESCE(user_companies.watch_requested_at, excluded.watch_requested_at)""",
                    (self.user_id, company_id, now, now),
                )

    def get_watch_pending_companies(self) -> list[dict]:
        """Return companies watched by any user but awaiting research (union, FR-010)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT c.id, c.name, c.website, c.careers_url, c.ats_provider,
                          c.ats_identifier, c.monitoring_status, c.scraping_method,
                          c.research_notes, c.research_confidence, c.researched_at
                   FROM companies c
                   JOIN user_companies uc ON uc.company_id = c.id
                   WHERE uc.monitored = 1
                     AND c.monitoring_status = 'watch_pending'
                   ORDER BY c.name"""
            ).fetchall()
        return [dict(r) for r in rows]

    def update_company_research(self, company_id: int, result) -> None:
        """Write research fields and transition status.

        result is a ResearchResult dataclass with at least:
          scraping_method, ats_provider, ats_board_slug, ats_board_url,
          detection_method, notes, confidence

        If detection_method == 'existing_data', only update researched_at
        (never overwrite existing ATS config).
        """
        now = _now()
        if getattr(result, "detection_method", None) == "existing_data":
            with self._conn() as conn:
                conn.execute(
                    "UPDATE companies SET researched_at = ? WHERE id = ?",
                    (now, company_id),
                )
            return

        # Determine target status based on scraping_method
        actionable = {"greenhouse", "lever", "workable", "ashby", "custom_html"}
        new_status = "watch_ready" if result.scraping_method in actionable else "watch_pending"

        with self._conn() as conn:
            conn.execute(
                """UPDATE companies SET
                       ats_provider = COALESCE(?, companies.ats_provider),
                       ats_identifier = COALESCE(?, companies.ats_identifier),
                       scraping_method = ?,
                       research_notes = ?,
                       research_confidence = ?,
                       monitoring_status = ?,
                       researched_at = ?
                   WHERE id = ?""",
                (
                    result.ats_provider,
                    result.ats_board_slug,
                    result.scraping_method,
                    result.notes,
                    result.confidence,
                    new_status,
                    now,
                    company_id,
                ),
            )

    def import_companies_from_csv(self, rows: list[dict]) -> dict:
        """Import companies from CSV rows. Sets monitoring_status='watch_pending'.

        Returns {"imported": int, "skipped": list[str]}.
        Deduplicates on case-insensitive name match against existing companies.
        """
        imported = 0
        skipped: list[str] = []
        now = _now()
        with self._conn() as conn:
            for row in rows:
                name = (row.get("Name") or "").strip()
                if not name:
                    continue
                name_norm = _normalize_company_name(name)
                if not name_norm:
                    continue

                existing = conn.execute(
                    "SELECT id, name FROM companies WHERE name_normalized = ?",
                    (name_norm,),
                ).fetchone()
                if existing:
                    skipped.append(existing["name"])
                    continue

                website = (row.get("Website") or "").strip() or None
                careers_url = (row.get("Career website") or "").strip() or None
                ats_provider = (row.get("ATS") or "").strip() or None
                x_handle = (row.get("X account") or "").strip() or None
                notes = (row.get("Comment") or row.get("Location") or "").strip() or None
                sector = (row.get("Field") or "").strip() or None

                conn.execute(
                    """INSERT INTO companies
                       (name, name_normalized, website, careers_url,
                        ats_provider, x_handle,
                        industry_sector, monitoring_status,
                        first_seen_at, last_seen_at, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'watch_pending',
                               ?, ?, ?)""",
                    (name, name_norm, website, careers_url,
                     ats_provider, x_handle, sector,
                     now, now, now),
                )
                # `notes` (the CSV "Comment") is a user decision → user_companies.
                if notes:
                    cid = conn.execute(
                        "SELECT id FROM companies WHERE name_normalized = ?",
                        (name_norm,),
                    ).fetchone()["id"]
                    conn.execute(
                        """INSERT INTO user_companies (user_id, company_id, status, notes, created_at)
                           VALUES (?, ?, 'prospect', ?, ?)
                           ON CONFLICT(user_id, company_id) DO UPDATE SET notes = excluded.notes""",
                        (self.user_id, cid, notes, now),
                    )
                imported += 1

        logger.info(
            f"[Storage] CSV import: {imported} imported, {len(skipped)} skipped"
        )
        return {"imported": imported, "skipped": skipped}

    def upsert_company_from_seed(self, row: dict) -> bool:
        """Upsert a company from seed data (data/companies.json).

        Match key: name (case-insensitive via name_normalized).
        Updates all config fields. Preserves id and researched_at.
        Never touches the jobs, scores, or run_logs tables.
        Returns True if a row was inserted or any field was changed.
        """
        name = (row.get("name") or "").strip()
        if not name:
            return False
        name_norm = _normalize_company_name(name)
        if not name_norm:
            return False
        now = _now()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT * FROM companies WHERE name_normalized = ?",
                (name_norm,),
            ).fetchone()

            seed_fields = {
                "name": name,
                "name_normalized": name_norm,
                "website": row.get("website"),
                "careers_url": row.get("careers_url"),
                "ats_provider": row.get("ats_provider"),
                "ats_identifier": row.get("ats_identifier"),
                "scraping_method": row.get("scraping_method"),
                "monitoring_status": row.get("monitoring_status", "watch_pending"),
                "research_notes": row.get("research_notes"),
                "research_confidence": row.get("research_confidence"),
                "x_handle": row.get("x_handle"),
                "industry_sector": row.get("industry_sector"),
                "company_country": row.get("company_country"),
            }

            if existing:
                changed = False
                updates = []
                params = []
                for col, val in seed_fields.items():
                    if col in ("name", "name_normalized"):
                        continue  # keys — never update
                    if col == "monitoring_status":
                        # Don't clobber the capability of a company any user is
                        # actively watching (user_companies.monitored=1) — the
                        # legacy 'watching' sentinel no longer exists post-split,
                        # and monitoring is the union of users' watch requests
                        # (Constitution VIII).
                        watched = conn.execute(
                            "SELECT 1 FROM user_companies "
                            "WHERE company_id = ? AND monitored = 1",
                            (existing["id"],),
                        ).fetchone()
                        if watched:
                            continue
                    existing_val = existing[col] if col in existing.keys() else None
                    if val != existing_val:
                        updates.append(f"{col} = ?")
                        params.append(val)
                        changed = True
                if changed:
                    updates.append("last_seen_at = ?")
                    params.append(now)
                    params.append(existing["id"])
                    conn.execute(
                        f"UPDATE companies SET {', '.join(updates)} WHERE id = ?",
                        params,
                    )
                return changed
            else:
                cols = [k for k in seed_fields]
                placeholders = ", ".join("?" for _ in cols)
                vals = [seed_fields[k] for k in cols]
                conn.execute(
                    f"""INSERT INTO companies
                        (name, name_normalized, website, careers_url,
                         ats_provider, ats_identifier, scraping_method,
                         monitoring_status, research_notes, research_confidence,
                         x_handle, industry_sector, company_country,
                         first_seen_at, last_seen_at, created_at)
                        VALUES ({placeholders}, ?, ?, ?)""",
                    vals + [now, now, now],
                )
                return True

    # ------------------------------------------------------------------
    # Purge — stale job cleanup
    # ------------------------------------------------------------------

    def count_purgeable_jobs(self, retention_days: int = 30) -> int:
        """Return how many jobs would be purged with the given retention (no side-effects)."""
        with self._conn() as conn:
            count = conn.execute(
                """SELECT COUNT(*) FROM jobs j
                   LEFT JOIN job_tracking jt ON j.id = jt.job_id
                   WHERE j.first_seen < date('now', '-' || ? || ' days')
                     AND (
                         jt.job_id IS NULL
                         OR (jt.status = 'new' AND (jt.notes IS NULL OR trim(jt.notes) = ''))
                     )
                     AND NOT EXISTS (SELECT 1 FROM interactions i WHERE i.job_id = j.id)
                     AND NOT EXISTS (SELECT 1 FROM job_applications a WHERE a.job_id = j.id)""",
                (retention_days,),
            ).fetchone()[0]
            return count

    def purge_stale_jobs(self, retention_days: int = 30) -> int:
        """Delete stale jobs (>retention_days old, untouched, no notes, no interactions).

        Cleans up job_applications, interactions, status_history, job_scores,
        job_tracking, and jobs in a transaction. Returns the number of job rows deleted.

        Jobs are protected from purging if they have:
          - A non-'new' tracking status (applied, queued, rejected, etc.)
          - Notes in job_tracking
          - Any interaction row (outreach, reply, call, etc.)
          - Any prepared application (cover letter, CV selection, etc.)
        """
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Compute purgeable job IDs once so the filter is defined in one place.
                conn.execute(
                    """CREATE TEMP TABLE _purgeable AS
                       SELECT j.id FROM jobs j
                       LEFT JOIN job_tracking jt ON j.id = jt.job_id
                       WHERE j.first_seen < date('now', '-' || ? || ' days')
                         AND (
                             jt.job_id IS NULL
                             OR (jt.status = 'new' AND (jt.notes IS NULL OR trim(jt.notes) = ''))
                         )
                         AND NOT EXISTS (SELECT 1 FROM interactions i WHERE i.job_id = j.id)
                         AND NOT EXISTS (SELECT 1 FROM job_applications a WHERE a.job_id = j.id)""",
                    (retention_days,),
                )
                # Delete from child tables first (no CASCADE in schema)
                for table in ("job_applications", "interactions", "status_history",
                              "job_scores", "job_tracking"):
                    conn.execute(
                        f"DELETE FROM {table} WHERE job_id IN (SELECT id FROM _purgeable)"
                    )
                cursor = conn.execute(
                    "DELETE FROM jobs WHERE id IN (SELECT id FROM _purgeable)"
                )
                count = cursor.rowcount
                conn.execute("DROP TABLE _purgeable")
                # Record a successful purge so /health can warn on a stale one.
                conn.execute(
                    "INSERT INTO config (key, value) VALUES ('purge.last_success', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (_now(),),
                )
                conn.execute("COMMIT")
                return count
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_purge_last_success(self) -> str | None:
        """ISO timestamp of the last successful purge, or None if never purged.

        Set atomically inside ``purge_stale_jobs`` (same transaction) so a skipped
        purge leaves the value stale and ``/api/health`` can warn on it.
        """
        return self.get_config("purge.last_success")

    # ------------------------------------------------------------------
    # Config key-value store
    # ------------------------------------------------------------------

    def get_config(self, key: str, default=None) -> str | None:
        # Per-user settings live on the `users` row (spec 039); keep their
        # `config` keys as a transparent alias so existing call sites are
        # unchanged (the old keys are dead, cleaned at step 10).
        if key == "active_profile_id":
            val = self.get_active_profile_id()
            return val if val else default
        if key == "onboarding_complete":
            return "true" if self.get_onboarding_completed() else default
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_config(self, key: str, value: str) -> None:
        if key == "active_profile_id":
            self.set_active_profile_id(value)
            return
        if key == "onboarding_complete":
            self.set_onboarding_completed(value == "true")
            return
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------------
    # Platform ingestion scope (spec 040 — config.ingestion_scope)
    # ------------------------------------------------------------------

    def get_ingestion_scope(self) -> dict:
        """Return the platform ingestion scope as a typed dict.

        Absent, malformed, or wrongly-typed config coerces to the safe default
        (open net, Constitution IX), following the `get_freshness_days` template.
        The spec-045 keys (`search_locations`, `greenhouse_boards`) default to
        `[]` individually when absent/malformed, without invalidating the
        spec-040 keys (older DBs lack them).
        """
        raw = self.get_config("ingestion_scope")
        if raw:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                titles = parsed.get("title_families")
                exclude = parsed.get("exclude")
                remote = parsed.get("remote_or_hybrid")
                if (isinstance(titles, list) and all(isinstance(x, str) for x in titles)
                        and isinstance(exclude, list)
                        and all(isinstance(x, str) for x in exclude)
                        and isinstance(remote, bool)):
                    locations = parsed.get("search_locations")
                    boards = parsed.get("greenhouse_boards")
                    return {
                        "title_families": titles,
                        "exclude": exclude,
                        "remote_or_hybrid": remote,
                        "search_locations": (
                            locations if isinstance(locations, list)
                            and all(isinstance(x, str) for x in locations) else []
                        ),
                        "greenhouse_boards": (
                            boards if isinstance(boards, list)
                            and all(isinstance(x, str) for x in boards) else []
                        ),
                    }
        return {
            "title_families": [],
            "exclude": [],
            "remote_or_hybrid": False,
            "search_locations": [],
            "greenhouse_boards": [],
        }

    def set_ingestion_scope(self, title_families: list[str],
                            exclude: list[str],
                            remote_or_hybrid: bool,
                            search_locations: list[str] | None = None,
                            greenhouse_boards: list[str] | None = None) -> None:
        """Persist the platform ingestion scope after validating types.

        `search_locations`/`greenhouse_boards` default to the current values
        (`None` = leave unchanged), so the three-key call sites are unchanged.
        """
        if not isinstance(title_families, list) or not all(isinstance(x, str) for x in title_families):
            raise ValueError("title_families must be a list[str]")
        if not isinstance(exclude, list) or not all(isinstance(x, str) for x in exclude):
            raise ValueError("exclude must be a list[str]")
        if not isinstance(remote_or_hybrid, bool):
            raise ValueError("remote_or_hybrid must be a bool")
        if search_locations is not None and (
                not isinstance(search_locations, list)
                or not all(isinstance(x, str) for x in search_locations)):
            raise ValueError("search_locations must be a list[str]")
        if greenhouse_boards is not None and (
                not isinstance(greenhouse_boards, list)
                or not all(isinstance(x, str) for x in greenhouse_boards)):
            raise ValueError("greenhouse_boards must be a list[str]")
        if search_locations is None or greenhouse_boards is None:
            current = self.get_ingestion_scope()
            if search_locations is None:
                search_locations = current["search_locations"]
            if greenhouse_boards is None:
                greenhouse_boards = current["greenhouse_boards"]
        scope = {
            "title_families": title_families,
            "exclude": exclude,
            "remote_or_hybrid": remote_or_hybrid,
            "search_locations": search_locations,
            "greenhouse_boards": greenhouse_boards,
        }
        self.set_config("ingestion_scope", json.dumps(scope))

    # ------------------------------------------------------------------
    # Per-user settings (spec 039 — moved out of `config` into `users`)
    # ------------------------------------------------------------------

    def get_active_profile_id(self) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT active_profile_id FROM users WHERE id = ?", (self.user_id,)
            ).fetchone()
            return row["active_profile_id"] if row else None

    def set_active_profile_id(self, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET active_profile_id = ? WHERE id = ?",
                (value, self.user_id),
            )

    def get_onboarding_completed(self) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT onboarding_completed_at FROM users WHERE id = ?", (self.user_id,)
            ).fetchone()
            return bool(row and row["onboarding_completed_at"])

    def set_onboarding_completed(self, value: bool) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET onboarding_completed_at = ? WHERE id = ?",
                (_now() if value else None, self.user_id),
            )

    def get_freshness_days(self) -> int:
        """Freshness window (posted_date admission cutoff) as an int.

        Single source for the configurable 30-day default. Absent, malformed,
        or non-positive config coerces to 30 (Constitution IX: pipeline runs
        without config; the Settings widget enforces a 7-day minimum).
        """
        try:
            days = int(self.get_config("freshness_days", default="30") or "30")
        except (TypeError, ValueError):
            return 30
        return days if days > 0 else 30

    # ------------------------------------------------------------------
    # Contacts + interactions
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_contact_name(first_name: str | None, last_name: str | None,
                                 full_name: str | None) -> str:
        """Build a normalized, sortable token string for name-matching.

        Example: "Sarah Müller" and "Müller, Sarah" both normalize to "muller sarah".
        """
        parts: list[str] = []
        if full_name:
            parts.extend(full_name.split())
        else:
            if first_name:
                parts.extend(first_name.split())
            if last_name:
                parts.extend(last_name.split())
        if not parts:
            return ""
        normalized = sorted(re.sub(r"[^a-z0-9]", "", p.lower()) for p in parts)
        return " ".join(n for n in normalized if n)

    def _lookup_contact(self, conn, *, company_id, linkedin_url, email,
                         normalized_name) -> dict | None:
        """Resolution lookup: linkedin_url > email > (company_id, normalized_name)."""
        if linkedin_url:
            row = conn.execute(
                "SELECT * FROM contacts WHERE linkedin_url = ? AND user_id = ?",
                (linkedin_url, self.user_id),
            ).fetchone()
            if row:
                return dict(row)
        if email:
            row = conn.execute(
                "SELECT * FROM contacts WHERE email = ? AND company_id = ? AND user_id = ?",
                (email.lower(), company_id, self.user_id),
            ).fetchone()
            if row:
                return dict(row)
        if normalized_name and company_id:
            # Match by normalized name within the same company.
            # We load all contacts for the company and compare client-side
            # since the normalized name is not stored denormalized.
            rows = conn.execute(
                "SELECT * FROM contacts WHERE company_id = ? AND user_id = ?",
                (company_id, self.user_id),
            ).fetchall()
            for row in rows:
                existing_norm = self._normalize_contact_name(
                    row["first_name"], row["last_name"], row["full_name"]
                )
                if existing_norm and existing_norm == normalized_name:
                    return dict(row)
        return None

    def upsert_contact(
        self,
        *,
        company_id: int,
        linkedin_url: str | None = None,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        full_name: str | None = None,
        role_title: str | None = None,
        role_family: str | None = None,
        seniority: str | None = None,
        email_status: str = "unknown",
        email_confidence: float | None = None,
        x_handle: str | None = None,
        telegram_handle: str | None = None,
        github_handle: str | None = None,
        phone: str | None = None,
        is_unverified: bool = False,
        notes: str | None = None,
    ) -> int:
        """Upsert a contact, resolving by linkedin_url > email > (company_id, name).

        Never overwrites non-NULL fields with NULL. Returns the contact id.
        """
        if role_family is not None and role_family not in CONTACT_ROLE_FAMILIES:
            raise ValueError(
                f"Invalid role_family: {role_family!r}. Valid: {CONTACT_ROLE_FAMILIES}"
            )
        if email_status not in CONTACT_EMAIL_STATUSES:
            raise ValueError(
                f"Invalid email_status: {email_status!r}. Valid: {CONTACT_EMAIL_STATUSES}"
            )
        if seniority is not None and seniority not in CONTACT_SENIORITIES:
            raise ValueError(
                f"Invalid seniority: {seniority!r}. Valid: {CONTACT_SENIORITIES}"
            )

        normalized_name = self._normalize_contact_name(first_name, last_name, full_name)
        now = _now()

        with self._conn() as conn:
            existing = self._lookup_contact(
                conn,
                company_id=company_id,
                linkedin_url=linkedin_url,
                email=email.lower() if email else None,
                normalized_name=normalized_name,
            )
            if existing:
                cid = existing["id"]
                updates = []
                params = []
                field_keys = (
                    "first_name", "last_name", "full_name",
                    "role_title", "role_family", "seniority",
                    "email", "email_status", "email_confidence",
                    "linkedin_url", "x_handle", "telegram_handle",
                    "github_handle", "phone",
                )
                incoming = {
                    "first_name": first_name,
                    "last_name": last_name,
                    "full_name": full_name,
                    "role_title": role_title,
                    "role_family": role_family,
                    "seniority": seniority,
                    "email": email.lower() if email else None,
                    "email_status": email_status,
                    "email_confidence": email_confidence,
                    "linkedin_url": linkedin_url,
                    "x_handle": x_handle,
                    "telegram_handle": telegram_handle,
                    "github_handle": github_handle,
                    "phone": phone,
                }
                for key in field_keys:
                    val = incoming[key]
                    if val is not None and existing[key] is None:
                        updates.append(f"{key} = ?")
                        params.append(val)
                # Special case: email_status might be "unknown" default — only
                # overwrite if incoming is non-default AND current is still "unknown"
                # and there's actual email context.
                if email_status != "unknown" and existing["email_status"] == "unknown":
                    if "email_status" not in {u.split()[0] for u in updates}:
                        updates.append("email_status = ?")
                        params.append(email_status)

                updates.append("last_seen_at = ?")
                params.append(now)
                updates.append("is_current = 1")
                params.append(cid)
                params.append(self.user_id)
                conn.execute(
                    f"UPDATE contacts SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
                    params,
                )
                return cid

            # Insert
            conn.execute(
                """INSERT INTO contacts (
                       user_id, company_id, first_name, last_name, full_name,
                       role_title, role_family, seniority,
                       email, email_status, email_confidence,
                       linkedin_url, x_handle, telegram_handle, github_handle,
                       phone, is_unverified, notes,
                       is_current, first_seen_at, last_seen_at, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (
                    self.user_id,
                    company_id, first_name, last_name, full_name,
                    role_title, role_family, seniority,
                    email.lower() if email else None, email_status, email_confidence,
                    linkedin_url, x_handle, telegram_handle, github_handle,
                    phone, int(is_unverified), notes,
                    now, now, now,
                ),
            )
            # TODO: detect company changes from LinkedIn refresh
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def log_interaction(
        self,
        *,
        company_id: int,
        type: str,
        contact_id: int | None = None,
        job_id: str | None = None,
        direction: str = "none",
        outcome: str | None = None,
        subject: str | None = None,
        body_excerpt: str | None = None,
        occurred_at: str | None = None,
        follow_up_due_at: str | None = None,
    ) -> int:
        """Log an interaction. Returns the new interaction id."""
        if type not in INTERACTION_TYPES:
            raise ValueError(
                f"Invalid interaction type: {type!r}. Valid: {INTERACTION_TYPES}"
            )
        if direction not in INTERACTION_DIRECTIONS:
            raise ValueError(
                f"Invalid direction: {direction!r}. Valid: {INTERACTION_DIRECTIONS}"
            )
        if outcome is not None and outcome not in INTERACTION_OUTCOMES:
            raise ValueError(
                f"Invalid outcome: {outcome!r}. Valid: {INTERACTION_OUTCOMES} or None"
            )

        now = _now()
        ts = occurred_at or now
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO interactions (
                       user_id, company_id, contact_id, job_id,
                       type, direction, outcome,
                       subject, body_excerpt,
                       occurred_at, follow_up_due_at, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.user_id, company_id, contact_id, job_id,
                 type, direction, outcome,
                 subject, body_excerpt,
                 ts, follow_up_due_at, now),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_contact_relationship_status(self, contact_id: int) -> str:
        """Derive relationship status from interaction history (no stored column)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT type, outcome FROM interactions
                   WHERE contact_id = ? AND user_id = ?
                   ORDER BY occurred_at DESC""",
                (contact_id, self.user_id),
            ).fetchall()

        if not rows:
            return "none"

        # Most recent terminal decision
        for r in rows:
            if r["type"] == "decision_received":
                if r["outcome"] == "negative":
                    return "declined"
                return "offer"

        types = {r["type"] for r in rows}
        if "interview" in types:
            return "interviewing"
        if "application_submitted" in types:
            return "applied"
        if "reply_received" in types:
            return "replied"
        if "outreach_sent" in types:
            return "cold_contacted"

        # Any non-discovery interaction
        non_discovery = types - {"discovered_on_posting", "discovered_manual", "note"}
        if non_discovery:
            return "engaged"

        return "none"

    def get_company_relationship_summary(self, company_id: int) -> dict:
        """Aggregate: total contacts, count by relationship status, last interaction."""
        with self._conn() as conn:
            contact_rows = conn.execute(
                "SELECT id FROM contacts WHERE company_id = ? AND user_id = ?",
                (company_id, self.user_id),
            ).fetchall()
            total_contacts = len(contact_rows)
            by_status: dict[str, int] = {}
            for cr in contact_rows:
                status = self.get_contact_relationship_status(cr["id"])
                by_status[status] = by_status.get(status, 0) + 1

            last_int = conn.execute(
                """SELECT occurred_at FROM interactions
                   WHERE company_id = ? AND user_id = ?
                   ORDER BY occurred_at DESC LIMIT 1""",
                (company_id, self.user_id),
            ).fetchone()

        return {
            "company_id": company_id,
            "total_contacts": total_contacts,
            "by_status": by_status,
            "last_interaction_at": last_int["occurred_at"] if last_int else None,
        }

    def get_company_contacts(self, company_id: int,
                              include_unverified: bool = True) -> list[dict]:
        """List contacts for a company."""
        with self._conn() as conn:
            if include_unverified:
                rows = conn.execute(
                    "SELECT * FROM contacts WHERE company_id = ? AND user_id = ? ORDER BY last_seen_at DESC",
                    (company_id, self.user_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM contacts WHERE company_id = ? AND user_id = ? AND is_unverified = 0 "
                    "ORDER BY last_seen_at DESC",
                    (company_id, self.user_id),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_company_interactions(self, company_id: int, limit: int = 50) -> list[dict]:
        """List interactions for a company, most recent first."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM interactions
                   WHERE company_id = ? AND user_id = ?
                   ORDER BY occurred_at DESC LIMIT ?""",
                (company_id, self.user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_contact_interactions(self, contact_id: int, limit: int = 50) -> list[dict]:
        """List interactions for a contact, most recent first."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM interactions
                   WHERE contact_id = ? AND user_id = ?
                   ORDER BY occurred_at DESC LIMIT ?""",
                (contact_id, self.user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_contact(self, contact_id: int) -> dict | None:
        """Fetch a single contact by id."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM contacts WHERE id = ? AND user_id = ?",
                (contact_id, self.user_id),
            ).fetchone()
            return dict(row) if row else None

    def get_companies(
        self,
        *,
        status: list[str] | None = None,
        search: str | None = None,
        exclude_blacklisted: bool = True,
        countries: list[str] | None = None,
        sectors: list[str] | None = None,
        sizes: list[str] | None = None,
        min_job_count: int | None = None,
        last_interaction_within_days: int | None = None,
        monitored: bool | None = None,
        monitorable: bool | None = None,
    ) -> list[dict]:
        """List companies with job/contact/interaction counts and last interaction date.

        Relationship columns (status/notes/monitored) come from the bound user's
        `user_companies` row, defaulting when absent (Constitution X)."""
        clauses = []
        params: list = [self.user_id] * 4  # uc JOIN + contacts + interactions + last_ix
        if exclude_blacklisted:
            clauses.append("COALESCE(uc.status, 'prospect') != 'blacklisted'")
        if status:
            placeholders = ", ".join("?" for _ in status)
            clauses.append(f"COALESCE(uc.status, 'prospect') IN ({placeholders})")
            params.extend(status)
        if search:
            clauses.append("(LOWER(c.name) LIKE ? OR LOWER(c.name_normalized) LIKE ?)")
            params.extend([f"%{search.lower()}%", f"%{search.lower()}%"])
        if countries:
            placeholders = ", ".join("?" for _ in countries)
            clauses.append(f"COALESCE(c.company_country, 'unknown') IN ({placeholders})")
            params.extend(countries)
        if sectors:
            placeholders = ", ".join("?" for _ in sectors)
            clauses.append(f"COALESCE(c.industry_sector, 'other') IN ({placeholders})")
            params.extend(sectors)
        if sizes:
            placeholders = ", ".join("?" for _ in sizes)
            clauses.append(f"COALESCE(c.company_size, 'unknown') IN ({placeholders})")
            params.extend(sizes)
        if last_interaction_within_days is not None:
            clauses.append(
                "last_ix.occurred_at IS NOT NULL AND "
                "last_ix.occurred_at >= datetime('now', ? || ' days')"
            )
            params.append(f"-{last_interaction_within_days}")
        if monitored is not None:
            if monitored:
                clauses.append("COALESCE(uc.monitored, 0) = 1")
            else:
                clauses.append("COALESCE(uc.monitored, 0) = 0")
        if monitorable is not None:
            if monitorable:
                clauses.append("(c.ats_provider IS NOT NULL OR c.scraper_id IS NOT NULL)")
            else:
                clauses.append("(c.ats_provider IS NULL AND c.scraper_id IS NULL)")

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        min_jobs_outer = ""
        if min_job_count is not None and min_job_count > 0:
            min_jobs_outer = f"WHERE job_count >= ?"

        query = f"""
            SELECT * FROM (
            SELECT
                c.id, c.name, c.website,
                COALESCE(uc.status, 'prospect') AS status,
                uc.notes                              AS notes,
                c.company_country, c.industry_sector, c.company_size,
                c.first_seen_at, c.last_seen_at, c.summary,
                c.ats_provider, c.ats_identifier, c.scraper_id,
                COALESCE(uc.monitored, 0)            AS monitored,
                c.monitoring_status, c.scraping_method, c.research_notes,
                c.research_confidence, c.researched_at,
                COALESCE(jcnt.cnt, 0)    AS job_count,
                COALESCE(ctcnt.cnt, 0)   AS contact_count,
                COALESCE(ixcnt.cnt, 0)   AS interaction_count,
                last_ix.occurred_at      AS last_interaction_at
            FROM companies c
            LEFT JOIN user_companies uc ON uc.company_id = c.id AND uc.user_id = ?
            LEFT JOIN (
                SELECT company_id, COUNT(*) AS cnt
                FROM jobs GROUP BY company_id
            ) jcnt ON jcnt.company_id = c.id
            LEFT JOIN (
                SELECT company_id, COUNT(*) AS cnt
                FROM contacts WHERE user_id = ? GROUP BY company_id
            ) ctcnt ON ctcnt.company_id = c.id
            LEFT JOIN (
                SELECT company_id, COUNT(*) AS cnt
                FROM interactions WHERE user_id = ? GROUP BY company_id
            ) ixcnt ON ixcnt.company_id = c.id
            LEFT JOIN (
                SELECT company_id, MAX(occurred_at) AS occurred_at
                FROM interactions WHERE user_id = ? GROUP BY company_id
            ) last_ix ON last_ix.company_id = c.id
            {where}
            ) sub
            {min_jobs_outer}
            ORDER BY COALESCE(last_interaction_at, last_seen_at) DESC, name ASC
        """
        if min_jobs_outer:
            params.append(min_job_count)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_company_by_id(self, company_id: int) -> dict | None:
        """Fetch a single company by PK, with counts and last interaction.

        Relationship columns (status/notes/monitored) come from the bound user's
        `user_companies` row, defaulting when absent (Constitution X)."""
        query = """
            SELECT
                c.id, c.name, c.website,
                COALESCE(uc.status, 'prospect') AS status,
                uc.notes                              AS notes,
                c.company_country, c.industry_sector, c.company_size,
                c.careers_url, c.ats_provider, c.ats_identifier,
                c.scraper_id,
                COALESCE(uc.monitored, 0)             AS monitored,
                c.first_seen_at, c.last_seen_at, c.summary,
                c.monitoring_status, c.scraping_method, c.research_notes,
                c.research_confidence, c.researched_at, c.x_handle,
                COALESCE(jcnt.cnt, 0)    AS job_count,
                COALESCE(ctcnt.cnt, 0)   AS contact_count,
                COALESCE(ixcnt.cnt, 0)   AS interaction_count,
                last_ix.occurred_at      AS last_interaction_at
            FROM companies c
            LEFT JOIN user_companies uc ON uc.company_id = c.id AND uc.user_id = ?
            LEFT JOIN (
                SELECT company_id, COUNT(*) AS cnt
                FROM jobs GROUP BY company_id
            ) jcnt ON jcnt.company_id = c.id
            LEFT JOIN (
                SELECT company_id, COUNT(*) AS cnt
                FROM contacts WHERE user_id = ? GROUP BY company_id
            ) ctcnt ON ctcnt.company_id = c.id
            LEFT JOIN (
                SELECT company_id, COUNT(*) AS cnt
                FROM interactions WHERE user_id = ? GROUP BY company_id
            ) ixcnt ON ixcnt.company_id = c.id
            LEFT JOIN (
                SELECT company_id, MAX(occurred_at) AS occurred_at
                FROM interactions WHERE user_id = ? GROUP BY company_id
            ) last_ix ON last_ix.company_id = c.id
            WHERE c.id = ?
        """
        with self._conn() as conn:
            row = conn.execute(
                query,
                (self.user_id, self.user_id, self.user_id, self.user_id, company_id),
            ).fetchone()
        return dict(row) if row else None

    def find_jobs_by_company(self, company: str) -> list[dict]:
        """Find applied jobs matching company name (case-insensitive, partial match).

        Only returns jobs with tracking status 'applied'.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT j.id, j.title, j.company, t.status
                FROM jobs j
                LEFT JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                WHERE lower(j.company) LIKE lower(?)
                  AND t.status = 'applied'
                """,
                (self.user_id, f"%{company}%"),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_jobs_for_company(self, company_id: int) -> list[dict]:
        """All jobs at a company, with best score across profiles."""
        query = """
            SELECT j.id, j.company_id, j.title, j.company, j.url, j.source, j.location,
                   j.base_location, j.posted_date, j.description,
                   j.first_seen, j.last_seen, j.extracted_at, j.extracted_by,
                   COALESCE(j.summary, '') AS summary,
                   COALESCE(j.work_mode, 'unknown') AS work_mode,
                   COALESCE(j.geo_zone, 'unknown') AS geo_zone,
                   COALESCE(c.company_size, 'unknown') AS company_size,
                   COALESCE(j.contract_type, 'unknown') AS contract_type,
                   COALESCE(c.company_country, 'unknown') AS company_country,
                   COALESCE(c.industry_sector, 'other') AS industry_sector,
                   COALESCE(j.language_required, 'unknown') AS language_required,
                   s.score, s.reason, s.scored_by,
                   s.country_code AS country_code,
                   t.status AS tracked_status,
                   COALESCE(t.status, 'new') AS status, t.notes,
                   s.profile_id as best_profile_id,
                   t.changed_at AS status_changed_at,
                   (SELECT COUNT(*) FROM job_scores WHERE job_id = j.id) AS score_count
            FROM jobs j
            LEFT JOIN companies c ON j.company_id = c.id
            LEFT JOIN job_scores s ON j.id = s.job_id
              AND s.score IS NOT NULL
              AND s.rowid = (
                SELECT s2.rowid FROM job_scores s2
                WHERE s2.job_id = j.id AND s2.score IS NOT NULL
                ORDER BY s2.score DESC, s2.profile_id ASC
                LIMIT 1
              )
            LEFT JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
            WHERE j.company_id = ?
            ORDER BY COALESCE(s.score, -1) DESC, j.last_seen DESC
        """
        with self._conn() as conn:
            rows = conn.execute(query, (self.user_id, company_id)).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                if d.get("score") is None and d.get("tracked_status") is None:
                    d["status"] = "unscored"
                result.append(d)
            return result

    def get_all_contacts(
        self,
        *,
        company_id: int | None = None,
        search: str | None = None,
        is_unverified: bool | None = None,
        role_family: str | None = None,
        exclude_blacklisted_companies: bool = True,
    ) -> list[dict]:
        """List all contacts across companies with optional filters."""
        clauses = []
        # uc JOIN + last_ix subquery + the ct.user_id filter (first WHERE clause)
        params: list = [self.user_id, self.user_id, self.user_id]
        clauses.append("ct.user_id = ?")
        if exclude_blacklisted_companies:
            clauses.append("COALESCE(uc.status, 'prospect') != 'blacklisted'")
        if company_id is not None:
            clauses.append("ct.company_id = ?")
            params.append(company_id)
        if is_unverified is not None:
            clauses.append("ct.is_unverified = ?")
            params.append(int(is_unverified))
        if role_family is not None:
            clauses.append("ct.role_family = ?")
            params.append(role_family)
        if search:
            clauses.append(
                "(LOWER(ct.full_name) LIKE ? OR LOWER(ct.first_name) LIKE ? "
                "OR LOWER(ct.last_name) LIKE ? OR LOWER(ct.email) LIKE ? "
                "OR LOWER(ct.role_title) LIKE ?)"
            )
            p = f"%{search.lower()}%"
            params.extend([p, p, p, p, p])

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        query = f"""
            SELECT
                ct.id, ct.first_name, ct.last_name, ct.full_name,
                ct.role_title, ct.role_family, ct.seniority,
                ct.email, ct.email_status, ct.email_confidence,
                ct.linkedin_url, ct.x_handle, ct.telegram_handle,
                ct.github_handle, ct.phone,
                ct.is_current, ct.is_unverified, ct.notes,
                ct.first_seen_at, ct.last_seen_at, ct.created_at,
                c.id AS company_id, c.name AS company_name,
                COALESCE(uc.status, 'prospect') AS company_status,
                last_ix.occurred_at AS last_interaction_at
            FROM contacts ct
            JOIN companies c ON ct.company_id = c.id
            LEFT JOIN user_companies uc ON uc.company_id = c.id AND uc.user_id = ?
            LEFT JOIN (
                SELECT contact_id, MAX(occurred_at) AS occurred_at
                FROM interactions WHERE user_id = ? GROUP BY contact_id
            ) last_ix ON last_ix.contact_id = ct.id
            {where}
            ORDER BY ct.last_seen_at DESC
        """
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Auto-interaction logging on status transitions
    # ------------------------------------------------------------------

    _STATUS_INTERACTION_MAP = {
        "applied":      ("application_submitted", "outbound", None),
        "interviewing": ("interview",             "none",     None),
        "rejected":     ("decision_received",     "inbound",  "negative"),
        "offer":        ("decision_received",     "inbound",  "positive"),
    }

    def _auto_log_status_interaction(self, job_id: str, new_status: str,
                                      _conn: sqlite3.Connection | None = None) -> None:
        """Create an interaction row for a job_tracking status transition."""
        if new_status not in self._STATUS_INTERACTION_MAP:
            return

        int_type, direction, outcome = self._STATUS_INTERACTION_MAP[new_status]
        now = _now()

        def _do(conn):
            row = conn.execute(
                "SELECT company_id FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            company_id = row["company_id"] if row else None
            if company_id is None:
                return
            conn.execute(
                """INSERT INTO interactions (
                       user_id, company_id, job_id,
                       type, direction, outcome,
                       occurred_at, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.user_id, company_id, job_id, int_type, direction, outcome, now, now),
            )

        if _conn is not None:
            _do(_conn)
        else:
            with self._conn() as conn:
                _do(conn)

    # ------------------------------------------------------------------
    # Profiles listing (for tracker UI)
    # ------------------------------------------------------------------

    def get_all_profiles(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, name, criteria FROM search_profiles WHERE user_id = ?",
                (self.user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_profile(self, profile_id: str) -> tuple[int, int]:
        """Delete a profile and all its scores. Returns (scores_deleted, profile_deleted)."""
        with self._conn() as conn:
            self._require_profile(conn, profile_id)
            conn.execute("DELETE FROM job_scores WHERE profile_id = ?", (profile_id,))
            scores = conn.execute("SELECT changes()").fetchone()[0]
            conn.execute(
                "DELETE FROM search_profiles WHERE id = ? AND user_id = ?",
                (profile_id, self.user_id),
            )
            profile = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
        return scores, profile

    def get_all_jobs_best_score(self, exclude_archived: bool = False,
                                 limit: int | None = None) -> list[dict]:
        """All jobs (scored and unscored), each shown once at its highest score across profiles.
        Unscored jobs appear with score=None and status='unscored'.
        Status and notes come from job_tracking (profile-independent).
        Company-level fields joined from companies table (Phase 4).
        """
        query = """
            SELECT j.id, j.company_id, j.title, j.company, j.url, j.source, j.location,
                   j.base_location, j.posted_date, j.description,
                   j.first_seen, j.last_seen, j.extracted_at, j.extracted_by,
                   COALESCE(j.summary, '') AS summary,
                   COALESCE(j.work_mode, 'unknown') AS work_mode,
                   COALESCE(j.geo_zone, 'unknown') AS geo_zone,
                   COALESCE(c.company_size, 'unknown') AS company_size,
                   COALESCE(j.contract_type, 'unknown') AS contract_type,
                   COALESCE(c.company_country, 'unknown') AS company_country,
                   COALESCE(c.industry_sector, 'other') AS industry_sector,
                   COALESCE(j.language_required, 'unknown') AS language_required,
                   s.score, s.reason, s.scored_by,
                   s.country_code AS country_code,
                   t.status AS tracked_status,
                   COALESCE(t.status, 'new') AS status, t.notes,
                   s.profile_id as best_profile_id,
                   t.changed_at AS status_changed_at,
                   (SELECT COUNT(*) FROM job_scores WHERE job_id = j.id) AS score_count
            FROM jobs j
            LEFT JOIN companies c ON j.company_id = c.id
            LEFT JOIN job_scores s ON j.id = s.job_id
              AND s.score IS NOT NULL
              AND s.rowid = (
                SELECT s2.rowid FROM job_scores s2
                WHERE s2.job_id = j.id AND s2.score IS NOT NULL
                ORDER BY s2.score DESC, s2.profile_id ASC
                LIMIT 1
              )
            LEFT JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
        """
        params: list = [self.user_id]
        if exclude_archived:
            query += " WHERE t.status IS NULL OR t.status != 'archived'"
        query += " ORDER BY COALESCE(s.score, -1) DESC, j.last_seen DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                if d.get("score") is None and d.get("tracked_status") is None:
                    d["status"] = "unscored"
                result.append(d)
            return result

    def get_all_for_tracker(self, profile_id: str, exclude_archived: bool = False,
                             limit: int | None = None) -> list[dict]:
        """All jobs for the Streamlit tracker — scored and unscored.
        Status and notes come from job_tracking (profile-independent).
        Company-level fields joined from companies table (Phase 4).
        """
        query = """SELECT j.id, j.company_id, j.title, j.company, j.url, j.source, j.location,
                          j.base_location, j.posted_date, j.description,
                          j.first_seen, j.last_seen, j.extracted_at, j.extracted_by,
                          j.monitored_company_id,
                          COALESCE(j.summary, '') AS summary,
                          COALESCE(j.work_mode, 'unknown') AS work_mode,
                          COALESCE(j.geo_zone, 'unknown') AS geo_zone,
                          COALESCE(c.company_size, 'unknown') AS company_size,
                          COALESCE(j.contract_type, 'unknown') AS contract_type,
                          COALESCE(c.company_country, 'unknown') AS company_country,
                          COALESCE(c.industry_sector, 'other') AS industry_sector,
                          COALESCE(j.language_required, 'unknown') AS language_required,
                          s.score, s.reason, s.scored_by,
                          COALESCE(s.country_code, j.country_code) AS country_code,
                          t.status AS tracked_status,
                          COALESCE(t.status, 'new') AS status, t.notes,
                          t.changed_at AS status_changed_at,
                          (SELECT COUNT(*) FROM job_scores WHERE job_id = j.id) AS score_count
                   FROM jobs j
                   LEFT JOIN companies c ON j.company_id = c.id
                   LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
                   LEFT JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                """
        params: list = [profile_id, self.user_id]
        if exclude_archived:
            query += " WHERE t.status IS NULL OR t.status != 'archived'"
        query += " ORDER BY COALESCE(s.score, -1) DESC, j.last_seen DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._conn() as conn:
            self._require_profile(conn, profile_id)
            rows = conn.execute(query, params).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                if d.get("score") is None and d.get("tracked_status") is None:
                    d["status"] = "unscored"
                result.append(d)
            return result


    # ------------------------------------------------------------------
    # Dashboard data
    # ------------------------------------------------------------------

    def get_dashboard_data(self) -> dict:
        """Return aggregate data for the Dashboard page widgets."""
        from datetime import date, timedelta

        today = date.today().isoformat()
        seven_days_ago = (date.today() - timedelta(days=7)).isoformat()
        fourteen_days_ago = (date.today() - timedelta(days=14)).isoformat()

        with self._conn() as conn:
            # Follow-ups due today
            follow_ups = [
                dict(r) for r in conn.execute(
                    """SELECT i.*, c.name AS company_name,
                              ct.full_name AS contact_name
                       FROM interactions i
                       JOIN companies c ON i.company_id = c.id
                       LEFT JOIN contacts ct ON i.contact_id = ct.id
                       WHERE i.user_id = ?
                         AND i.follow_up_due_at <= ?
                       ORDER BY i.follow_up_due_at ASC
                       LIMIT 20""",
                    (self.user_id, today),
                ).fetchall()
            ]

            # Recent inbound (7 days)
            recent_inbound = [
                dict(r) for r in conn.execute(
                    """SELECT i.*, c.name AS company_name,
                              ct.full_name AS contact_name
                       FROM interactions i
                       JOIN companies c ON i.company_id = c.id
                       LEFT JOIN contacts ct ON i.contact_id = ct.id
                       WHERE i.user_id = ?
                         AND i.direction = 'inbound'
                         AND i.occurred_at >= ?
                       ORDER BY i.occurred_at DESC
                       LIMIT 20""",
                    (self.user_id, seven_days_ago),
                ).fetchall()
            ]

            # Unverified contacts count
            unverified_count = conn.execute(
                "SELECT COUNT(*) FROM contacts WHERE is_unverified = 1 AND user_id = ?",
                (self.user_id,),
            ).fetchone()[0]

            # Stale active outreach (no interaction in 14 days)
            stale_outreach = [
                dict(r) for r in conn.execute(
                    """SELECT c.*,
                              COALESCE(uc.status, 'prospect')  AS status,
                              uc.notes                           AS notes,
                              COALESCE(uc.monitored, 0)          AS monitored,
                              last_ix.last_at AS last_interaction_at
                       FROM companies c
                       LEFT JOIN user_companies uc ON uc.company_id = c.id AND uc.user_id = ?
                       LEFT JOIN (
                           SELECT company_id, MAX(occurred_at) AS last_at
                           FROM interactions WHERE user_id = ? GROUP BY company_id
                       ) last_ix ON last_ix.company_id = c.id
                       WHERE COALESCE(uc.status, 'prospect') = 'active_outreach'
                         AND (last_ix.last_at IS NULL
                              OR last_ix.last_at < ?)
                       ORDER BY COALESCE(last_ix.last_at, c.last_seen_at) ASC
                       LIMIT 20""",
                    (self.user_id, self.user_id, fourteen_days_ago),
                ).fetchall()
            ]

        return {
            "follow_ups_due_today": follow_ups,
            "recent_inbound": recent_inbound,
            "unverified_contacts_count": unverified_count,
            "stale_active_outreach": stale_outreach,
        }


    # ------------------------------------------------------------------
    # Pipeline run log
    # ------------------------------------------------------------------

    def log_run(
        self,
        profile_id: str,
        jobs_scraped: int,
        jobs_scored: int,
        jobs_above_threshold: int,
        status: str,
        error_msg: str = None,
        duration_seconds: float = None,
        run_type: str = "full",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO runs
                   (ran_at, profile_id, jobs_scraped, jobs_scored, jobs_above_threshold, status, error_msg, duration_seconds, run_type)
                   VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?)""",
                (profile_id, jobs_scraped, jobs_scored, jobs_above_threshold, status, error_msg, duration_seconds, run_type),
            )

    def update_last_run(
        self,
        jobs_scored: int = None,
        jobs_above_threshold: int = None,
        status: str = None,
        duration_seconds: float = None,
    ) -> None:
        """Update the most recent run entry — called by score.py after scraping."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM runs ORDER BY ran_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return
            updates = []
            params = []
            if jobs_scored is not None:
                updates.append("jobs_scored = ?")
                params.append(jobs_scored)
            if jobs_above_threshold is not None:
                updates.append("jobs_above_threshold = ?")
                params.append(jobs_above_threshold)
            if status is not None:
                updates.append("status = ?")
                params.append(status)
            if duration_seconds is not None:
                updates.append("duration_seconds = ?")
                params.append(duration_seconds)
            if updates:
                params.append(row["id"])
                conn.execute(
                    f"UPDATE runs SET {', '.join(updates)} WHERE id = ?", params
                )

    def get_last_run(self, profile_id: str = None) -> dict | None:
        with self._conn() as conn:
            if profile_id:
                row = conn.execute(
                    "SELECT * FROM runs WHERE profile_id = ? ORDER BY ran_at DESC, id DESC LIMIT 1",
                    (profile_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM runs ORDER BY ran_at DESC, id DESC LIMIT 1"
                ).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Catalogue stats + contact role breakdown (spec 037)
    # ------------------------------------------------------------------

    def get_database_stats(self) -> dict:
        """Catalogue counts plus user-scoped activity counts (dashboard + settings).

        ``total_jobs``/``total_companies`` are shared catalogue; ``total_scores``
        and ``distinct_scored`` count only the bound user's profiles (``job_scores``
        is user space — spec 041).
        """
        with self._conn() as conn:
            total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            total_scores = conn.execute(
                """SELECT COUNT(*)
                   FROM job_scores s
                   JOIN search_profiles p ON p.id = s.profile_id AND p.user_id = ?""",
                (self.user_id,),
            ).fetchone()[0]
            distinct_scored = conn.execute(
                """SELECT COUNT(DISTINCT s.job_id)
                   FROM job_scores s
                   JOIN search_profiles p ON p.id = s.profile_id AND p.user_id = ?""",
                (self.user_id,),
            ).fetchone()[0]
            total_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
            total_contacts = conn.execute(
                "SELECT COUNT(*) FROM contacts WHERE user_id = ?", (self.user_id,)
            ).fetchone()[0]
            unverified_contacts = conn.execute(
                "SELECT COUNT(*) FROM contacts WHERE is_unverified = 1 AND user_id = ?",
                (self.user_id,),
            ).fetchone()[0]
            total_interactions = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE user_id = ?", (self.user_id,)
            ).fetchone()[0]
        return {
            "total_jobs": total_jobs,
            "total_scores": total_scores,
            "distinct_scored": distinct_scored,
            "total_companies": total_companies,
            "total_contacts": total_contacts,
            "unverified_contacts": unverified_contacts,
            "total_interactions": total_interactions,
        }

    def get_contacts_by_role_family(self) -> list[tuple[str, int]]:
        """Contacts grouped by role_family, most common first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT role_family, COUNT(*) FROM contacts WHERE user_id = ? GROUP BY role_family ORDER BY COUNT(*) DESC",
                (self.user_id,),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    _ALLOWED_CONTACT_FIELDS = frozenset({
        "is_unverified", "last_verified_at", "is_current", "notes",
    })

    def update_contact_fields(self, contact_id: int, fields: dict) -> None:
        """Update allowlisted contact columns. Mirrors ``update_company_fields``."""
        invalid = set(fields) - self._ALLOWED_CONTACT_FIELDS
        if invalid:
            raise ValueError(f"Unknown contact fields: {invalid}")
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [contact_id, self.user_id]
        with self._conn() as conn:
            conn.execute(f"UPDATE contacts SET {cols} WHERE id = ? AND user_id = ?", vals)

    def bulk_mark_contacts_verified(self, contact_ids: list[int], last_verified_at: str) -> None:
        """Mark many contacts verified in a single transaction."""
        if not contact_ids:
            return
        with self._conn() as conn:
            for cid in contact_ids:
                conn.execute(
                    "UPDATE contacts SET is_unverified = 0, last_verified_at = ? WHERE id = ? AND user_id = ?",
                    (last_verified_at, cid, self.user_id),
                )

    def get_tracking_status(self, job_id: str) -> str | None:
        """Current tracking status for a job, or ``None`` when untracked."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM job_tracking WHERE job_id = ? AND user_id = ?",
                (job_id, self.user_id),
            ).fetchone()
        return row["status"] if row else None

    def get_contacts_discovered_for_job(self, job_id: str) -> list[dict]:
        """Contacts linked to a job via ``discovered_on_posting`` interactions."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT DISTINCT ct.* FROM contacts ct
                   JOIN interactions i ON i.contact_id = ct.id
                   WHERE i.job_id = ? AND i.user_id = ? AND ct.user_id = ?
                     AND i.type = 'discovered_on_posting'
                   ORDER BY ct.last_seen_at DESC""",
                (job_id, self.user_id, self.user_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_calibration_signals(self, profile_id: str) -> dict:
        """Read-only calibration signals for ``context_tuner`` (spec 037).

        Returns ``over_scored`` / ``under_scored`` (tuples), ``score_buckets``
        (``(label, relevant, true_negative)``), and ``top_sectors`` /
        ``top_countries`` — all derived from archived/applied cohorts.
        """
        with self._conn() as conn:
            conn.row_factory = None  # plain tuples, matching the original caller
            self._require_profile(conn, profile_id)

            over_rows = conn.execute(
                """SELECT j.title, j.company, s.score, t.notes
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                     JOIN job_scores s   ON j.id = s.job_id
                    WHERE t.status = 'archived' AND s.score >= 8
                      AND s.profile_id = ?
                      AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)
                    ORDER BY s.score DESC
                    LIMIT 10""",
                (self.user_id, profile_id),
            ).fetchall()

            under_rows = conn.execute(
                """SELECT j.title, j.company, s.score, t.status
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                     JOIN job_scores s   ON j.id = s.job_id
                    WHERE t.status IN ('applied', 'interviewing', 'offer', 'rejected', 'withdrawn') AND s.score <= 6
                      AND s.profile_id = ?
                    ORDER BY s.score ASC
                    LIMIT 10""",
                (self.user_id, profile_id),
            ).fetchall()

            buckets = [
                ("9-10", "s.score >= 9"),
                ("7-8",  "s.score BETWEEN 7 AND 8"),
                ("5-6",  "s.score BETWEEN 5 AND 6"),
                ("0-4",  "s.score <= 4"),
            ]
            score_buckets = []
            for label, cond in buckets:
                rel = conn.execute(
                    f"""SELECT COUNT(*) FROM jobs j
                         JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                         JOIN job_scores s ON j.id = s.job_id
                        WHERE t.status IN ('applied', 'interviewing', 'offer', 'rejected', 'withdrawn')
                          AND s.profile_id = ?
                          AND {cond}
                          AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)""",
                    (self.user_id, profile_id),
                ).fetchone()[0]
                tn = conn.execute(
                    f"""SELECT COUNT(*) FROM jobs j
                         JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                         JOIN job_scores s ON j.id = s.job_id
                        WHERE t.status = 'archived'
                          AND s.profile_id = ?
                          AND {cond}
                          AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)""",
                    (self.user_id, profile_id),
                ).fetchone()[0]
                score_buckets.append((label, rel, tn))

            sector_over = conn.execute(
                """SELECT COALESCE(c.industry_sector, 'other'), COUNT(*) AS n
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                     JOIN job_scores s ON j.id = s.job_id
                     LEFT JOIN companies c ON j.company_id = c.id
                    WHERE t.status = 'archived' AND s.score >= 8
                      AND s.profile_id = ?
                      AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)
                    GROUP BY 1 ORDER BY n DESC LIMIT 5""",
                (self.user_id, profile_id),
            ).fetchall()

            country_over = conn.execute(
                """SELECT COALESCE(c.company_country, 'unknown'), COUNT(*) AS n
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                     JOIN job_scores s ON j.id = s.job_id
                     LEFT JOIN companies c ON j.company_id = c.id
                    WHERE t.status = 'archived' AND s.score >= 8
                      AND s.profile_id = ?
                      AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)
                    GROUP BY 1 ORDER BY n DESC LIMIT 5""",
                (self.user_id, profile_id),
            ).fetchall()

        return {
            "over_scored": over_rows,
            "under_scored": under_rows,
            "score_buckets": score_buckets,
            "top_sectors": sector_over,
            "top_countries": country_over,
        }

    def delete_scores_for_profile(self, profile_id: str) -> int:
        """Delete all scores for a profile (does NOT touch ``search_profiles``).

        Returns the number of rows deleted.
        """
        with self._conn() as conn:
            self._require_profile(conn, profile_id)
            conn.execute("DELETE FROM job_scores WHERE profile_id = ?", (profile_id,))
            return conn.execute("SELECT changes()").fetchone()[0]

    def get_best_score_profile_id(self, job_id: str) -> str | None:
        """The bound user's profile_id with the highest score for a job, or ``None``."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT s.profile_id
                   FROM job_scores s
                   JOIN search_profiles p ON p.id = s.profile_id AND p.user_id = ?
                   WHERE s.job_id = ? AND s.score IS NOT NULL
                   ORDER BY s.score DESC LIMIT 1""",
                (self.user_id, job_id),
            ).fetchone()
        return row["profile_id"] if row else None

    def get_export_seed_companies(self) -> list[dict]:
        """Companies suitable for git-versioned seed export (excludes env fields)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT name, website, careers_url, ats_provider, ats_identifier,
                          scraping_method, monitoring_status,
                          research_notes, research_confidence, x_handle,
                          industry_sector, company_country
                   FROM companies
                   WHERE monitoring_status != 'unmonitored'
                      OR ats_provider IS NOT NULL
                      OR scraper_id IS NOT NULL
                   ORDER BY name"""
            ).fetchall()
        return [dict(r) for r in rows]

    def get_jobs_missing_description(self) -> list[dict]:
        """Jobs with a NULL description, for the backfill-descriptions worker."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, url, source FROM jobs WHERE description IS NULL ORDER BY source, last_seen DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_job_description(self, job_id: str, description: str) -> None:
        """Store a fetched description for a job."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET description = ? WHERE id = ?",
                (description, job_id),
            )

    # ------------------------------------------------------------------
    # Preference report + action items (spec 037)
    # ------------------------------------------------------------------

    def get_cohort_jobs(self, statuses: list[str]) -> list[dict]:
        """Jobs in the given tracking statuses, with company/cohort columns."""
        placeholders = ",".join("?" * len(statuses))
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT j.id, j.title, j.company, j.company_id, j.url,
                           t.status, t.notes,
                           COALESCE(c.industry_sector, 'other') AS industry_sector,
                           COALESCE(j.work_mode, 'unknown')     AS work_mode,
                           COALESCE(j.geo_zone, 'unknown')      AS geo_zone,
                           COALESCE(c.company_country, 'unknown') AS company_country,
                           COALESCE(j.language_required, 'unknown') AS language_required,
                           COALESCE(c.company_size, 'unknown')  AS company_size
                      FROM jobs j
                      JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                      LEFT JOIN companies c ON j.company_id = c.id
                     WHERE t.status IN ({placeholders})""",
                [self.user_id, *statuses],
            ).fetchall()
        return [dict(r) for r in rows]

    def get_applications_count(self) -> int:
        """Number of prepared applications (rows in ``job_applications``)."""
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM job_applications WHERE user_id = ?", (self.user_id,)
            ).fetchone()[0]

    def get_classified_jobs(self, profile_id: str) -> tuple:
        """Per-profile cohort classification for the preference report.

        Returns ``(true_positive, llm_under_scored, true_negative, filter_cleanup)``.
        """
        with self._conn() as conn:
            self._require_profile(conn, profile_id)
            rows = conn.execute(
                """SELECT j.id, j.title, j.company, j.company_id, j.url,
                          t.status, t.notes,
                          COALESCE(c.industry_sector, 'other') AS industry_sector,
                          COALESCE(j.work_mode, 'unknown')     AS work_mode,
                          COALESCE(j.geo_zone, 'unknown')      AS geo_zone,
                          COALESCE(c.company_country, 'unknown') AS company_country,
                          COALESCE(j.language_required, 'unknown') AS language_required,
                          COALESCE(c.company_size, 'unknown')  AS company_size,
                          s.score, s.reason AS score_reason, s.scored_by
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                     LEFT JOIN companies c ON j.company_id = c.id
                     LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
                    WHERE t.status IN ('applied', 'interviewing', 'offer', 'rejected', 'withdrawn', 'archived')""",
                (self.user_id, profile_id),
            ).fetchall()

        tp, us, tn, fc = [], [], [], []
        for r in rows:
            d = dict(r)
            status = d["status"]
            sb = (d.get("scored_by") or "")
            score = d.get("score")
            is_tier0_reject = sb.startswith("tier_0") and score is not None and score <= 3

            if status in ("applied", "interviewing", "offer", "rejected", "withdrawn"):
                if is_tier0_reject:
                    us.append(d)
                else:
                    tp.append(d)
            else:  # archived
                if is_tier0_reject:
                    fc.append(d)
                else:
                    tn.append(d)

        assert len(tp) + len(us) + len(tn) + len(fc) == len(rows), \
            "cohort classification is not exhaustive"
        return tp, us, tn, fc

    def get_company_sector(self, company_name: str) -> str:
        """industry_sector for a company by name, defaulting to ``"other"``."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT industry_sector FROM companies WHERE name = ?", (company_name,)
            ).fetchone()
        return (row["industry_sector"] or "other") if row else "other"

    def get_calibration_deltas(self) -> dict:
        """Global over-scored / under-scored jobs for the calibration-deltas section."""
        with self._conn() as conn:
            over_rows = conn.execute(
                """SELECT j.title, j.company, s.score, s.profile_id, t.notes, s.scored_by
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                     JOIN job_scores s   ON j.id = s.job_id
                    WHERE t.status = 'archived' AND s.score >= 8
                    ORDER BY s.score DESC
                    LIMIT 15""",
                (self.user_id,),
            ).fetchall()

            under_rows = conn.execute(
                """SELECT j.title, j.company, s.score, s.profile_id, t.status
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                     JOIN job_scores s   ON j.id = s.job_id
                    WHERE t.status IN ('applied', 'interviewing', 'offer', 'rejected', 'withdrawn') AND s.score <= 6
                    ORDER BY s.score ASC""",
                (self.user_id,),
            ).fetchall()

        return {
            "over_scored": [dict(r) for r in over_rows],
            "under_scored": [dict(r) for r in under_rows],
        }

    def get_archived_notes(self) -> list[dict]:
        """Non-empty archive notes, for the archive-themes word-frequency section."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT notes FROM job_tracking WHERE status = 'archived' AND notes IS NOT NULL AND notes != '' AND user_id = ?",
                (self.user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_few_shot_anchors(self, profile_id: str) -> dict:
        """Strong-match (positive) and clear-reject (negative) anchor rows for a profile."""
        with self._conn() as conn:
            self._require_profile(conn, profile_id)
            pos_rows = conn.execute(
                """SELECT j.title, j.company, COALESCE(c.industry_sector, 'other') AS sector,
                          COALESCE(j.work_mode, 'unknown') AS work_mode,
                          COALESCE(c.company_country, 'unknown') AS country,
                          s.score, s.reason AS score_reason
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                     JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
                     LEFT JOIN companies c ON j.company_id = c.id
                    WHERE t.status IN ('applied', 'interviewing', 'offer', 'rejected', 'withdrawn', 'ready', 'queued')
                      AND s.score IS NOT NULL
                    ORDER BY s.score DESC""",
                (self.user_id, profile_id),
            ).fetchall()

            neg_rows = conn.execute(
                """SELECT j.title, j.company, COALESCE(c.industry_sector, 'other') AS sector,
                          COALESCE(j.work_mode, 'unknown') AS work_mode,
                          COALESCE(c.company_country, 'unknown') AS country,
                          s.score, s.reason AS score_reason, t.notes
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                     JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
                     LEFT JOIN companies c ON j.company_id = c.id
                    WHERE t.status = 'archived'
                      AND s.score IS NOT NULL
                    ORDER BY s.score ASC""",
                (self.user_id, profile_id),
            ).fetchall()

        return {
            "pos": [dict(r) for r in pos_rows],
            "neg": [dict(r) for r in neg_rows],
        }

    def get_action_item_candidates(self, profile_id: str) -> dict:
        """Calibration + denylist/sector/country/source signals for ``generate_action_items``."""
        with self._conn() as conn:
            self._require_profile(conn, profile_id)
            over_rows = conn.execute(
                """SELECT j.title, j.company, s.score, t.notes, s.profile_id
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                     JOIN job_scores s   ON j.id = s.job_id
                    WHERE t.status = 'archived' AND s.score >= 8
                      AND s.profile_id = ?
                      AND NOT (s.scored_by LIKE 'tier_0%%' AND s.score <= 3)
                    ORDER BY s.score DESC""",
                (self.user_id, profile_id),
            ).fetchall()

            under_rows = conn.execute(
                """SELECT j.title, j.company, s.score, t.status, s.profile_id
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                     JOIN job_scores s   ON j.id = s.job_id
                    WHERE t.status IN ('applied', 'interviewing', 'offer', 'rejected', 'withdrawn') AND s.score <= 6
                      AND s.profile_id = ?
                    ORDER BY s.score ASC""",
                (self.user_id, profile_id),
            ).fetchall()

            deny_rows = conn.execute(
                """SELECT j.company, COUNT(*) AS n
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                    WHERE t.status = 'archived'
                      AND j.company_id IS NOT NULL
                      AND j.company NOT IN (
                          SELECT DISTINCT j2.company FROM jobs j2
                          JOIN job_tracking t2 ON j2.id = t2.job_id AND t2.user_id = ?
                          WHERE t2.status IN ('applied', 'interviewing', 'offer', 'rejected', 'withdrawn')
                      )
                    GROUP BY j.company
                    HAVING COUNT(*) >= 3
                    ORDER BY n DESC""",
                (self.user_id, self.user_id),
            ).fetchall()

            sector_rows = conn.execute(
                """SELECT COALESCE(c.industry_sector, 'other') AS sector, COUNT(*) AS n
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                     LEFT JOIN companies c ON j.company_id = c.id
                    WHERE t.status = 'archived'
                      AND COALESCE(c.industry_sector, 'other') NOT IN (
                          SELECT DISTINCT COALESCE(c2.industry_sector, 'other')
                          FROM jobs j2
                          JOIN job_tracking t2 ON j2.id = t2.job_id AND t2.user_id = ?
                          LEFT JOIN companies c2 ON j2.company_id = c2.id
                          WHERE t2.status IN ('applied', 'interviewing', 'offer', 'rejected', 'withdrawn')
                      )
                    GROUP BY 1
                    HAVING COUNT(*) >= 5
                    ORDER BY n DESC""",
                (self.user_id, self.user_id),
            ).fetchall()

            country_rows = conn.execute(
                """SELECT COALESCE(c.company_country, 'unknown') AS country, COUNT(*) AS n
                     FROM jobs j
                     JOIN job_tracking t ON j.id = t.job_id AND t.user_id = ?
                     LEFT JOIN companies c ON j.company_id = c.id
                    WHERE t.status = 'archived'
                      AND c.company_country IS NOT NULL
                      AND c.company_country != 'unknown'
                      AND c.company_country NOT IN (
                          SELECT DISTINCT c2.company_country FROM jobs j2
                          JOIN job_tracking t2 ON j2.id = t2.job_id AND t2.user_id = ?
                          LEFT JOIN companies c2 ON j2.company_id = c2.id
                          WHERE t2.status IN ('applied', 'interviewing', 'offer', 'rejected', 'withdrawn')
                            AND c2.company_country IS NOT NULL
                      )
                    GROUP BY 1
                    HAVING COUNT(*) >= 3
                    ORDER BY n DESC""",
                (self.user_id, self.user_id),
            ).fetchall()

            source_rows = conn.execute(
                """SELECT j.source, COUNT(*) AS total,
                          COUNT(CASE WHEN s.score >= 8 THEN 1 END) AS strong
                     FROM jobs j
                     LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
                    WHERE j.source IS NOT NULL AND j.source != ''
                    GROUP BY j.source
                    HAVING total >= 5 AND strong = 0
                    ORDER BY total DESC""",
                (profile_id,),
            ).fetchall()

        return {
            "over_scored": [dict(r) for r in over_rows],
            "under_scored": [dict(r) for r in under_rows],
            "denylist": [dict(r) for r in deny_rows],
            "sectors": [dict(r) for r in sector_rows],
            "countries": [dict(r) for r in country_rows],
            "sources": [dict(r) for r in source_rows],
        }

    # ------------------------------------------------------------------
    # Catalogue read methods (shared facts — no user-space fields) — spec 041
    # ------------------------------------------------------------------

    _CATALOGUE_JOB_COLS = """
        j.id, j.title, j.company, j.company_id, j.url, j.source,
        j.location, j.base_location, j.posted_date, j.description,
        j.first_seen, j.last_seen, j.extracted_at, j.extracted_by,
        j.monitored_company_id,
        j.canonical_url, j.norm_title, j.norm_company,
        COALESCE(j.summary, '') AS summary,
        COALESCE(j.work_mode, 'unknown') AS work_mode,
        COALESCE(j.geo_zone, 'unknown') AS geo_zone,
        COALESCE(c.company_size, 'unknown') AS company_size,
        COALESCE(j.contract_type, 'unknown') AS contract_type,
        COALESCE(c.company_country, 'unknown') AS company_country,
        COALESCE(c.industry_sector, 'other') AS industry_sector,
        COALESCE(j.language_required, 'unknown') AS language_required,
        j.country_code
    """

    def get_catalogue_jobs(self, *, company_id: int | None = None,
                           source: str | None = None,
                           posted_after: str | None = None) -> list[dict]:
        """Shared-catalogue job facts, no user-space fields (spec 041, US3-3).

        Optional AND-ed filters: `company_id` (int), `source` (exact),
        `posted_after` (ISO date, keeps rows with posted_date >= it). Ordered
        `last_seen DESC`. Pagination is the API layer's job."""
        where = []
        params: list = []
        if company_id is not None:
            where.append("j.company_id = ?")
            params.append(company_id)
        if source is not None:
            where.append("j.source = ?")
            params.append(source)
        if posted_after is not None:
            where.append("j.posted_date >= ?")
            params.append(posted_after)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {self._CATALOGUE_JOB_COLS} "
                f"FROM jobs j LEFT JOIN companies c ON j.company_id = c.id"
                f"{where_sql} ORDER BY j.last_seen DESC",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_catalogue_job(self, job_id: str) -> dict | None:
        """Shared-catalogue facts for one job (no user-space fields), or None."""
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT {self._CATALOGUE_JOB_COLS} "
                f"FROM jobs j LEFT JOIN companies c ON j.company_id = c.id "
                f"WHERE j.id = ?",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_company_facts(self, company_id: int) -> dict | None:
        """Shared company facts only — no user-space coalesces/aggregates (US3-3)."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT c.id, c.name, c.website, c.careers_url,
                          c.company_country, c.industry_sector, c.company_size,
                          c.ats_provider, c.ats_identifier, c.scraper_id,
                          c.first_seen_at, c.last_seen_at, c.summary,
                          c.monitoring_status, c.scraping_method, c.research_notes,
                          c.research_confidence, c.researched_at, c.x_handle
                   FROM companies c
                   WHERE c.id = ?""",
                (company_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_user(self) -> dict | None:
        """The bound user's `users` row (id, display_name, active_profile_id,
        onboarding_completed_at), or None."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id, display_name, active_profile_id, onboarding_completed_at
                   FROM users WHERE id = ?""",
                (self.user_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_user_scores_for_job(self, job_id: str) -> list[dict]:
        """Scores for a job restricted to the bound user's profiles, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT s.profile_id, s.score, s.reason, s.scored_by, s.scored_at
                   FROM job_scores s
                   JOIN search_profiles p ON p.id = s.profile_id AND p.user_id = ?
                   WHERE s.job_id = ?
                   ORDER BY s.scored_at DESC""",
                (self.user_id, job_id),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # API tokens — scoped read access (spec 041). CLI-only create/list/revoke.
    # ------------------------------------------------------------------

    def create_api_token(self, name: str, scopes: list[str],
                         user_id: str | None = None,
                         expires_at: str | None = None) -> str:
        """Generate a raw token, store its SHA-256 hash, and return the raw token.

        The raw token is returned exactly once and never persisted. Unknown
        scopes raise `ValueError`; `user_id` defaults to `self.user_id`."""
        unknown = [s for s in scopes if s not in API_TOKEN_SCOPES]
        if unknown:
            raise ValueError(f"Unknown scope(s): {', '.join(unknown)}")
        raw = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        owner = user_id if user_id is not None else self.user_id
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO api_tokens
                   (id, user_id, name, token_hash, scopes, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, owner, name, token_hash,
                 json.dumps(scopes), _now(), expires_at),
            )
        return raw

    def list_api_tokens(self, user_id: str | None = None) -> list[dict]:
        """Token rows (no secret material), newest first. `user_id=None` → all users."""
        with self._conn() as conn:
            if user_id is None:
                rows = conn.execute(
                    """SELECT id, user_id, name, scopes, created_at, last_used_at,
                              revoked_at, expires_at
                       FROM api_tokens ORDER BY created_at DESC"""
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, user_id, name, scopes, created_at, last_used_at,
                              revoked_at, expires_at
                       FROM api_tokens WHERE user_id = ? ORDER BY created_at DESC""",
                    (user_id,),
                ).fetchall()
            return [dict(r) for r in rows]

    def revoke_api_token(self, token_id: str) -> None:
        """Revoke a token by id (set revoked_at = now). Unknown id → ValueError.

        Idempotent: revoking an already-revoked token succeeds (no-op write)."""
        with self._conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM api_tokens WHERE id = ?", (token_id,)
            ).fetchone()
            if exists is None:
                raise ValueError(f"Unknown token id: {token_id}")
            conn.execute(
                "UPDATE api_tokens SET revoked_at = ? WHERE id = ?",
                (_now(), token_id),
            )

    def resolve_api_token(self, token: str) -> dict | None:
        """Hash + lookup a raw token; return its row (or None when unknown).

        Returns `{id, user_id, name, scopes, revoked_at, expires_at, last_used_at}`
        with `scopes` parsed to a list. Callers decide 401/403."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id, user_id, name, scopes, revoked_at, expires_at, last_used_at
                   FROM api_tokens WHERE token_hash = ?""",
                (token_hash,),
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["scopes"] = json.loads(d["scopes"]) if d["scopes"] else []
        except (TypeError, ValueError):
            d["scopes"] = []
        return d

    def touch_api_token(self, token_id: str) -> None:
        """Set last_used_at = now (throttled by the API layer, FR-005)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
                (_now(), token_id),
            )


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_prefix(ts) -> str:
    """Return the YYYY-MM-DD prefix of an ISO timestamp/date string."""
    return (ts or "")[:10]


def _fmt_dm(ts) -> str:
    """Format an ISO date/timestamp prefix as dd/mm (e.g. '14/10')."""
    d = _date_prefix(ts)
    return f"{d[8:10]}/{d[5:7]}" if len(d) == 10 else d


def _lifecycle_event_label(events: list[dict], i: int) -> str:
    """Human label for events[i], given the job's full chronological event list (FR-020)."""
    e = events[i]
    t = e["type"]
    if t == "application_submitted":
        return "Applied"
    if t == "interview_invited":
        return "Interview invited"
    if t == "interview":
        round_no = sum(1 for x in events[: i + 1] if x["type"] == "interview")
        return f"Interview · round {round_no}"

    prior = events[:i]
    edate = _date_prefix(e["occurred_at"])
    interviews_before = sum(
        1 for x in prior
        if x["type"] == "interview" and _date_prefix(x["occurred_at"]) <= edate
    )
    prior_offer = any(
        x["type"] == "decision_received" and x["outcome"] == "positive" for x in prior
    )
    suffix = "s" if interviews_before != 1 else ""

    if t == "decision_received":
        if e["outcome"] == "positive":
            return "Offer"
        if prior_offer:
            return "Rejected — offer withdrawn by employer"
        if interviews_before == 0:
            return "Rejected — no interview"
        return f"Rejected — after {interviews_before} interview{suffix}"
    # withdrawn
    if prior_offer:
        return "Withdrawn — offer declined"
    if interviews_before == 0:
        return "Withdrawn — no interview"
    return f"Withdrawn — after {interviews_before} interview{suffix}"
