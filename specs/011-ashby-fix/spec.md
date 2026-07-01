# Spec 011 — Fix Ashby ATS Scraper (correct public endpoint)

> Spec for Claude Code. Read `scrapers/ats/ashby.py` and `scrapers/base.py` before starting.
> Do not modify `storage.py`, `models.py`, `profiles.py`, `main.py`, or any other scraper.

---

## Context

The existing `scrapers/ats/ashby.py` uses the wrong endpoint:

```
# BROKEN — requires API key auth → always returns 401
https://api.ashbyhq.com/posting-api/v1/boards/{board}/jobs
```

Ashby exposes a separate **public job board API** that requires no authentication:

```
# CORRECT — public, no auth required, confirmed working 2026-06-25
GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
```

This spec replaces the broken implementation with the correct endpoint.
The class name, file location, and interface remain identical — only the HTTP call changes.

---

## Confirmed API response shape

Validated live against `https://api.ashbyhq.com/posting-api/job-board/ashby?includeCompensation=true`
on 2026-06-25. Response is JSON with a top-level `jobs` array:

```json
{
  "jobs": [
    {
      "id": "7458d4e9-...",
      "title": "Engineering Manager, EU",
      "department": "Engineering",
      "team": "EMEA Engineering",
      "employmentType": "FullTime",
      "location": "Portugal",
      "secondaryLocations": [
        {
          "location": "Switzerland",
          "address": {
            "postalAddress": {
              "addressCountry": "Switzerland",
              "addressRegion": "",
              "addressLocality": ""
            }
          }
        }
      ],
      "isListed": true,
      "isRemote": true,
      "workplaceType": "Remote",
      "publishedAt": "2024-03-04T14:29:08.532+00:00",
      "jobUrl": "https://jobs.ashbyhq.com/ashby/7458d4e9-...",
      "applyUrl": "https://jobs.ashbyhq.com/ashby/7458d4e9-.../application",
      "descriptionHtml": "<p>...</p>",
      "address": {
        "postalAddress": {
          "addressCountry": "Portugal"
        }
      }
    }
  ]
}
```

No pagination — all listed jobs are returned in a single call.

---

## Changes to `scrapers/ats/ashby.py`

### 1. Fix the endpoint URL constant

```python
# OLD (broken)
BASE_URL = "https://api.ashbyhq.com/posting-api/v1/boards"

# NEW (correct)
BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"
```

### 2. Fix the HTTP call

```python
# OLD (broken)
r = client.get(f"{BASE_URL}/{board}/jobs")

# NEW (correct)
r = client.get(f"{BASE_URL}/{board}", params={"includeCompensation": "true"})
```

### 3. Fix the response parsing

The response shape differs from what the broken code assumed:

```python
# OLD (broken — wrong keys)
postings = data.get("jobs", []) or data.get("postings", []) or data
if isinstance(postings, dict):
    postings = postings.get("results", []) or []

# NEW (correct — top-level "jobs" array, no nesting)
postings = data.get("jobs", [])
```

### 4. Fix field mapping

Update field extraction to match the confirmed response shape:

| JobPosting field | Ashby API field | Notes |
|---|---|---|
| `title` | `title` | Direct |
| `location` | `location` | Primary location string |
| `url` | `jobUrl` | Prefer `jobUrl`, fallback to `applyUrl` |
| `posted_date` | `publishedAt` | ISO datetime, take first 10 chars |
| `description` | `descriptionHtml` | HTML — pass as-is |
| `source` | `"Ashby"` | Hardcoded |
| `company` | From `self._targets` entry | Company name from DB target |

Filter out unlisted jobs: `if not item.get("isListed", True): continue`

### 5. No other changes

Keep `SOURCE_NAME`, `ENABLED`, class structure, error handling pattern, and
`time.sleep(1)` between companies exactly as they are.

---

## DB update for Arrakis

Arrakis was reverted to `watch_pending` during the investigation. After this fix is deployed
and verified, update its status:

```sql
UPDATE companies
SET monitoring_status = 'watching',
    ats_provider = 'ashby',
    ats_identifier = 'Arrakis'
WHERE name LIKE '%Arrakis%';
```

Verify with:
```sql
SELECT name, ats_provider, ats_identifier, monitoring_status
FROM companies
WHERE name LIKE '%Arrakis%';
```

---

## Validation

Test the fixed scraper before updating Arrakis status:

```bash
python main.py --monitored-only --mock --profile unified_jc
```

Expected: Arrakis jobs appear in output with `source = "Ashby"`.

Also validate directly:
```bash
curl "https://api.ashbyhq.com/posting-api/job-board/Arrakis?includeCompensation=true"
```

Expected: JSON response with `jobs` array (may be empty if no open roles at time of test).

---

## Non-objectives

- No discovery of new Ashby companies (monitoring agent handles that)
- No support for the authenticated Ashby ATS API (requires per-customer API key)
- No changes to how `ats_identifier` is stored or resolved

---

## Acceptance criteria

- [ ] `curl https://api.ashbyhq.com/posting-api/job-board/Arrakis` returns 200 (not 401)
- [ ] `python main.py --monitored-only --mock` shows Arrakis jobs with `source = "Ashby"`
- [ ] `isListed = false` jobs are filtered out
- [ ] `posted_date` is correctly parsed from `publishedAt`
- [ ] HTTP errors still log `[Ashby] {slug}: HTTP {status}` and continue without crashing
- [ ] No changes to any other file
