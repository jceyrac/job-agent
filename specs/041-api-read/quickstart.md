# Quickstart — validate spec 041 (API v1 read-only)

Run on the dev Mac (`.venv/`) against a **copy** of a real DB, never the live
`data/jobs.db`. The point is to prove: (1) the API serves over `core` with no SQL
of its own, (2) auth/scope semantics are exactly 401/403/404, (3) every endpoint's
payload equals its `JobStorage` source, and (4) staging answers on the tailnet with
the same figures as the staging tracker.

## Prerequisites

```bash
cd /Users/jeanclaudevd/AI-Suite/job_agent
source .venv/bin/activate
pip install "fastapi>=0.110,<1" "uvicorn>=0.29,<1"   # versions ≥14 days old (FR-002)
cp data/jobs.db /tmp/spec041.db                      # work on a copy
```

## 1. Tests (contracts + auth + guard + OpenAPI)

```bash
python -m pytest tests/test_api_contract.py tests/test_api_auth.py \
                 tests/test_api_guard.py tests/test_api_openapi.py -q
```

Expected: all green. `test_api_guard.py` fails if `api/` calls a non-allow-listed
`JobStorage` method, imports `sqlite3`, or builds a `JobStorage` not bound to the
token user (outside `deps.py`'s auth path).

## 2. Serve the API locally against the copy

```bash
JOB_AGENT_DATA_DIR=/tmp JOB_AGENT_REQUIRE_DB=0 \
  uvicorn api.main:app --host 127.0.0.1 --port 8601
# (JOB_AGENT_DATA_DIR=/tmp makes DB_PATH = /tmp/jobs.db; copy the DB there too)
```

In a second shell:

```bash
export JOB_AGENT_API_URL=http://127.0.0.1:8601
curl -s $JOB_AGENT_API_URL/health
# {"status":"ok","version":"<sha>"}

python -m scripts.api_token create --name smoke --scopes read:catalogue,read:me
# → prints the raw token once; capture it as TOKEN

curl -s -H "Authorization: Bearer $TOKEN" $JOB_AGENT_API_URL/v1/me
curl -s -H "Authorization: Bearer $TOKEN" "$JOB_AGENT_API_URL/v1/me/feed?limit=5"
curl -s -H "Authorization: Bearer $TOKEN" "$JOB_AGENT_API_URL/v1/jobs?limit=5"
```

Verify: `/health` needs no token; `/v1/*` without a token → `401`; a
`read:catalogue`-only token on `/v1/me` → `403`; a foreign/absent contact id → `404`.

## 3. Parity: API payload == `JobStorage` output

```bash
python - <<'PY'
from core.storage import JobStorage
import urllib.request, json, os

db = JobStorage("/tmp/spec041.db")          # unbound → default user
pid = db.get_active_profile_id()
feed = db.get_all_for_tracker(pid)

req = urllib.request.Request("http://127.0.0.1:8601/v1/me/feed?limit=500",
                             headers={"Authorization": "Bearer " + os.environ["TOKEN"]})
api = json.load(urllib.request.urlopen(req))
assert api["total"] == len(feed)
assert api["items"][0]["id"] == feed[0]["id"]
print("feed parity ok")
PY
```

## 4. Full suite

```bash
python -m pytest tests/ -q
```

Expected: the existing suite stays green (tracker untouched), plus the new API
tests and the extended `tests/test_isolation.py`.

## 5. Staging (US4-1, FR-011)

On verva:

```bash
cd /opt/job-agent
./scripts/staging.sh up <full-sha>
# now also runs the API on 127.0.0.1:8602, exposed as
#   tailscale serve --bg --https=8443 --set-path /api http://127.0.0.1:8602
```

Create a staging token on the staging volume, then from the Mac:

```bash
curl -s https://<verva>.<tailnet>.ts.net:8443/api/health          # valid cert, no -k
curl -s -H "Authorization: Bearer $STAGING_TOKEN" \
  https://<verva>.<tailnet>.ts.net:8443/api/v1/me/feed?limit=5
curl -s -H "Authorization: Bearer $STAGING_TOKEN" \
  https://<verva>.<tailnet>.ts.net:8443/api/v1/me/jobs/<id>
curl -s -H "Authorization: Bearer $STAGING_TOKEN" \
  https://<verva>.<tailnet>.ts.net:8443/api/v1/me/contacts
```

Verify the feed figures match the staging tracker's figures (SC-004); tracker
parity vs prod image is identical; then `./scripts/staging.sh down` removes the
API container and the 8443 serve entry.

## 6. Prod (US4-2, SC-005)

After the standard release flow: `https://<verva>.<tailnet>.ts.net/api/health`
returns `200` with a valid certificate; `http://<verva LAN IP>:8601` is refused;
tracker and the next cron run are unaffected.
