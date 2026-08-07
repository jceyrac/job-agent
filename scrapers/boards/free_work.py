"""Free-Work.com scraper — API JSON publique api.free-work.com/job_postings.

Scrapes the JSON-LD/Hydra API with scoping by job category slug.
All fields are structured — no HTML scraping, no detail pages, no auth.

Read-only, API-first. Backend: Turnover-IT / AGSI.

Spec 025.
"""

import re
import time
from datetime import date, datetime

import requests

from models import JobFilter, JobPosting
from scrapers.base import BaseScraper

# ── Config ─────────────────────────────────────────────────────────────────
API_URL = "https://api.free-work.com/job_postings"
BASE_URL = "https://www.free-work.com/fr/tech-it/job-mission"
USER_AGENT = "job-agent/1.0 (jceyrac@pm.me)"
HEADERS = {"User-Agent": USER_AGENT}
ITEMS_PER_PAGE = 100
REQUEST_DELAY = 1.0         # seconds between slug requests (courtesy)
MAX_PAGES_PER_SLUG = 10     # safety cap

# Job category slugs to scope the search (modifiable without touching logic).
# These are the "where to look" filter — not a desirability filter.
FREE_WORK_SLUGS = [
    # Cœur PM/PO
    "product-owner",
    "responsable-produit",
    "chef-de-projet-informatique",
    "chef-de-projet-digital",
    # Agile / Leadership projet
    "project-management-officer",
    "scrum-master",
    "directeur-de-projet",
    "coach-agile",
    # Data / Transformation (PM-adjacent)
    "directeur-de-la-data-cdo",
    "directeur-de-la-transformation-digitale-cdo",
    "manager-de-transition",
]


