# Research — Domain Package `core/` (spec 034, roadmap step 1c)

Phase 0 output. Verified against source at `7a44b92` (2026-10-08). The spec is
already clarified; this file resolves the *how* — the mechanical decisions the
move depends on — not open product questions.

---

## 1. The `core/` package layout

**Decision**: `core/` is a new package (`core/__init__.py`, empty). The 27 domain
modules move to `core/<name>.py`; `scrapers/` → `core/scrapers/`; `cv_agent/` →
`core/cv_agent/`. `tracker.py`, `tracker_views/`, `scripts/`, `tests/` and the
five one-offs stay at the root. No shims, no root aliases.

The 27 modules (FR-006) are exactly the root `.py` files minus `tracker.py`,
`fill_orp_pdf.py` (gitignored) and the five one-offs
(`migrate_expired_status`, `migrate_profile_independent_tracking`,
`migrate_single_status`, `test_wellfound`, `tracker_legacy`):

```text
ats_detection, backfill_descriptions, company_researcher, context_tuner,
create_profile, cv_extract, email_monitor, export_jobs, export_seed, filters,
job_actions, llm, main, models, monitoring_agent, notifier, paths,
preference_report, prepare, profile_generator, profiles, score, scorer, scrape,
seed, storage, title_gate
```

**Rationale**: this is the structure the constitution's Monorepo constraint
names (`core/` = domaine), and the spec's non-goals bound the blast radius by
leaving the Streamlit surface at the root. `git mv` preserves history (FR-006);
no rewrite of logic.

**Verified**: no relative imports exist anywhere (grep for `^\s*(from|import) \.`
is empty), so the rewrite is a pure name-prefix operation with no `from .` →
`from core.` disambiguation.

---

## 2. The rewrite script (`scripts/rewrite_core_imports.py`, FR-007)

**Decision**: one committed, idempotent, re-runnable script performs the entire
import + string-reference rewrite in a single mechanical pass over every `.py`
file (root, `core/`, `tracker_views/`, `scripts/`, `tests/`). No edits by hand.

**Moved top-level names**: the 27 modules ∪ `scrapers` ∪ `cv_agent`.

**Import rewrite rules** (only when the name is a moved top-level and not
already `core.`-prefixed):

| Before | After |
|---|---|
| `import <moved>` | `from core import <moved>` |
| `from <moved> import X` | `from core.<moved> import X` |
| `from <moved>.<sub> import X` | `from core.<moved>.<sub> import X` |
| `import <moved>.<sub>` | `import core.<moved>.<sub>` |

The `import <moved>` → `from core import <moved>` form matches the spec's own
example (`from core import llm`) and keeps the bare name usable unchanged. Inside
`core/` the same rules apply (absolute `core.` everywhere, per FR-007 and US2-2),
so `core/scrape.py` does `from core.storage import JobStorage`.

**String-reference rewrite rules** (FR-008), all idempotent via the "already
`core.`-prefixed → skip" guard:

1. `-m` subprocess args — `"-m", "scrape"` → `"-m", "core.scrape"`; same for
   `score`, `export_seed`. (14 occurrences: `main.py` ×4, `scrape.py` ×2,
   `tracker_views/jobs.py` ×3, `tracker_views/settings.py` ×4,
   `monitoring_agent.py` ×1.)
2. `importlib.import_module` literals and f-string static prefixes —
   `"scrapers.greenhouse"` → `"core.scrapers.greenhouse"`,
   `f"scrapers.{subpkg}.{module_name}"` → `f"core.scrapers.{subpkg}.{module_name}"`
   (`scrape.py:32,48,224,228`, `storage.py:1081`, plus the `from scrapers.greenhouse
   import CRYPTO_WEB3_BOARDS` at `storage.py:1078`).
3. `mock.patch` / `monkeypatch.setattr` string targets in `tests/` —
   `"llm.call"` (×9) → `"core.llm.call"`, `"cv_agent.nodes.interrupt"` (×3) →
   `"core.cv_agent.nodes.interrupt"`, `"scrapers.boards.joinup.time.sleep"`,
   `"scrapers.boards.free_work.time.sleep"`, `"scrapers.boards.free_work.FreeWorkScraper._fetch_slug"`
   → `core.scrapers.*`. Stdlib targets (`"builtins.input"`, `"sys.stdout"`) are
   untouched (never a moved name).
4. **No file-path-string rule.** Source-tree *file* paths (`scrapers/…`) are **not**
   rewritten by the script — they are derived from `SCRAPERS_SRC_DIR` in `paths.py`
   (see §3, §5), so Phase B changes only that constant. The generated-spec/stub
   *prose* strings that mention `scrapers/…` are hand-updated in the same Phase-B
   commit and listed by the FR-015 grep audit, not by this script.

**Repo-root location for the script itself**: it locates the repo via
`core.paths.PROJECT_ROOT` (post-move this is the repo root, §5), run from the repo
root so `core/` is importable even before `pyproject.toml` is updated. It uses
**no `__file__`**, so the FR-004 guard needs no extra allow-list entry.

**Idempotency**: every rule is guarded by "top-level name already `core.`-prefixed
→ skip". A second run is a byte-for-byte no-op (SC-002).

