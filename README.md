# job_agent

Personal automation agent for a Senior Product Manager job search across
**Web3 / DeFi / fintech / AI**, with a single unified search profile, a
Streamlit tracker + lightweight CRM, and targeted company monitoring via ATS
adapters.

---

## How it works

```
Scrape (wide net) → Extract (structured fields) → Score (LLM, per profile) → Tracker (Streamlit)
                  ↘ Monitored-only scrape (ATS adapters) ↗
```

1. **`scrape.py`** fetches raw postings from job boards (LinkedIn, Indeed,
   Web3Career, RemoteOK, WeWorkRemotely, CryptoJobsList, and more) and writes
   them to SQLite. The scraper is a *wide net* — no relevance filtering at this
   stage.
2. **`score.py --extract`** runs a profile-independent extraction pass: work
   mode, geo zone, company size, sector, country, contract type, language.
3. **`score.py --profile <id>`** runs the full pipeline for one search
   profile: pre-filter → extract any unextracted survivors → LLM evaluation
   (1–10 score + reasoning) via DeepSeek, with deterministic Tier-0 rules (company
   blacklist, country/sector/language exclusions, soft penalties) ahead of any
   LLM call.
4. **`tracker.py`** (Streamlit) is the human interface: browse and triage
   scored jobs, manage companies/contacts/interactions (lightweight CRM), edit
   the active search profile, and configure monitored companies.
5. **`scrape.py --monitored-only`** runs a separate, company-keyed scrape: for
   a curated list of companies you actively target, it queries their ATS
   (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable) directly and
   pulls **all** their open roles — bypassing the noisy aggregation boards
   entirely.

All scoring and filtering decisions live in one **unified search profile**
(`unified_jc` by default), edited from the Settings page or via
`profile_generator.py` during onboarding.

> **Note**: today, step 5 must be run manually
> (`python scrape.py --monitored-only`) or via the Settings UI — `main.py`'s
> full pipeline runs only steps 1–4. Wiring monitoring into the full pipeline
> as an optional, config-gated step is specced in
> `specs/002-pipeline-monitoring-step/` (not yet implemented).

---

## Architecture

```
job_agent/
├── main.py              # Thin orchestrator: scrape → extract → score (subprocess pipeline)
├── scrape.py             # Broad scrape (--monitored-only for company-keyed mode)
├── score.py               # Extraction + LLM evaluation (--extract / --profile / --rescore / --mock)
├── scorer.py              # DeepSeek LLM calls (extraction + evaluation)
├── title_gate.py          # Deterministic PM-title gate (pre-LLM, protects DeepSeek quota)
├── ats_detection.py        # Resolves an ATS provider/identifier from a careers URL
├── storage.py              # SQLite persistence (WAL mode) — all DB access goes through here
├── models.py               # JobPosting / JobFilter dataclasses
├── filters.py              # JobFilterEngine (title, location, work mode, company size…)
├── profiles.py             # SearchProfile dataclass + ALL_PROFILES
├── profile_generator.py    # Onboarding: questionnaire + CV → structured profile
├── context_tuner.py         # LLM-generated scoring_context proposals
├── preference_report.py     # --full / --action-items / --suggest-context / --apply-context
├── prepare.py                # Application package prep (cover letter, CV bullets, research)
├── job_actions.py             # extract_one / score_one / contact discovery
├── notifier.py                 # Email digest + Joplin export
├── email_monitor.py             # Parses inbound emails for application status (Proton via Hydroxide)
│
├── scrapers/
│   ├── base.py            # BaseScraper ABC (enable/disable via config, optional targets=)
│   ├── boards/             # Aggregation boards — wide-net, query-driven
│   │   ├── linkedin.py, indeed.py (+ _jobspy_helpers.py)
│   │   ├── web3career.py, remoteok.py, weworkremotely.py
│   │   ├── cryptojobslist.py, cryptojobs_com.py, defi_jobs.py
│   │   ├── tietalent.py, jobup.py, wellfound.py
│   ├── ats/                 # Company-keyed ATS adapters — used by --monitored-only
│   │   ├── ashby.py, lever.py, smartrecruiters.py, workable.py, workday.py
│   ├── greenhouse.py         # Greenhouse (broad CRYPTO_WEB3_BOARDS net + monitored-only path)
│   └── company_sites/        # Bespoke per-company scrapers (currently empty)
│
├── tracker.py               # Streamlit entry point (multi-page app)
└── tracker_views/
    ├── dashboard.py          # Follow-ups, recent inbound, stale outreach
    ├── jobs.py / job_detail.py / job_helpers.py   # Job list + detail, filters, status actions
    ├── companies.py / company_detail.py           # Company list/detail, monitoring badges + toggle
    ├── contacts.py / contact_detail.py            # Lightweight CRM
    ├── preferences.py         # Feedback loop / preference report UI
    ├── onboarding.py           # First-run wizard (questionnaire + CV → profile)
    ├── settings.py              # Profile editor, scraper toggles, monitored companies, setup
    └── shared.py                # Cached loaders, badges, filters, monitoring helpers
```

