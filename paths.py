"""paths.py — single source of truth for the data directory.

Set JOB_AGENT_DATA_DIR to run the app against an alternate data directory:
  - a throwaway dir for new-user testing (JOB_AGENT_DATA_DIR=data_test)
  - a mounted volume in Docker
Defaults to <repo_root>/data, preserving previous behavior.
"""
import os

_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("JOB_AGENT_DATA_DIR") or os.path.join(_ROOT, "data")

# Ensure the dir exists so a fresh (test) data dir works out of the box —
# SQLite needs the parent dir present to create the DB file.
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "jobs.db")


def data_path(*parts: str) -> str:
    """Build a path under the active data directory."""
    return os.path.join(DATA_DIR, *parts)