---

## 3. Gap found: `monitoring_agent.py` relative file-path strings

**Finding**: besides its `ROOT = Path(__file__).resolve().parent` (FR-002), the
dev tool carries relative file-path strings that reference the *files* being
moved, which the FR-005 module-reference guard cannot see (they are paths, not
importable modules):

```text
monitoring_agent.py:30   GREENHOUSE_CONFIG_PATH = "scrapers/greenhouse.py"
monitoring_agent.py:34-35 ATS_CONFIG_PATHS = {"lever": ("scrapers/ats/lever.py", …), …}
monitoring_agent.py:172  _write_scraper_stub(provider, f"scrapers/ats/{provider}.py")
monitoring_agent.py:270  _write_scraper_stub("custom", f"scrapers/company_sites/{slug}.py")
```

Lines 30–35 are vestigial (spec 006-pre removed config-file editing), but 172/270
are live: they write scraper stubs under `ROOT / "scrapers/…"`. After the move
`ROOT` becomes `PROJECT_ROOT` (FR-002) and the stubs must land under
`core/scrapers/`. **Decision**: do **not** rewrite these strings. Introduce a
single `SCRAPERS_SRC_DIR = os.path.join(PROJECT_ROOT, "scrapers")` constant in
`paths.py` (pre-move) and derive every source-tree scraper path from it; Phase B
changes only the constant to `core/scrapers` (FR-009). A guard test (FR-014)
asserts `SCRAPERS_SRC_DIR` and its `ats/`/`boards/`/`company_sites/` subdirectories
exist, so a wrong constant fails the suite. The generated-spec/stub *prose* strings
(`_write_ats_spec` lines ~190/199, stub `Reference: scrapers/ats/lever.py`) are not
filesystem paths — they are listed by the FR-015 grep audit and hand-updated to
`core/scrapers/…` in Phase B.

---

## 4. Scraper discovery without `__file__` (FR-003)

**Decision**: `scrape.py:21` (`scrapers_dir = os.path.join(os.path.dirname(__file__),
"scrapers")`) is replaced by enumerating the package's `__path__`:

```python
import core.scrapers.boards as _b, core.scrapers.ats as _a, core.scrapers.company_sites as _c
# …then pkgutil.iter_modules(_b.__path__) / _a.__path__ / _c.__path__ …
```

