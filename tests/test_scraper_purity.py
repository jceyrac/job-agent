"""Scraper purity (spec 045, FR-001 / SC-001).

Two layers:

1. A source-scan guard: no module under ``core/scrapers/`` may import
   ``core.storage``/``JobStorage``/``load_active_profile`` or call
   ``get_config``/``set_config``. Targets/locations/boards/slugs/enabled arrive
   injected by the orchestrator; auto-disable is reported back (never persisted)
   by the scraper.

2. Per-scraper unit tests with an injected plan slice + a fake HTTP/jobspy layer
   (no DB, no network): each of the five previously-storage-aware scrapers
   consumes its injected slice and returns postings.
"""

import pathlib
import re

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRAPERS_DIR = _PROJECT_ROOT / "core" / "scrapers"

# Banned: any import of the storage seam or the profile loader, or a config
# read/write call. `get_config`/`set_config` are JobStorage methods — a scraper
# calling them is reading/writing the DB directly.
_BANNED_RE = re.compile(
    r"core\.storage|\bJobStorage\b|\bload_active_profile\b|\bget_config\b|\bset_config\b"
)


def _strip_docstrings_and_comments(src: str) -> str:
    """Drop triple-quoted strings and # line comments so prose never false-positives."""
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"'''(?:.|\n)*?'''", "", src)
    src = re.sub(r"#[^\n]*", "", src)
    return src


def _scraper_files():
    yield from sorted(_SCRAPERS_DIR.rglob("*.py"))


# ── FR-001 guard: no storage/profile/config access under core/scrapers/ ────────

def test_scrapers_do_not_touch_storage_or_profile():
    offenders: dict[str, list[str]] = {}
    for path in _scraper_files():
        src = _strip_docstrings_and_comments(path.read_text(encoding="utf-8"))
        hits = [m.group(0) for m in _BANNED_RE.finditer(src)]
        if hits:
            offenders[str(path.relative_to(_PROJECT_ROOT))] = hits

    assert not offenders, (
        "scrapers must not import storage/profile or read/write config:\n"
        + "\n".join(f"  {p}: {h}" for p, hs in offenders.items() for h in hs)
    )


def test_scanner_detects_storage_and_profile_access():
    assert _BANNED_RE.search("from core.storage import JobStorage") is not None
    assert _BANNED_RE.search("load_active_profile(self._storage)") is not None
    assert _BANNED_RE.search("db.get_config('scraper.indeed.enabled')") is not None
    assert _BANNED_RE.search("self._storage.set_config(k, v)") is not None


def test_scanner_ignores_injected_plan_and_report_disable():
    # The post-refactor surface: injected slices + report_disable must not trip the guard.
    src = _strip_docstrings_and_comments(
        "locations = self._locations or LINKEDIN_LOCATIONS\n"
        "self.report_disable('3 consecutive timeouts')\n"
        "def enabled_config_key(cls): return f'scraper.{cls.SOURCE_NAME.lower()}.enabled'\n"
    )
    assert _BANNED_RE.search(src) is None


# ── LinkedIn: locations come from the injected plan (no load_active_profile) ────

def test_linkedin_uses_injected_locations(monkeypatch):
    from core.models import JobFilter
    from core.scrapers.boards import linkedin as mod

    calls: list[dict] = []

    def fake_scrape(timeout_seconds, **kwargs):
        calls.append(kwargs)
        return None  # timeout → skip

    monkeypatch.setattr(mod, "scrape_with_timeout", fake_scrape)
    monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: None)

    scraper = mod.LinkedInScraper(locations=["Switzerland", "France"])
    jobs = scraper.fetch(JobFilter(titles=["product manager"]))

    assert jobs == []
    assert {c["location"] for c in calls} == {"Switzerland", "France"}
    assert {c["search_term"] for c in calls} == {"product manager"}


# ── Indeed: locations come from the injected plan (no load_active_profile) ─────

