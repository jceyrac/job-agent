from scrapers.base import BaseScraper
from models import JobFilter, JobPosting


class BeInCryptoJobsScraper(BaseScraper):
    SOURCE_NAME = "BeInCrypto Jobs"
    # JS-rendered — static scraping returns 0 job links
    ENABLED = False

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        print(f"[{self.SOURCE_NAME}] ⚠️ Disabled — site is JS-rendered, no static job data available")
        return []
