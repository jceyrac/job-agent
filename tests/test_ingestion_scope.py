"""
tests/test_ingestion_scope.py — guard: ingestion reads the platform scope only (spec 040).

Three invariants, enforced by source scanning:

1. No ingestion code (``core/scrape.py``, ``core/scrapers/**``) reads the profile's
   ``job_titles`` / ``title_exclude`` / ``scrape_remote_or_hybrid`` /
   ``effective_search_locations`` / ``greenhouse_boards``. Ingestion must go
   through ``JobStorage.get_ingestion_scope()`` (Constitution I — the scope is a
   platform parameter, never profile-derived).

2. ``jobs.filtered_non_product`` is now write-only in the schema: no runtime code
   reads or writes it outside the migration that creates the column. ``models.py``'s
   dataclass field is the one legitimate non-SQL reference.

3. Board scrapers (indeed, linkedin) take their title terms from the ``JobFilter``
   passed by ``scrape.py``, not from ``JobStorage.get_ingestion_scope()`` — ``scrape.py``
   is the single reader of the scope for board sources (Constitution VIII; step 7
   removed storage access from scrapers). Greenhouse likewise takes its ``boards``
   from the injected plan, never a scope or profile read.

Negative fixtures prove the scanners themselves catch the banned forms.
"""
import inspect
import pathlib
import re

from core.storage import JobStorage
from core.scrapers.base import BaseScraper

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

# ── Invariant 1: ingestion reads the scope, not the profile title fields ──────

_INGESTION_FILES = (
    [_PROJECT_ROOT / "core" / "scrape.py"]
    + sorted((_PROJECT_ROOT / "core" / "scrapers").rglob("*.py"))
)

_INGESTION_RE = re.compile(
    r"(?<![\w])(?:job_titles|title_exclude|scrape_remote_or_hybrid"
    r"|effective_search_locations|greenhouse_boards)(?![\w])"
)

# ── Invariant 2: filtered_non_product is write-only in the schema ─────────────

# storage.py methods that legitimately touch the column while creating it
# (the ALTER TABLE lives in _migrate_monitored_companies).
_STORAGE_MIGRATION_FUNCTIONS = frozenset({
    "_init_db",
    "_migrate_application_lifecycle",
    "_migrate_user_space_separation",
    "_migrate_ingestion_scope",
    "_migrate_monitored_companies",
    "_migrate_company_researcher",
    "_seed_greenhouse_boards",
})

_FILTERED_RE = re.compile(r"(?<![\w])filtered_non_product(?![\w])")

# ── Invariant 3: board scrapers read titles from JobFilter, not the scope ──────

_BOARD_SCRAPER_FILES = (
    _PROJECT_ROOT / "core" / "scrapers" / "boards" / "indeed.py",
    _PROJECT_ROOT / "core" / "scrapers" / "boards" / "linkedin.py",
)

_SCOPE_READ_RE = re.compile(r"(?<![\w])get_ingestion_scope(?![\w])")


def _strip_docstrings_and_comments(src: str) -> str:
    """Drop triple-quoted strings and # line comments so prose never false-positives."""
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"'''(?:.|\n)*?'''", "", src)
    src = re.sub(r"#[^\n]*", "", src)
    return src


def _ingestion_field_hits(src: str) -> list[str]:
    return [m.group(0) for m in _INGESTION_RE.finditer(_strip_docstrings_and_comments(src))]


def _filtered_hits(src: str) -> list[str]:
    return [m.group(0) for m in _FILTERED_RE.finditer(src)]


def _storage_method_sources():
    """Yield (name, stripped source) for every non-migration JobStorage method."""
    for name in dir(JobStorage):
        if name in _STORAGE_MIGRATION_FUNCTIONS:
            continue
        obj = getattr(JobStorage, name)
        if not callable(obj):
            continue
        try:
            yield name, _strip_docstrings_and_comments(inspect.getsource(obj))
        except (OSError, TypeError):
            continue


# ── Guard tests ────────────────────────────────────────────────────────────────

