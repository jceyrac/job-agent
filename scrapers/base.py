from abc import ABC, abstractmethod
from models import JobFilter, JobPosting


class BaseScraper(ABC):
    SOURCE_NAME: str = "Unknown"
    ENABLED: bool = True

    @abstractmethod
    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        ...
