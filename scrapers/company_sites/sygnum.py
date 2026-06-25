"""Sygnum Bank career scraper — custom WordPress AJAX endpoint.

Careers page: https://www.sygnum.com/careers-portal/
API: wp-admin/admin-ajax.php?action=fetch_careers (nonce required)
The nonce is static (embedded in page HTML as data-nonce attribute).
"""

import re
import time

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

BASE_URL = "https://www.sygnum.com"
CAREERS_PATH = "/careers-portal/"
AJAX_PATH = "/wp-admin/admin-ajax.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}{CAREERS_PATH}",
}


class SygnumScraper(BaseScraper):
    SOURCE_NAME = "Sygnum"
    ENABLED = True

    def fetch(self, job_filter: JobFilter | None = None) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        try:
            with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
                # Step 1: Fetch the careers page to get the nonce
                r = client.get(f"{BASE_URL}{CAREERS_PATH}")
                if r.status_code != 200:
                    print(f"  [{self.SOURCE_NAME}] Page HTTP {r.status_code}")
                    return []

                # Extract nonce from data-nonce attribute
                match = re.search(r'data-nonce="([^"]+)"', r.text)
                if not match:
                    print(f"  [{self.SOURCE_NAME}] Nonce not found in page")
                    return []
                nonce = match.group(1)

                # Step 2: Fetch job listings via AJAX
                r2 = client.get(f"{BASE_URL}{AJAX_PATH}",
                                params={"action": "fetch_careers",
                                        "_wpnonce": nonce})
                if r2.status_code != 200:
                    print(f"  [{self.SOURCE_NAME}] AJAX HTTP {r2.status_code}")
                    return []

                data = r2.json()
                if not isinstance(data, list):
                    print(f"  [{self.SOURCE_NAME}] Unexpected response format")
                    return []

                for item in data:
                    title = item.get("title", "")
                    location = item.get("location", "") or "Unknown"
                    department = item.get("department", "")
                    url = item.get("application_url", "") or \
                          f"{BASE_URL}{CAREERS_PATH}"

                    work_type = item.get("work_type", "")
                    if "home office" in work_type.lower():
                        work_mode = "hybrid"
                    elif "remote" in work_type.lower():
                        work_mode = "remote"
                    else:
                        work_mode = None

                    jobs.append(JobPosting(
                        source=self.SOURCE_NAME,
                        title=title,
                        company="Sygnum Bank",
                        location=location,
                        url=url,
                        posted_date=None,
                        description=department or None,
                        work_mode=work_mode,
                        base_location=location,
                    ))
        except Exception as e:
            print(f"  [{self.SOURCE_NAME}] {e}")
            return []

        print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched")
        return jobs
