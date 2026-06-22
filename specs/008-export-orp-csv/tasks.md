# Tasks: Export CSV ORP

**Input**: Design documents from `specs/008-export-orp-csv/`

**Prerequisites**: plan.md ✅, spec.md ✅

**Tests**: Manual verification only (CLI script, no unit test infrastructure for standalone scripts)

**Organization**: Single user story delivers full MVP; P2/P3 are incremental flags on the same script.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)

---

## Phase 1: Core Script (US1 — Export mensuel)

**Purpose**: The standalone CLI script that delivers the P1 monthly export

- [ ] T001 [US1] Write `export_orp.py` — imports, constants, argparse CLI (--month, --from, --to, --statuses) in project root
- [ ] T002 [US1] Write `resolve_date_range()` — month default vs --from/--to priority logic in `export_orp.py`
- [ ] T003 [US1] Write `fetch_jobs()` — SQLite query with `jobs JOIN job_tracking` on `jt.changed_at` in `export_orp.py`
- [ ] T004 [US1] Write `format_for_orp()` — status→résultat mapping, activity deduction, CSV row assembly in `export_orp.py`
- [ ] T005 [US1] Write `write_csv()` — utf-8-sig output, `data/orp_YYYY-MM.csv` naming, empty-guard in `export_orp.py`
- [ ] T006 [US1] Write `print_summary()` — per-status count + terminal preview in `export_orp.py`
- [ ] T007 [US1] Write `main()` orchestration + `if __name__ == "__main__"` guard in `export_orp.py`

---

## Phase 2: Extended Date Range (US2)

**Purpose**: --from/--to support with priority over --month

- [ ] T008 [US2] Implement --from/--to taking priority over --month with warning message in `resolve_date_range()` in `export_orp.py`

---

## Phase 3: Status Filtering (US3)

**Purpose**: --statuses flag with multi-select and correct mapping

- [ ] T009 [US3] Ensure --statuses accepts all 7 valid statuses, defaults to applied, and maps each to correct ORP résultat in `export_orp.py`

---

## Phase 4: Verification

**Purpose**: Run against live dev DB and validate output

- [ ] T010 Run `python export_orp.py --month 2026-06` against dev DB, verify CSV opens in LibreOffice, check 10 columns, check day/month split, check counts
- [ ] T011 Run `python export_orp.py --statuses applied rejected archived` and verify résultat mapping + motif column
- [ ] T012 Run `python export_orp.py --from 2026-01-01 --to 2026-06-22` and verify date range filtering

---

## Notes

- All tasks target a single file: `export_orp.py` at repo root
- No task touches `storage.py`, `models.py`, or any other existing file
- No new dependencies — stdlib only
- T008 and T009 are small modifications to functions created in T002 and T004 respectively; in practice T001–T007 produce the complete script
