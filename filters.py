import re
from datetime import date, timedelta

from models import JobFilter, JobPosting


class JobFilterEngine:
    @staticmethod
    def apply(jobs: list[JobPosting], job_filter: JobFilter) -> tuple[list[JobPosting], int, int]:
        results = []
        excluded_date = 0
        cutoff = job_filter.date_from or (date.today() - timedelta(days=30))

        for job in jobs:
            # Date filter: freshness window (configurable via date_from; 30d fallback)
            # None means undated — exclude so stale jobs can't slip through
            if not job.posted_date or job.posted_date < cutoff:
                excluded_date += 1
                continue

            searchable = " ".join([
                job.title,
                job.company,
                job.location,
                job.description or "",
                " ".join(job.tags),
            ]).lower()

            # Exclude if any exclude word is present (word-boundary match, so
            # "intern" doesn't false-positive on "international"/"internet")
            if any(re.search(rf"\b{re.escape(ex.lower())}\b", searchable) for ex in job_filter.exclude):
                continue

            # Title is a hard requirement: job title must match at least one PM title term
            if job_filter.titles:
                title_lower = job.title.lower()
                if not any(t.lower() in title_lower for t in job_filter.titles):
                    continue

            # Keywords are optional context (used for scoring, not hard filtering)
            # — no hard keyword gate here

            # Locations OR match
            if job_filter.locations:
                loc_lower = job.location.lower()
                if not any(loc.lower() in loc_lower for loc in job_filter.locations):
                    continue

            # Remote only filter
            if job_filter.remote_only:
                if "remote" not in job.location.lower():
                    continue

            # Remote or hybrid filter (excludes fully on-site jobs)
            if job_filter.remote_or_hybrid:
                loc_lower = job.location.lower()
                remote_signals = ("remote", "hybrid", "worldwide", "anywhere")
                if not any(s in loc_lower for s in remote_signals):
                    continue

            results.append(job)
        return results, excluded_date, 0
