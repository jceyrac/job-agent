from scrapers.base import BaseScraper
from models import JobFilter, JobPosting


class JobsChScraper(BaseScraper):
    SOURCE_NAME = "Jobs.ch"
    # No __NEXT_DATA__, content is JS-rendered — static scraping yields no results
    ENABLED = False

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        print(f"[{self.SOURCE_NAME}] ⚠️ Disabled — JS-rendered, no static job data available")
        return []
