# Implementation Plan: Pipeline Monitoring Step

**Branch**: `002-pipeline-monitoring-step` | **Date**: 2026-06-15 | **Spec**: [spec.md](./spec.md)

## Summary

Wire the `--monitored-only` scrape path into `main.py`'s full pipeline. Add a config key to control it, CLI flags to override it, and Settings UI to manage it.

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: None new. Uses existing `subprocess`, `argparse`, Streamlit widgets.

**Storage**: No schema changes. One new config key: `monitoring.enabled_in_pipeline`.

**Files touched**: `main.py` (CLI + pipeline logic), `tracker_views/settings.py` (checkbox + button).

**Tests**: Manual validation (CLI flag combinations, config persistence).

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Filet large | ✅ | Monitoring step is additive, broad scrape unchanged |
| II. Deux chemins | ✅ | Config key = prose path, `main.py` flags = code path |
| V. Chirurgical | ✅ | Only `main.py` and `settings.py` touched, no schema changes |
| VI. Validation empirique | ✅ | 5 acceptance scenarios per flag, config, and UI |
| IX. Scoring optionnel | ✅ | Pipeline still works without monitoring |

## Phase Breakdown

Single phase — no data model changes, no new files.

## Files changed

- `main.py` — argparse flags, decision logic, step renumbering
- `tracker_views/settings.py` — checkbox + "Run monitoring only" button