class FreeWorkScraper(BaseScraper):
    SOURCE_NAME = "free_work"
    ENABLED = True
    ACQUISITION_MODEL = "board"
    SUPPORTS_DISCOVERY = True

    # Overridable for testing (quickstart scenarios)
    FREE_WORK_SLUGS = FREE_WORK_SLUGS

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _strip_html(html_text: str | None) -> str:
        """Strip HTML tags and collapse whitespace."""
        if not html_text:
            return ""
        text = re.sub(r"<[^>]+>", " ", html_text)
        text = re.sub(r"&[a-z]+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _build_summary(posting: dict) -> str | None:
        """Build FR-readable summary from experienceLevel + duration info."""
        parts = []

        level = posting.get("experienceLevel")
        if level and level != "unknown":
            parts.append(f"Niveau: {level}.")

        contracts = posting.get("contracts", [])
        is_freelance = "contractor" in contracts

        if is_freelance:
            dur_val = posting.get("durationValue")
            dur_period = posting.get("durationPeriod")
            renewable = posting.get("renewable")

            if dur_val and dur_period:
                period_fr = {"month": "mois", "year": "an", "week": "semaine"}
                period_label = period_fr.get(dur_period, dur_period)
                if dur_val > 1:
                    period_label += "s"
                dur_str = f"{dur_val} {period_label}"
            else:
                duration = posting.get("duration")
                dur_str = f"{duration} mois" if duration else None

            if dur_str:
                ren_str = "renouvelable" if renewable else "non renouvelable"
                parts.append(f"Mission: {dur_str} ({ren_str}).")

        return " ".join(parts) if parts else None

    @staticmethod
    def _build_salary(posting: dict) -> tuple[str | None, str | None]:
        """Return (salary, salary_text) formatted from API fields."""
        contracts = posting.get("contracts", [])
        is_freelance = "contractor" in contracts

        if is_freelance:
            min_s = posting.get("minDailySalary")
            max_s = posting.get("maxDailySalary")
            if min_s and max_s:
                salary = f"{min_s}-{max_s} €/jour"
                return salary, salary
            if min_s and not max_s:
                salary = f"{min_s} €/jour"
                return salary, salary
            if max_s and not min_s:
                salary = f"{max_s} €/jour"
                return salary, salary
            return None, None
        else:
            min_s = posting.get("minAnnualSalary")
            max_s = posting.get("maxAnnualSalary")
            if min_s and max_s:
                salary = f"{min_s // 1000}k-{max_s // 1000}k €/an"
                salary_text = f"{min_s}-{max_s} €/an"
                return salary, salary_text
            if min_s and not max_s:
                salary = f"{min_s // 1000}k €/an"
                salary_text = f"{min_s} €/an"
                return salary, salary_text
            if max_s and not min_s:
                salary = f"{max_s // 1000}k €/an"
                salary_text = f"{max_s} €/an"
                return salary, salary_text
            return None, None

    @staticmethod
    def _contract_type(contracts: list[str]) -> str | None:
        """Map API contracts array to contract_type string.

        On double-contract ["permanent", "contractor"] → "freelance"
        (cross-border actionability per spec FR-8).
        """
        if not contracts:
            return None
        if "contractor" in contracts:
            return "freelance"
        if "permanent" in contracts:
            return "permanent"
        return None

    @staticmethod
    def _contract_tags(contracts: list[str]) -> list[str]:
        """Preserve raw contract info as tags."""
        return [f"contract:{c}" for c in contracts]

    @staticmethod
    def _work_mode(remote_mode: str | None) -> str | None:
        if not remote_mode:
            return "on-site"
        mapping = {"full": "remote", "partial": "hybrid"}
        return mapping.get(remote_mode, "on-site")

    def _build_jobposting(self, posting: dict) -> JobPosting:
        """Map one API posting dict → JobPosting dataclass."""
        title = posting.get("title", "") or ""
        company_name = (posting.get("company") or {}).get("name", "") or ""
        location_data = posting.get("location") or {}
        location = location_data.get("label", "") or ""
        country_code = location_data.get("countryCode", "")
        base_country = location_data.get("country", "")
        base_location = base_country if base_country else None

        # URL: /fr/tech-it/job-mission/{job.slug}/{posting.slug}
        job_data = posting.get("job") or {}
        job_slug = job_data.get("slug", "")
        posting_slug = posting.get("slug", "")
        url = f"{BASE_URL}/{job_slug}/{posting_slug}" if job_slug and posting_slug else ""

        # Description: concat + strip HTML + truncate
        desc_parts = [
            posting.get("description") or "",
            posting.get("candidateProfile") or "",
            posting.get("companyDescription") or "",
        ]
        description = " ".join(self._strip_html(p) for p in desc_parts).strip()
        if len(description) > 3000:
            description = description[:3000]

        # Posted date
        posted_date = None
        published_at = posting.get("publishedAt")
        if published_at:
            try:
                posted_date = date.fromisoformat(published_at[:10])
            except (ValueError, TypeError):
                pass

        # Contract
        contracts = posting.get("contracts") or []
        contract_type = self._contract_type(contracts)

        # Salary
        salary, salary_text = self._build_salary(posting)

        # Work mode
        work_mode = self._work_mode(posting.get("remoteMode"))

        # Summary
        summary = self._build_summary(posting)

        # Tags: skills + contract tags
        tags = []
        for skill in posting.get("skills") or []:
            skill_name = skill.get("name") if isinstance(skill, dict) else None
            if skill_name:
                tags.append(skill_name)
        tags.extend(self._contract_tags(contracts))

        return JobPosting(
            source=self.SOURCE_NAME,
            title=title,
            company=company_name,
            location=location,
            url=url,
            description=description or None,
            posted_date=posted_date,
            salary=salary,
            salary_text=salary_text,
            summary=summary,
            work_mode=work_mode,
            base_location=base_location,
            contract_type=contract_type,
            country_code=country_code if country_code else None,
            tags=tags,
            # geo_zone, company_size, industry_sector, language_required
            # left to None — derived by the scorer per constitution
        )

    def _fetch_slug(self, slug: str) -> list[dict]:
        """Fetch all job postings for a single job category slug.

        Paginates until exhausted (or MAX_PAGES_PER_SLUG safety cap).
        Returns raw posting dicts (not yet mapped to JobPosting).
        """
        results: list[dict] = []
        for page in range(1, MAX_PAGES_PER_SLUG + 1):
            params = {
                "page": page,
                "itemsPerPage": ITEMS_PER_PAGE,
                "jobs": slug,
            }
            try:
                resp = requests.get(
                    API_URL, params=params, headers=HEADERS, timeout=30
                )
                if resp.status_code != 200:
                    print(
                        f"  ⚠️  [free_work] slug '{slug}' returned "
                        f"HTTP {resp.status_code} on page {page} — stopping"
                    )
                    break

                data = resp.json()
                members = data.get("hydra:member") or []
                results.extend(members)

                total = data.get("hydra:totalItems", 0)
                if len(results) >= total or len(members) < ITEMS_PER_PAGE:
                    break

                time.sleep(0.3)  # courtesy delay between pages of same slug

            except requests.RequestException as e:
                print(f"  ⚠️  [free_work] slug '{slug}' page {page}: {e}")
                break
            except (ValueError, KeyError) as e:
                print(f"  ⚠️  [free_work] slug '{slug}' page {page}: "
                      f"invalid JSON — {e}")
                break

        return results

    # ── Main fetch ──────────────────────────────────────────────────────

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        """Fetch job postings from Free-Work API, scoped to FREE_WORK_SLUGS.

        Iterates over slugs, paginates per slug, deduplicates by API id,
        and returns JobPosting objects. No desirability filtering —
        that's the scorer's job.
        """
        postings: list[JobPosting] = []
        seen_ids: set[int] = set()
        n_skipped = 0

        # Scope de source statique et curé (Principe I) : la sélection de slugs ne
        # dépend JAMAIS du profil. Le filtrage de désirabilité est au scorer.
        slugs = list(self.FREE_WORK_SLUGS)
        n_slugs = len(slugs)

        for slug_idx, slug in enumerate(slugs, start=1):
            t0 = time.monotonic()

            raw = self._fetch_slug(slug)
            n_raw = len(raw)
            n_mapped = 0

            for posting in raw:
                pid = posting.get("id")
                if pid is not None and pid in seen_ids:
                    n_skipped += 1
                    continue
                if pid is not None:
                    seen_ids.add(pid)

                job = self._build_jobposting(posting)
                postings.append(job)
                n_mapped += 1

            elapsed = time.monotonic() - t0
            print(
                f"  [free_work] '{slug}' ({slug_idx}/{n_slugs}): "
                f"{n_raw} fetched, {n_mapped} new ({elapsed:.1f}s)"
            )

            # Courtesy delay between slugs
            if slug_idx < n_slugs:
                time.sleep(REQUEST_DELAY)

        # Summary
        freelance = sum(1 for p in postings if p.contract_type == "freelance")
        permanent = sum(1 for p in postings if p.contract_type == "permanent")
        print(
            f"free_work: {len(postings)} jobs after dedup "
            f"({n_skipped} cross-listed skipped), "
            f"{permanent} CDI, {freelance} freelance"
        )

        return postings
