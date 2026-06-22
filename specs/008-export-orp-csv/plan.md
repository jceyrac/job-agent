# Implementation Plan: Export CSV ORP

**Branch**: `008-export-orp-csv` | **Date**: 2026-06-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/008-export-orp-csv/spec.md`

## Summary

Rewrite `export_orp.py` — a standalone CLI script that queries the live SQLite DB (`jobs JOIN job_tracking`), produces a `data/orp_YYYY-MM.csv` file compatible with Swiss ORP form 716.007, and prints a terminal summary. Single file, stdlib only, zero changes to existing code.

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: None (stdlib: `sqlite3`, `csv`, `argparse`, `datetime`, `calendar`, `pathlib`, `sys`)

**Storage**: SQLite via read-only `sqlite3.connect()` — no `JobStorage`, no ORM

**Testing**: Manual verification on dev DB (`data/jobs.db`); no automated tests (CLI script, single-purpose)

**Target Platform**: macOS (dev) + Ubuntu server (prod, `/opt/job-agent/`)

**Project Type**: CLI script (single file, standalone)

**Performance Goals**: <2s on 4000+ job DB (single SQL query, no row-by-row processing)

**Constraints**: No new dependencies; no modification of `storage.py`, `models.py`, or any existing file

**Scale/Scope**: One file (`export_orp.py`), ~150 lines, 3 CLI flags, 10 CSV columns

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Filet large, point de filtrage unique | ✅ N/A | Read-only export, no filtering decisions |
| II. Deux chemins d'amélioration | ✅ Pass | Code path — single file change, git → deploy |
| III. Profil unifié unique | ✅ Pass | Profile-independent (covers all jobs) |
| IV. Structure déterministe, prose LLM uniquement | ✅ Pass | No LLM; all logic is deterministic mapping |
| V. Modification chirurgicale | ✅ Pass | One file, no opportunistic refactors, no existing files touched |
| VI. Validation empirique avant livraison | ✅ Pass | Will verify against live DB before commit |
| VII. Sécurité d'abord | ✅ Pass | Read-only DB access, no secrets, no network |
| VIII. Scrapers organisés par modèle d'acquisition | ✅ N/A | Not a scraper |
| IX. Le scoring est une couche optionnelle | ✅ Pass | Works without scores (reads jobs + job_tracking only) |

**Verdict**: No violations. All applicable principles pass.

## Project Structure

### Documentation (this feature)

```text
specs/008-export-orp-csv/
├── plan.md              # This file
├── spec.md              # Feature specification
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
export_orp.py            # THE ONLY FILE — standalone CLI script (rewritten in place)
```

**Structure Decision**: Single-file CLI script at repo root. No `src/`, no `tests/`, no new directories. The existing `export_orp.py` is overwritten.

## Phase 0 — Research

### DB schema verification

Confirmed via `storage.py` (lines 168–218):

| Table | Column | Type | Used for |
|-------|--------|------|----------|
| `jobs` | `id` | TEXT PK | JOIN key |
| `jobs` | `title` | TEXT NOT NULL | CSV "Description du poste" |
| `jobs` | `company` | TEXT | CSV "Entreprise / Adresse" |
| `jobs` | `location` | TEXT | CSV "Entreprise / Adresse" suffix |
| `jobs` | `source` | TEXT | Activity deduction (currently unused) |
| `jobs` | `work_mode` | TEXT | CSV "Description du poste" suffix |
| `jobs` | `url` | TEXT | CSV "URL" |
| `job_tracking` | `job_id` | TEXT PK | JOIN key |
| `job_tracking` | `status` | TEXT NOT NULL | Filter + CSV "Résultat" mapping |
| `job_tracking` | `notes` | TEXT | CSV "Motif si négatif" |
| `job_tracking` | `changed_at` | TEXT | **Date filter** — NOT `updated_at` (bug in existing script) |

### Existing script bugs identified

| Line | Current (wrong) | Correct |
|------|-----------------|---------|
| 79 | `date(jt.updated_at) AS status_date` | `date(jt.changed_at) AS status_date` |
| 84 | `AND date(jt.updated_at) BETWEEN ? AND ?` | `AND date(jt.changed_at) BETWEEN ? AND ?` |
| 165 | Creates empty CSV file even with 0 rows | Guard: only write if `rows` is non-empty |
| — | Missing `--from`/`--to` priority over `--month` | Add warning when both provided |
| — | Missing résumé par statut (FR-010) | Add `collections.Counter` summary after preview |

### What stays from existing script

- Query structure (JOIN jobs + job_tracking, WHERE status IN + date BETWEEN)
- CSV column layout and formatting logic
- `utf-8-sig` encoding
- Terminal preview format
- `DB_PATH` resolution via `Path(__file__).parent`

## Phase 1 — Design

### CLI interface

```
python export_orp.py                           # current month, status=applied
python export_orp.py --month 2026-05           # specific month
python export_orp.py --from 2026-05-15 --to 2026-06-14
python export_orp.py --statuses applied rejected archived
```

**`--from`/`--to` over `--month`**: If both `--from` AND `--month` are provided, print `[WARN] --from/--to prend priorité sur --month` and use `--from`/`--to`. Same for `--to`.

### SQL query

```sql
SELECT j.title, j.company, j.location, j.url, j.source, j.work_mode,
       jt.status, date(jt.changed_at) AS status_date, jt.notes
