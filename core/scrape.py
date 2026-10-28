import importlib
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

import httpx

from dotenv import load_dotenv
load_dotenv()

from core.filters import JobFilterEngine
from core.models import JobFilter, JobPosting
from core.paths import DB_PATH
from core.storage import JobStorage, _normalize_key
from core.title_gate import title_excluded, title_matches_profile

from core.scrapers import discover_scrapers
from core.scrapers.greenhouse import GREENHOUSE_BOARDS_SEED
from core.scrapers.ats.lever import LEVER_SLUGS_SEED
from core.scrapers.ats.workable import WORKABLE_SLUGS_SEED

# ── Injected plan slice helpers (spec 045, US1) ───────────────────────────────
#
# Scrapers no longer read storage/profile. The orchestrator computes their
# targets/locations/boards/slugs/enabled from its existing direct `JobStorage`
# reads and injects them (Constitution VIII). The merge helpers below are moved
# verbatim from the scraper modules (greenhouse/lever/workable) so the broad
# sweep still merges the seed list with DB watching slugs.


def get_greenhouse_boards(db: JobStorage | None) -> list[str]:
    """Return Greenhouse board slugs from DB (watching + greenhouse) merged with seed.

    Falls back to GREENHOUSE_BOARDS_SEED if DB is None or returns zero rows.
    Legacy watching companies have scraping_method=NULL and are not returned
    by the DB query — they remain covered by the seed list.
    """
    if db is None:
        return list(GREENHOUSE_BOARDS_SEED)
    rows = db.get_watching_companies_by_method("greenhouse")
    slugs = [r["ats_identifier"] for r in rows if r.get("ats_identifier")]
    # Seed first so legacy companies are always covered; DB slugs appended
    return list(dict.fromkeys(list(GREENHOUSE_BOARDS_SEED) + slugs))


def get_lever_slugs(db: JobStorage | None) -> list[str]:
    """Return Lever slugs from DB (watching + lever) merged with seed."""
    if db is None:
        return list(LEVER_SLUGS_SEED)
    rows = db.get_watching_companies_by_method("lever")
    slugs = [r["ats_identifier"] for r in rows if r.get("ats_identifier")]
    return list(dict.fromkeys(list(LEVER_SLUGS_SEED) + slugs))


def get_workable_slugs(db: JobStorage | None) -> list[str]:
    """Return Workable slugs from DB (watching + workable) merged with seed."""
    if db is None:
        return list(WORKABLE_SLUGS_SEED)
    rows = db.get_watching_companies_by_method("workable")
    slugs = [r["ats_identifier"] for r in rows if r.get("ats_identifier")]
    return list(dict.fromkeys(list(WORKABLE_SLUGS_SEED) + slugs))


def _scraper_enabled(db: JobStorage, cls) -> bool:
    """Config-gated enabled state for a scraper class (mirrors the old `is_enabled`)."""
    val = db.get_config(cls.enabled_config_key())
    if val is None:
        return cls.ENABLED
    return val.lower() == "true"


def _scraper_kwargs(db: JobStorage, cls, scope: dict) -> dict:
    """Compute the injected plan slice for a scraper class from direct reads."""
    name = getattr(cls, "SOURCE_NAME", "")
    if name == "LinkedIn":
        return {"locations": scope["search_locations"]}
    if name == "Indeed":
        return {"locations": scope["search_locations"]}
    if name == "Greenhouse":
        return {"title_families": scope["title_families"],
                "boards": get_greenhouse_boards(db)}
    if name == "Lever":
        return {"slugs": get_lever_slugs(db)}
    if name == "Workable":
        return {"slugs": get_workable_slugs(db)}
    return {}


def _persist_disable(db: JobStorage, cls, reason: str) -> None:
    """Persist a scraper's auto-disable event (the old `BaseScraper.disable`)."""
    prefix = cls._config_prefix()
    db.set_config(f"{prefix}.enabled", "false")
    db.set_config(f"{prefix}.disabled_reason", reason)


# ── Ingest-via-API (spec 045, US4) ────────────────────────────────────────────
#
# When `ingest_via_api` is on, the orchestrator stops writing jobs/companies to
# the DB directly and instead: GETs the run plan, runs the (pure) scrapers with
# that plan, POSTs the collected postings to `/v1/ingest/batch` in small chunks,
# and POSTs auto-disable / run events. The token is the platform principal's
# ingest token; any auth/network/server failure is loud (non-zero exit) — never
# a silent fallback to the direct path (US5-2 / FR-005).

