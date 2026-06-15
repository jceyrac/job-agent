# Feature Specification: Pipeline Monitoring Step

**Feature Branch**: `002-pipeline-monitoring-step`

**Created**: 2026-06-15

**Status**: Draft

**Input**: `prompts/BUILD_pipeline_monitoring_step.md` — wire `--monitored-only` into `main.py` full pipeline.

## User Scenarios & Testing

### User Story 1 - Monitoring runs automatically in full pipeline (Priority: P1)

The user runs `python main.py` as usual. If they have at least one monitored company and haven't disabled the feature, the pipeline includes a monitored-company scrape step before the broad scrape, so new openings from targeted companies are collected and scored alongside everything else.

**Why this priority**: Monitoring has no value if the user must remember to run it manually. The full pipeline is the default entry point.

**Independent Test**: `python main.py` with ≥1 monitored company and config unset → monitored scrape runs as Step 1, followed by broad scrape, extraction, scoring.

**Acceptance Scenarios**:

1. **Given** ≥1 monitored company exists and config key is unset, **When** `python main.py` runs, **Then** the monitored-company scrape step executes before the broad scrape.
2. **Given** 0 monitored companies exist, **When** `python main.py` runs, **Then** no monitoring step executes and the pipeline is identical to today's behavior.
3. **Given** config `monitoring.enabled_in_pipeline = "false"`, **When** `python main.py` runs, **Then** the monitoring step is skipped regardless of how many monitored companies exist.

---

### User Story 2 - CLI control over monitoring (Priority: P2)

The user can override the monitoring behavior from the command line without touching Settings.

**Why this priority**: CLI flags enable cron overrides and ad-hoc runs without UI access.

**Independent Test**: `python main.py --no-monitoring` skips the monitoring step; `python main.py --monitored-only` runs only monitoring + extract + score.

**Acceptance Scenarios**:

1. **Given** ≥1 monitored company, **When** `python main.py --no-monitoring` runs, **Then** the monitoring step is skipped and only broad scrape → extract → score executes.
2. **Given** ≥1 monitored company, **When** `python main.py --monitored-only` runs, **Then** only monitored scrape → extract → score executes (broad scrape omitted entirely).
3. **Given** both `--monitored-only` and `--no-monitoring` passed, **When** `python main.py` starts, **Then** it exits with an error and a clear message, no subprocess calls made.

---

### User Story 3 - Settings UI controls (Priority: P3)

The user can enable/disable pipeline monitoring from the Settings page, and trigger a monitoring-only run with one click.

**Why this priority**: UI convenience — the feature works from CLI first.

**Independent Test**: Toggle the checkbox in Settings, verify `main.py` respects it. Click "Run monitoring only", verify `scrape.py --monitored-only` runs as a background process.

**Acceptance Scenarios**:

1. **Given** the Settings page is open, **When** the user toggles the "Include monitored-company scrape in the full pipeline" checkbox, **Then** the config key is updated and the change persists across reruns.
2. **Given** the Settings page is open, **When** the user clicks "🎯 Run monitoring only", **Then** `scrape.py --monitored-only` runs as a background process with the same live-output/stop/completion UI as the existing scrape/score buttons.

---

### Edge Cases

- What if `main.py --monitored-only` runs and there are 0 monitored companies? The `scrape.py --monitored-only` subprocess exits 0 with "Nothing to monitor", then extraction and scoring run normally (finding nothing new). No special handling in `main.py`.
- What if a step in the pipeline fails? The existing `try/except` block catches `subprocess.CalledProcessError`, calls `db.log_run(status="error")`, and prints the error — unchanged.

## Requirements

### Functional Requirements

- **FR-001**: `main.py` MUST support a `--monitored-only` flag that runs only the monitored-company scrape step followed by extraction and scoring (no broad scrape).
- **FR-002**: `main.py` MUST support a `--no-monitoring` flag that skips the monitored-company scrape step even if enabled in config.
- **FR-003**: `--monitored-only` and `--no-monitoring` MUST be mutually exclusive — passing both MUST exit with an error.
- **FR-004**: `main.py` MUST read config key `monitoring.enabled_in_pipeline` to decide whether to include the monitoring step. Absence of the key MUST default to enabled.
- **FR-005**: When the monitoring step is enabled via config AND at least one monitored company exists, `main.py` MUST run `scrape.py --monitored-only` before the broad scrape step.
- **FR-006**: Step labels in `main.py` output MUST be renumbered dynamically to reflect which steps actually execute (omitted steps do not appear).
- **FR-007**: The Settings page MUST include a checkbox "Include monitored-company scrape in the full pipeline run" that reads and writes `monitoring.enabled_in_pipeline`.
- **FR-008**: The Settings page MUST include a "🎯 Run monitoring only" button that launches `scrape.py --monitored-only` as a background process using the existing subprocess pattern.
- **FR-009**: No new DB columns, tables, or migrations. The config key uses the existing `config` table.
- **FR-010**: `scrape.py` and `score.py` internals MUST NOT be modified.

### Key Entities

- **Config key**: `monitoring.enabled_in_pipeline` — boolean string in the existing `config` table. Absence means enabled.

## Success Criteria

- **SC-001**: `python main.py` with 0 monitored companies runs identically to today (no extra subprocess, no extra log lines).
- **SC-002**: `python main.py` with ≥1 monitored company includes the monitoring step as Step 1.
- **SC-003**: `python main.py --no-monitoring` skips the monitoring step regardless of config.
- **SC-004**: `python main.py --monitored-only` omits the broad scrape entirely.
- **SC-005**: `python main.py --monitored-only --no-monitoring` exits with error, no subprocess calls.
- **SC-006**: Settings checkbox reflects and persists the config value.
- **SC-007**: "Run monitoring only" button runs as a background process with live output.

## Assumptions

- `scrape.py --monitored-only` already works and exits 0 even when there are 0 monitored companies.
- `get_monitored_companies()` already exists in `storage.py` and returns companies where `monitored = TRUE`.
- The existing `main.py` pattern of `subprocess.run()` + `try/except` + `db.log_run()` is preserved unchanged.
- Config key convention: absence = enabled (same as `scraper.<name>.enabled`).