---

## Data model (SQLite, `data/jobs.db`)

| Table | Purpose |
| --- | --- |
| `jobs` | Raw + extracted job postings (one row per job, shared across profiles) |
| `companies` | Company registry — status, enrichment, ATS provider/identifier, `monitored` flag |
| `search_profiles` | Search profile definitions (criteria JSON, including `scoring_context` prose) |
| `job_scores` | Per-job × per-profile score, reason, comp flag |
| `job_tracking` / `status_history` | Pipeline status (`new` → `saved`/`applied`/`rejected`/`archived`/`expired`) |
| `contacts` / `interactions` | Lightweight CRM — people at target companies, touchpoint timeline |
| `runs` | Pipeline run log (`run_type`: `full` or `monitored_only`) |
| `config` | Key-value store — active profile, scraper enable/disable flags, secrets metadata |
| `migrations` | Idempotent schema migration tracking |

All schema changes go through `storage.py`'s `_init_db()` migration chain —
new databases get the full schema, existing ones are migrated in place on
first connection.

---

## Targeted company monitoring

In addition to the wide-net board scrapers, you can mark specific companies as
**monitored**. A monitored company has a resolved ATS (`ats_provider` +
`ats_identifier`, e.g. `greenhouse` / `fireblocks`) and a `monitored` flag.

- **Add a company**: paste its careers URL in Settings → "Monitored
  Companies". `ats_detection.py` resolves the ATS provider from the hostname
  (or a single page fetch for vanity domains like `careers.coinbase.com`), or
  falls back to manual provider/identifier entry.
- **Toggle monitoring**: a single toggle per company, shown in the Companies
  list, company detail page, and Settings. The toggle reflects a three-state
  model:
  - **not monitorable** — no ATS/scraper resolved → no badge, no toggle
  - **monitorable, scraper disabled** — toggle shown but disabled, with a
    pointer to the relevant scraper toggle in Settings
  - **monitorable, scraper enabled** — interactive toggle; badge shows
    `🟢 Monitored · {provider}` or `⚪ Monitorable · {provider}`
- **Run the monitoring scrape**:
  ```bash
  python scrape.py --monitored-only
  ```
  Iterates monitored companies grouped by ATS provider, fetches *all* their
  openings (no title filtering at scrape time), and writes them with
  `monitored_company_id` set.
- **Title gate** (`title_gate.py`): before any LLM call, jobs from monitored
  companies are checked against a PM-family inclusion list (case-insensitive
  substring match, errs on inclusion). Non-matching jobs are marked
  `filtered_non_product` and never consume API quota.
- **Scoring signal**: "this job is from a monitored company" is injected into
  the LLM scoring context as prose — a strong positive signal, not a hard
  score override. A clearly mismatched role (junior, wrong company type) still
  scores low.

---

## Getting started

### 1. Prerequisites