FROM jobs j
JOIN job_tracking jt ON j.id = jt.job_id
WHERE jt.status IN (?, ...)
  AND date(jt.changed_at) BETWEEN ? AND ?
ORDER BY jt.changed_at ASC
```

### Status → Résultat mapping

| DB status | ORP résultat |
|-----------|-------------|
| `applied`, `queued`, `ready`, `saved`, `new` | en suspens |
| `rejected`, `archived`, `expired` | négatif |

### Activity deduction

All sources → `"par lettre / électronique"` (simplified per FR-011; all jobs come from online platforms).

### CSV columns (10)

1. Jour (day of month)
2. Mois (month number)
3. Entreprise / Adresse (`company — location`)
4. Personne contactée / Tél. (empty)
5. Description du poste (`title (work_mode)`)
6. Assignation ORP (`Non`)
7. Activité (`par lettre / électronique`)
8. Résultat (mapped from status)
9. Motif si négatif (notes if rejected/archived, else empty)
10. URL

### Terminal output

```
Période : 2026-06-01 → 2026-06-30
Statuts  : ['applied']

[OK] 12 entrée(s) exportée(s) → data/orp_2026-06.csv

--- Résumé par statut ---
  applied: 12

--- Aperçu ---
  03/06  Acme Corp — Lausanne                [en suspens]
  05/06  Example SA — Geneva                 [en suspens]
  ...
```

### Data flow

```
args → resolve_date_range() → fetch_jobs(SQL) → format_for_orp() → write_csv() + print_summary()
```

## Complexity Tracking

No violations. Table intentionally left empty.

## Implementation Steps

### Step 1 — Rewrite `export_orp.py`

Single file, ~150 lines. Structure:
1. Imports (stdlib only)
2. Constants (`DB_PATH`, `OUTPUT_DIR`, `STATUSES_DEFAULT`, `STATUS_MAP`)
3. `parse_args()` — argparse setup
4. `resolve_date_range()` — month vs from/to resolution
5. `fetch_jobs()` — SQLite query with JOIN
6. `format_for_orp()` — row-by-row mapping
7. `write_csv()` — CSV writer with utf-8-sig
8. `print_summary()` — terminal preview + per-status count
9. `main()` — orchestration
10. `if __name__ == "__main__"` guard

### Step 2 — Verify

Run against live dev DB:
```bash
python export_orp.py --month 2026-06
python export_orp.py --statuses applied rejected archived
python export_orp.py --from 2026-01-01 --to 2026-06-22
```

Check:
- CSV opens in LibreOffice (utf-8-sig BOM)
- Column count = 10
- Dates are split day/month
- Résultat mapping correct
- Summary counts match CSV rows