_INGEST_DEFAULT_BASE_URL = "http://api:8601"   # in-network service name (compose)
_INGEST_MAX_POSTINGS = 100                     # ≤ ~100 postings per request (R5)
_INGEST_MAX_BYTES = 4 * 1024 * 1024            # ≤ the 5 MB route cap, with headroom


class _IngestError(Exception):
    """Classified ingest-API failure — already human-readable, token never included."""


def _ingest_via_api(db: JobStorage) -> bool:
    """The Constitution-XI switch (R6): `ingest_via_api` config key, default off.

    The **config key** is the single source of truth — production flips it in the
    DB with no redeploy (FR-010). `INGEST_VIA_API` (env) is a *staging/test*
    bootstrap only: it is consulted solely when the config key is absent, and that
    use is logged loudly to stderr. An env override set alongside a configured key
    is ignored (and logged) — it must never silently change production behaviour,
    and it must not appear in verva's `.env`.
    """
    env = os.environ.get("INGEST_VIA_API")
    cfg = db.get_config("ingest_via_api")

    if cfg is not None and cfg.strip() != "":
        # Config key set → authoritative. Log any ignored env override.
        on = cfg.strip().lower() in ("1", "true", "on", "yes")
        if env is not None and env.strip() != "":
            print(f"⚠ INGEST_VIA_API env set ({env.strip()!r}) but "
                  f"config.ingest_via_api is authoritative ({cfg.strip()!r}) — "
                  f"env ignored", file=sys.stderr)
        return on

    # Config key absent → env is the staging/test bootstrap, logged loudly.
    if env is not None and env.strip() != "":
        print(f"⚠ INGEST_VIA_API env override used (config.ingest_via_api unset): "
              f"{env.strip()!r} — staging/test only", file=sys.stderr)
        return env.strip().lower() in ("1", "true", "on", "yes")
    return False


def _load_ingest_token() -> str:
    """Bearer token from `JOB_AGENT_API_TOKEN`, else a local credentials file.

    The file must be owner-only (mode 0600) — a group/world-readable token file is
    refused, mirroring the cv_agent's token rule.
    """
    token = os.environ.get("JOB_AGENT_API_TOKEN")
    if token:
        return token.strip()

    path = os.environ.get("JOB_AGENT_API_TOKEN_FILE") or os.path.join(
        os.path.expanduser("~"), ".config", "job_agent", "agent_token")
    try:
        with open(path, encoding="utf-8") as f:
            token = f.read().strip()
    except OSError as e:
        raise _IngestError(
            f"no ingest API token — set JOB_AGENT_API_TOKEN or create {path} "
            f"(mode 0600)") from e
    if not token:
        raise _IngestError(f"ingest API token file {path} is empty")

    mode = os.stat(path).st_mode & 0o777
    if mode & 0o077:
        raise _IngestError(
            f"ingest API token file {path} has permissive mode {oct(mode)} — "
            f"run `chmod 600` on it before use")
    return token


class _IngestClient:
    """Minimal httpx client for the ingest endpoints (plan / batch / events)."""

    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = (base_url or os.environ.get("JOB_AGENT_API_URL")
                         or _INGEST_DEFAULT_BASE_URL).rstrip("/")
        self.token = token if token is not None else _load_ingest_token()
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={"Authorization": f"Bearer {self.token}"},
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, *, json=None) -> dict:
        try:
            resp = self._client.request(method, path, json=json)
        except httpx.HTTPError as e:
            raise _IngestError(
                f"ingest API unreachable ({self.base_url}): {e}") from e
        if resp.status_code in (401, 403):
            raise _IngestError(
                "ingest API auth failed — token invalid/expired or missing the "
                "`ingest` scope")
        if resp.status_code >= 500:
            raise _IngestError(
                f"ingest API server error (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise _IngestError(
                f"ingest API rejected the request (HTTP {resp.status_code}): "
                f"{resp.text[:200]}")
        return resp.json()

    def get_plan(self) -> dict:
        return self._request("GET", "/v1/ingest/plan")

    def submit_batch(self, postings: list[dict]) -> dict:
        return self._request("POST", "/v1/ingest/batch", json=postings)

    def submit_event(self, event: dict) -> dict:
        return self._request("POST", "/v1/ingest/events", json=event)


