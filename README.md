# job_agent

Multi-profile job scraping and scoring system for **Senior Product Manager** roles.

Scrapes 12+ job boards, stores everything in SQLite, scores each posting with a Groq LLM per search profile, and delivers a digest via email and Joplin. A Streamlit tracker UI lets you browse, filter, and track application status.

---

## How it works

```
scrape.py → SQLite DB → score.py (Groq LLM, per profile) → tracker.py (Streamlit UI)
                                                           → email digest + Joplin note
```

1. **`scrape.py`** fetches raw job postings from all enabled scrapers and stores only new ones in the DB (deduplication by URL).
2. **`score.py`** reads unscored jobs from the DB, applies SQL pre-filters, runs each through a Groq LLM with profile-specific context, and writes scores back. Multiple profiles can score the same job independently.
3. **`tracker.py`** is a Streamlit app for reviewing scored jobs, filtering by date/location/geo/status, and tracking applications.

### Scoring model chain (Groq, with automatic fallback)

| Priority | Model |
|----------|-------|
| 1 | `llama-3.3-70b-versatile` |
| 2 | `meta-llama/llama-4-scout-17b-16e-instruct` |
| 3 | `groq/compound` |
| 4 | `llama-3.1-8b-instant` |

If a model hits its daily quota, the next one in the chain is tried automatically.

---

## CLI reference

### Scrape new jobs

Fetches from all enabled scrapers and adds only new postings to the DB. Safe to run daily — duplicates are silently skipped.

```bash
python scrape.py
```

### List existing profiles

```bash
python create_profile.py --list
```

### Create a new profile

Interactive wizard. Prompts for ID, name, work modes, geo zones, company sizes, score threshold, scoring context (multi-line, end with `END`), and location keywords for pre-filtering.

```bash
python create_profile.py
```

After saving, run the scorer:

```bash
python score.py --profile <profile_id>
```

### Score unscored jobs for an existing profile

Only jobs not yet scored for this profile are processed. Safe to re-run after scraping new jobs.

```bash
python score.py --profile <profile_id>
```

### Rescore all jobs for a profile (wipes existing scores)

```bash
python score.py --profile <profile_id> --rescore
```

### Test a profile's scoring context (no DB writes)

Scores 3 hardcoded sample jobs to verify the profile's LLM instructions produce sensible results.

```bash
python score.py --profile <profile_id> --mock
```

### Delete a profile

Removes the profile and all its scores from the DB. Prompts for confirmation.

```bash
python create_profile.py --delete <profile_id>
```

### Launch the tracker UI

```bash
streamlit run tracker.py
```

---

## Search profiles

Profiles live in `profiles.py` (built-in) and can be created interactively via `create_profile.py`. Each profile controls:

| Setting | Purpose |
|---------|---------|
| `allowed_geo_zones` | Post-scoring geo filter (e.g. `europe`, `global_remote`) |
| `allowed_work_modes` | Post-scoring work mode filter (e.g. `remote`, `hybrid`) |
| `location_keywords` | Pre-scoring SQL filter — only jobs matching at least one keyword in `base_location` are scored |
| `pre_filter` | Additional SQL filters: `title_contains`, `exclude_title_contains`, `location_contains`, `exclude_location_contains` |
| `score_threshold` | Minimum score to appear in the digest |
| `scoring_context` | LLM system-prompt suffix with profile-specific scoring instructions |
| `company_sizes` | Post-scoring company size filter |

### Built-in profiles

| ID | Name | Focus |
|----|------|-------|
| `web3_remote` | Web3 Remote | Senior PM, Web3/DeFi/AI, fully remote globally |
| `ch_hybrid` | Switzerland Hybrid | Senior PM, Switzerland hybrid/remote, all tech verticals |

---

## Database schema

Three core tables:

| Table | Key | Purpose |
|-------|-----|---------|
| `jobs` | `id` (SHA-256 of URL) | Raw job postings, shared across all profiles |
| `job_scores` | `(job_id, profile_id)` | LLM scores and metadata — pure scoring, no pipeline state |
| `job_tracking` | `job_id` | Pipeline status (`new→queued→ready→applied→rejected→archived`) and notes — profile-independent |
| `job_applications` | `job_id` | Application analysis and cover letter — profile-independent |

The status of a job is **profile-independent**: marking a job "applied" in one profile view marks it applied everywhere. Scores remain per-profile since the same job can be evaluated differently under different search criteria.

---

## Tracker UI

The Streamlit tracker (`streamlit run tracker.py`) provides:

