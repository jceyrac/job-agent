import json
import os
from datetime import date
from pathlib import Path

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

# TODO: Wellfound bloqué en scraping direct
# Options testées :
#   1) GET /role/... → 403
#   2) GET /jobs/... → 403
#   3) Playwright headless → Cloudflare bot protection (page 2534 bytes, aucun job)
# Solution retenue : RapidAPI Startup Jobs API
#   Endpoint : GET https://startup-jobs-api.p.rapidapi.com/active-jb-7d
#   Params   : source=wellfound, title_filter=product+manager
#   Plan     : BASIC — 10 appels/mois (reset 1er du mois)

RAPIDAPI_HOST = "startup-jobs-api.p.rapidapi.com"
RAPIDAPI_URL = f"https://{RAPIDAPI_HOST}/active-jb-7d"
QUOTA_FILE = Path.home() / ".ai-suite" / "wellfound_quota.json"
MONTHLY_LIMIT = 8  # keep 2 calls in reserve out of 10


def _load_quota() -> dict:
    try:
        if QUOTA_FILE.exists():
            return json.loads(QUOTA_FILE.read_text())
    except Exception:
        pass
    return {"month": "", "calls_used": 0}


def _save_quota(quota: dict) -> None:
    QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUOTA_FILE.write_text(json.dumps(quota))


def _check_and_increment_quota() -> bool:
    """Returns True if an API call is allowed, increments counter. False if quota exceeded."""
    quota = _load_quota()
    current_month = date.today().strftime("%Y-%m")
    if quota.get("month") != current_month:
        quota = {"month": current_month, "calls_used": 0}

    if quota["calls_used"] >= MONTHLY_LIMIT:
        next_reset = f"1er {date.today().strftime('%B %Y').replace(date.today().strftime('%B'), _next_month())}"
        print(
            f"[Wellfound] ⚠️  Quota mensuel atteint ({MONTHLY_LIMIT}/10 appels utilisés). "
            f"Prochain reset : 1er du mois."
        )
        return False

    quota["calls_used"] += 1
    _save_quota(quota)
    return True


def _next_month() -> str:
    d = date.today()
    if d.month == 12:
        return date(d.year + 1, 1, 1).strftime("%B %Y")
    return date(d.year, d.month + 1, 1).strftime("%B %Y")


class WellfoundScraper(BaseScraper):
    SOURCE_NAME = "Wellfound"
    ENABLED = True

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        api_key = os.getenv("X_RAPIDAPI_KEY")
        if not api_key:
            print(f"[{self.SOURCE_NAME}] ⚠️ X_RAPIDAPI_KEY not set — skipping")
            return []

        if not _check_and_increment_quota():
            return []

        try:
            r = httpx.get(
                RAPIDAPI_URL,
                headers={
                    "X-RapidAPI-Key": api_key,
                    "X-RapidAPI-Host": RAPIDAPI_HOST,
                },
                params={
                    "source": "wellfound",
                    "title_filter": "product manager",
                },
                timeout=15,
            )

            if r.status_code == 429:
                print(f"[{self.SOURCE_NAME}] ⚠️ HTTP 429 — quota RapidAPI épuisé ce mois-ci")
                # Revert the counter increment since the call didn't succeed
                quota = _load_quota()
                quota["calls_used"] = max(0, quota["calls_used"] - 1)
                _save_quota(quota)
                return []

            if r.status_code != 200:
                print(f"[{self.SOURCE_NAME}] ⚠️ HTTP {r.status_code}")
                return []

            raw_jobs = r.json()
            if not isinstance(raw_jobs, list):
                print(f"[{self.SOURCE_NAME}] ⚠️ Unexpected response format")
                return []

            jobs = []
            for item in raw_jobs:
                # Location: prefer cities_derived, fall back to remote
                remote = item.get("remote_derived", False)
                cities = item.get("cities_derived") or []
                if remote:
                    location = "Remote"
                    work_mode = "remote"
                elif cities:
                    location = cities[0]
                    work_mode = "unknown"
                else:
                    location = "Unknown"
                    work_mode = "unknown"

                # Contract type hint
                emp_type = (item.get("employment_type") or "").upper()
                if "FULL_TIME" in emp_type:
                    contract_type = "permanent"
                elif "CONTRACT" in emp_type or "FREELANCE" in emp_type:
                    contract_type = "freelance"
                else:
                    contract_type = "unknown"

                jobs.append(JobPosting(
                    source=self.SOURCE_NAME,
                    title=item.get("title", ""),
                    company=item.get("organization", ""),
                    location=location,
                    url=item.get("url", ""),
                    posted_date=None,
                    description=None,
                    tags=[],
                    salary=None,
                    work_mode=work_mode,
                    contract_type=contract_type,
                ))

            quota = _load_quota()
            print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched (quota: {quota['calls_used']}/{MONTHLY_LIMIT} appels utilisés ce mois)")
            return jobs

        except Exception as e:
            print(f"[{self.SOURCE_NAME}] ⚠️ Error: {e}")
            return []
