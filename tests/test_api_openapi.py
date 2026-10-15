"""OpenAPI snapshot test (spec 041, FR-008).

The committed `api/openapi.json` is the source of truth for the API surface.
Regenerate the live schema and diff against the snapshot; any drift (a new path,
a renamed field, a changed response model) fails the test. The `info.version`
is normalized to a placeholder so the snapshot stays stable across commits
(the version is the running image's git SHA, not part of the contract).
"""
import json
import os

from api.main import create_app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT = os.path.join(REPO_ROOT, "api", "openapi.json")


def _normalized_schema() -> dict:
    schema = create_app().openapi()
    schema["info"]["version"] = "SNAPSHOT"
    return schema


def test_openapi_matches_snapshot():
    with open(SNAPSHOT, encoding="utf-8") as fh:
        expected = json.load(fh)
    assert _normalized_schema() == expected, (
        "OpenAPI drift — regenerate api/openapi.json (see tests/test_api_openapi.py)"
    )


def test_openapi_has_no_auth_on_health():
    schema = _normalized_schema()
    health = schema["paths"]["/health"]["get"]
    assert "security" not in health, "GET /health must be unauthenticated"
    # The v1 paths are all versioned under the root_path /api prefix.
    assert "/v1/jobs" in schema["paths"]
    assert "/v1/me" in schema["paths"]
