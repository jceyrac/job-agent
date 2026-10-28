"""core.scrapers — scraper package.

`discover_scrapers()` enumerates every enabled scraper class (boards, ATS,
company sites, plus root-level legacy modules). It lives here rather than in
`core/scrape.py` so the API ingest router can list sources without importing
the orchestrator (which the API guard forbids). Imports are lazy, so
`import core.scrapers` stays cheap.
"""
import importlib
import pkgutil

_SUBPACKAGES = ("boards", "ats", "company_sites")


def _scraper_classes_in(module) -> list[type]:
    """Every enabled scraper class defined in `module` (skips BaseScraper)."""
    found: list[type] = []
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if (
            isinstance(obj, type)
            and hasattr(obj, "fetch")
            and hasattr(obj, "SOURCE_NAME")
            and obj.__name__ != "BaseScraper"
            and getattr(obj, "ENABLED", True)
        ):
            found.append(obj)
    return found


def discover_scrapers() -> list[type]:
    scraper_classes: list[type] = []

    # Scan sub-packages: boards/, ats/, company_sites/.
    for subpkg in _SUBPACKAGES:
        pkg = importlib.import_module(f"core.scrapers.{subpkg}")
        for _, module_name, _ in pkgutil.iter_modules(pkg.__path__):
            if module_name in ("base", "__init__"):
                continue
            module = importlib.import_module(f"core.scrapers.{subpkg}.{module_name}")
            scraper_classes.extend(_scraper_classes_in(module))

    # Root-level scrapers (greenhouse + legacy disabled modules).
    for _, module_name, _ in pkgutil.iter_modules(__path__):
        if module_name in ("base", "__init__"):
            continue
        module = importlib.import_module(f"core.scrapers.{module_name}")
        for obj in _scraper_classes_in(module):
            if obj not in scraper_classes:
                scraper_classes.append(obj)

    return scraper_classes
