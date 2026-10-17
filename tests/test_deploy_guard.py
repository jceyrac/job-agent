"""Guard that deploy.sh refuses to run while staging containers are up.

`scripts/guard_staging_down.sh` is the guard deploy.sh pipes `docker ps` names
into. These tests feed it container-name lists and assert it allows a deploy
only when no `job-agent-staging*` container is present.
"""
from __future__ import annotations

import os
import subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(REPO_ROOT, "scripts", "guard_staging_down.sh")


def _run(names):
    return subprocess.run(
        ["bash", GUARD],
        input="".join(f"{n}\n" for n in names),
        capture_output=True,
        text=True,
    )


def test_allows_deploy_when_no_staging_containers():
    p = _run(["job-tracker", "job-api"])
    assert p.returncode == 0
    assert p.stdout == ""


def test_blocks_deploy_when_staging_tracker_running():
    p = _run(["job-tracker", "job-agent-staging", "job-agent-staging-api"])
    assert p.returncode == 1
    assert "staging" in p.stderr


def test_blocks_deploy_when_staging_api_running():
    p = _run(["job-api", "job-agent-staging-api"])
    assert p.returncode == 1
    assert "staging" in p.stderr
