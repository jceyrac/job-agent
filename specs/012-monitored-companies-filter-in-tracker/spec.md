# Spec 012 — Monitored Companies Filter in Tracker

> Scope: tracker.py sidebar + storage.py query. No schema change. No new dependencies.
> Read `tracker.py`, `storage.py`, and `models.py` before starting.
> Do not modify `main.py`, `scrape.py`, `score.py`, or any scraper.

---

## Goal

Add a "Source type" filter to the Jobs tab sidebar in `tracker.py` that lets the user isolate jobs that were retrieved via the monitoring pipeline (direct ATS board scraping) vs. jobs that came in through general job board scrapers.

---

## Context

`jobs.monitored_company_id` (INTEGER, nullable) is already populated by the monitoring pipeline when a job is scraped from a watched company's ATS board. It is `NULL` for all jobs coming from general scrapers (JobSpy, CryptoJobsList, Greenhouse board list, etc.). No new DB field is required.

---

## Changes required

### 1. `storage.py` — `get_all_for_tracker()`

Verify that `monitored_company_id` is included in the SELECT. If not, add it. The field must be present in the returned dicts so `tracker.py` can filter on it in Python.

No other changes to `storage.py`.

### 2. `tracker.py` — sidebar filter

Add a selectbox at the bottom of the sidebar filters section (below the existing Source multiselect), under a visual separator (`st.divider()`):

```python
source_type = st.selectbox(
    "Source type",
    options=["All", "Monitored companies", "Job boards only"],
    index=0
)
```

Apply the filter in the Python filtering logic alongside existing filters:

```python
if source_type == "Monitored companies":
    jobs = [j for j in jobs if j.get("monitored_company_id") is not None]
elif source_type == "Job boards only":
    jobs = [j for j in jobs if j.get("monitored_company_id") is None]
```

No SQL change — filtering stays in Python per the existing pattern.

---

## Acceptance criteria

- [ ] "Source type" selectbox appears in the sidebar below existing filters
- [ ] Selecting "Monitored companies" shows only jobs with `monitored_company_id IS NOT NULL`
- [ ] Selecting "Job boards only" shows only jobs with `monitored_company_id IS NULL`
- [ ] "All" (default) shows no change in behaviour vs. current
- [ ] Filter composes correctly with all existing sidebar filters (profile, score, status, work mode, etc.)
- [ ] No regression on existing tracker functionality
