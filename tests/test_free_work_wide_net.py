"""Regression test: free_work scraper always fetches ALL curated slugs.

Locks Constitution Principle I — slug coverage must NEVER depend on the
profile. Any reintroduction of profile-driven slug gating (like the removed
SLUG_KEYWORDS + _relevant_slugs) will break this test.
"""

from models import JobFilter
from scrapers.boards.free_work import FreeWorkScraper


def test_free_work_always_fetches_all_slugs(monkeypatch):
    """fetch() must query all FREE_WORK_SLUGS regardless of job_filter.titles.

    Three different profiles — one narrow ("product"), one empty, one broad
    ("agile","scrum") — must all result in the same set of API calls.
    """
    slugs_called: list[str] = []

    def fake_fetch_slug(self, slug: str):
        slugs_called.append(slug)
        return []

    # Neutralize both sleep calls (_fetch_slug page delay + fetch slug-to-slug delay)
    monkeypatch.setattr("scrapers.boards.free_work.FreeWorkScraper._fetch_slug",
                        fake_fetch_slug)
    monkeypatch.setattr("scrapers.boards.free_work.time.sleep", lambda _: None)

    scraper = FreeWorkScraper(storage=None)

    # Case 1: narrow profile
    scraper.fetch(JobFilter(titles=["product"]))
    assert set(slugs_called) == set(FreeWorkScraper.FREE_WORK_SLUGS), (
        f"Case 1 failed: got {set(slugs_called)}, "
        f"expected all {len(FreeWorkScraper.FREE_WORK_SLUGS)} slugs"
    )
    slugs_called.clear()

    # Case 2: empty titles → same wide net
    scraper.fetch(JobFilter(titles=[]))
    assert set(slugs_called) == set(FreeWorkScraper.FREE_WORK_SLUGS), (
        f"Case 2 failed: got {set(slugs_called)}, "
        f"expected all {len(FreeWorkScraper.FREE_WORK_SLUGS)} slugs"
    )
    slugs_called.clear()

    # Case 3: different keywords → same wide net
    scraper.fetch(JobFilter(titles=["agile", "scrum"]))
    assert set(slugs_called) == set(FreeWorkScraper.FREE_WORK_SLUGS), (
        f"Case 3 failed: got {set(slugs_called)}, "
        f"expected all {len(FreeWorkScraper.FREE_WORK_SLUGS)} slugs"
    )
