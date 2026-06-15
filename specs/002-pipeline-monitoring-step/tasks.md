# Tasks: Pipeline Monitoring Step

**Input**: `prompts/BUILD_pipeline_monitoring_step.md`

## Phase 1: Implementation

- [x] T001 Read `main.py`, `scrape.py`, `storage.py` config helpers, and `tracker_views/settings.py` `_render_run_controls`
- [x] T002 Add `--monitored-only` and `--no-monitoring` flags to `main.py` argparse, with mutual exclusivity check
- [x] T003 Add decision logic in `main.py`: read `monitoring.enabled_in_pipeline` config, default enabled; skip if `--no-monitoring` or 0 monitored companies
- [x] T004 Insert monitoring step before broad scrape in the pipeline, with dynamic step numbering
- [x] T005 Handle `--monitored-only` path: run monitoring scrape → extract → score, omit broad scrape entirely
- [x] T006 Preserve existing timing/elapsed-time prints and `db.log_run()`/`db.update_last_run()` error handling
- [x] T007 Add "Include monitored-company scrape in the full pipeline run" checkbox to `_render_monitored_companies()` in Settings
- [x] T008 Add "🎯 Run monitoring only" button to `_render_run_controls()` in Settings, using existing `subprocess.Popen` background pattern
- [x] T009 Manual validation: `main.py` with 0 monitored companies, `--no-monitoring`, `--monitored-only`, mutual exclusion error, Settings checkbox persistence, "Run monitoring only" button

## Format

All tasks are single-phase — no parallel tasks needed.
