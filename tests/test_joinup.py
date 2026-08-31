"""Offline tests for the Joinup scraper (Spec 027).

Every test runs against captured fixtures — no network I/O. Page fetching is
monkeypatched to serve fixture data; the wide-net test locks Constitution I
(no relevance filtering inside the scraper).
"""

import os
from datetime import timedelta

from models import JobFilter
from scrapers.boards.joinup import JoinupScraper

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_results(filename: str) -> dict:
    with open(os.path.join(FIXTURES_DIR, filename), encoding="utf-8") as f:
        html = f.read()
    scraper = JoinupScraper()
    data = scraper._extract_next_data(html)
    assert data is not None, f"{filename}: __NEXT_DATA__ not found"
    results = scraper._page_results(data)
    assert results is not None, f"{filename}: results[0] shape unexpected"
    return results


PAGE1 = _load_results("joinup_browse_jobs.html")
PAGE2 = _load_results("joinup_browse_jobs_page2.html")


def _install_fake_fetch(monkeypatch, pages: dict):
    """Patch JoinupScraper._fetch_page to serve `pages` (dict page→results) and
    patch time.sleep. Returns the list of requested page numbers (int)."""
    requested: list[int] = []

    def fake_fetch_page(self, page):
        requested.append(page)
        return pages.get(page)

    monkeypatch.setattr(JoinupScraper, "_fetch_page", fake_fetch_page)
    monkeypatch.setattr("scrapers.boards.joinup.time.sleep", lambda _: None)
    return requested


def _with_nbpages(results: dict, nb_pages: int) -> dict:
    """Copy a results dict with nbPages overridden (bounds pagination in tests)."""
    return {"hits": results["hits"], "nbPages": nb_pages, "nbHits": results["nbHits"]}


class _FakeStorage:
    def __init__(self):
        self.config_calls: list[tuple] = []

    def get_config(self, key):
        return None

    def set_config(self, key, value):
        self.config_calls.append((key, value))


# ── Parsing / mapping ────────────────────────────────────────────────────────

def test_parses_page1_fixture():
    jobs = [JoinupScraper._hit_to_job(h) for h in PAGE1["hits"]]
    assert len(jobs) == 10
    for job in jobs:
        assert job.source == "Joinup"
        assert job.title and job.title.strip()
        assert job.company and job.company.strip()
        assert job.url.startswith("https://joinup.ch/job/")


def test_id_10230_mapping():
    job = JoinupScraper._hit_to_job(PAGE1["hits"][0])
    assert job.url == (
        "https://joinup.ch/job/"
        "founding-commercial-partner-68b89d3c-9a42-46d8-8e66-2bf0540909a0-10230"
    )
    assert job.company == "Quantum Highlands"
    assert job.location == "Remote"
    assert job.work_mode == "remote"
    assert job.posted_date is not None


def test_non_remote_location_work_mode_none():
    zurich = next(
        JoinupScraper._hit_to_job(h)
        for h in PAGE1["hits"]
        if h.get("location") == "Zürich"
    )
    assert zurich.location == "Zürich"
    assert zurich.work_mode is None


# ── Pagination / wide-net ────────────────────────────────────────────────────

def test_page2_disjoint_older_ids(monkeypatch):
    pages = {1: _with_nbpages(PAGE1, 2), 2: _with_nbpages(PAGE2, 2)}
    requested = _install_fake_fetch(monkeypatch, pages)

    jobs = JoinupScraper().fetch(JobFilter())

    assert requested == [1, 2]
    assert len(jobs) == 20  # disjoint → no hit-id dedup dropped any
    ids1 = {h["id"] for h in PAGE1["hits"]}
    ids2 = {h["id"] for h in PAGE2["hits"]}
    assert ids1.isdisjoint(ids2)
    newest_page2 = max(
        JoinupScraper._hit_to_job(h).posted_date for h in PAGE2["hits"]
    )
    oldest_page1 = min(
        JoinupScraper._hit_to_job(h).posted_date for h in PAGE1["hits"]
    )
    assert newest_page2 < oldest_page1  # page 2 strictly older


def test_date_cutoff_stops_pagination(monkeypatch):
    # date_from just past page-2's newest job → page 2 entirely older.
    page2_newest = max(
        JoinupScraper._hit_to_job(h).posted_date for h in PAGE2["hits"]
    )
    date_from = page2_newest + timedelta(days=1)

    # nbPages=10 so that, absent the cutoff, fetch() would keep paging.
    pages = {1: _with_nbpages(PAGE1, 10), 2: _with_nbpages(PAGE2, 10)}
    requested = _install_fake_fetch(monkeypatch, pages)

    jobs = JoinupScraper().fetch(JobFilter(date_from=date_from))

    assert requested == [1, 2]  # page 3+ never requested
    # The scraper still returns everything it parsed (page 1 + page 2); the
    # date filter itself is the orchestrator's JobFilterEngine, not the scraper.
    assert len(jobs) == 20


def test_wide_net_no_relevance_filter(monkeypatch):
    """fetch() must request identical pages regardless of job_filter.titles."""
    pages = {1: _with_nbpages(PAGE1, 2), 2: _with_nbpages(PAGE2, 2)}
    requested = _install_fake_fetch(monkeypatch, pages)

    scraper = JoinupScraper()
    for titles in (["product"], [], ["agile", "scrum"]):
        requested.clear()
        jobs = scraper.fetch(JobFilter(titles=titles))
        assert requested == [1, 2], f"titles={titles} → pages {requested}"
        assert len(jobs) == 20  # same wide net, no title filtering


def test_malformed_next_data_returns_empty(monkeypatch):
    storage = _FakeStorage()
    requested = _install_fake_fetch(monkeypatch, {})  # _fetch_page → None
    scraper = JoinupScraper(storage=storage)

    jobs = scraper.fetch(JobFilter())

    assert jobs == []
    assert requested == [1]
    assert storage.config_calls == []  # no auto-disable on parse failure
