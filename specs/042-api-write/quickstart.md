# Quickstart — validate the write endpoints (spec 042)

Runnable checks that prove the write API works end-to-end. Run locally against a
temp DB (no live data) first, then on staging (US5).

## Prerequisites

```bash
cd /Users/jeanclaudevd/AI-Suite/job_agent
pip install -e ".[dev]"            # once
```

## 1. Local API + temp DB

```bash
# One-off smoke via pytest (no server needed)
python -m pytest tests/test_api_contract.py tests/test_api_guard.py \
                 tests/test_api_auth.py tests/test_isolation.py -q
```

## 2. Serve locally and mint write tokens

```bash
uvicorn api.main:app --host 127.0.0.1 --port 8601 &
# mint tokens against the live DB (or a scratch DB via JOB_AGENT_DATA_DIR)
python -m scripts.api_token create --name local --scopes read:me,write:me
python -m scripts.api_token create --name local-cat --scopes write:catalogue
```

## 3. Exercise a status write (round-trip)

```bash
BASE=http://127.0.0.1:8601
TOKEN=<paste write:me token>
JOB=<a real job id>

# write a lifecycle status (occurred_at required — never defaulted to "now")
curl -sS -X PUT "$BASE/v1/me/jobs/$JOB/status" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"status":"applied","occurred_at":"2026-10-01","notes":"via API"}'

# read it back — the status must reflect the write
curl -sS "$BASE/v1/me/jobs/$JOB" -H "Authorization: Bearer $TOKEN"
```

## 4. Lifecycle event with a past date (never defaulted to "now")

```bash
curl -sS -X POST "$BASE/v1/me/jobs/$JOB/events" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"event":"interview","occurred_at":"2026-10-01T10:00:00","new_status":"interviewing"}'
```

## 5. Company blacklist then restore

```bash
curl -sS -X PATCH "$BASE/v1/me/companies/1" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"status":"blacklisted"}'
curl -sS -X PATCH "$BASE/v1/me/companies/1" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"status":"prospect"}'
```

## 6. Scope enforcement (must be 403)

```bash
# a read-only token cannot write
curl -sS -X PUT "$BASE/v1/me/jobs/$JOB/status" \
  -H "Authorization: Bearer <read:me-only token>" \
  -H 'Content-Type: application/json' -d '{"status":"applied","occurred_at":"2026-10-01"}' -o /dev/null -w '%{http_code}\n'  # 403

# a write:me token cannot write catalogue facts
curl -sS -X PATCH "$BASE/v1/companies/1" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"website":"https://example.com"}' -o /dev/null -w '%{http_code}\n'  # 403
```

## 7. Body-size cap (must be 413)

```bash
python -c "print('{\"notes\":\"' + 'x'*70000 + '\"}')" > /tmp/big.json
curl -sS -X PUT "$BASE/v1/me/jobs/$JOB/status" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data @/tmp/big.json -o /dev/null -w '%{http_code}\n'  # 413
```

## 8. Staging round-trip (US5)

1. Stage the exact SHA (`scripts/staging.sh up <full-sha>`).
2. Mint a staging `write:me` token inside the staging `job-api` container.
3. From the Mac, run each write once (status, past-dated event, company
   blacklist+restore, contact edit, interaction) against
   `https://<verva>.<tailnet>.ts.net/api`.
4. Confirm the effects appear in the staging tracker, and a write made in the
   staging tracker is visible through the API.
5. Confirm tracker/API parity is byte-identical *before* the API writes.
