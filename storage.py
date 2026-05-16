"""
storage.py — SQLite persistence layer for job_agent
=====================================================
Schéma 3 tables :
  - jobs          : données brutes, jamais rescorées, partagées entre profils
  - search_profiles : définition des profils (criteria JSON)
  - job_scores    : scoring par job × profil, statut de suivi

Usage typique dans main.py :
    db = JobStorage("data/jobs.db")
    db.upsert_profile(profile)
    new, cached = db.split_new_cached(all_jobs, profile.id)
    # scorer uniquement `new`
    db.save_scored(job, result, profile.id)
    db.touch_many([j["id"] for j in cached])
"""

import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

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
    "application_submitted", "interview", "decision_received",
    "note",
})
INTERACTION_DIRECTIONS = frozenset({"inbound", "outbound", "none"})
INTERACTION_OUTCOMES = frozenset({"positive", "negative"})

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

-- Company status change history — append-only
CREATE TABLE IF NOT EXISTS company_status_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  INTEGER NOT NULL,
    status      TEXT NOT NULL,
    note        TEXT,
    changed_at  TEXT NOT NULL,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);
CREATE INDEX IF NOT EXISTS idx_company_status_history ON company_status_history (company_id, changed_at DESC);

-- Données brutes des offres — une ligne par job, partagée entre profils
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    company       TEXT,
    company_id    INTEGER,
    url           TEXT,
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

-- Pipeline status — one row per job, profile-independent
CREATE TABLE IF NOT EXISTS job_tracking (
    job_id     TEXT PRIMARY KEY,
    status     TEXT NOT NULL DEFAULT 'new',
    notes      TEXT,
    changed_at TEXT
);

-- Status change history — one row per transition, append-only
CREATE TABLE IF NOT EXISTS status_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL,
    status     TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE INDEX IF NOT EXISTS idx_status_history_job ON status_history (job_id, changed_at DESC);

