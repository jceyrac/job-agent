# API exposure — prerequisites for a future public opening (spec 041, FR-017)

The v1 API is **tailnet-only** (Constitution VII): it listens on
`127.0.0.1:8601`, is served over the tailnet under
`https://<verva>.<tailnet>.ts.net/api`, and authenticates with scoped bearer
tokens. This document records what must exist **before** any public exposure —
**nothing here is implemented.** Opening the API publicly is a deliberate,
separate step, not a config flip.

## Prerequisites (all required before any public exposure)

- **Human login + browser session.** An identity provider (or passkeys) and
  HTTP-only, Secure session cookies for the future `web/` front. Bearer tokens
  alone are not a browser login.
- **Rate limiting / brute-force protection.** Per-client and per-token limits on
  authentication and data routes. Today's FR-014 only *logs* failed attempts and
  counts them per client address in memory; it does not block.
- **Token rotation policy.** Expiry is already supported (FR-013); a public
  exposure needs a documented rotation cadence and a revocation workflow.
- **Security headers and request-size limits.** HSTS, content-type guards,
  `X-Content-Type-Options`. Write bodies are already capped at 64 KB
  (spec 042); a public surface still needs the headers.
- **CSRF protection.** Not needed while auth is bearer-only (no ambient cookie
  credentials to ride along). It becomes mandatory the moment the future `web/`
  front introduces HTTP-only session cookies (the "human login" item above) —
  state-changing routes will need origin/Referer checks or CSRF tokens.
- **Audit-log retention.** A retention window and access-control policy for the
  failed-auth and access logs.
- **Dependency update policy.** FastAPI/uvicorn/pydantic are pinned ≥14 days old
  (FR-002); a public surface needs a routine for tracking and applying security
  updates.
- **Security review.** A review of the auth path, token handling, and serializers
  before opening the surface.
- **Constitution VII amendment.** Principle VII currently mandates tailnet-only
  exposure and must be amended as part of the decision to open publicly.

## Candidate entry points (decide at opening time)

- **Own domain** — e.g. `jobs-api.jeromeceyrac.com` behind the existing reverse
  proxy, terminating TLS on the proxy.
- **Identity-aware proxy** — e.g. Cloudflare Access in front of the API, moving
  authentication out of the application.

## What already exists (foundations, not exposure)

- Scoped bearer tokens bound to a user (`read:catalogue`, `read:me`,
  `write:me`, `write:catalogue`), SHA-256 hashes only, optional expiry
  (`create --expires-in <days>`).
- Generic JSON errors with no stack traces or internal paths.
- A configurable base URL (`JOB_AGENT_API_URL`) so moving off the tailnet name is
  a configuration change, not a rewrite.
