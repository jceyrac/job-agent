# Quickstart: validate spec 038

## Prerequisites

- `.venv` activated; `pip install -e ".[dev]"` already done.
- `.cv_pipeline/cv_data_master.json` present (the master, including the new
  `optional_roles`/`optional_bullets`/`optional_education` sections added 2026-10-11).

## Unit tests (no live LLM)

```bash
python -m pytest tests/test_geo_ch.py tests/test_cv_agent_lint.py tests/test_cv_agent_boundary.py -q
```

Covered:
- detector edge cases (Berlin → `non_CH`; "Remote (Switzerland)" → `CH`;
  "Zurich or Berlin" → `mixed`; no location → `unknown`; multilingual names; `CH-8000`).
- precedence: each source; `--contact` over `--country` over `db_country` over detector.
- renderer raises on a missing/invalid profile.
- golden-prompt: `proposed_profile` + "DACH"/"Swiss employer"/"CH-viable" absent from
  `ANALYZE_AND_PLAN_PROMPT`.
- lint: US2-1..4 and US4-1..3.

## Full suite

```bash
python -m pytest tests/ -q   # must be 100% green
```

## Empirical runs (US3) on the Mac

```bash
# 1. chargecloud (Cologne) → french (detected or default+flag), lint clean
python -m core.cv_agent.cli --paste --auto --title "..." --company "chargecloud" < posting.txt

# 2. a Swiss-based posting (Sygnum Zurich / Neho Lausanne) → swiss
python -m core.cv_agent.cli --paste --auto --title "..." --company "Sygnum" < posting.txt

# 3. a country-less remote posting → french + contact_profile_flag == "unknown_country"
python -m core.cv_agent.cli --paste --auto --title "..." --company "..." < remote.txt
```

Confirm `review.json` (printed path) shows the expected `contact_profile`,
`contact_profile_source` and `contact_profile_flag`.

## Staging skip — import check (FR-009)

```bash
grep -rn "geo_ch" core/ tracker_views/ tracker.py   # only core/geo_ch.py + core/cv_agent/*
```

No tracker/cron path imports the classifier ⇒ no staging required.

## Release

ff-only merge to `main` → `scripts/deploy.sh` (image alignment only) → confirm
`git -C /opt/job-agent rev-parse HEAD` equals the merged SHA.
