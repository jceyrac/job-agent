"""Env-var isolation for staging runs of the cv_agent (spec 044, pre-staging).

A staging run of the agent against the staging API must not write to the real
Nextcloud "Job applications" folder, the real render output root, or the real
LangGraph checkpointer — thread id == job id, and staging job ids equal prod ids,
so each surface is made configurable by an env var and unit-tested here:

- ``CV_AGENT_CHECKPOINT_DB`` → isolated checkpointer file
- ``CV_OUTPUT_DIR``           → isolated render output root
- ``CV_PUBLISH=off``          → disable Nextcloud publication outright
- ``CV_PUBLISH_DIR``          → redirect the remote folder to a scratch subtree
"""

import os
from unittest.mock import patch

import pytest

from core import paths
from core.cv_agent import nextcloud_publish
from core.cv_agent import nodes
from core.cv_agent import renderer
from core.cv_agent.cli import _checkpoint_db_path


# ── checkpointer path (CV_AGENT_CHECKPOINT_DB) ───────────────────────────────

def test_checkpoint_db_defaults_to_data_path(monkeypatch):
    monkeypatch.delenv("CV_AGENT_CHECKPOINT_DB", raising=False)
    assert _checkpoint_db_path() == paths.data_path("cv_agent_checkpoints.sqlite")


def test_checkpoint_db_env_override(monkeypatch):
    monkeypatch.setenv("CV_AGENT_CHECKPOINT_DB", "/tmp/staging_checkpoints.sqlite")
    assert _checkpoint_db_path() == "/tmp/staging_checkpoints.sqlite"


# ── Nextcloud publication (CV_PUBLISH / CV_PUBLISH_DIR) ─────────────────────

def test_publish_off_disables_even_with_credentials(monkeypatch):
    monkeypatch.setenv("CV_PUBLISH", "off")
    monkeypatch.setenv("CV_NC_BASE_URL", "https://nc.example.com")
    monkeypatch.setenv("CV_NC_USER", "u")
    monkeypatch.setenv("CV_NC_APP_PASSWORD", "p")
    assert nextcloud_publish.publish_application("/tmp/x", "Acme", "Role") is None


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "no"])
def test_publish_disabled_variants(value, monkeypatch):
    monkeypatch.setenv("CV_PUBLISH", value)
    assert nextcloud_publish._publish_disabled() is True


def test_publish_not_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CV_PUBLISH", raising=False)
    assert nextcloud_publish._publish_disabled() is False


def test_publish_dir_override(monkeypatch):
    monkeypatch.setenv("CV_PUBLISH_DIR", "Documents/00 Staging")
    assert nextcloud_publish._app_folder() == "Documents/00 Staging"


def test_publish_dir_default(monkeypatch):
    monkeypatch.delenv("CV_PUBLISH_DIR", raising=False)
    assert nextcloud_publish._app_folder() == "Documents/01 Job/Job applications"


# ── render output root (CV_OUTPUT_DIR) ───────────────────────────────────────

_RENDER_STATE = {
    "cv_content": {"title": "T"},
    "proposed_profile": "swiss",
    "slug": "acme-role",
    "job": {"company": "Acme", "title": "Role", "id": "job-1"},
    "title_override": "",
}


def test_render_uses_cv_output_dir_env(monkeypatch):
    monkeypatch.setenv("CV_OUTPUT_DIR", "/tmp/staging_cv_outputs")
    with patch.object(
        nodes, "render_documents", return_value={"pdf": "/x", "local_dir": "/y"}
    ) as rd:
        nodes.render(dict(_RENDER_STATE))
    # output_root is the 5th positional arg of render_documents(...).
    assert rd.call_args[0][4] == "/tmp/staging_cv_outputs"


def test_render_defaults_output_root(monkeypatch):
    monkeypatch.delenv("CV_OUTPUT_DIR", raising=False)
    with patch.object(
        nodes, "render_documents", return_value={"pdf": "/x", "local_dir": "/y"}
    ) as rd:
        nodes.render(dict(_RENDER_STATE))
    assert rd.call_args[0][4] == renderer.default_output_root()