The three sub-packages are imported directly and `pkgutil.iter_modules(<pkg>.__path__)`
replaces the current `iter_modules([pkg_path])`. The root-level fallback loop
(`scrape.py:44-48`, for legacy `greenhouse` etc.) becomes
`pkgutil.iter_modules(core.scrapers.__path__)`. `importlib.import_module` strings
are rewritten by §2 rule 2. This is the **last `__file__` in runtime code besides
`paths.py`** (confirming the spec's Context table).

---

## 5. `paths.py` — `PROJECT_ROOT` one level up + `CV_PIPELINE_DIR` + `SCRAPERS_SRC_DIR` (FR-001, FR-002, FR-009)

**Decision** (the only location changes, FR-009):

```python
# core/paths.py
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
CV_PIPELINE_DIR = os.environ.get(
    "CV_PIPELINE_DIR",
    os.path.join(os.path.dirname(PROJECT_ROOT), ".cv_pipeline"),
)
SCRAPERS_SRC_DIR = os.path.join(PROJECT_ROOT, "core", "scrapers")  # pre-move: "scrapers"
```

`DATA_DIR`, `OUTPUT_DIR`, `ENV_PATH`, `DB_PATH`, `data_path()` are unchanged and
resolve to the same values because they derive from `PROJECT_ROOT`. `SCRAPERS_SRC_DIR`
is the **only source-tree location that changes value** across the move: `scrapers/`
pre-move → `core/scrapers/` post-move (the constant changes, not the strings that use it).

`cv_agent/renderer.py` (FR-001) drops its local `_REPO_ROOT`/`CV_PIPELINE_DIR`
(lines 31–35) and imports `CV_PIPELINE_DIR` from `core.paths`, keeping
`MASTER_CV_PATH = os.path.join(CV_PIPELINE_DIR, "cv_data_master.json")` derived
locally. Verified the computed value is unchanged: old
`dirname(dirname(abspath(__file__)))` = repo root, `dirname(_REPO_ROOT)` =
`…/AI-Suite`; new `dirname(PROJECT_ROOT)` = `…/AI-Suite`. ✓

---

## 6. Guard tests (FR-004, FR-005)

**Decision**: extend the existing repo-scan guards with a new file
`tests/test_core_module_refs.py`, keeping `test_installable_project.py` and
`test_location_independence.py` as-is (their allow-lists change only where a path
moves — see §7).

**FR-004 — no `__file__` in runtime code, allow-listed by pattern**: an AST scan
of runtime `*.py` (the 27 domain modules, `scrapers/`, `cv_agent/`, `tracker.py`,
`tracker_views/`, `scripts/`; skip `.venv`, `.git`, `__pycache__`, `tests`,
`specs`) that fails on any `__file__` outside two *patterns*, not a directory
allow-list: (a) `paths.py`, the single allowed `__file__`-derived location; (b) a
call to `is_active_page` whose sole argument is the `__file__` name (matched via
AST). `tracker_views/` is therefore covered, not exempt. This is strictly broader
than spec 032's FR-006(a) — it catches the `renderer.py`, `monitoring_agent.py`
and `scrape.py` patterns the earlier guard missed, plus the two Phase-A script
violations (`scripts/duplicate_report.py`, `scripts/fingerprint.py`) now fixed.
The rewrite script itself uses no `__file__`.

**FR-005 — resolve every string module reference**: an AST-based test that, for
each of the three string kinds, resolves the name to an importable module (or
module + attribute) and fails with `file:line` otherwise:

- `-m` args: walk `subprocess.run`/`subprocess.Popen` (and the `_launch_bg` helper
  in `tracker_views`) call lists, find the literal after `"-m"`, `importlib.util.find_spec("core." + name)`.
- `importlib.import_module(<arg>)`: for a literal string, resolve whole; for an
  f-string, resolve the static prefix before the first `{`.
- `tests/` `mock.patch`/`monkeypatch.setattr` string targets: split at the last
  `.` into module/attr, `find_spec(module)` + `getattr(module, attr)` (for a pure
  module target like `"llm.call"`, resolve `core.llm.call`). Stdlib targets
  (`builtins.*`, `sys.*`) resolve against their module directly.

**FR-014 — assert source-tree base dirs exist**: import `SCRAPERS_SRC_DIR` from
`paths` and assert `os.path.isdir()` for `SCRAPERS_SRC_DIR` and its `ats/`,
`boards/`, `company_sites/` subdirectories. This ties the constant to reality:
if the constant points at the wrong tree (pre- or post-move), the suite fails.
Negative fixture: a temporary value for `SCRAPERS_SRC_DIR` that does not exist.

Each guard must demonstrably fail when its violation is reintroduced (SC-001),
covered by a negative fixture (a temp file with a bad `-m`/`importlib`/`patch`
string, a temporary `__file__`, or a non-existent `SCRAPERS_SRC_DIR`).

---

## 7. `pyproject.toml` (FR-010) and guard allow-list updates

**Decision**: `py-modules = ["tracker"]` (the only root module left after the
move; the five one-offs stay allow-listed and undeclared, `fill_orp_pdf.py` stays
gitignored). `packages` gains `core`, `core.scrapers`,
`core.scrapers.ats`, `core.scrapers.boards`, `core.scrapers.company_sites`,
`core.cv_agent`; `tracker_views`, `scripts` stay. The spec-033 root-module guard
(`test_installable_project.py`) is updated: it must scan `core/` modules against
`packages` (or assert the root contains only `tracker.py` + allow-list), so a
domain module left at the root fails (US2-5).

---

## 8. Non-Python string references (FR-011) — manual, not the script

- `docker-compose.yml`: `agent` `command: python main.py` →
  `python -m core.main`; `email-monitor` `command: python email_monitor.py` →
  `python -m core.email_monitor`. The `tracker` command stays `streamlit run
  tracker.py` (tracker stays at root).
- `scripts/deploy.sh:59`: `docker exec job-tracker python seed.py` →
  `docker exec job-tracker python -m core.seed`.
- `CLAUDE.md` / `docs/*.md` / constitution Stable-core list → `core/` paths
  (FR-012; constitution amendment is a PATCH, wording only).

These are hand-edited in the same Phase-B commit (the script rewrites Python only).

---

## 9. Edge cases verified

- **LangGraph checkpointer** (`data/cv_agent_checkpoints.sqlite`, 5.4 MB on the
  dev Mac): may hold pickled module paths (`cv_agent.state.*`). The spec's
  Assumptions already confirm **no unfinished session** (user finished his
  application); leftover threads may be discarded. Pre-US2 check is a task, not a
  code change.
- **`streamlit run tracker.py`** keeps working: tracker + `tracker_views/` stay at
  root; only their imports become `core.*` (edge case in spec).
- **`deploy.sh` re-exec** (already present) guarantees the new
  `python -m core.seed` line is the one run after `git pull` (spec edge case).
- **`cv_agent/__init__.py`** does `from cv_agent.graph import compile_graph` →
  becomes `from core.cv_agent.graph import compile_graph` (absolute inside core).
- **`prompts/`** is historical and not rewritten (spec edge case).

---

## 10. Alternatives considered

- **`import core.storage` everywhere vs `from core import …`** — the codebase's
  dominant style is `from X import Y`; the spec's examples use `from core import
  llm`, so `from core import <moved>` for bare imports and `from core.X import Y`
  for from-imports. Rejected `import core.X` only where it would force a mass
  rename of bare names.
- **`ast`-based rewrite (structural) vs regex** — a regex rewrite with the
  "already `core.`-prefixed → skip" guard is idempotent and simpler to audit for a
  one-shot tool; `ast` unparsing would risk comment/formatting churn. Rejected.
- **Shims (`core/storage.py` re-exporting from root)** — explicitly forbidden by
  the spec (no shims) and would defeat "one way to import".
