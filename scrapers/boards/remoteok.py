import json
import re
import urllib.request
from datetime import date, datetime, timezone
from scrapers.base import BaseScraper
from models import JobFilter, JobPosting


class RemoteOKScraper(BaseScraper):
    SOURCE_NAME = "RemoteOK"
    ENABLED = True
    API_URL = "https://remoteok.com/api"

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        try:
            req = urllib.request.Request(
                self.API_URL,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

            # First element is metadata — skip it
            jobs = []
            for item in data[1:]:
                posted_date = None
                raw_date = item.get("date")
                if raw_date:
                    try:
                        posted_date = datetime.fromisoformat(raw_date).date()
                    except ValueError:
                        pass

                description = item.get("description", "")
                if description:
                    description = re.sub(r"<[^>]+>", " ", description).strip()
                    description = re.sub(r"\s+", " ", description)

                salary = None
                s_min = item.get("salary_min", 0)
                s_max = item.get("salary_max", 0)
                if s_min or s_max:
                    salary = f"${s_min:,}–${s_max:,}"

                raw_loc = item.get("location") or ""
                # RemoteOK is a remote-first board — all jobs are remote-ok.
                # Empty / "Remote" = truly global; a city/country = remote but anchored there.
                location = raw_loc if raw_loc else "Remote"
                base_location = raw_loc if raw_loc and raw_loc.lower() != "remote" else None

                jobs.append(JobPosting(
                    source=self.SOURCE_NAME,
                    title=item.get("position", ""),
                    company=item.get("company", ""),
                    location=location,
                    url=item.get("url", ""),
                    posted_date=posted_date,
                    description=description,
                    tags=item.get("tags", []),
                    salary=salary,
                    work_mode="remote",
                    base_location=base_location,
                ))
            return jobs
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] Error fetching API: {e}")
            return []
