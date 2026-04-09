import json
import re
from datetime import date, datetime
from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

import httpx

BASE_URL = "https://tietalent.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://google.com",
}


class TieTalentScraper(BaseScraper):
    SOURCE_NAME = "TieTalent"
    ENABLED = True
    URL = f"{BASE_URL}/en/jobs?q=product+manager&location=Switzerland"

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        try:
            from bs4 import BeautifulSoup
            r = httpx.get(self.URL, headers=HEADERS, timeout=15, follow_redirects=True)
            if r.status_code != 200:
                print(f"[{self.SOURCE_NAME}] ⚠️ HTTP {r.status_code}")
                return []

            soup = BeautifulSoup(r.text, "html.parser")
            script = soup.find("script", id="__NEXT_DATA__")
            if not script:
                print(f"[{self.SOURCE_NAME}] ⚠️ __NEXT_DATA__ not found")
                return []

            data = json.loads(script.string)
            raw_jobs = data["props"]["pageProps"].get("liveJobs", [])

            jobs = []
            for item in raw_jobs:
                locations = item.get("locations", [])
                remote_only = item.get("remoteOnly", False)
                if remote_only:
                    location = "Remote"
                else:
                    location = locations[0]["name"] if locations else "Switzerland"

                posted_date = None
                raw_date = item.get("publishedAt", "")
                if raw_date:
                    try:
                        posted_date = datetime.fromisoformat(raw_date[:10]).date()
                    except ValueError:
                        pass

                description = item.get("description") or ""
                if description:
                    description = re.sub(r"<[^>]+>", " ", description)
                    description = re.sub(r"\s+", " ", description).strip()

                tags = [s["name"] for s in item.get("skills", [])]
                work_mode = "remote" if remote_only else "unknown"

                country = (locations[0].get("country") if locations else None) or None
                base_location = country if country else ("Worldwide" if remote_only else None)

                jobs.append(JobPosting(
                    source=self.SOURCE_NAME,
                    title=item.get("name", ""),
                    company=item.get("companyName", ""),
                    location=location,
                    url=f"{BASE_URL}/en/jobs/{item['id']}",
                    posted_date=posted_date,
                    description=description or None,
                    tags=tags,
                    salary=None,
                    work_mode=work_mode,
                    base_location=base_location,
                ))
            print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched")
            return jobs
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] ⚠️ Error: {e}")
            return []
