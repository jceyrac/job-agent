"""api_client.py — the cv_agent's only window to the server (spec 044, US3).

The agent is a *pure API client*: it reads/writes jobs, scores, statuses and the
application only through the FastAPI v1 API, never through ``JobStorage``. This
module owns that boundary — an ``httpx`` client carrying a scoped bearer token,
the error mapping, and a module-level singleton with proxy functions so
``nodes``/``cli`` call ``api.get_job(...)`` without holding any client state.

Environment contract (spec 044, ``contracts/agents-api.md`` §5):

- ``JOB_AGENT_API_URL``   — base URL (tailnet ``https://<verva>…/api``).
- ``JOB_AGENT_API_TOKEN`` — bearer token, or a local credentials file (mode 0600)
  at ``JOB_AGENT_API_TOKEN_FILE`` (default ``~/.config/job_agent/agent_token``).
- The token is **never** logged, never echoed into an error message, never
  committed.

Errors map to ``ApiError`` with a ``kind`` (``auth`` / ``not_found`` /
``unreachable``) so the CLI can stop with an explicit message *before* any LLM
spend (FR-005).
"""

import os
import stat

import httpx

# Default location of the local credentials file (owner-only, mode 0600).
_DEFAULT_TOKEN_FILE = os.path.join(
    os.path.expanduser("~"), ".config", "job_agent", "agent_token"
)


class ApiError(Exception):
    """A classified API/network failure, already human-readable.

    ``kind`` is ``"auth"`` (401/403), ``"not_found"`` (404) or ``"unreachable"``
    (5xx / network / timeout). The message never contains the token.
    """

    def __init__(self, message: str, *, kind: str = "unreachable", status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


def _load_token() -> str:
    """Bearer token from ``JOB_AGENT_API_TOKEN``, else a local credentials file.

    The file must be owner-only (mode 0600): a group/world-readable token file is
    refused rather than silently read, so a stray ``chmod`` can't leak the token.
    """
    token = os.environ.get("JOB_AGENT_API_TOKEN")
    if token:
        return token.strip()

    path = os.environ.get("JOB_AGENT_API_TOKEN_FILE") or _DEFAULT_TOKEN_FILE
    try:
        with open(path, encoding="utf-8") as f:
            token = f.read().strip()
    except OSError as e:
        raise ApiError(
            f"no API token — set JOB_AGENT_API_TOKEN or create {path} (mode 0600)"
        ) from e
    if not token:
        raise ApiError(f"API token file {path} is empty")

    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        raise ApiError(
            f"API token file {path} has permissive mode {oct(mode)} — run "
            "`chmod 600` on it before use"
        )
    return token


class AgentClient:
    """Synchronous httpx client over the v1 API, carrying a scoped bearer token."""

    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float = 30.0, transport=None):
        self.base_url = (base_url or os.environ.get("JOB_AGENT_API_URL") or "").rstrip("/")
        if not self.base_url:
            raise ApiError("no API base URL — set JOB_AGENT_API_URL")
        self.token = token if token is not None else _load_token()
        if not self.token:
            raise ApiError("no API token — set JOB_AGENT_API_TOKEN")

        kwargs = {
            "base_url": self.base_url,
            "timeout": httpx.Timeout(timeout, connect=timeout),
            "headers": {"Authorization": f"Bearer {self.token}"},
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    # ── internal ────────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, *, json=None) -> dict:
        """One JSON request; returns the decoded body, raising ``ApiError``."""
        try:
            resp = self._client.request(method, path, json=json)
        except httpx.HTTPError as e:
            raise ApiError(f"API unreachable ({self.base_url}): {e}") from e

        if resp.status_code in (401, 403):
            raise ApiError(
                "API authentication failed — token missing/expired or lacking "
                "the required scope", kind="auth", status=resp.status_code,
            )
        if resp.status_code == 404:
            raise ApiError("resource not found on the server", kind="not_found", status=404)
        if resp.status_code >= 500:
            raise ApiError(
                f"API server error (HTTP {resp.status_code})",
                kind="unreachable", status=resp.status_code,
            )
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = str(resp.json().get("detail", ""))
            except Exception:
                detail = ""
            suffix = f": {detail}" if detail else ""
            raise ApiError(f"API rejected the request (HTTP {resp.status_code}){suffix}",
                           status=resp.status_code)
        return resp.json()

    # ── the seven calls the agent makes ─────────────────────────────────────

    def get_me(self) -> dict:
        """`GET /v1/me` — identity, active profile id, and the profile list."""
        return self._request("GET", "/v1/me")

    def get_job(self, job_id: str) -> dict:
        """`GET /v1/me/jobs/{id}` — job + user-scoped scores + application."""
        try:
            return self._request("GET", f"/v1/me/jobs/{job_id}")
        except ApiError as e:
            if e.kind == "not_found":
                raise ApiError(f"job {job_id!r} not found on the server",
                               kind="not_found") from e
            raise

    def submit_job(self, payload: dict) -> dict:
        """`POST /v1/jobs` — submit a posting; returns ``{job_id, created}``."""
        return self._request("POST", "/v1/jobs", json=payload)

    def enqueue_score_job(self, job_id: str, profile_id: str) -> dict:
        """`POST /v1/me/tasks` — enqueue a single-job scoring task."""
        return self._request(
            "POST", "/v1/me/tasks",
            json={"type": "score_job", "params": {"job_id": job_id, "profile_id": profile_id}},
        )

    def get_task(self, task_id: str) -> dict:
        """`GET /v1/me/tasks/{id}` — poll a task's status."""
        return self._request("GET", f"/v1/me/tasks/{task_id}")

    def set_status(self, job_id: str, status: str, notes: str | None = None) -> dict:
        """`PUT /v1/me/jobs/{id}/status` — ``archived`` (skip) / ``ready`` (approve)."""
        return self._request(
            "PUT", f"/v1/me/jobs/{job_id}/status", json={"status": status, "notes": notes},
        )

    def set_application(self, job_id: str, analysis: str, cover_letter: str) -> dict:
        """`PUT /v1/me/jobs/{id}/application` — record the application row."""
        return self._request(
            "PUT", f"/v1/me/jobs/{job_id}/application",
            json={"analysis": analysis, "cover_letter": cover_letter},
        )


# ── module-level singleton + proxy functions ────────────────────────────────

_client: AgentClient | None = None


def get_client() -> AgentClient:
    """The process-wide client, built lazily from the environment."""
    global _client
    if _client is None:
        _client = AgentClient()
    return _client


def _set_client(client: AgentClient | None) -> None:
    """Test hook: replace the singleton (``None`` resets to lazy construction)."""
    global _client
    _client = client


def reset_client() -> None:
    """Test hook: drop the singleton so the next call rebuilds from env."""
    _set_client(None)


# Proxy functions — the call sites in nodes/cli import this module as ``api``.

def get_me() -> dict:
    return get_client().get_me()


def get_job(job_id: str) -> dict:
    return get_client().get_job(job_id)


def submit_job(payload: dict) -> dict:
    return get_client().submit_job(payload)


def enqueue_score_job(job_id: str, profile_id: str) -> dict:
    return get_client().enqueue_score_job(job_id, profile_id)


def get_task(task_id: str) -> dict:
    return get_client().get_task(task_id)


def set_status(job_id: str, status: str, notes: str | None = None) -> dict:
    return get_client().set_status(job_id, status, notes)


def set_application(job_id: str, analysis: str, cover_letter: str) -> dict:
    return get_client().set_application(job_id, analysis, cover_letter)