def _api_scraper_kwargs(cls, plan: dict) -> dict:
    """The injected plan slice for a scraper, from the API plan (US4).

    Mirrors `_scraper_kwargs` field-for-field, sourcing watching slugs from the
    plan's `board_slugs` instead of the DB, so the API path injects exactly what
    the direct path would (identity).
    """
    name = getattr(cls, "SOURCE_NAME", "")
    if name == "LinkedIn":
        return {"locations": plan["search_locations"]}
    if name == "Indeed":
        return {"locations": plan["search_locations"]}
    if name == "Greenhouse":
        boards = list(dict.fromkeys(
            list(GREENHOUSE_BOARDS_SEED) + plan["board_slugs"]["greenhouse_watching"]))
        return {"title_families": plan["title_families"], "boards": boards}
    if name == "Lever":
        slugs = list(dict.fromkeys(list(LEVER_SLUGS_SEED) + plan["board_slugs"]["lever"]))
        return {"slugs": slugs}
    if name == "Workable":
        slugs = list(dict.fromkeys(list(WORKABLE_SLUGS_SEED) + plan["board_slugs"]["workable"]))
        return {"slugs": slugs}
    return {}


def _posting_payload(job: JobPosting) -> dict:
    """The raw-field payload for one collected posting (no computed fields)."""
    return {
        "source": job.source,
        "title": job.title,
        "company": job.company,
        "url": job.url or None,
        "location": job.location or None,
        "description": job.description,
        "base_location": job.base_location,
        "posted_date": job.posted_date.isoformat() if job.posted_date else None,
        "monitored_company_id": job.monitored_company_id,
    }


def _chunk_postings(postings: list[JobPosting]) -> list[list[JobPosting]]:
    """Split collected postings into ≤ ~100-posting / ≤ 5 MB requests (R5)."""
    chunks: list[list[JobPosting]] = []
    current: list[JobPosting] = []
    current_bytes = 0
    for job in postings:
        size = len(json.dumps(_posting_payload(job)).encode("utf-8"))
        if current and (len(current) >= _INGEST_MAX_POSTINGS
                        or current_bytes + size > _INGEST_MAX_BYTES):
            chunks.append(current)
            current = []
            current_bytes = 0
        current.append(job)
        current_bytes += size
    if current:
        chunks.append(current)
    return chunks


def _submit_postings(client: _IngestClient, postings: list[JobPosting]) -> tuple[int, int, int]:
    """POST postings in chunks; return (new, existing, dropped_engaged) totals."""
    new = existing = dropped = 0
    for chunk in _chunk_postings(postings):
        result = client.submit_batch([_posting_payload(j) for j in chunk])
        new += int(result.get("new", 0))
        existing += int(result.get("existing", 0))
        dropped += int(result.get("dropped_engaged", 0))
    return new, existing, dropped


def deduplicate(jobs: list[JobPosting]) -> list[JobPosting]:
    seen = set()
    unique = []
    for job in jobs:
        if job.url not in seen:
            seen.add(job.url)
            unique.append(job)
    return unique


def dedupe_cross_source(jobs: list[JobPosting]) -> list[JobPosting]:
    groups: dict[str, list[JobPosting]] = {}
    no_key: list[JobPosting] = []
    for job in jobs:
        key = _normalize_key(job.title, job.company)
        if not key:
            no_key.append(job)
        else:
            groups.setdefault(key, []).append(job)

    deduped: list[JobPosting] = list(no_key)
    for group in groups.values():
        if len(group) == 1:
            deduped.append(group[0])
        else:
            def _sort_key(j: JobPosting):
                desc_len = len(j.description or "")
                # None posted_date loses to any real date
                date_val = j.posted_date.toordinal() if j.posted_date else 0
                return (desc_len, date_val)
            deduped.append(max(group, key=_sort_key))

    return deduped


