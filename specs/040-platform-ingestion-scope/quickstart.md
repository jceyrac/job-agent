# Quickstart — validate spec 040 (platform ingestion scope)

Run on the dev Mac (`.venv/`), against a **copy** of a real DB, never the live
`data/jobs.db`. The point is to prove: (1) the scope is seeded identically to the old
profile values, (2) parity is **byte-identical** before any scoring run, (3) the first
scoring run converges the flagged set via the new Tier 0 title rule without changing
what the user sees as a good match.

## Prerequisites

```bash
cd /Users/jeanclaudevd/AI-Suite/job_agent
source .venv/bin/activate
cp data/jobs.db /tmp/spec040.db            # work on a copy
DAYS=$(python -c "from core.storage import JobStorage; print(JobStorage('/tmp/spec040.db').get_freshness_days())")
echo "freshness window = $DAYS days"
```

## 1. Migration seeds the scope and is idempotent

```bash
python -c "from core.storage import JobStorage; s = JobStorage('/tmp/spec040.db'); print(s.get_ingestion_scope())"
# open again → no further change (recorded in migrations)
python -c "from core.storage import JobStorage; s = JobStorage('/tmp/spec040.db'); print(s.get_ingestion_scope())"
```

Expected: `title_families` == the active profile's `job_titles`,
`exclude` == `title_exclude`, `remote_or_hybrid` == `scrape_remote_or_hybrid`
(**False** for the seed profile — not `True`). Two consecutive opens produce the same
dict. The migration writes **no** `job_scores` rows.

## 2. Parity — byte-identical before scoring (research §1)

```bash
# candidate (this branch) on the copy
python -m scripts.fingerprint --db /tmp/spec040.db --as-of 2026-10-14 --ref candidate > /tmp/spec040_cand.json
# pre-step image on the same copy (see step-3 parity procedure; --ref prod)
python -m scripts.fingerprint --db /tmp/spec040.db --as-of 2026-10-14 --ref prod > /tmp/spec040_prod.json
python -m scripts.compare_fingerprints /tmp/spec040_prod.json /tmp/spec040_cand.json
```

Expected: **no diff at all** — the migration writes only `config.ingestion_scope` and
makes no score change, so the candidate and pre-step fingerprints are identical.

## 3. Ingestion reads the scope, not the profile

```bash
python -m core.scrape --source <one cheap board>        # same job count as before
python -m core.scrape --monitored-only --no-score        # same monitored companies
```

Then edit the scope and confirm ingestion follows it, not the profile (SC-003):

```bash
python -m scripts.ingestion_scope --set-titles "product manager"   # narrow
# re-run one board — fewer/narrower results, unaffected by a profile edit
```

## 4. Scoring emits Tier 0 title rows instead of the flag (expected-change check)

Compute `N` — the flagged, unscored jobs within the freshness window:

```bash
N=$(sqlite3 /tmp/spec040.db "SELECT COUNT(*) FROM jobs j WHERE j.filtered_non_product = 1 AND NOT EXISTS (SELECT 1 FROM job_scores s WHERE s.job_id = j.id) AND (j.posted_date >= date('now', '-$DAYS days') OR j.posted_date IS NULL OR j.posted_date = '')")
echo "N = $N"
```

Then score and assert exactly `N` new title rows and nothing else changed:

```bash
python -m core.score --profile <active>                  # Tier 0 title rows now appear
sqlite3 /tmp/spec040.db "SELECT reason, COUNT(*) FROM job_scores WHERE scored_by='tier_0' AND reason LIKE 'filtered: title%' GROUP BY reason"
python -m scripts.fingerprint --db /tmp/spec040.db --as-of 2026-10-14 --ref after_score > /tmp/spec040_after.json
python -m scripts.compare_fingerprints /tmp/spec040_cand.json /tmp/spec040_after.json
```

Expected: `set_job_filtered_non_product` no longer exists. The `after_score` diff vs
the pre-score `candidate` fingerprint is confined to the `+N` Tier 0 title rows —
`counts.job_scores +N`, `feed.by_score_band` `unscored −N` / `<5 +N`, `feed.by_status`
`unscored −N` / `new +N`, `scores.distribution` `"1": N`, `stats.scored +N` — and no
other score changed.

> **Note on `N`:** the flag was only ever set for a title *mismatch*, so
> `filtered_non_product = 1` is the right proxy for "title fails `job_titles`". If the
> profile also has non-empty `title_exclude`, a monitored job whose title *matches*
> `job_titles` but contains an exclude word was never flagged (it was excluded by the
> pre-filter instead) and will now get a `filtered: excluded title word (<w>)` row too —
> additive, not counted in `N`. SC-001 defines `N` precisely as "title fails
> `job_titles` **or** `title_exclude`".

## 5. Guard tests

```bash
python -m pytest tests/test_ingestion_scope.py -v
python -m pytest tests/ -v
```

Expected: green. The guard fails if any ingestion code (`core/scrape.py`,
`core/scrapers/**`) reads `job_titles` / `title_exclude` / `scrape_remote_or_hybrid`,
or if any runtime code writes `jobs.filtered_non_product` outside the migration.

## 6. Tracker smoke (no visible change except score badges)

```bash
streamlit run tracker.py
```

Feed, digest, status change, job detail — a previously-flagged monitored job now shows
score `1` ("filtered: title") instead of "unscored" after the scoring run; it is still
absent from the digest (score < threshold). The Settings job-titles field shows the
read-only note: "Ingestion uses the platform scope — edit with `python -m
scripts.ingestion_scope`".
