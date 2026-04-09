#!/usr/bin/env python3
"""
Run all scraper live tests and print a summary table.

Usage:
    python tests/run_all.py          # from project root
    cd tests && python run_all.py

Exit code: 0 if all scrapers pass or skip, 1 if any scraper errors or fails
on a non-optional field.
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scraper_checks import run_scraper, print_result, ScraperResult, FIELDS
from test_storage import run_storage_tests


def _load_scrapers():
    from scrapers.remoteok        import RemoteOKScraper
    from scrapers.web3career      import Web3CareerScraper
    from scrapers.weworkremotely  import WeWorkRemotelyScraper
    from scrapers.cryptojobs_com  import CryptoJobsComScraper
    from scrapers.cryptojobslist  import CryptoJobsListScraper
    from scrapers.tietalent       import TieTalentScraper
    from scrapers.greenhouse      import GreenhouseScraper
    from scrapers.defi_jobs       import DeFiJobsScraper
    from scrapers.jobup           import JobupScraper
    from scrapers.wellfound       import WellfoundScraper
    from scrapers.jobspy_scraper  import JobSpyScraper

    # optional_fields: fields that are best-effort for this source.
    # A None/missing value raises ⚠️  instead of ❌ and does not cause FAIL.
    NO_LOC = {"base_location"}

    return [
        # (scraper_class,        env_key_required, optional_fields)
        (RemoteOKScraper,        "",               NO_LOC),  # global-remote jobs have no city
        (Web3CareerScraper,      "",               NO_LOC),  # source doesn't populate city/country
        (WeWorkRemotelyScraper,  "",               set()),
        (CryptoJobsComScraper,   "",               set()),
        (CryptoJobsListScraper,  "",               NO_LOC),  # some listings carry no location
        (TieTalentScraper,       "",               set()),
        (GreenhouseScraper,      "",               set()),
        (DeFiJobsScraper,        "",               NO_LOC),  # crypto.jobs has no base location
        (JobupScraper,           "",               set()),
        (WellfoundScraper,       "X_RAPIDAPI_KEY", NO_LOC),
        (JobSpyScraper,          "",               NO_LOC),  # remote listings carry no city
    ]


def main():
    scrapers = _load_scrapers()
    results: list[ScraperResult] = []

    print(f"Running {len(scrapers)} scraper tests  (live HTTP — no mocks)\n")

    for cls, env_key, opt_fields in scrapers:
        print(f"  → {cls.SOURCE_NAME} …", end="", flush=True)
        t0 = time.time()
        r = run_scraper(cls, env_key, optional_fields=opt_fields)
        elapsed = time.time() - t0
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⚠️ ", "ERROR": "💥"}.get(r.status, "?")
        print(f"\r  {icon} {cls.SOURCE_NAME:<22} {elapsed:5.1f}s")
        results.append(r)

    # ── Per-scraper detailed breakdown ────────────────────────────────────────
    print("\n" + "═" * 72)
    print("DETAILED RESULTS")
    print("═" * 72)
    for r in results:
        print_result(r)

    # ── Summary table ─────────────────────────────────────────────────────────
    print("\n\n" + "═" * 112)
    print("SUMMARY")
    print("═" * 112)

    col_w = 9
    header = (f"  {'Scraper':<22} {'Results':>7}  "
              + "  ".join(f"{f[:col_w]:<{col_w}}" for f in FIELDS)
              + "  Status")
    print(header)
    print("─" * 112)

    any_fail = False
    for r in results:
        if r.status == "SKIP":
            cells = "  ".join(f"{'⚠️ SKIP':<{col_w}}" for _ in FIELDS)
            row = f"  {r.scraper_name:<22} {'—':>7}  {cells}  ⚠️  SKIPPED"
        elif r.status == "ERROR":
            cells = "  ".join(f"{'💥 ERR':<{col_w}}" for _ in FIELDS)
            row = f"  {r.scraper_name:<22} {'—':>7}  {cells}  💥 ERROR"
            any_fail = True
        else:
            cells = []
            for field in FIELDS:
                passed, total, _ = r.field_stats[field]
                is_opt = field in r.optional_fields
                if total == 0:
                    cells.append(f"{'❌ 0/0':<{col_w}}")
                elif passed == total:
                    cells.append(f"{'✅':<{col_w}}")
                elif is_opt:
                    cells.append(f"{'⚠️':<{col_w}}")   # known limitation
                else:
                    cells.append(f"{'❌'+str(passed)+'/'+str(total):<{col_w}}")
            status_icon = "✅ PASS" if r.status == "PASS" else "❌ FAIL"
            if r.status == "FAIL":
                any_fail = True
            row = (f"  {r.scraper_name:<22} {r.n_results:>7}  "
                   + "  ".join(cells)
                   + f"  {status_icon}")
        print(row)

    print("─" * 112)
    overall = "✅  ALL PASS" if not any_fail else "❌  SOME FAILED"
    print(f"\n  {overall}\n")

    # ── Storage unit tests ────────────────────────────────────────────────────
    print("\n" + "═" * 72)
    print("STORAGE TESTS (in-memory DB)")
    print("═" * 72)
    storage_results = run_storage_tests()
    passed = sum(1 for _, ok, _ in storage_results if ok)
    total  = len(storage_results)
    if passed < total:
        any_fail = True
        for name, ok, err in storage_results:
            if not ok:
                print(f"  ❌ {name}: {err}")
    print(f"\n  {passed}/{total} storage tests passed")

    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