def dedupe_against_db(jobs: list[JobPosting], storage: JobStorage) -> list[JobPosting]:
    """Drop scraped jobs whose (normalize_title, normalize_company) matches
    an existing row with status in {applied, ready, queued, archived}.
    Keeps the existing row, updates its last_seen.
    Does NOT dedup against status='new' — those are still in the queue.
    """
    engaged = storage.get_engaged_job_keys()
    if not engaged:
        return jobs

    # Build in-memory set of (title, company, id) keys from engaged jobs
    engaged_keys: dict[str, str] = {}  # normalized_key → job_id
    for row in engaged:
        key = _normalize_key(row["title"], row["company"])
        if key:
            engaged_keys[key] = row["id"]

    if not engaged_keys:
        return jobs

    touched_ids: set[str] = set()
    result: list[JobPosting] = []
    dropped = 0

    for job in jobs:
        key = _normalize_key(job.title, job.company)
        if key and key in engaged_keys:
            dropped += 1
            touched_ids.add(engaged_keys[key])
        else:
            result.append(job)

    if touched_ids:
        storage.touch_many(list(touched_ids))

    if dropped:
        print(f"DB dedup: {dropped} job(s) dropped (already engaged) — "
              f"{len(touched_ids)} existing row(s) last_seen updated")

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Job scraping pipeline")
    parser.add_argument("--monitored-only", action="store_true",
                        help="Only scrape monitored companies (company-keyed, no broad boards)")
    parser.add_argument("--no-score", action="store_true",
                        help="Skip extraction + scoring after monitored-only scrape")
    args = parser.parse_args()

    t0 = time.monotonic()
    db = JobStorage(DB_PATH)

    if _ingest_via_api(db):
        client = None
        try:
            client = _IngestClient()
            plan = client.get_plan()
            if args.monitored_only:
                _run_monitored_only_via_api(client, plan)
            else:
                _run_broad_scrape_via_api(client, plan)
        except _IngestError as e:
            print(f"❌ ingest via API failed: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            if client is not None:
                client.close()
    else:
        from core.profiles import load_active_profile
        profile = load_active_profile(db)

        if args.monitored_only:
            _run_monitored_only(db, profile, run_scoring=not args.no_score)
        else:
            _run_broad_scrape(db, profile)

    elapsed = round(time.monotonic() - t0, 1)
    print(f"\nScrape complete ({elapsed:.0f}s)")


def _run_monitored_only(db: JobStorage, profile, *, run_scoring: bool = True) -> None:
    """Company-keyed monitoring run — only monitored companies, no broad boards.

    If run_scoring is True (the default), extraction and scoring run immediately
    after scraping so monitored jobs are scored without waiting for a full pipeline.
    """
    import importlib
    t_start = time.monotonic()

    monitored = db.get_monitored_companies()
    if not monitored:
        print("Nothing to monitor — no companies with monitored=true.")
        db.log_run(
            profile_id=profile.id,
            jobs_scraped=0, jobs_scored=0, jobs_above_threshold=0,
            status="scraped", duration_seconds=0, run_type="monitored_only",
        )
        return

    # Group companies by ats_provider
    by_provider: dict[str, list[dict]] = {}
    for company in monitored:
        provider = company.get("ats_provider")
        if provider:
            by_provider.setdefault(provider, []).append(company)

    print(f"Monitored-only run: {len(monitored)} companies across "
          f"{len(by_provider)} providers ({', '.join(sorted(by_provider))})")

    seen_urls: set[str] = set()
    total_fetched = 0
    total_new = 0

    scope = db.get_ingestion_scope()

    for provider, companies in sorted(by_provider.items()):
        gate = db.get_config(f"monitoring.ats.{provider}.enabled")
        if gate is not None and gate.lower() == "false":
            print(f"[{provider}] monitoring paused (master switch off) — {len(companies)} companies skipped")
            continue
        print(f"\n[{provider}] {len(companies)} companies")
        try:
            module = importlib.import_module(f"core.scrapers.ats.{provider}")
        except (ImportError, ModuleNotFoundError):
            # Fall back to root-level scraper
            try:
                module = importlib.import_module(f"core.scrapers.{provider}")
            except (ImportError, ModuleNotFoundError):
                print(f"  ⚠️ No adapter found for provider '{provider}' — skipped")
                continue

        ScraperClass = None
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (isinstance(obj, type) and hasattr(obj, "fetch")
                    and hasattr(obj, "SOURCE_NAME")
                    and obj.__name__ != "BaseScraper"):
                ScraperClass = obj
                break

        if ScraperClass is None:
            print(f"  ⚠️ No scraper class found in {module} — skipped")
            continue

        scraper = ScraperClass(targets=companies, **_scraper_kwargs(db, ScraperClass, scope))
        print(f"  Fetching from {scraper.SOURCE_NAME}...")

        # Fetch all openings (no filtering — pure function per spec)
        try:
            raw = scraper.fetch(None)  # No JobFilter in monitoring mode
        except Exception as e:
            print(f"  ❌ [{scraper.SOURCE_NAME}] fetch failed: {e}")
            continue

        total_fetched += len(raw)
        print(f"  → {len(raw)} jobs fetched")

        # URL dedup + DB dedup
        unique_batch = []
        for job in raw:
            if job.url and job.url in seen_urls:
                continue
            unique_batch.append(job)
            if job.url:
                seen_urls.add(job.url)

        if not unique_batch:
            print(f"  → 0 new (all URL-duplicates)")
            continue

        unique_batch = dedupe_against_db(unique_batch, db)

        # Write to DB with monitored_company_id
        before_count = db.get_stats(profile.id)["total"]

        # Build lookup: company name → company id for monitored_company_id
        company_name_to_id: dict[str, int] = {}
        for c in companies:
            company_name_to_id[c["name"].lower()] = c["id"]

        for job in unique_batch:
            company_id = None
            if job.company and job.company.strip():
                try:
                    company_id = db.upsert_company(job.company.strip())
                except ValueError:
                    pass
            # Find monitored_company_id by matching company name
            mon_id = company_name_to_id.get(
                (job.company or "").strip().lower()
            ) or company_id
            db.save_unscored(job, company_id=company_id,
                            monitored_company_id=mon_id)

        after_count = db.get_stats(profile.id)["total"]
        batch_new = after_count - before_count
        total_new += batch_new
        print(f"  → {batch_new} new saved to DB")

        # Polite delay between providers
        time.sleep(1.0)

    already_count = total_fetched - total_new
    print(f"\nMonitored-only scrape: {total_fetched} fetched, {total_new} new, "
          f"{already_count} already in DB")
    db.log_run(
        profile_id=profile.id,
        jobs_scraped=total_fetched, jobs_scored=0, jobs_above_threshold=0,
        status="scraped",
        duration_seconds=round(time.monotonic() - t_start, 1),
        run_type="monitored_only",
    )

    # ── Immediately extract & score the new jobs (unless suppressed) ─────
    if run_scoring:
        print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Extracting + scoring monitored jobs...")
        subprocess.run([sys.executable, "-m", "core.score", "--extract"], check=False)
        subprocess.run([sys.executable, "-m", "core.score", "--profile", profile.id], check=False)


def _run_broad_scrape(db: JobStorage, profile) -> None:
    """Broad scrape: all enabled scrapers (boards + discovery), no monitoring filter."""
    t_start = time.monotonic()
    scope = db.get_ingestion_scope()
    job_filter = JobFilter(
        titles=scope["title_families"],
        exclude=scope["exclude"],
        remote_or_hybrid=scope["remote_or_hybrid"],
        date_from=date.today() - timedelta(days=db.get_freshness_days()),
    )

    scraper_classes = discover_scrapers()
    print(f"Scrapers found: {[s.SOURCE_NAME for s in scraper_classes]}")

    seen_urls: set[str] = set()
    total_fetched = 0
    total_new = 0
    total_excluded_date = 0
    total_excluded_title = 0

    for ScraperClass in scraper_classes:
        if (getattr(ScraperClass, "ACQUISITION_MODEL", "board") == "company_keyed"
                and not getattr(ScraperClass, "SUPPORTS_DISCOVERY", False)):
            continue  # monitoring-only source — not part of the broad/discovery sweep
        if not _scraper_enabled(db, ScraperClass):
            print(f"  ⚠️ [{ScraperClass.SOURCE_NAME}] disabled — skipped")
            continue
        scraper = ScraperClass(**_scraper_kwargs(db, ScraperClass, scope))
        print(f"Fetching from {scraper.SOURCE_NAME}...")
        raw = scraper.fetch(job_filter)
        if scraper.auto_disabled_reason:
            _persist_disable(db, ScraperClass, scraper.auto_disabled_reason)
        print(f"  → {len(raw)} jobs fetched")
        filtered, excl_date, _ = JobFilterEngine.apply(raw, job_filter)
        total_excluded_date += excl_date
        total_fetched += len(filtered)
        print(f"  → {len(filtered)} after filter")

        unique_batch = []
        for job in filtered:
            if job.url and job.url in seen_urls:
                continue
            unique_batch.append(job)
            if job.url:
                seen_urls.add(job.url)

        if not unique_batch:
            print(f"  → 0 new (all URL-duplicates of previous scrapers)")
            continue

        unique_batch = dedupe_against_db(unique_batch, db)

        before_count = db.get_stats(profile.id)["total"]
        for job in unique_batch:
            if not title_matches_profile(job.title, scope["title_families"]) \
                    or title_excluded(job.title, scope["exclude"]) is not None:
                total_excluded_title += 1
                continue
            company_id = None
            if job.company and job.company.strip():
                try:
                    company_id = db.upsert_company(job.company.strip())
                except ValueError:
                    pass
            db.save_unscored(job, company_id=company_id)

        after_count = db.get_stats(profile.id)["total"]
        batch_new = after_count - before_count
        total_new += batch_new
        print(f"  → {batch_new} new saved to DB, {len(unique_batch) - batch_new} already in DB")

    already_count = total_fetched - total_new
    print(f"\nScrape complete: {total_fetched} fetched, {total_new} new, {already_count} already in DB")
    if total_excluded_date:
        print(f"📅 {total_excluded_date} jobs excluded (posted > {db.get_freshness_days()} days ago)")
    if total_excluded_title:
        print(f"🔤 {total_excluded_title} jobs excluded (title not in platform scope)")

    db.log_run(
        profile_id=profile.id,
        jobs_scraped=total_fetched,
        jobs_scored=0,
        jobs_above_threshold=0,
        status="scraped",
        duration_seconds=round(time.monotonic() - t_start, 1),
    )


def _run_monitored_only_via_api(client: _IngestClient, plan: dict) -> None:
    """Company-keyed monitoring run with the API as the writer (US4).

    Mirrors `_run_monitored_only` field-for-field, but: reads monitored companies
    and gates from the plan, submits postings to `/v1/ingest/batch` (engaged dedup
    + company upsert happen server-side), and reports the run via an event. The
    platform principal has no profile, so the direct path's immediate extraction +
    scoring subprocess is not run here — scoring stays a worker/direct-path step.
    """
    t_start = time.monotonic()

    monitored = plan["monitored_targets"]
    if not monitored:
        print("Nothing to monitor — no companies with monitored=true.")
        client.submit_event({
            "type": "run", "jobs_scraped": 0, "duration_seconds": 0.0,
            "run_type": "monitored_only",
        })
        return

    by_provider: dict[str, list[dict]] = {}
    for company in monitored:
        provider = company.get("ats_provider")
        if provider:
            by_provider.setdefault(provider, []).append(company)

    print(f"Monitored-only run: {len(monitored)} companies across "
          f"{len(by_provider)} providers ({', '.join(sorted(by_provider))})")

    seen_urls: set[str] = set()
    total_fetched = 0
    total_new = 0

    for provider, companies in sorted(by_provider.items()):
        if not plan["monitoring_gates"].get(provider, True):
            print(f"[{provider}] monitoring paused (master switch off) — "
                  f"{len(companies)} companies skipped")
            continue
        print(f"\n[{provider}] {len(companies)} companies")
        try:
            module = importlib.import_module(f"core.scrapers.ats.{provider}")
        except (ImportError, ModuleNotFoundError):
            try:
                module = importlib.import_module(f"core.scrapers.{provider}")
            except (ImportError, ModuleNotFoundError):
                print(f"  ⚠️ No adapter found for provider '{provider}' — skipped")
                continue

        ScraperClass = None
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (isinstance(obj, type) and hasattr(obj, "fetch")
                    and hasattr(obj, "SOURCE_NAME")
                    and obj.__name__ != "BaseScraper"):
                ScraperClass = obj
                break

        if ScraperClass is None:
            print(f"  ⚠️ No scraper class found in {module} — skipped")
            continue

        scraper = ScraperClass(targets=companies, **_api_scraper_kwargs(ScraperClass, plan))
        print(f"  Fetching from {scraper.SOURCE_NAME}...")

        try:
            raw = scraper.fetch(None)
        except Exception as e:
            print(f"  ❌ [{scraper.SOURCE_NAME}] fetch failed: {e}")
            continue

        total_fetched += len(raw)
        print(f"  → {len(raw)} jobs fetched")

        unique_batch = []
        for job in raw:
            if job.url and job.url in seen_urls:
                continue
            unique_batch.append(job)
            if job.url:
                seen_urls.add(job.url)

        if not unique_batch:
            print(f"  → 0 new (all URL-duplicates)")
            continue

        # Annotate monitored_company_id by name (the company id is the plan target's).
        company_name_to_id = {c["name"].lower(): c["company_id"] for c in companies}
        for job in unique_batch:
            job.monitored_company_id = company_name_to_id.get(
                (job.company or "").strip().lower())

        n_new, n_existing, n_dropped = _submit_postings(client, unique_batch)
        total_new += n_new
        print(f"  → {n_new} new saved via batch "
              f"({n_existing} existing, {n_dropped} dropped engaged)")

        time.sleep(1.0)

    already_count = total_fetched - total_new
    print(f"\nMonitored-only scrape: {total_fetched} fetched, {total_new} new, "
          f"{already_count} already in DB")
    client.submit_event({
        "type": "run",
        "jobs_scraped": total_fetched,
        "duration_seconds": round(time.monotonic() - t_start, 1),
        "run_type": "monitored_only",
    })


def _run_broad_scrape_via_api(client: _IngestClient, plan: dict) -> None:
    """Broad scrape with the API as the writer: plan → run → batch + events (US4).

    Mirrors `_run_broad_scrape` field-for-field; only the write step differs —
    postings are POSTed to `/v1/ingest/batch` (engaged dedup + company upsert
    server-side) and the run is reported via an event.
    """
    t_start = time.monotonic()
    job_filter = JobFilter(
        titles=plan["title_families"],
        exclude=plan["exclude"],
        remote_or_hybrid=plan["remote_or_hybrid"],
        date_from=date.today() - timedelta(days=plan["freshness_days"]),
    )

    scraper_classes = discover_scrapers()
    print(f"Scrapers found: {[s.SOURCE_NAME for s in scraper_classes]}")

    seen_urls: set[str] = set()
    total_fetched = 0
    total_new = 0
    total_excluded_date = 0
    total_excluded_title = 0

    for ScraperClass in scraper_classes:
        if (getattr(ScraperClass, "ACQUISITION_MODEL", "board") == "company_keyed"
                and not getattr(ScraperClass, "SUPPORTS_DISCOVERY", False)):
            continue
        if not plan["enabled_sources"].get(ScraperClass.slug(), False):
            print(f"  ⚠️ [{ScraperClass.SOURCE_NAME}] disabled — skipped")
            continue
        scraper = ScraperClass(**_api_scraper_kwargs(ScraperClass, plan))
        print(f"Fetching from {scraper.SOURCE_NAME}...")
        raw = scraper.fetch(job_filter)
        if scraper.auto_disabled_reason:
            client.submit_event({
                "type": "source_disabled",
                "source": ScraperClass.slug(),
                "reason": scraper.auto_disabled_reason,
            })
        print(f"  → {len(raw)} jobs fetched")
        filtered, excl_date, _ = JobFilterEngine.apply(raw, job_filter)
        total_excluded_date += excl_date
        total_fetched += len(filtered)
        print(f"  → {len(filtered)} after filter")

        unique_batch = []
        for job in filtered:
            if job.url and job.url in seen_urls:
                continue
            unique_batch.append(job)
            if job.url:
                seen_urls.add(job.url)

        # Title gate (verbatim from the direct path — never drops a posting that
        # survived JobFilterEngine.apply, kept for parity).
        kept = []
        for job in unique_batch:
            if not title_matches_profile(job.title, plan["title_families"]) \
                    or title_excluded(job.title, plan["exclude"]) is not None:
                total_excluded_title += 1
                continue
            kept.append(job)

        if not kept:
            print(f"  → 0 new (all URL-duplicates or title-excluded)")
            continue

        n_new, n_existing, n_dropped = _submit_postings(client, kept)
        total_new += n_new
        print(f"  → {n_new} new via batch "
              f"({n_existing} existing, {n_dropped} dropped engaged)")

    already_count = total_fetched - total_new
    print(f"\nScrape complete: {total_fetched} fetched, {total_new} new, "
          f"{already_count} already in DB")
    if total_excluded_date:
        print(f"📅 {total_excluded_date} jobs excluded (posted > {plan['freshness_days']} days ago)")
    if total_excluded_title:
        print(f"🔤 {total_excluded_title} jobs excluded (title not in platform scope)")

    client.submit_event({
        "type": "run",
        "jobs_scraped": total_fetched,
        "duration_seconds": round(time.monotonic() - t_start, 1),
        "run_type": "full",
    })


if __name__ == "__main__":
    main()
