"""
Shared assertion logic for all scraper live tests.

Each scraper is tested against the same contract:
  - returns ≥ 1 result
  - every result has non-empty title, company, url (http…)
  - posted_date is a date object (not None)
  - base_location is a non-empty string (not None / "Not found")
  - work_mode is one of the known values

Fields listed in optional_fields are best-effort for a given scraper:
a missing value raises ⚠️  instead of ❌ and does not affect PASS/FAIL.
"""

import sys
import os
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import JobFilter, JobPosting

VALID_WORK_MODES = {"remote", "hybrid", "on-site", "unknown"}
N_SAMPLE = 5


def _check(value, label: str) -> tuple[bool, str]:
    if label == "title":
        ok = isinstance(value, str) and len(value.strip()) > 0
        return ok, value[:40] if ok else f"EMPTY ({value!r})"
    if label == "company":
        ok = isinstance(value, str) and len(value.strip()) > 0
        return ok, value[:30] if ok else f"EMPTY ({value!r})"
    if label == "url":
        ok = isinstance(value, str) and value.startswith("http")
        return ok, value[:50] if ok else f"BAD ({value!r})"
    if label == "posted_date":
        ok = isinstance(value, date)
        return ok, str(value) if ok else f"None/invalid ({value!r})"
    if label == "base_location":
        ok = isinstance(value, str) and len(value.strip()) > 0 and value != "Not found"
        return ok, value[:30] if ok else f"MISSING ({value!r})"
    if label == "work_mode":
        ok = value in VALID_WORK_MODES
        return ok, value if ok else f"INVALID ({value!r})"
    return False, f"unknown label {label!r}"


FIELDS = ["title", "company", "url", "posted_date", "base_location", "work_mode"]


class ScraperResult:
    def __init__(self, scraper_name: str, optional_fields: set[str] | None = None):
        self.scraper_name = scraper_name
        self.optional_fields: set[str] = optional_fields or set()
        self.status = "PASS"
        self.skip_reason: str = ""
        self.error: str = ""
        self.n_results: int = 0
        self.field_stats: dict[str, tuple[int, int, list]] = {f: (0, 0, []) for f in FIELDS}

    def mark_skip(self, reason: str):
        self.status = "SKIP"
        self.skip_reason = reason

    def mark_error(self, err: str):
        self.status = "ERROR"
        self.error = err

    def record(self, jobs: list[JobPosting]):
        self.n_results = len(jobs)
        if not jobs:
            self.status = "FAIL"
            return
        sample = jobs[:N_SAMPLE]
        hard_fail = False
        for field in FIELDS:
            passed, total, examples = 0, 0, []
            for job in sample:
                val = getattr(job, field, None)
                ok, detail = _check(val, field)
                total += 1
                if ok:
                    passed += 1
                elif field not in self.optional_fields:
                    hard_fail = True
                examples.append((ok, detail))
            self.field_stats[field] = (passed, total, examples)
        if hard_fail:
            self.status = "FAIL"


def run_scraper(scraper_class, env_key: str = "",
                optional_fields: set[str] | None = None) -> ScraperResult:
    result = ScraperResult(scraper_class.SOURCE_NAME, optional_fields)

    if env_key and not os.getenv(env_key):
        result.mark_skip(f"missing env var {env_key}")
        return result

    try:
        scraper = scraper_class()
        jobs = scraper.fetch(JobFilter())
        result.record(jobs)
    except Exception as e:
        result.mark_error(str(e))

    return result


def print_result(r: ScraperResult):
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⚠️ ", "ERROR": "💥"}.get(r.status, "?")
    print(f"\n{icon} {r.scraper_name}  [{r.status}]")

    if r.status == "SKIP":
        print(f"   {r.skip_reason}")
        return
    if r.status == "ERROR":
        print(f"   {r.error}")
        return

    print(f"   {r.n_results} results returned")
    for field, (passed, total, examples) in r.field_stats.items():
        is_optional = field in r.optional_fields
        if passed == total:
            bar = "✅"
        elif is_optional:
            bar = "⚠️ "   # known limitation — not a hard failure
        else:
            bar = "❌"
        opt_note = " (optional)" if is_optional and passed < total else ""
        print(f"   {bar} {field:<14} {passed}/{total}{opt_note}", end="")
        fails = [detail for ok, detail in examples if not ok]
        if fails:
            print(f"  — {fails[0]}", end="")
        print()
