import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from core.storage import normalize_url, normalize_title, normalize_company


@dataclass
class JobPosting:
    source: str
    title: str
    company: str
    location: str
    url: str
    canonical_url: Optional[str] = None  # computed in __post_init__ via normalize_url()
    norm_title: Optional[str] = None      # computed in __post_init__ via normalize_title()
    norm_company: Optional[str] = None    # computed in __post_init__ via normalize_company()
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
        if self.title:
            self.norm_title = normalize_title(self.title)
        if self.company:
            self.norm_company = normalize_company(self.company)

    @property
    def id(self) -> str:
        """Deterministic ID derived from canonical URL (or raw URL, or title+company+source as fallback)."""
        if not self.canonical_url and not self.url and (not self.title or not self.company):
            raise ValueError(
                "cannot compute job id: no URL and title or company is empty — "
                "this would collide with another posting missing the same field"
            )
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
            "norm_title": self.norm_title,
            "norm_company": self.norm_company,
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


# Relocated from core.job_actions._dict_to_posting (spec 036, FR-009) — the
# agents import this public name instead of the private helper, pinning the
# agent ↔ domain boundary.
def posting_from_dict(d: dict) -> JobPosting:
    """Reconstruct a JobPosting from a DB row dict (for storage write calls).
    Company-level fields (country, sector, size) come from companies table joins,
    not from the jobs table — they are absent from raw jobs rows after Phase 5.
    """
    posted_date = None
    raw_date = d.get("posted_date")
    if raw_date:
        try:
            posted_date = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass
    extracted_at = d.get("extracted_at")
    if extracted_at:
        try:
            extracted_at = datetime.strptime(str(extracted_at)[:19], "%Y-%m-%dT%H:%M:%S")
        except (ValueError, TypeError):
            extracted_at = None
    return JobPosting(
        source=d.get("source") or "",
        title=d.get("title") or "",
        company=d.get("company") or "",
        location=d.get("location") or "",
        url=d.get("url") or "",
        posted_date=posted_date,
        description=d.get("description"),
        tags=[],
        salary=None,
        work_mode=d.get("work_mode"),
        base_location=d.get("base_location"),
        summary=d.get("summary"),
        company_size=d.get("company_size"),
        contract_type=d.get("contract_type"),
        geo_zone=d.get("geo_zone"),
        country_code=d.get("country_code"),
        salary_text=d.get("salary_text"),
        comp_annual_eur=d.get("comp_annual_eur"),
        company_country=d.get("company_country"),
        industry_sector=d.get("industry_sector"),
        language_required=d.get("language_required"),
        extracted_at=extracted_at,
        extracted_by=d.get("extracted_by"),
        company_summary=d.get("company_summary"),
        company_website=d.get("company_website"),
    )