-- Application content — one row per job
CREATE TABLE IF NOT EXISTS job_applications (
    job_id              TEXT PRIMARY KEY,
    profile_id          TEXT,
    analysis            TEXT,
    cover_letter        TEXT,
    cv_bullets_selected  TEXT,
    company_research     TEXT,
    screening_answers    TEXT,
    language             TEXT,
    prepared_by          TEXT,
    prepared_at          TEXT,
    created_at           TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_last_seen  ON jobs (last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_scores_profile  ON job_scores (profile_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_tracking_status ON job_tracking (status);

-- Key-value config store (active_profile_id, etc.)
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Contacts — people discovered at companies (recruiters, hiring managers, etc.)
CREATE TABLE IF NOT EXISTS contacts (
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
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_linkedin
    ON contacts (linkedin_url) WHERE linkedin_url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contacts_company    ON contacts (company_id);
CREATE INDEX IF NOT EXISTS idx_contacts_email      ON contacts (email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contacts_unverified ON contacts (is_unverified) WHERE is_unverified = 1;

-- Interactions — timeline of touchpoints with companies/contacts/jobs
CREATE TABLE IF NOT EXISTS interactions (
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
    FOREIGN KEY (company_id) REFERENCES companies(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    FOREIGN KEY (job_id)     REFERENCES jobs(id)
);
CREATE INDEX IF NOT EXISTS idx_interactions_company  ON interactions (company_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_interactions_contact  ON interactions (contact_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_interactions_job      ON interactions (job_id);
CREATE INDEX IF NOT EXISTS idx_interactions_followup ON interactions (follow_up_due_at)
    WHERE follow_up_due_at IS NOT NULL;
"""


# ---------------------------------------------------------------------------
# JobStorage
# ---------------------------------------------------------------------------

class JobStorage:

    def __init__(self, db_path: str = "data/jobs.db"):
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
                from profiles import ALL_PROFILES
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

            # Phase 5 — drop company-level columns from jobs (SQLite 3.35+)
            jobs_cols_current = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            for col in ("company_country", "industry_sector", "company_size"):
                if col in jobs_cols_current:
                    try:
                        conn.execute(f"ALTER TABLE jobs DROP COLUMN {col}")
                        logger.info(f"[Storage] Phase 5 cleanup: dropped {col} from jobs")
                    except sqlite3.OperationalError:
                        logger.warning(
                            f"[Storage] Phase 5: cannot drop {col} (SQLite < 3.35) — "
                            f"column left as dead weight"
                        )

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

        logger.debug(f"[Storage] DB ready at {self.db_path}")

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
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Gestion des profils
    # ------------------------------------------------------------------

    def upsert_profile(self, profile) -> None:
        """Insère ou met à jour un profil de recherche."""
        criteria = json.dumps(profile.to_criteria_dict())
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO search_profiles (id, name, criteria) VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name = excluded.name, criteria = excluded.criteria""",
                (profile.id, profile.name, criteria),
            )

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------

    def get_score_result(self, job_id: str, profile_id: str) -> Optional[dict]:
        """Retourne le dict de scoring pour ce job+profil, None si absent."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT s.score, s.reason, s.scored_by,
                          COALESCE(j.summary, '') AS summary,
                          COALESCE(j.work_mode, 'unknown') AS work_mode,
                          COALESCE(j.geo_zone, 'unknown') AS geo_zone,
                          COALESCE(c.company_size, 'unknown') AS company_size,
                          COALESCE(j.contract_type, 'unknown') AS contract_type,
                          COALESCE(c.company_country, 'unknown') AS company_country,
                          COALESCE(c.industry_sector, 'other') AS industry_sector,
                          COALESCE(j.language_required, 'unknown') AS language_required
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
                       (name, name_normalized, website, careers_url, status,
                        first_seen_at, last_seen_at, created_at)
                       VALUES (?, ?, ?, ?, 'prospect', ?, ?, ?)""",
                    (name, name_norm, website, careers_url, now, now, now))
                return conn.execute(
                    "SELECT id FROM companies WHERE name_normalized = ?",
                    (name_norm,)).fetchone()["id"]

    def set_company_status(self, company_id: int, status: str,
                           note: str | None = None) -> None:
        """Update a company's status and record the transition."""
        if status not in COMPANY_STATUSES:
            raise ValueError(f"Invalid company status: {status!r}. Valid: {COMPANY_STATUSES}")
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "UPDATE companies SET status = ? WHERE id = ?",
                (status, company_id))
            conn.execute(
                """INSERT INTO company_status_history
                   (company_id, status, note, changed_at)
                   VALUES (?, ?, ?, ?)""",
                (company_id, status, note, now))

    def _upsert_job_raw(self, job, conn, now: str, company_id: int | None = None) -> None:
        """Insère ou met à jour la table jobs (données brutes uniquement)."""
        conn.execute(
            """INSERT INTO jobs (
                   id, title, company, company_id, url, source, location, base_location,
                   posted_date, description, first_seen, last_seen
               ) VALUES (
                   :id, :title, :company, :company_id, :url, :source, :location, :base_location,
                   :posted_date, :description, :now, :now
               )
               ON CONFLICT(id) DO UPDATE SET
                   last_seen  = excluded.last_seen,
                   base_location = excluded.base_location,
                   company_id = COALESCE(jobs.company_id, excluded.company_id)""",
            {
                "id":            job.id,
                "title":         job.title,
                "company":       getattr(job, "company", None),
                "company_id":    company_id,
                "url":           getattr(job, "url", None),
                "source":        getattr(job, "source", None),
                "location":      getattr(job, "location", None),
                "base_location": getattr(job, "base_location", None),
                "posted_date":   str(getattr(job, "posted_date", "") or ""),
                "description":   getattr(job, "description", None),
                "now":           now,
            },
        )

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
        params: list = [profile_id]

        _company_fields = (
            "COALESCE(c.company_country, 'unknown') AS company_country,"
            "COALESCE(c.industry_sector, 'other') AS industry_sector,"
            "COALESCE(c.company_size, 'unknown') AS company_size"
        )
        if rescore:
            base = (f"SELECT j.*, {_company_fields} FROM jobs j"
                    " LEFT JOIN companies c ON j.company_id = c.id"
                    " LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?"
                    " LEFT JOIN job_tracking t ON j.id = t.job_id")
            clauses.append("(t.status IS NULL OR t.status NOT IN ('rejected', 'archived'))")
        else:
            base = (f"SELECT j.*, {_company_fields} FROM jobs j"
                    " LEFT JOIN companies c ON j.company_id = c.id"
                    " LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?"
                    " LEFT JOIN job_tracking t ON j.id = t.job_id")
            clauses.append("(s.job_id IS NULL OR s.score IS NULL)")
            clauses.append("(t.status IS NULL OR t.status NOT IN ('rejected', 'archived'))")
        clauses.append("(c.id IS NULL OR c.status != 'blacklisted')")

        if pre_filter:
            loc_kws = pre_filter.get("location_contains", [])
            if loc_kws:
                # Any keyword matching location OR base_location is sufficient; NULL base_location is ignored
                or_parts = " OR ".join(
                    "(LOWER(j.location) LIKE ? OR (j.base_location IS NOT NULL AND LOWER(j.base_location) LIKE ?))"
                    for _ in loc_kws
                )
                clauses.append(f"({or_parts})")
                for kw in loc_kws:
                    params += [f"%{kw.lower()}%", f"%{kw.lower()}%"]

            excl_loc = pre_filter.get("exclude_location_contains", [])
            if excl_loc:
                not_parts = " AND ".join(
                    "(LOWER(j.location) NOT LIKE ? AND (j.base_location IS NULL OR LOWER(j.base_location) NOT LIKE ?))"
                    for _ in excl_loc
                )
                clauses.append(f"({not_parts})")
                for kw in excl_loc:
                    params += [f"%{kw.lower()}%", f"%{kw.lower()}%"]

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

        clauses.append(
            "(j.posted_date >= date('now', '-30 days') OR j.posted_date IS NULL OR j.posted_date = '')"
        )
        where = " AND ".join(clauses)
        query = f"{base} WHERE {where}"

        with self._conn() as conn:
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
                       contract_type = ?,
                       summary = ?,
                       extracted_at = ?,
                       extracted_by = ?
                 WHERE id = ?""",
                (
                    fields.get("language_required", "unknown"),
                    fields.get("work_mode", "unknown"),
                    fields.get("geo_zone", "unknown"),
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

    def _update_job_extraction_fields(self, job, score_result: dict, conn, now: str,
                                       company_id: int | None = None) -> None:
        """Write job-level structured fields to jobs table, company fields to companies."""
        extracted_by = getattr(job, "extracted_by", None) or score_result.get("scored_by")
        conn.execute(
            """UPDATE jobs SET
                   summary = ?, work_mode = ?, geo_zone = ?,
                   contract_type = ?, language_required = ?,
                   extracted_at = ?, extracted_by = ?
             WHERE id = ?""",
            (
                score_result.get("summary"),
                score_result.get("work_mode", "unknown"),
                score_result.get("geo_zone", "unknown"),
                score_result.get("contract_type", "unknown"),
                score_result.get("language_required", "unknown"),
                now,
                extracted_by,
                job.id,
            ),
        )
        # Write company-level fields to companies table (reuse conn)
        if not company_id:
            row = conn.execute(
                "SELECT company_id FROM jobs WHERE id = ?", (job.id,)
            ).fetchone()
            if row:
                company_id = row["company_id"]
        if company_id:
            self.update_company_enrichment(
                company_id, score_result,
                enriched_by=extracted_by, _conn=conn)

    def save_scored(self, job, score_result: dict, profile_id: str,
                    company_id: int | None = None) -> None:
        """
        Sauvegarde un job avec son score pour un profil donné.
        Après Phase 1e.4: only writes profile-dependent columns to job_scores.
        """
        now = _now()
        with self._conn() as conn:
            self._upsert_job_raw(job, conn, now, company_id=company_id)
            conn.execute(
                """INSERT INTO job_scores (
                       job_id, profile_id,
                       score, reason, scored_by, scored_at
                   ) VALUES (
                       ?, ?, ?, ?, ?, ?
                   )
                   ON CONFLICT(job_id, profile_id) DO UPDATE SET
                       score     = excluded.score,
                       reason    = excluded.reason,
                       scored_by = excluded.scored_by,
                       scored_at = excluded.scored_at""",
                (job.id, profile_id, score_result.get("score"),
                 score_result.get("reason"), score_result.get("scored_by", "unknown"),
                 now),
            )
            # Also write structured fields to jobs table (Phase 1e)
            self._update_job_extraction_fields(job, score_result, conn, now,
                                               company_id=company_id)

    def save_unscored(self, job, company_id: int | None = None) -> None:
        """
        Enregistre un job sans score (échec scorer persistant).
        Le job sera retenté au prochain run (absent de job_scores).
        """
        now = _now()
        with self._conn() as conn:
            self._upsert_job_raw(job, conn, now, company_id=company_id)

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
                   WHERE t.status IN ('applied', 'ready', 'queued', 'archived')
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

    VALID_STATUSES = {"new", "queued", "ready", "applied", "rejected", "archived"}

    def set_status(self, job_id: str, status: str, notes: str = None) -> None:
        if status not in self.VALID_STATUSES:
            raise ValueError(f"Statut invalide : {status!r}. Valides : {self.VALID_STATUSES}")
        now = _now()
        with self._conn() as conn:
            if notes is not None:
                conn.execute(
                    """INSERT INTO job_tracking (job_id, status, notes, changed_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(job_id) DO UPDATE SET
                           status = excluded.status,
                           notes = excluded.notes,
                           changed_at = excluded.changed_at""",
                    (job_id, status, notes, now),
                )
            else:
                conn.execute(
                    """INSERT INTO job_tracking (job_id, status, changed_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(job_id) DO UPDATE SET
                           status = excluded.status,
                           changed_at = excluded.changed_at""",
                    (job_id, status, now),
                )
            conn.execute(
                "INSERT INTO status_history (job_id, status, changed_at) VALUES (?, ?, ?)",
                (job_id, status, now),
            )
            self._auto_log_status_interaction(job_id, status, _conn=conn)

    # ------------------------------------------------------------------
    # Requêtes pour digest / tracker
    # ------------------------------------------------------------------

    def get_digest(self, profile_id: str, min_score: int = 5, status: str = "new") -> list[dict]:
        with self._conn() as conn:
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
                       COALESCE(t.status, 'new') AS status, t.notes,
                       t.changed_at AS status_changed_at
                FROM jobs j
                JOIN job_scores s ON j.id = s.job_id
                LEFT JOIN companies c ON j.company_id = c.id
                LEFT JOIN job_tracking t ON j.id = t.job_id
                WHERE s.profile_id = ? AND s.score >= ?
            """
            params: list = [profile_id, min_score]
            if status:
                query += " AND COALESCE(t.status, 'new') = ?"
                params.append(status)
            query += " ORDER BY s.score DESC, j.last_seen DESC"
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self, profile_id: str | None = None) -> dict:
        """Quick stats for logging/display. Pass None to aggregate across all profiles."""
        with self._conn() as conn:
            total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            if profile_id is None:
                scored = conn.execute(
                    "SELECT COUNT(DISTINCT job_id) FROM job_scores"
                ).fetchone()[0]
                hot = conn.execute(
                    "SELECT COUNT(DISTINCT job_id) FROM job_scores WHERE score >= 9"
                ).fetchone()[0]
                solid = conn.execute(
                    "SELECT COUNT(DISTINCT job_id) FROM job_scores WHERE score BETWEEN 7 AND 8"
                ).fetchone()[0]
                by_status = dict(
                    conn.execute(
                        """SELECT COALESCE(t.status, 'new'), COUNT(*)
                           FROM jobs j
                           LEFT JOIN job_tracking t ON j.id = t.job_id
                           GROUP BY COALESCE(t.status, 'new')"""
                    ).fetchall()
                )
            else:
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
                           LEFT JOIN job_tracking t ON s.job_id = t.job_id
                           WHERE s.profile_id = ? GROUP BY COALESCE(t.status, 'new')""",
                        (profile_id,),
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
                   WHERE c.status = 'blacklisted'"""
            ).fetchone()
            return row[0] if row else 0

    # ------------------------------------------------------------------
    # Application content (job_applications table, profile-independent)
    # ------------------------------------------------------------------

    def get_application(self, job_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM job_applications WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def save_application(self, job_id: str, analysis: str, cover_letter: str) -> None:
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO job_applications (job_id, analysis, cover_letter, created_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       analysis = excluded.analysis,
                       cover_letter = excluded.cover_letter""",
                (job_id, analysis, cover_letter, now),
            )
            conn.execute(
                """INSERT INTO job_tracking (job_id, status) VALUES (?, 'ready')
                   ON CONFLICT(job_id) DO UPDATE SET status = 'ready'""",
                (job_id,),
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
            conn.execute(
                """INSERT INTO job_applications (
                       job_id, profile_id, cover_letter,
                       cv_bullets_selected, company_research, screening_answers,
                       language, prepared_by, prepared_at, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       profile_id         = excluded.profile_id,
                       cover_letter        = excluded.cover_letter,
                       cv_bullets_selected  = excluded.cv_bullets_selected,
                       company_research     = excluded.company_research,
                       screening_answers    = excluded.screening_answers,
                       language             = excluded.language,
                       prepared_by          = excluded.prepared_by,
                       prepared_at          = excluded.prepared_at""",
                (job_id, profile_id, cover_letter,
                 json.dumps(cv_bullets_selected, ensure_ascii=False) if cv_bullets_selected else None,
                 json.dumps(company_research, ensure_ascii=False) if company_research else None,
                 json.dumps(screening_answers, ensure_ascii=False) if screening_answers else None,
                 language, prepared_by, now, now),
            )
            conn.execute(
                """INSERT INTO job_tracking (job_id, status) VALUES (?, 'ready')
                   ON CONFLICT(job_id) DO UPDATE SET status = 'ready'""",
                (job_id,),
            )

    def get_job_for_prepare(self, job_id: str) -> dict | None:
        """Fetch a single job with all extracted fields for preparation."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT j.*,
                          COALESCE(j.summary, '') AS summary,
                          COALESCE(j.work_mode, 'unknown') AS work_mode,
                          COALESCE(j.geo_zone, 'unknown') AS geo_zone,
                          COALESCE(c.company_size, 'unknown') AS company_size,
                          COALESCE(j.contract_type, 'unknown') AS contract_type,
                          COALESCE(c.company_country, 'unknown') AS company_country,
                          COALESCE(c.industry_sector, 'other') AS industry_sector,
                          COALESCE(j.language_required, 'unknown') AS language_required
                   FROM jobs j
                   LEFT JOIN companies c ON j.company_id = c.id
                   WHERE j.id = ?""",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_queued_jobs_unprepared(self) -> list[dict]:
        """Jobs with status='queued' that have no prepared application yet."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT j.* FROM jobs j
                   JOIN job_tracking t ON j.id = t.job_id
                   LEFT JOIN job_applications a ON j.id = a.job_id
                   WHERE t.status = 'queued'
                     AND (a.job_id IS NULL OR a.prepared_at IS NULL)
                   ORDER BY j.last_seen DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def get_queued_jobs(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT j.id, j.title, j.company, j.url, j.description
                   FROM jobs j
                   JOIN job_tracking t ON j.id = t.job_id
                   WHERE t.status = 'queued'
                   ORDER BY j.last_seen DESC""",
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Config key-value store
    # ------------------------------------------------------------------

    def get_config(self, key: str, default=None) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_config(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

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
                "SELECT * FROM contacts WHERE linkedin_url = ?", (linkedin_url,)
            ).fetchone()
            if row:
                return dict(row)
        if email:
            row = conn.execute(
                "SELECT * FROM contacts WHERE email = ? AND company_id = ?",
                (email.lower(), company_id),
            ).fetchone()
            if row:
                return dict(row)
        if normalized_name and company_id:
            # Match by normalized name within the same company.
            # We load all contacts for the company and compare client-side
            # since the normalized name is not stored denormalized.
            rows = conn.execute(
                "SELECT * FROM contacts WHERE company_id = ?", (company_id,)
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
                conn.execute(
                    f"UPDATE contacts SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                return cid

            # Insert
            conn.execute(
                """INSERT INTO contacts (
                       company_id, first_name, last_name, full_name,
                       role_title, role_family, seniority,
                       email, email_status, email_confidence,
                       linkedin_url, x_handle, telegram_handle, github_handle,
                       phone, is_unverified, notes,
                       is_current, first_seen_at, last_seen_at, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (
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
                       company_id, contact_id, job_id,
                       type, direction, outcome,
                       subject, body_excerpt,
                       occurred_at, follow_up_due_at, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (company_id, contact_id, job_id,
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
                   WHERE contact_id = ?
                   ORDER BY occurred_at DESC""",
                (contact_id,),
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
                "SELECT id FROM contacts WHERE company_id = ?", (company_id,)
            ).fetchall()
            total_contacts = len(contact_rows)
            by_status: dict[str, int] = {}
            for cr in contact_rows:
                status = self.get_contact_relationship_status(cr["id"])
                by_status[status] = by_status.get(status, 0) + 1

            last_int = conn.execute(
                """SELECT occurred_at FROM interactions
                   WHERE company_id = ?
                   ORDER BY occurred_at DESC LIMIT 1""",
                (company_id,),
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
                    "SELECT * FROM contacts WHERE company_id = ? ORDER BY last_seen_at DESC",
                    (company_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM contacts WHERE company_id = ? AND is_unverified = 0 "
                    "ORDER BY last_seen_at DESC",
                    (company_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_company_interactions(self, company_id: int, limit: int = 50) -> list[dict]:
        """List interactions for a company, most recent first."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM interactions
                   WHERE company_id = ?
                   ORDER BY occurred_at DESC LIMIT ?""",
                (company_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_contact_interactions(self, contact_id: int, limit: int = 50) -> list[dict]:
        """List interactions for a contact, most recent first."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM interactions
                   WHERE contact_id = ?
                   ORDER BY occurred_at DESC LIMIT ?""",
                (contact_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_contact(self, contact_id: int) -> dict | None:
        """Fetch a single contact by id."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM contacts WHERE id = ?", (contact_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_companies(
        self,
        *,
        status: list[str] | None = None,
        search: str | None = None,
        exclude_blacklisted: bool = True,
    ) -> list[dict]:
        """List companies with job/contact/interaction counts and last interaction date."""
        clauses = []
        params: list = []
        if exclude_blacklisted:
            clauses.append("(c.status IS NULL OR c.status != 'blacklisted')")
        if status:
            placeholders = ", ".join("?" for _ in status)
            clauses.append(f"c.status IN ({placeholders})")
            params.extend(status)
        if search:
            clauses.append("(LOWER(c.name) LIKE ? OR LOWER(c.name_normalized) LIKE ?)")
            params.extend([f"%{search.lower()}%", f"%{search.lower()}%"])

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        query = f"""
            SELECT
                c.id, c.name, c.website, c.status,
                c.company_country, c.industry_sector, c.company_size,
                c.first_seen_at, c.last_seen_at,
                COALESCE(jcnt.cnt, 0)    AS job_count,
                COALESCE(ctcnt.cnt, 0)   AS contact_count,
                COALESCE(ixcnt.cnt, 0)   AS interaction_count,
                last_ix.occurred_at      AS last_interaction_at
            FROM companies c
            LEFT JOIN (
                SELECT company_id, COUNT(*) AS cnt
                FROM jobs GROUP BY company_id
            ) jcnt ON jcnt.company_id = c.id
            LEFT JOIN (
                SELECT company_id, COUNT(*) AS cnt
                FROM contacts GROUP BY company_id
            ) ctcnt ON ctcnt.company_id = c.id
            LEFT JOIN (
                SELECT company_id, COUNT(*) AS cnt
                FROM interactions GROUP BY company_id
            ) ixcnt ON ixcnt.company_id = c.id
            LEFT JOIN (
                SELECT company_id, MAX(occurred_at) AS occurred_at
                FROM interactions GROUP BY company_id
            ) last_ix ON last_ix.company_id = c.id
            {where}
            ORDER BY COALESCE(last_ix.occurred_at, c.last_seen_at) DESC, c.name ASC
        """
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

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
        params: list = []
        if exclude_blacklisted_companies:
            clauses.append("(c.status IS NULL OR c.status != 'blacklisted')")
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
                c.status AS company_status,
                last_ix.occurred_at AS last_interaction_at
            FROM contacts ct
            JOIN companies c ON ct.company_id = c.id
            LEFT JOIN (
                SELECT contact_id, MAX(occurred_at) AS occurred_at
                FROM interactions GROUP BY contact_id
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
                       company_id, job_id,
                       type, direction, outcome,
                       occurred_at, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (company_id, job_id, int_type, direction, outcome, now, now),
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
                "SELECT id, name, criteria FROM search_profiles"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_profile(self, profile_id: str) -> tuple[int, int]:
        """Delete a profile and all its scores. Returns (scores_deleted, profile_deleted)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM job_scores WHERE profile_id = ?", (profile_id,))
            scores = conn.execute("SELECT changes()").fetchone()[0]
            conn.execute("DELETE FROM search_profiles WHERE id = ?", (profile_id,))
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
                   t.status AS tracked_status,
                   COALESCE(t.status, 'new') AS status, t.notes,
                   s.profile_id as best_profile_id,
                   t.changed_at AS status_changed_at
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
            LEFT JOIN job_tracking t ON j.id = t.job_id
        """
        params: list = []
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
        query = """SELECT j.id, j.title, j.company, j.url, j.source, j.location,
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
                          t.status AS tracked_status,
                          COALESCE(t.status, 'new') AS status, t.notes,
                          t.changed_at AS status_changed_at
                   FROM jobs j
                   LEFT JOIN companies c ON j.company_id = c.id
                   LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
                   LEFT JOIN job_tracking t ON j.id = t.job_id
                """
        params: list = [profile_id]
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
                       WHERE i.follow_up_due_at <= ?
                       ORDER BY i.follow_up_due_at ASC
                       LIMIT 20""",
                    (today,),
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
                       WHERE i.direction = 'inbound'
                         AND i.occurred_at >= ?
                       ORDER BY i.occurred_at DESC
                       LIMIT 20""",
                    (seven_days_ago,),
                ).fetchall()
            ]

            # Unverified contacts count
            unverified_count = conn.execute(
                "SELECT COUNT(*) FROM contacts WHERE is_unverified = 1"
            ).fetchone()[0]

            # Stale active outreach (no interaction in 14 days)
            stale_outreach = [
                dict(r) for r in conn.execute(
                    """SELECT c.*, last_ix.last_at AS last_interaction_at
                       FROM companies c
                       LEFT JOIN (
                           SELECT company_id, MAX(occurred_at) AS last_at
                           FROM interactions GROUP BY company_id
                       ) last_ix ON last_ix.company_id = c.id
                       WHERE c.status = 'active_outreach'
                         AND (last_ix.last_at IS NULL
                              OR last_ix.last_at < ?)
                       ORDER BY COALESCE(last_ix.last_at, c.last_seen_at) ASC
                       LIMIT 20""",
                    (fourteen_days_ago,),
                ).fetchall()
            ]

        return {
            "follow_ups_due_today": follow_ups,
            "recent_inbound": recent_inbound,
            "unverified_contacts_count": unverified_count,
            "stale_active_outreach": stale_outreach,
        }


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
