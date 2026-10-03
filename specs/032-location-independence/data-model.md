# Data Model: Location Independence (roadmap step 1a)

**No database schema change** (non-goal). This spec introduces a single conceptual
entity — the **location configuration** — realised as the public API of `paths.py`,
plus one environment contract and an import-time guard.

---

## Entity: Location configuration (`paths.py`)

The set of paths every runtime module must obtain from `paths.py`. `PROJECT_ROOT` is
the only value derived from `__file__`; everything else is derived from
`PROJECT_ROOT` or an environment override.

| Field | Type | Derivation | Notes |
|-------|------|-----------|-------|
| `PROJECT_ROOT` | `str` (absolute) | `os.path.dirname(os.path.abspath(__file__))` | Not overridable. |
| `DATA_DIR` | `str` (absolute) | `os.environ.get("JOB_AGENT_DATA_DIR")` or `PROJECT_ROOT/data` | The DB lives here. |
| `DB_PATH` | `str` | `os.path.join(DATA_DIR, "jobs.db")` | Guarded by `JOB_AGENT_REQUIRE_DB`. |
| `OUTPUT_DIR` | `str` (absolute) | `os.environ.get("JOB_AGENT_OUTPUT_DIR")` or `PROJECT_ROOT/outputs` | Ephemeral digests (JSON/HTML/MD). |
| `ENV_PATH` | `str` | `os.path.join(PROJECT_ROOT, ".env")` | Secrets file, never committed. |
| `data_path(*parts)` | `str` | `os.path.join(DATA_DIR, *parts)` | Helper for files under `DATA_DIR` (e.g. `companies.json`). |

### Environment contract

| Variable | Values | Effect |
|----------|--------|--------|
| `JOB_AGENT_DATA_DIR` | absolute/relative dir | Overrides `DATA_DIR` (default `PROJECT_ROOT/data`). |
| `JOB_AGENT_OUTPUT_DIR` | absolute/relative dir | Overrides `OUTPUT_DIR` (default `PROJECT_ROOT/outputs`). |
| `JOB_AGENT_REQUIRE_DB` | `"1"` (any other value → unset) | Enables the import-time guard. |

### Guard state machine

```
import paths
  ├─ JOB_AGENT_REQUIRE_DB unset
  │    → os.makedirs(DATA_DIR, exist_ok=True)   # unchanged behaviour (onboarding)
  ├─ JOB_AGENT_REQUIRE_DB == "1" and DB_PATH exists
  │    → proceed; NO makedirs                    # prod path
  └─ JOB_AGENT_REQUIRE_DB == "1" and DB_PATH missing
       → raise RuntimeError(<path + env vars>)    # nothing created
```

The guard checks `DB_PATH` existence only (not integrity, not table counts) — integrity
is `scripts/health_check.py`'s concern (spec 031). Scripts that do not import `paths`
(e.g. `backup_db.py`, `fingerprint.py`, which take an explicit `--db`) are unaffected.

---

## Location-site mapping (FR-004)

Every site below is replaced by the `paths` value in the "To" column, with no behaviour
change in dev or prod.

| Site | From | To |
|------|------|----|
| `export_jobs.py:26-27` | `Path(__file__).parent / "data" / "jobs.db"`; same for output | `DB_PATH` (via `paths`); `OUTPUT_DIR = Path(DATA_DIR)` |
| `tracker_views/preferences.py:114` | `JobStorage("data/jobs.db")` | `JobStorage(DB_PATH)` |
| `tracker_views/shared.py:95` | `os.path.join(os.path.dirname(__file__), "..", ".env")` | `ENV_PATH` |
| `notifier.py:106,210` | `os.path.join(os.path.dirname(__file__), "outputs")` | `OUTPUT_DIR` |
| `score.py:568` | `os.path.join(os.path.dirname(__file__), "outputs")` | `OUTPUT_DIR` |
| `prepare.py:568` | `os.path.join(os.path.dirname(__file__), "outputs", "applications")` | `os.path.join(OUTPUT_DIR, "applications")` |
| `seed.py:18-28` | `open("data/companies.json")` | `open(data_path("companies.json"))` |
| `export_seed.py:45` | `output_path = "data/companies.json"` | `output_path = data_path("companies.json")` |
| `scripts/scraper_toggle.py:14` | `JobStorage("/app/data/jobs.db")` | `JobStorage(DB_PATH)` |

Note: `export_jobs.py` currently uses its `DB_PATH` as a `pathlib.Path` (`.exists()`);
after the change it becomes a `str` from `paths`, so the existence check must wrap it
in `Path(...)`. This is a mechanical detail, captured in the tasks.

---

## Launch-site mapping (FR-005)

14 sites; module name = filename minus `.py`; `-u` preserved only where present today;
all other args/`check`/`capture_output`/`cwd` unchanged.

| Site | From | To |
|------|------|----|
| `main.py:77` | `[sys.executable, "scrape.py", "--monitored-only", "--no-score"]` | `[sys.executable, "-m", "scrape", "--monitored-only", "--no-score"]` |
| `main.py:89` | `[sys.executable, "scrape.py"]` | `[sys.executable, "-m", "scrape"]` |
| `main.py:100` | `[sys.executable, "score.py", "--extract"]` | `[sys.executable, "-m", "score", "--extract"]` |
| `main.py:113` | `[sys.executable, "score.py", "--profile", active_id]` | `[sys.executable, "-m", "score", "--profile", active_id]` |
| `scrape.py:318` | `[sys.executable, "score.py", "--extract"]` | `[sys.executable, "-m", "score", "--extract"]` |
| `scrape.py:319` | `[sys.executable, "score.py", "--profile", profile.id]` | `[sys.executable, "-m", "score", "--profile", profile.id]` |
| `tracker_views/jobs.py:119` | `[sys.executable, "-u", "scrape.py"]` | `[sys.executable, "-u", "-m", "scrape"]` |
| `tracker_views/jobs.py:123` | `[sys.executable, "-u", "score.py", "--profile", active_id]` | `[sys.executable, "-u", "-m", "score", "--profile", active_id]` |
| `tracker_views/jobs.py:135` | `[sys.executable, "score.py", "--extract"]` | `[sys.executable, "-m", "score", "--extract"]` |
| `tracker_views/settings.py:200` | `[sys.executable, "-u", "scrape.py"]` | `[sys.executable, "-u", "-m", "scrape"]` |
| `tracker_views/settings.py:215` | `[sys.executable, "-u", "scrape.py", "--monitored-only"]` | `[sys.executable, "-u", "-m", "scrape", "--monitored-only"]` |
| `tracker_views/settings.py:230` | `[sys.executable, "-u", "score.py", "--profile", active_id]` | `[sys.executable, "-u", "-m", "score", "--profile", active_id]` |
| `tracker_views/settings.py:824` | `[sys.executable, "score.py", "--extract"]` | `[sys.executable, "-m", "score", "--extract"]` |
| `monitoring_agent.py:83` | `[sys.executable, "export_seed.py"]` (`cwd=ROOT`) | `[sys.executable, "-m", "export_seed"]` (`cwd=ROOT` kept) |