def test_indeed_uses_injected_locations(monkeypatch):
    from core.models import JobFilter
    from core.scrapers.boards import indeed as mod

    calls: list[dict] = []

    def fake_scrape(timeout_seconds, **kwargs):
        calls.append(kwargs)
        return None  # timeout → skip

    monkeypatch.setattr(mod, "scrape_with_timeout", fake_scrape)
    monkeypatch.setattr(mod, "patch_requests_for_indeed", lambda: object())
    monkeypatch.setattr(mod, "unpatch_requests", lambda o: None)
    monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: None)

    scraper = mod.IndeedScraper(locations=["United Kingdom", "France"])
    jobs = scraper.fetch(JobFilter(titles=["product manager"]))

    assert jobs == []
    assert {c["country_indeed"] for c in calls} == {"uk", "france"}


# ── Greenhouse: boards + title families come from the injected plan ────────────

def test_greenhouse_uses_injected_boards_and_titles(monkeypatch):
    from core.models import JobFilter
    from core.scrapers import greenhouse as mod

    fetched: list[str] = []

    class _Resp:
        status_code = 200

        def __init__(self, token):
            self._token = token

        def json(self):
            return {"jobs": [{
                "title": "Product Manager",
                "absolute_url": f"https://boards.greenhouse.io/{self._token}/jobs/1",
                "location": {"name": "Remote - USA"},
                "updated_at": "2026-01-05T00:00:00+00:00",
                "content": "",
            }]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            token = url.split("/boards/")[1].split("/")[0]
            fetched.append(token)
            return _Resp(token)

    monkeypatch.setattr(mod.httpx, "Client", _Client)
    monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: None)

    scraper = mod.GreenhouseScraper(boards=["coinbase", "paxos"],
                                    title_families=["product manager"])
    jobs = scraper.fetch(JobFilter(titles=["product manager"]))

    assert fetched == ["coinbase", "paxos"]
    assert len(jobs) == 2
    assert {j.company for j in jobs} == {"Coinbase", "Paxos"}
    assert all(j.title == "Product Manager" for j in jobs)
    assert all(j.work_mode == "remote" for j in jobs)
    assert all(j.base_location == "United States" for j in jobs)


# ── Lever: slugs come from the injected plan (no get_lever_slugs) ──────────────

def test_lever_uses_injected_slugs(monkeypatch):
    from core.scrapers.ats import lever as mod

    fetched: list[str] = []

    class _Resp:
        status_code = 200

        def __init__(self, slug):
            self._slug = slug

        def json(self):
            return [{
                "text": "Product Manager",
                "hostedUrl": f"https://jobs.lever.co/{self._slug}/1",
                "categories": {"location": "Remote"},
            }]

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            slug = url.split("/postings/")[1].split("?")[0]
            fetched.append(slug)
            return _Resp(slug)

    monkeypatch.setattr(mod.httpx, "Client", _Client)
    monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: None)

    scraper = mod.LeverScraper(slugs=["impossiblecloud"])
    jobs = scraper.fetch(None)

    assert fetched == ["impossiblecloud"]
    assert len(jobs) == 1
    assert jobs[0].company == "Impossiblecloud"
    assert jobs[0].title == "Product Manager"


# ── Workable: slugs come from the injected plan (no get_workable_slugs) ────────

def test_workable_uses_injected_slugs(monkeypatch):
    from core.scrapers.ats import workable as mod

    fetched: list[str] = []

    class _Resp:
        status_code = 200

        def __init__(self, slug):
            self._slug = slug

        def json(self):
            return {
                "results": [{
                    "title": "Product Manager",
                    "url": f"https://apply.workable.com/{self._slug}/j/1",
                    "location": {"city": "Zurich", "country": "Switzerland"},
                }],
                "next_page": None,
            }

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kwargs):
            slug = url.split("/accounts/")[1].split("/")[0]
            fetched.append(slug)
            return _Resp(slug)

    monkeypatch.setattr(mod.httpx, "Client", _Client)
    monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: None)

    scraper = mod.WorkableScraper(slugs=["walletconnect"])
    jobs = scraper.fetch(None)

    assert fetched == ["walletconnect"]
    assert len(jobs) == 1
    assert jobs[0].company == "Walletconnect"
    assert jobs[0].title == "Product Manager"
