import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class JobPosting:
    source: str
    title: str
    company: str
    location: str
    url: str
    posted_date: Optional[date] = None
    description: Optional[str] = None  # max 200 chars
    tags: list[str] = field(default_factory=list)
    salary: Optional[str] = None
    summary: Optional[str] = None
    work_mode: Optional[str] = None      # "remote" | "hybrid" | "on-site" | "unknown"
    base_location: Optional[str] = None  # physical anchor country/city, e.g. "United States", "London, UK", "Worldwide"
    company_size: Optional[str] = None   # "startup" | "scaleup" | "sme" | "large" | "unknown"
    contract_type: Optional[str] = None  # "permanent" | "freelance" | "contract" | "internship" | "unknown"
    geo_zone: Optional[str] = None       # "europe" | "us_only" | "apac" | "latam" | "global_remote" | "unknown"

    def __post_init__(self):
        if self.description and len(self.description) > 1000:
            self.description = self.description[:1000]

    @property
    def id(self) -> str:
        """Deterministic ID derived from URL (or title+company+source as fallback)."""
        key = self.url or f"{self.title}::{self.company}::{self.source}"
        return hashlib.sha256(key.encode()).hexdigest()[:20]

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "posted_date": self.posted_date.isoformat() if self.posted_date else None,
            "description": self.description,
            "tags": self.tags,
            "salary": self.salary,
            "summary": self.summary,
            "work_mode": self.work_mode,
            "base_location": self.base_location or "Not found",
            "company_size": self.company_size,
            "contract_type": self.contract_type,
            "geo_zone": self.geo_zone,
        }


@dataclass
class JobFilter:
    keywords: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    date_from: Optional[date] = None
    remote_only: bool = False
    remote_or_hybrid: bool = False
    company_sizes: list[str] = field(default_factory=list)    # OR filter, empty = no filter
    contract_types: list[str] = field(default_factory=list)   # OR filter, empty = no filter
    allowed_geo_zones: list[str] = field(default_factory=list)  # OR filter, empty = no filter
