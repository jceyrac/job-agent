# Research: Configurable Freshness Window

## Call-site map (verified against live source)

The "30 days" freshness concept is expressed in exactly three places, plus the
carrier field that none of them fills:

| Location | Expression | Mechanism | Undated jobs |
|----------|-----------|-----------|--------------|
| `filters.py:11` | `cutoff = date.today() - timedelta(days=30)` | Python `date.today()` | **dropped** |
| `storage.py:1356` | `posted_date >= date('now','-30 days') OR posted_date IS NULL OR = ''` | SQLite `date('now')` | **kept** |
| `models.py:104` | `JobFilter.date_from: Optional[date] = None` | carrier field | n/a — never set |
| `scrape.py:325` | broad `JobFilter(...)` — no `date_from` | n/a | n/a |

`JobFilterEngine.apply` is called from exactly one place: `scrape.py:351`
(broad path). `_run_monitored_only` (scrape.py:184) builds no `JobFilter` and
does no date filtering. `scripts/filter_funnel.py` also builds a `JobFilter`
with `date_from=None` and calls `JobFilterEngine.apply` — it keeps the 30-day
fallback.

## Decisions

### 1. Single helper on `JobStorage`
**Decision**: `JobStorage.get_freshness_days() -> int` coerces the config string
to int and falls back to 30 on absent/malformed (`int(... or "30")` inside a
`try/except (TypeError, ValueError)`).
**Rationale**: One place for coercion; all three consumers (`scrape.py`,
`storage.py:1356`, `settings.py`) call it. `get_config`/`set_config` already
exist and `purge_retention_days` is the precedent.
**Alternatives**: a module-level function would need the config value threaded
in from each caller — more surface, no benefit.

### 2. `date_from` is the single carrier; only the broad path sets it
**Decision**: Set `date_from` on the broad `JobFilter` only. The monitored path
does no date filtering and must stay unchanged.
**Rationale**: `JobFilterEngine.apply` is invoked solely from the broad path
(scrape.py:351). `date_from` already exists on `JobFilter` (models.py:104) — no
model change.
**Alternatives**: setting it in `_run_monitored_only` would add filtering where
none exists today, breaking the byte-identical invariant.

### 3. Each layer keeps its own date mechanism — only "30" becomes a parameter
**Decision**: `filters.py` keeps `date.today() - timedelta(days=...)`;
`storage.py:1356` keeps `date('now','-N days')`. The only change is that the
integer is sourced from `freshness_days` instead of literal `30`.
**Rationale**: This preserves byte-identical output at `freshness_days=30`.
Unifying the two mechanisms (e.g. both use Python dates) would risk a
timezone-boundary discrepancy and widen the diff — out of scope.
**Alternatives**: a shared "compute cutoff date" helper is over-engineering for
two call sites with different runtimes (Python vs SQL).

### 4. Widget mirrors `_render_purge`
**Decision**: `st.number_input(min_value=7, max_value=180, value=db.get_freshness_days())`
+ Save button → `set_config("freshness_days", ...)`, help text noting (a) next-run
effect and (b) the freshness-vs-purge relationship (freshness keys off
`posted_date`/admission; purge keys off `first_seen`/survival — if freshness >
purge, untouched jobs still vanish at the purge horizon).
**Rationale**: `_render_purge` (settings.py:341) is the exact precedent; keeping
the same bounds (7–180) and interaction avoids a new UI pattern.
**Alternatives**: a live-apply slider would break the "effective next run"
contract and require touching the running pipeline state.

### 5. SQL interpolation is safe
**Decision**: Interpolate the coerced `int` (never the raw config string) into
the SQLite date expression via an f-string.
**Rationale**: `get_freshness_days()` guarantees an `int`; no injection surface.
**Alternatives**: a bound parameter would require restructuring the clause
list — unnecessary for a validated integer.
