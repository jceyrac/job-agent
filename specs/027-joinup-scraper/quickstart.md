# Quickstart — Joinup scraper validation

Prereqs: `.venv` active, run from repo root (`AI-Suite/job_agent`).

## 1. Offline unit test (no network)

```bash
python -m pytest tests/test_joinup.py -q
```

Expected: all tests pass; every test reads from
`tests/fixtures/joinup_browse_jobs*.html` and monkeypatches the page-fetch so
**no network I/O** occurs.

## 2. Full offline suite (regression)

```bash
python -m pytest tests/ -q
```

Expected: existing 162 storage tests + all scraper parsing tests pass.

## 3. Live scraper check (optional, hits joinup.ch)

```bash
python tests/run_all.py
```

Expected: a `JoinupScraper` row with `base_location` and `work_mode` marked
optional (⚠️), the rest ✅. `run_all.py` is intentionally live and not part of
the commit gate.

## 4. Manual live fetch

```bash
python -c "from scrapers.boards.joinup import JoinupScraper; from models import JobFilter; \
print(len(JoinupScraper().fetch(JobFilter())))"
```

Expected: a positive count of `JobPosting`s from the first few pages (the
date-cutoff stops pagination early when `date_from` is unset it pages until
`nbPages` or `MAX_PAGES`).