- Python 3.11+
- A [DeepSeek](https://platform.deepseek.com) account (API key) for LLM extraction and evaluation — **optional if you only want scraping**
- Docker + Docker Compose — **optional**, only needed for server deployment

The app runs natively on **Windows, macOS, and Linux** without Docker. All
dependencies are pure Python or have prebuilt wheels for all three platforms.

> **Windows note**: `email_monitor.py` (inbound email parsing via Hydroxide,
> the Proton Mail IMAP bridge) is Linux/macOS only and not needed for the
> core scrape → score → track flow. Everything else — scraping, scoring,
> the Streamlit tracker, and the pipeline — works without modification on
> Windows.

Docker is only required if you want to deploy to a server with the
always-on `tracker` service and cron-triggered `agent` container (see
[Deployment](#deployment) below). On Windows, you can schedule
`python main.py` via Task Scheduler instead of cron.

### 2. Clone and install

```bash
git clone https://github.com/jceyrac/job-agent.git
cd job-agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Required API keys and subscriptions

**`DEEPSEEK_API_KEY` is the only key required to run the full pipeline.**
All other keys are optional.

> **Running without LLM (scraping only)**: if you don't want to set up a
> DeepSeek account, you can still run `scrape.py` and `scrape.py
> --monitored-only` to populate the DB with raw job postings. Jobs will
> appear in the tracker without scores, summaries, or structured fields
> (work mode, geo zone, sector). The score filter in the Jobs tab won't
> apply, but you can browse and triage raw jobs directly. This is useful
> for testing the scraping layer or if you prefer to review jobs manually
> without LLM assistance.

| Service | Variable | Required? | Notes |
| --- | --- | --- | --- |
| [DeepSeek](https://platform.deepseek.com) | `DEEPSEEK_API_KEY` | ✅ For scoring | Sole LLM backend for extraction and evaluation. Cost is negligible — a full pipeline run (100 jobs) costs roughly $0.01. |
| Gmail SMTP | `GMAIL_FROM` + `GMAIL_APP_PASSWORD` | Optional | Sends the daily email digest. Requires a [Google App Password](https://myaccount.google.com/apppasswords). |
| Any email address | `NOTIFY_TO` | Optional | Digest recipient. Required if Gmail SMTP is configured. |
| [Joplin](https://joplinapp.org) | `JOPLIN_TOKEN` | Optional | Exports job notes to Joplin via Web Clipper. Get the token in Joplin → Tools → Web Clipper → Advanced options. |
| [RapidAPI](https://rapidapi.com) | `X_RAPIDAPI_KEY` | Optional | Powers the Wellfound scraper. Free tier limited to 10 req/month. |

These can be set in `.env` before launching, or entered directly from the
Streamlit **Settings** page after onboarding (written to `.env`, never stored
in the database).

```bash
cp .env.example .env
# Edit .env and paste your DEEPSEEK_API_KEY
```

> **Note on scraper coverage without optional keys**: without `X_RAPIDAPI_KEY`,
> the Wellfound scraper is disabled. All other board scrapers (LinkedIn, Indeed,
> Web3Career, RemoteOK, CryptoJobsList, and more) work without any extra key.
> Monitored-company ATS scrapers (Greenhouse, Lever, Ashby, Workable,
> Workday) also require no key.

### 4. First run — onboarding wizard

```bash
streamlit run tracker.py
```

On first launch, the onboarding wizard walks you through:

1. **Template** — pick a starting point (General tech, Fintech, Web3, or blank)
2. **Criteria** — target roles, location, work modes, company sizes, industries, languages
3. **Scraping mode** — choose between broad board scraping, targeted company monitoring, or both
4. **CV upload** — optional; extracts your experience to improve the scoring rubric
5. **Generate** — calls DeepSeek to write a personalised `scoring_context` (the essay the scorer reads)
6. **Review** — edit and refine the rubric before saving

This creates your active search profile and configures which pipeline steps run.

### 5. Run the pipeline

```bash
python main.py
```

This runs, in sequence, the steps you enabled during onboarding:

1. `scrape.py --monitored-only` — company-keyed ATS scrape (if monitoring is enabled and companies are configured)
2. `scrape.py` — broad scrape across all enabled job boards (if board scraping is enabled)
3. `score.py --extract` — profile-independent structured field extraction
4. `score.py --profile <active>` — LLM scoring, digest, and notifications

You can also run steps individually:

```bash
python scrape.py                  # broad board scrape
python scrape.py --monitored-only # company-keyed monitoring scrape only
python score.py --extract         # extraction only (profile-independent)
python score.py --profile unified_jc [--rescore] [--limit N]
python score.py --mock --profile unified_jc   # sanity-check scoring_context against fixed test jobs
```

Or use CLI flags to override the pipeline mode for a single run:

```bash
python main.py --monitored-only   # only the monitoring scrape + extract + score
python main.py --no-monitoring    # skip monitoring, run broad scrape only
python main.py --no-scrape        # skip broad scrape (e.g. extract+score only)
```

### 6. Use the tracker

```bash
streamlit run tracker.py
```

- **Jobs** — browse scored jobs, filter by score/location/work mode, update status
- **Companies** — company registry, monitoring status, relationship notes
- **Contacts** — lightweight CRM for recruiters and hiring managers
- **Dashboard** — follow-ups due, recent inbound interactions, stale outreach
- **Settings** — profile editor, scraper toggles, monitored companies, API key setup

---

## Deployment

`docker-compose.yml` defines two active services and one experimental one:

- **`tracker`** — always-on Streamlit UI on port 8501
- **`agent`** — `python main.py`, triggered by cron (not kept alive)
- **`email-monitor`** — experimental; parses inbound recruiter emails via a Proton Mail IMAP bridge (Hydroxide) running on the host. Not used in normal operation — requires manual Hydroxide setup and is Linux/macOS only.

```bash
docker compose up -d tracker
# cron: docker compose run --rm agent
```

---

## ORP Export — formulaire suisse 716.007

Le script `export_orp.py` génère un CSV compatible avec le formulaire mensuel
de recherches d'emploi exigé par l'ORP (Office Régional de Placement).

```bash
python export_orp.py                          # mois courant, statut=applied
python export_orp.py --month 2026-05          # mois spécifique
python export_orp.py --from 2026-05-01 --to 2026-05-31
python export_orp.py --statuses applied rejected archived
```

**Output** : `data/orp_YYYY-MM.csv` encodé en `utf-8-sig` (BOM) — ouvrable
tel quel dans Excel et LibreOffice Calc.

**Colonnes** (10) : Jour | Mois | Entreprise / Adresse | Personne contactée /
Tél. | Description du poste | Assignation ORP | Activité | Résultat | Motif
si négatif | URL

Le script affiche un résumé par statut et un aperçu dans le terminal après
chaque export. Aucune dépendance hors stdlib — il s'exécute directement sur
le serveur Live (`ssh` → `cd /opt/job-agent && python export_orp.py`).

**Mapping statut → résultat** :

| Statut DB | Résultat ORP |
|-----------|-------------|
| `applied`, `queued`, `ready`, `saved`, `new` | en suspens |
| `rejected`, `archived`, `expired` | négatif |

---

## Development

- `specs/` — SpecKit feature specs (`spec.md` + `plan.md` + `tasks.md` per feature)
  - `001-monitored-companies/` — targeted company monitoring, ATS adapters,
    title gate, scoring signal, monitoring status UI (implemented)
  - `002-pipeline-monitoring-step/` — wires the monitored-company scrape into
    `main.py`'s full pipeline via a config setting + CLI flags (spec ready,
    not yet implemented)
  - `013-deepseek-only-backend/` — removes Groq and Gemini, simplifies scorer
    to DeepSeek only (implemented)
- `CLAUDE.md` — runtime instructions for Claude Code (never-modify file list,
  code style, active spec pointer)
- `tests/` — `pytest tests/` (in-memory SQLite, run before any commit
  touching `storage.py`)

**Code style**: surgical edits, no new dependencies without discussion,
preserve existing logic, prose-heavy specs before implementation.

---

## License

MIT
