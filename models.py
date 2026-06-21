import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from storage import normalize_url


@dataclass
class JobPosting:
    source: str
    title: str
    company: str
    location: str
    url: str
    canonical_url: Optional[str] = None  # computed in __post_init__ via normalize_url()
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

    # Phase 1e — profile-independent extraction fields, persisted on the jobs table
    company_country: Optional[str] = None    # extracted by LLM
    industry_sector: Optional[str] = None    # controlled list (web3_crypto, fintech, ...)
    language_required: Optional[str] = None  # controlled list (english, french, ...)
    extracted_at: Optional[datetime] = None  # NULL = extraction not yet run
    extracted_by: Optional[str] = None        # model identifier that performed extraction

    # Monitored companies — provenance
    monitored_company_id: Optional[int] = None  # FK → companies.id, set at scrape time
    filtered_non_product: bool = False          # title gate disposition
    country_code: Optional[str] = None        # ISO 3166-1 alpha-2, e.g. CH, DE, FR, US
    salary_text: Optional[str] = None           # free-text salary signal from extraction
    comp_annual_eur: Optional[int] = None       # normalized annual EUR, when parseable

    # Company-level metadata extracted alongside job fields (pushed to companies table)
    company_summary: str | None = None
    company_website: str | None = None

    def __post_init__(self):
        if self.description and len(self.description) > 3000:
            self.description = self.description[:3000]
        if self.url and self.source:
            self.canonical_url = normalize_url(self.url, self.source)

    @property
    def id(self) -> str:
        """Deterministic ID derived from canonical URL (or raw URL, or title+company+source as fallback)."""
        key = self.canonical_url or self.url or f"{self.title}::{self.company}::{self.source}"
        return hashlib.sha256(key.encode()).hexdigest()[:20]

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "canonical_url": self.canonical_url,
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
            "company_country": self.company_country,
            "industry_sector": self.industry_sector,
            "language_required": self.language_required,
            "extracted_at": self.extracted_at.isoformat() if self.extracted_at else None,
            "extracted_by": self.extracted_by,
            "country_code": self.country_code,
            "salary_text": self.salary_text,
            "comp_annual_eur": self.comp_annual_eur,
            "company_summary": self.company_summary,
            "company_website": self.company_website,
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