def test_ingestion_never_reads_profile_title_fields():
    offenders: dict[str, list[str]] = {}
    for path in _INGESTION_FILES:
        hits = _ingestion_field_hits(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path.relative_to(_PROJECT_ROOT))] = hits

    assert not offenders, (
        "ingestion code reads profile job_titles/title_exclude/scrape_remote_or_hybrid/"
        "effective_search_locations/greenhouse_boards:\n"
        + "\n".join(f"  {p}: {h}" for p, hs in offenders.items() for h in hs)
    )


def test_storage_never_touches_filtered_non_product_outside_migration():
    offenders: dict[str, list[str]] = {}
    for name, src in _storage_method_sources():
        hits = _filtered_hits(src)
        if hits:
            offenders[name] = hits

    assert not offenders, (
        "non-migration JobStorage methods reference filtered_non_product:\n"
        + "\n".join(f"  {n}: {h}" for n, hs in offenders.items() for h in hs)
    )


def test_set_job_filtered_non_product_removed():
    assert not hasattr(JobStorage, "set_job_filtered_non_product"), (
        "set_job_filtered_non_product must be gone (title judgement is a Tier 0 rule now)"
    )


def test_no_runtime_filtered_non_product_in_core_modules():
    """scrape.py / score.py / scorer.py / job_actions.py must not reference the flag."""
    offenders: list[str] = []
    for path in (_PROJECT_ROOT / "core").rglob("*.py"):
        if path.name in ("storage.py", "models.py"):
            continue
        src = path.read_text(encoding="utf-8")
        if _filtered_hits(_strip_docstrings_and_comments(src)):
            offenders.append(str(path.relative_to(_PROJECT_ROOT)))
    assert not offenders, f"runtime core modules reference filtered_non_product: {offenders}"


def test_broad_scrape_builds_filter_from_scope_not_profile():
    """_run_broad_scrape passes a JobFilter built from the scope, not the profile."""
    from unittest import mock
    from core import scrape as scrape_mod

    captured = {}

    class _FakeScraper(BaseScraper):
        SOURCE_NAME = "Fake"
        ACQUISITION_MODEL = "board"
        def fetch(self, job_filter):
            captured["job_filter"] = job_filter
            return []

    class _FakeDB:
        def get_ingestion_scope(self):
            return {"title_families": ["scoped title"], "exclude": ["scoped_ex"],
                    "remote_or_hybrid": True,
                    "search_locations": ["Switzerland"],
                    "greenhouse_boards": ["board1"]}
        def get_freshness_days(self):
            return 30
        def get_engaged_job_keys(self):
            return []
        def get_stats(self, pid):
            return {"total": 0}
        def get_config(self, key):
            return None
        def log_run(self, **kwargs):
            pass

    class _FakeProfile:
        id = "p1"
        job_titles = ["profile title"]
        title_exclude = ["profile_ex"]
        scrape_remote_or_hybrid = False

    with mock.patch.object(scrape_mod, "discover_scrapers", return_value=[_FakeScraper]), \
         mock.patch.object(scrape_mod, "JobFilterEngine") as engine:
        engine.apply.return_value = ([], 0, [])
        scrape_mod._run_broad_scrape(_FakeDB(), _FakeProfile())

    jf = captured["job_filter"]
    assert jf.titles == ["scoped title"]
    assert jf.exclude == ["scoped_ex"]
    assert jf.remote_or_hybrid is True
    # the profile's differing title fields must NOT leak into the filter
    assert jf.titles != _FakeProfile.job_titles


