# Quickstart: Configurable Freshness Window

Validation guide — proves the feature end-to-end without touching prod data.

## Prerequisites

- `.venv/` activated; `data/jobs.db` present.
- Baseline test run recorded: `python -m pytest tests/ -q` (all green before edits).

## 1. Regression invariant (byte-identical at default 30)

```bash
# helper falls back to 30 when the key is absent/malformed
python -c "from storage import JobStorage; s=JobStorage('data/jobs.db'); print(s.get_freshness_days())"   # → 30
python -c "from storage import JobStorage; s=JobStorage('data/jobs.db'); s.set_config('freshness_days','abc'); print(s.get_freshness_days())"  # → 30

# filter honors date_from; falls back to 30d when None
python -m pytest tests/test_freshness.py -q
```

Expected: the broad `JobFilter` carries `date_from = today − 30d`; filters.py and
storage.py:1356 produce output identical to today (each layer keeps its own date
mechanism — only "30" is now a parameter).

## 2. The one intended behavioral change (Joinup early-stop)

```bash
python scrape.py
```

Expected: Joinup's broad fetch pages ~3 pages instead of ~112 (grep the log for
`[Joinup]`); the surfaced jobs match the pre-change set within 30d.

## 3. Settings widget (no-deploy edit)

```bash
streamlit run tracker.py
# Settings → "Freshness Window" → set 10 → Save
```

Expected: `set_config` writes `freshness_days="10"`; `SELECT value FROM config
WHERE key='freshness_days'` returns `10`; the next `python scrape.py` / `main.py`
uses the 10-day window.

## 4. Full regression

```bash
python -m pytest tests/ -q
python -m pytest tests/test_joinup.py::test_date_cutoff_stops_pagination -q
```

Expected: full suite green (incl. the 162-test storage suite and the FELFEL case
in `test_title_gate.py`); the Joinup pagination test still passes.

## 5. Reset to default

```bash
python -c "from storage import JobStorage; s=JobStorage('data/jobs.db'); s.set_config('freshness_days','30')"
```
