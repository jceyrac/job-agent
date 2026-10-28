from __future__ import annotations

from abc import ABC, abstractmethod

from core.models import JobFilter, JobPosting


class BaseScraper(ABC):
    SOURCE_NAME: str = "Unknown"
    ENABLED: bool = True
    ACQUISITION_MODEL: str = "board"        # "board" | "company_keyed"
    SUPPORTS_DISCOVERY: bool = True         # runs a seed/known-company sweep in the broad path

    def __init__(
        self,
        targets: list[dict] | None = None,
        locations: list[str] | None = None,
        boards: list[str] | None = None,
        slugs: list[str] | None = None,
        title_families: list[str] | None = None,
        enabled: bool = True,
    ):
        # Scrapers are pure functions (spec 045, FR-001): every input the
        # orchestrator used to let them read from storage/profile is now injected.
        self._targets = targets  # list of company dicts with ats_provider, ats_identifier, etc.
        self._locations = locations
        self._boards = boards
        self._slugs = slugs
        self._title_families = title_families
        self._enabled = enabled
        self.auto_disabled_reason: str | None = None

    @classmethod
    def slug(cls) -> str:
        """Config slug for this scraper (lowercase, punctuation → underscore)."""
        return cls.SOURCE_NAME.lower().replace(" ", "_").replace(":", "_")

    @classmethod
    def enabled_config_key(cls) -> str:
        """Single source of truth for the scraper.enabled config key."""
        return f"scraper.{cls.slug()}.enabled"

    @classmethod
    def _config_prefix(cls) -> str:
        """Config key prefix for this scraper (without trailing dot)."""
        return f"scraper.{cls.slug()}"

    def report_disable(self, reason: str) -> None:
        """Report an auto-disable decision for the orchestrator to persist.

        The scraper no longer writes config itself (pure function) — it records
        the event on the instance and the orchestrator (`core/scrape.py` direct
        path, or `POST /v1/ingest/events` on the API path) persists it.
        """
        self.auto_disabled_reason = reason
        print(f"  🚫 [{self.SOURCE_NAME}] auto-disable requested: {reason}")

    @abstractmethod
    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        ...
