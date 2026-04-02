from scrapers.base import BaseScraper
from models import JobFilter, JobPosting


class MaltScraper(BaseScraper):
    SOURCE_NAME = "Malt"
    # JS-rendered SPA — static scraping returns no job data
    # Note: freelance platform — jobs would get tag "freelance"
    ENABLED = False

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        print(f"[{self.SOURCE_NAME}] ⚠️ Disabled — JS-rendered SPA, no static job data available")
        return []
