from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from models import JobFilter, JobPosting

if TYPE_CHECKING:
    from storage import JobStorage


class BaseScraper(ABC):
    SOURCE_NAME: str = "Unknown"
    ENABLED: bool = True
    ACQUISITION_MODEL: str = "board"        # "board" | "company_keyed"
    SUPPORTS_DISCOVERY: bool = True         # runs a seed/known-company sweep in the broad path

    def __init__(self, storage: JobStorage = None, targets: list[dict] | None = None):
        self._storage = storage
        self._targets = targets  # list of company dicts with ats_provider, ats_identifier, etc.

    @classmethod
    def enabled_config_key(cls) -> str:
        """Single source of truth for the scraper.enabled config key."""
        slug = cls.SOURCE_NAME.lower().replace(" ", "_").replace(":", "_")
        return f"scraper.{slug}.enabled"

    @classmethod
    def _config_prefix(cls) -> str:
        """Config key prefix for this scraper (without trailing dot)."""
        slug = cls.SOURCE_NAME.lower().replace(" ", "_").replace(":", "_")
        return f"scraper.{slug}"

    def is_enabled(self) -> bool:
        if self._storage is None:
            return self.ENABLED
        val = self._storage.get_config(self.enabled_config_key())
        if val is None:
            return self.ENABLED
        return val.lower() == "true"

    def disable(self, reason: str) -> None:
        if self._storage is None:
            return
        prefix = self._config_prefix()
        self._storage.set_config(f"{prefix}.enabled", "false")
        self._storage.set_config(f"{prefix}.disabled_reason", reason)
        print(f"  🚫 [{self.SOURCE_NAME}] auto-disabled: {reason}")

    @abstractmethod
    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        ...
