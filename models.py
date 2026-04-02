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
    company_size: Optional[str] = None   # "startup" | "scaleup" | "sme" | "large" | "unknown"
    contract_type: Optional[str] = None  # "permanent" | "freelance" | "contract" | "internship" | "unknown"

    def __post_init__(self):
        if self.description and len(self.description) > 200:
            self.description = self.description[:200]

    def to_json(self) -> dict:
        return {
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
            "company_size": self.company_size,
            "contract_type": self.contract_type,
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
    company_sizes: list[str] = field(default_factory=list)   # OR filter, empty = no filter
    contract_types: list[str] = field(default_factory=list)  # OR filter, empty = no filter
