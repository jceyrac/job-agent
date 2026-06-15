# Data Model: Monitored Companies

**Feature**: 001-monitored-companies
**Date**: 2026-06-15

## Entities

### Company (extended)

Extends the existing `companies` table. New columns in **bold**.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | Existing |
| `name` | TEXT | NOT NULL | Existing |
| `website` | TEXT | | Existing |
| `country` | TEXT | | Existing |
| `industry_sector` | TEXT | | Existing |
| `size` | TEXT | | Existing |
| `status` | TEXT | | Existing (CRM status) |
| **`careers_url`** | TEXT | NULLABLE | URL of the company's careers/jobs page. Used for ATS detection. |
| **`ats_provider`** | TEXT | NULLABLE | One of: `greenhouse`, `lever`, `ashby`, `workday`, `smartrecruiters`, `workable`. NULL if no ATS identified. |
| **`ats_identifier`** | TEXT | NULLABLE | Provider-specific identifier (Greenhouse token, Lever token, Ashby board slug, Workday tenant+site, SmartRecruiters company ID, Workable account ID). |
| **`scraper_id`** | TEXT | NULLABLE | For bespoke scrapers in `company_sites/`. Mutually exclusive with `ats_provider` in practice (a company uses one scraping method). |
| **`monitored`** | BOOLEAN | DEFAULT FALSE | Whether this company is actively monitored. Pause = set to FALSE. |
| **`detected_at`** | TIMESTAMP | NULLABLE | When the ATS provider was first detected/resolved. |

**Derived states**:
- *Scrapable*: `ats_provider IS NOT NULL OR scraper_id IS NOT NULL`
- *Monitorable* (toggle activatable in UI): same as scrapable
- *Monitored*: `monitored = TRUE`
- *Paused*: `monitored = FALSE` (scraping config preserved; re-enable by setting TRUE)

**Relationships**:
- Company -(1)→ Jobs: a company can have many jobs (via `jobs.company_id` FK)
- Company ←(1) Contacts, Interactions: CRM data — out of scope for this feature

**Seed data**: ~30 Greenhouse boards from the current `CRYPTO_WEB3_BOARDS` constant migrated as rows:
```sql
INSERT INTO companies (name, ats_provider, ats_identifier, monitored)
VALUES ('Fireblocks', 'greenhouse', 'fireblocks', true);
-- ... (~30 boards)
```

### Job (extended)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | TEXT | PK (SHA-256 of URL) | Existing |
| ... | | | All existing columns unchanged |
| **`monitored_company_id`** | INTEGER | NULLABLE, FK → companies.id | Set at scrape time if the job came from a monitored company. NULL for jobs from broad boards or non-monitored companies. |
| **`filtered_non_product`** | BOOLEAN | DEFAULT FALSE | Set to TRUE by the title gate when the job's title doesn't match the PM family. These jobs never reach the LLM. |

**Derived states**:
- *From monitored company*: `monitored_company_id IS NOT NULL`
- *Filtered by title gate*: `filtered_non_product = TRUE`
- *Reaches scoring*: `filtered_non_product = FALSE` (gate passed) OR gate not yet run

**Note**: `monitored_company_id` is set at scrape time (when the monitoring run writes the job) and is immutable thereafter. It is NOT set retroactively — if a company is monitored after its jobs were already scraped via broad boards, those existing jobs do not gain the flag.

### Migrations

New dedicated table for tracking applied schema migrations:

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | Migration ID |
| `name` | TEXT | NOT NULL, UNIQUE | Migration name (e.g., "add_monitored_companies") |
| `applied_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | When the migration was applied |

**Migrations for this feature**:
1. `add_monitored_companies` — Add `careers_url`, `ats_provider`, `ats_identifier`, `scraper_id`, `monitored`, `detected_at` to `companies`
2. `add_monitored_company_id` — Add `monitored_company_id` FK to `jobs`
3. `add_filtered_non_product` — Add `filtered_non_product` to `jobs`
4. `add_migrations_table` — Create the `migrations` table itself (bootstrap)

### Pipeline Runs (extended)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| ... | | | Existing columns |
| **`run_type`** | TEXT | DEFAULT 'full' | `full` (broad scrape + discovery) or `monitored_only` (monitoring-only run) |

## State Transitions

### Company monitoring lifecycle

```
[Company created without scrape_method]
  → User adds careers_url → ATS detected → scrape_method resolved
  → User toggles monitored = TRUE
  → [Monitored: active]
  → User pauses (monitored = FALSE) → scraping config preserved
  → User resumes (monitored = TRUE)
  → User pauses again... (indefinitely)
```

### Job from monitored company

```
[ATS returns openings for monitored company]
  → All openings written to jobs table (monitored_company_id set)
  → Title gate runs (pre-LLM):
      ├── PASS (PM family match) → proceeds to extraction + scoring
      └── FAIL (non-PM title) → filtered_non_product = TRUE, no LLM call
```

## Validation Rules

1. `ats_provider` must be one of the known providers if non-NULL
2. If `ats_provider` is set, `ats_identifier` must also be set (and vice versa)
3. `monitored = TRUE` requires `ats_provider IS NOT NULL OR scraper_id IS NOT NULL` (enforced at application level, not DB constraint — allows migration flexibility)
4. `monitored_company_id` on jobs must reference a valid `companies.id` where `monitored = TRUE` at the time of insertion
