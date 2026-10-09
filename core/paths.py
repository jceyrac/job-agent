"""paths.py — single source of truth for every location the app depends on.

Set JOB_AGENT_DATA_DIR to run the app against an alternate data directory:
  - a throwaway dir for new-user testing (JOB_AGENT_DATA_DIR=data_test)
  - a mounted volume in Docker
Defaults to <repo_root>/data, preserving previous behavior.

Other locations:
  JOB_AGENT_OUTPUT_DIR — outputs directory (default <repo_root>/outputs)
  ENV_PATH             — <repo_root>/.env (not overridable)
  CV_PIPELINE_DIR      — .cv_pipeline render harness, sibling of the repo root (env-overridable)
  SCRAPERS_SRC_DIR     — source-tree scraper directory (<repo_root>/core/scrapers)

Guard:
  JOB_AGENT_REQUIRE_DB=1 — refuse to import (creating nothing) if the DB file
  is missing, so prod can only fail loudly, never silently split data.
"""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("JOB_AGENT_DATA_DIR") or os.path.join(PROJECT_ROOT, "data")
OUTPUT_DIR = os.environ.get("JOB_AGENT_OUTPUT_DIR") or os.path.join(PROJECT_ROOT, "outputs")
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")
CV_PIPELINE_DIR = os.environ.get("CV_PIPELINE_DIR") or os.path.join(os.path.dirname(PROJECT_ROOT), ".cv_pipeline")
SCRAPERS_SRC_DIR = os.path.join(PROJECT_ROOT, "core", "scrapers")
DB_PATH = os.path.join(DATA_DIR, "jobs.db")

if os.environ.get("JOB_AGENT_REQUIRE_DB") == "1":
    if not os.path.exists(DB_PATH):
        raise RuntimeError(
            f"JOB_AGENT_REQUIRE_DB=1 but no database at {DB_PATH}. "
            f"Set JOB_AGENT_DATA_DIR to point at the real data directory "
            f"(or unset JOB_AGENT_REQUIRE_DB for a fresh/test install)."
        )
else:
    # Ensure the dir exists so a fresh (test) data dir works out of the box —
    # SQLite needs the parent dir present to create the DB file.
    os.makedirs(DATA_DIR, exist_ok=True)


def data_path(*parts: str) -> str:
    """Build a path under the active data directory."""
    return os.path.join(DATA_DIR, *parts)