- **Profile selector** — view jobs scored under a specific profile, or all profiles at once (best score per job)
- **Status pipeline** — New → Queued → Ready → Applied, with Rejected and Archived for dismissals
- **Filters** — min score, posted within, location, work mode, geo zone, company size, source
- **Per-job** — notes, application content (analysis + cover letter when status = Ready), direct link to job posting
- **Stats bar** — counts per status across the current view

---

## Scrapers

| Source | Method | Descriptions | Status | Notes |
|--------|--------|-------------|--------|-------|
| **LinkedIn** | `python-jobspy` | ✅ | ✅ Active | Europe-scoped, 4 queries × 20 results |
| **Indeed** | `python-jobspy` | ✅ | ✅ Active | 4 queries × 9 countries + Switzerland |
| **Greenhouse** | Public API | ✅ | ✅ Active | 30 crypto/Web3/fintech boards |
| **WeWorkRemotely** | RSS | ✅ | ✅ Active | |
| **Web3Career** | HTML scraping | ✅ | ✅ Active | Fetches individual job pages for descriptions |
| **RemoteOK** | JSON API | ✅ | ✅ Active | |
| **CryptoJobsList** | `__NEXT_DATA__` + JSON-LD | ✅ | ✅ Active | Individual pages at `/jobs/<slug>` |
| **CryptoJobs.com** | HTML scraping | ✅ | ✅ Active | Fetches individual job pages for descriptions |
| **DeFi Jobs** | HTML scraping | ✅ | ✅ Active | Falls back to crypto.jobs; JSON-LD descriptions |
| **TieTalent** | `__NEXT_DATA__` | ✅ | ✅ Active | Swiss-focused |
| **Jobup.ch** | HTML scraping | ❌ | ✅ Active | Swiss-focused; JS-rendered pages, no description available |
| **Wellfound** | RapidAPI | ✅ | ⚠️ Limited | 10 calls/month on free plan |
| **Xing** | HTML scraping | — | ❌ Disabled | JS-rendered |
| **Malt** | — | — | ❌ Disabled | JS-rendered SPA |
| **BeInCrypto Jobs** | — | — | ❌ Disabled | JS-rendered |
| **Jobs.ch** | — | — | ❌ Disabled | JS-rendered |

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/jceyrac/job-agent.git
cd job-agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_APIKEY` | ✅ | Groq API key — free at [console.groq.com](https://console.groq.com) |
| `GMAIL_FROM` | ✅ | Gmail address for sending the digest |
| `GMAIL_APP_PASSWORD` | ✅ | Gmail App Password — [generate here](https://myaccount.google.com/apppasswords) |
| `NOTIFY_TO` | ✅ | Destination email |
| `JOPLIN_TOKEN` | Optional | Joplin Web Clipper token |
| `X_RAPIDAPI_KEY` | Optional | RapidAPI key for Wellfound |

### 3. Typical daily workflow

```bash
# 1. Fetch new jobs from all scrapers
python scrape.py

# 2. Score new jobs for each profile
python score.py --profile web3_remote
python score.py --profile ch_hybrid

# 3. Review in the tracker UI
streamlit run tracker.py
```

---

## Running tests

```bash
python tests/test_storage.py
```

Unit tests using an in-memory SQLite DB — safe to run at any time, no network calls, no DB writes. Covers schema contracts, profile-independent status/application design, scoring, digest queries, and edge cases.

```bash
python tests/run_all.py
```

Live integration tests — each scraper makes real HTTP calls. Exit code `0` if all pass or skip gracefully, `1` if any fail.

---

## Project structure

```
job_agent/
├── scrape.py                              # Scrape all enabled sources → SQLite
├── score.py                               # Score jobs per profile with Groq LLM
├── tracker.py                             # Streamlit review UI
├── create_profile.py                      # CLI: create / list / delete profiles
├── main.py                                # Orchestrator: scrape → score
├── profiles.py                            # Built-in profile definitions
├── scorer.py                              # Groq LLM scoring (4-model fallback chain)
├── storage.py                             # SQLite persistence layer
├── models.py                              # JobPosting dataclass
├── filters.py                             # Pre-LLM filter engine
├── notifier.py                            # Email digest + Joplin export
├── backfill_descriptions.py              # One-shot: fetch descriptions for NULL rows
├── migrate_single_status.py              # Migration: merged application_status → status
├── migrate_profile_independent_tracking.py # Migration: status/notes → job_tracking table
├── scrapers/                              # One module per job board
│   ├── base.py
│   ├── jobspy_scraper.py
│   ├── greenhouse.py
│   └── ...
├── tests/
│   ├── test_storage.py                    # Unit tests (in-memory DB)
│   └── run_all.py                         # Live scraper integration tests
├── data/jobs.db                           # SQLite database (gitignored)
└── outputs/                               # JSON digests (gitignored)
```

---

## License

MIT
