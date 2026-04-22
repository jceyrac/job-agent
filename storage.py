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
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schéma
# ---------------------------------------------------------------------------

SCHEMA = """
-- Données brutes des offres — une ligne par job, partagée entre profils
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    company       TEXT,
    url           TEXT,
    source        TEXT,
    location      TEXT,
    base_location TEXT,
    posted_date   TEXT,
    description   TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
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
    job_id  TEXT PRIMARY KEY,
    status  TEXT NOT NULL DEFAULT 'new',
    notes   TEXT
);

-- Application content — one row per job, profile-independent
CREATE TABLE IF NOT EXISTS job_applications (
    job_id       TEXT PRIMARY KEY,
    analysis     TEXT,
    cover_letter TEXT,
    created_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_last_seen  ON jobs (last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_scores_profile  ON job_scores (profile_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_tracking_status ON job_tracking (status);

-- Key-value config store (active_profile_id, etc.)
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
);
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
            existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "base_location" not in existing:
                conn.execute("ALTER TABLE jobs ADD COLUMN base_location TEXT")
                logger.info("[Storage] Migration: added base_location column to jobs")
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
                """SELECT score, reason, summary, work_mode, geo_zone,
                          company_size, contract_type, scored_by
                   FROM job_scores WHERE job_id = ? AND profile_id = ?""",
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

    def _upsert_job_raw(self, job, conn, now: str) -> None:
        """Insère ou met à jour la table jobs (données brutes uniquement)."""
        conn.execute(
            """INSERT INTO jobs (
                   id, title, company, url, source, location, base_location,
                   posted_date, description, first_seen, last_seen
               ) VALUES (
                   :id, :title, :company, :url, :source, :location, :base_location,
                   :posted_date, :description, :now, :now
               )
               ON CONFLICT(id) DO UPDATE SET
                   last_seen     = excluded.last_seen,
                   base_location = excluded.base_location""",
            {
                "id":            job.id,
                "title":         job.title,
                "company":       getattr(job, "company", None),
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
        """
        clauses = []
        params: list = [profile_id]

        if rescore:
            base = ("SELECT j.* FROM jobs j"
                    " LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?"
                    " LEFT JOIN job_tracking t ON j.id = t.job_id")
            clauses.append("(t.status IS NULL OR t.status NOT IN ('rejected', 'archived'))")
        else:
            base = ("SELECT j.* FROM jobs j"
                    " LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?"
                    " LEFT JOIN job_tracking t ON j.id = t.job_id")
            clauses.append("(s.job_id IS NULL OR s.score IS NULL)")
            clauses.append("(t.status IS NULL OR t.status NOT IN ('rejected', 'archived'))")

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

        where = " AND ".join(clauses)
        query = f"{base} WHERE {where}"

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def save_scored(self, job, score_result: dict, profile_id: str) -> None:
        """
        Sauvegarde un job avec son score pour un profil donné.
        `score_result` est le dict retourné par scorer.score_job().
        """
        now = _now()
        with self._conn() as conn:
            self._upsert_job_raw(job, conn, now)
            conn.execute(
                """INSERT INTO job_scores (
                       job_id, profile_id,
                       score, reason, summary, work_mode, geo_zone,
                       company_size, contract_type, scored_by, scored_at
                   ) VALUES (
                       :job_id, :profile_id,
                       :score, :reason, :summary, :work_mode, :geo_zone,
                       :company_size, :contract_type, :scored_by, :scored_at
                   )
                   ON CONFLICT(job_id, profile_id) DO UPDATE SET
                       score         = excluded.score,
                       reason        = excluded.reason,
                       summary       = excluded.summary,
                       work_mode     = excluded.work_mode,
                       geo_zone      = excluded.geo_zone,
                       company_size  = excluded.company_size,
                       contract_type = excluded.contract_type,
                       scored_by     = excluded.scored_by,
                       scored_at     = excluded.scored_at""",
                {
                    "job_id":       job.id,
                    "profile_id":   profile_id,
                    "score":        score_result.get("score"),
                    "reason":       score_result.get("reason"),
                    "summary":      score_result.get("summary"),
                    "work_mode":    score_result.get("work_mode", "unknown"),
                    "geo_zone":     score_result.get("geo_zone", "unknown"),
                    "company_size": score_result.get("company_size", "unknown"),
                    "contract_type": score_result.get("contract_type", "unknown"),
                    "scored_by":    score_result.get("scored_by", "unknown"),
                    "scored_at":    now,
                },
            )

    def save_unscored(self, job) -> None:
        """
        Enregistre un job sans score (échec scorer persistant).
        Le job sera retenté au prochain run (absent de job_scores).
        """
        now = _now()
        with self._conn() as conn:
            self._upsert_job_raw(job, conn, now)

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
        with self._conn() as conn:
            if notes is not None:
                conn.execute(
                    """INSERT INTO job_tracking (job_id, status, notes) VALUES (?, ?, ?)
                       ON CONFLICT(job_id) DO UPDATE SET status = excluded.status, notes = excluded.notes""",
                    (job_id, status, notes),
                )
            else:
                conn.execute(
                    """INSERT INTO job_tracking (job_id, status) VALUES (?, ?)
                       ON CONFLICT(job_id) DO UPDATE SET status = excluded.status""",
                    (job_id, status),
                )

    # ------------------------------------------------------------------
    # Requêtes pour digest / tracker
    # ------------------------------------------------------------------

    def get_digest(self, profile_id: str, min_score: int = 5, status: str = "new") -> list[dict]:
        with self._conn() as conn:
            query = """
                SELECT j.*, s.score, s.reason, s.summary, s.work_mode, s.geo_zone,
                       s.company_size, s.contract_type, s.scored_by, s.scored_at,
                       COALESCE(t.status, 'new') AS status, t.notes
                FROM jobs j
                JOIN job_scores s ON j.id = s.job_id
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

    def get_stats(self, profile_id: str) -> dict:
        """Statistiques rapides pour logging/affichage."""
        with self._conn() as conn:
            total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
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

    # ------------------------------------------------------------------
    # Application content (job_applications table, profile-independent)
    # ------------------------------------------------------------------

    def get_application(self, job_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT analysis, cover_letter FROM job_applications WHERE job_id = ?",
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

    def get_all_jobs_best_score(self) -> list[dict]:
        """All scored jobs, each shown once at its highest score across profiles.
        Status and notes come from job_tracking (profile-independent).
        """
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT j.*, s.score, s.reason, s.summary, s.work_mode, s.geo_zone,
                       s.company_size, s.contract_type, s.scored_by,
                       COALESCE(t.status, 'new') AS status, t.notes,
                       s.profile_id as best_profile_id
                FROM jobs j
                JOIN job_scores s ON j.id = s.job_id
                LEFT JOIN job_tracking t ON j.id = t.job_id
                WHERE s.score IS NOT NULL
                  AND s.rowid = (
                    SELECT s2.rowid FROM job_scores s2
                    WHERE s2.job_id = j.id AND s2.score IS NOT NULL
                    ORDER BY s2.score DESC, s2.profile_id ASC
                    LIMIT 1
                  )
                ORDER BY s.score DESC, j.last_seen DESC
            """).fetchall()
            return [dict(r) for r in rows]

    def get_all_for_tracker(self, profile_id: str) -> list[dict]:
        """All jobs for the Streamlit tracker — scored and unscored.
        Status and notes come from job_tracking (profile-independent).
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT j.*, s.score, s.reason, s.summary, s.work_mode, s.geo_zone,
                          s.company_size, s.contract_type, s.scored_by,
                          COALESCE(t.status, 'new') AS status, t.notes
                   FROM jobs j
                   LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
                   LEFT JOIN job_tracking t ON j.id = t.job_id
                   ORDER BY COALESCE(s.score, -1) DESC, j.last_seen DESC""",
                (profile_id,),
            ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                if d.get("score") is None:
                    d["status"] = "unscored"
                result.append(d)
            return result


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
