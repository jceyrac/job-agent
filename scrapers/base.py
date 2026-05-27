from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from models import JobFilter, JobPosting

if TYPE_CHECKING:
    from storage import JobStorage


class BaseScraper(ABC):
    SOURCE_NAME: str = "Unknown"
    ENABLED: bool = True

    def __init__(self, storage: JobStorage = None):
        self._storage = storage

    def is_enabled(self) -> bool:
        if self._storage is None:
            return self.ENABLED
        key = f"scraper.{self.SOURCE_NAME.lower().replace(' ', '_').replace(':', '_')}.enabled"
        val = self._storage.get_config(key)
        if val is None:
            return self.ENABLED
        return val.lower() == "true"

    def disable(self, reason: str) -> None:
        if self._storage is None:
            return
        prefix = f"scraper.{self.SOURCE_NAME.lower().replace(' ', '_').replace(':', '_')}"
        self._storage.set_config(f"{prefix}.enabled", "false")
        self._storage.set_config(f"{prefix}.disabled_reason", reason)
        print(f"  🚫 [{self.SOURCE_NAME}] auto-disabled: {reason}")

    @abstractmethod
    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        ...
