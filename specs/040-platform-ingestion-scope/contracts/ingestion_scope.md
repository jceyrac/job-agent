# Contract — `config.ingestion_scope` accessors + CLI (FR-001, FR-004)

## 1. `JobStorage` typed accessors (the only read/write path)

```python
def get_ingestion_scope(self) -> dict:
    """Return {title_families: list[str], exclude: list[str], remote_or_hybrid: bool}.
    Absent/malformed key → {"title_families": [], "exclude": [], "remote_or_hybrid": False}."""

def set_ingestion_scope(self, title_families: list[str], exclude: list[str],
                        remote_or_hybrid: bool) -> None:
    """Validate types and write the JSON blob to config['ingestion_scope'].
    Raise ValueError on non-list/non-bool input (never silently coerce)."""
```

**Guarantees:**
- Round-trip: `get_ingestion_scope()` after `set_ingestion_scope(a, b, c)` returns the
  same lists/bool (order preserved).
- No other module reads `config['ingestion_scope']` raw — ingestion goes through
  `get_ingestion_scope()`; the CLI goes through `set_ingestion_scope()`.
- Idempotent and side-effect-free on read.

## 2. `scripts/ingestion_scope.py` CLI (FR-004 — prose path, no deploy)

Run as `python -m scripts.ingestion_scope <subcommand>`.

| Subcommand | Args | Effect | Exit |
|------------|------|--------|------|
| `--show` | — | Pretty-print the current scope (JSON + human summary) | 0 |
| `--set-titles` | one title per line via `--titles a,b,c` or stdin | Replace `title_families` | 0 |
| `--set-exclude` | same shape | Replace `exclude` | 0 |
| `--remote-or-hybrid` | `on` \| `off` | Set `remote_or_hybrid` | 0 |
| *(none)* | — | Alias for `--show` | 0 |

**Rules:**
- Each mutation reads-modifies-writes the whole scope (no partial JSON drift), then
  re-prints `--show` so the result is visible.
- Invalid input (e.g. `--remote-or-hybrid maybe`) → `stderr` message, exit `2`.
- Uses `JobStorage(DB_PATH)` (default user) — no LLM, no scraping, no long work.
- Empty `--set-titles` is allowed and means "open ingestion" (logs a warning).

**Example:**
```bash
python -m scripts.ingestion_scope --show
python -m scripts.ingestion_scope --set-titles "product manager,head of product"
python -m scripts.ingestion_scope --remote-or-hybrid on
```

## 3. Consumers of the scope

- `core/scrape.py` `_run_broad_scrape`: `JobFilter(titles=scope["title_families"],
  exclude=scope["exclude"], remote_or_hybrid=scope["remote_or_hybrid"],
  date_from=today - freshness_days)`.
- `core/scrapers/greenhouse.py`: `title_matches_profile(title, scope["title_families"])`.
- Nothing in `core/score.py` / `core/scorer.py` reads the scope.