def test_broad_scrape_pre_save_title_gate_matches_scope():
    """The pre-save gate (scope-keyed) must save exactly the jobs whose title
    matches title_families and carries no excluded word — reproducing the previous
    profile-keyed gate byte-for-byte when scope == profile values (borderline
    titles from the 57/15 analysis are the regression cases)."""
    from unittest import mock

    from core import scrape as scrape_mod
    from core.models import JobPosting

    db = JobStorage(":memory:")
    db.set_ingestion_scope(
        title_families=["product manager", "head of product"],
        exclude=["intern", "junior"],
        remote_or_hybrid=False,
    )

    class _FakeProfile:
        id = "unified_jc"

    def _post(title):
        return JobPosting(
            source="Fake", title=title, company="Acme", location="Remote",
            url=f"https://example.com/{title.lower().replace(' ', '-')}",
        )

    postings = [
        _post("Fintech Co-Founder / Head of Product (100% remote)"),  # match → save
        _post("Senior Product Manager - MetaMask"),                   # match → save
        _post("Product Manager (HR Technology) Intern"),              # match + excluded word → skip
        _post("Junior Product Manager"),                              # match + excluded word → skip
        _post("Head of Trading"),                                     # mismatch → skip
        _post("Chief of Staff"),                                      # mismatch → skip
    ]

    class _FakeScraper(BaseScraper):
        SOURCE_NAME = "Fake"
        ACQUISITION_MODEL = "board"
        def fetch(self, job_filter):
            return postings

    with mock.patch.object(scrape_mod, "discover_scrapers", return_value=[_FakeScraper]), \
         mock.patch.object(scrape_mod, "JobFilterEngine") as engine:
        engine.apply.return_value = (postings, 0, [])
        scrape_mod._run_broad_scrape(db, _FakeProfile())

    with db._conn() as conn:
        saved = {r["title"] for r in conn.execute("SELECT title FROM jobs").fetchall()}

    assert saved == {
        "Fintech Co-Founder / Head of Product (100% remote)",
        "Senior Product Manager - MetaMask",
    }


def test_board_scrapers_read_titles_from_job_filter_not_scope():
    """indeed.py / linkedin.py must use job_filter.titles, not get_ingestion_scope()."""
    offenders: dict[str, list[str]] = {}
    for path in _BOARD_SCRAPER_FILES:
        src = _strip_docstrings_and_comments(path.read_text(encoding="utf-8"))
        if _SCOPE_READ_RE.search(src):
            offenders[str(path.relative_to(_PROJECT_ROOT))] = ["get_ingestion_scope"]

    assert not offenders, (
        "board scrapers must read titles from the JobFilter, not the scope:\n"
        + "\n".join(f"  {p}: {h}" for p, hs in offenders.items() for h in hs)
    )


# ── Negative fixtures — the scanners must catch each form ──────────────────────

def test_scanner_detects_ingestion_profile_field_reads():
    assert _ingestion_field_hits("terms = _p.job_titles or SEARCH_TERMS") == ["job_titles"]
    assert _ingestion_field_hits("profile.scrape_remote_or_hybrid") == ["scrape_remote_or_hybrid"]
    assert _ingestion_field_hits("profile.title_exclude") == ["title_exclude"]
    assert _ingestion_field_hits("profile.effective_search_locations()") == ["effective_search_locations"]
    assert _ingestion_field_hits("profile.greenhouse_boards") == ["greenhouse_boards"]


def test_scanner_ignores_scope_read():
    # get_ingestion_scope()["title_families"] is the legitimate ingestion path.
    assert _ingestion_field_hits('scope = db.get_ingestion_scope()["title_families"]') == []


def test_scanner_ignores_get_greenhouse_boards_helper():
    # get_greenhouse_boards is the orchestrator's DB helper in scrape.py, not a profile
    # read — the `_` prefix keeps the token from matching the word-boundary guard.
    assert _ingestion_field_hits("boards = get_greenhouse_boards(db)") == []


def test_scanner_detects_filtered_non_product():
    assert _filtered_hits("UPDATE jobs SET filtered_non_product = TRUE") == ["filtered_non_product"]
    assert _filtered_hits("SELECT j.filtered_non_product FROM jobs j") == ["filtered_non_product"]


def test_scanner_detects_board_scope_read():
    assert _SCOPE_READ_RE.search('terms = self._storage.get_ingestion_scope()["title_families"]') is not None


def test_scanner_ignores_job_filter_titles():
    # job_filter.titles is the legitimate board-scraper path.
    assert _SCOPE_READ_RE.search("terms = job_filter.titles or SEARCH_TERMS") is None
