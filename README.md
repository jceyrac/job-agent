# job_agent

Automated job search agent for **Senior Product Manager** roles in Web3, DeFi, AI, and crypto.

Scrapes 12+ job boards, scores each posting with a Groq LLM, filters by relevance, and delivers a daily digest via email and Joplin notes.

---

## How it works

```
Scrapers → Filter → Scorer (Groq LLM) → Email digest + Joplin note
```

1. **Scrapers** fetch raw job postings from multiple sources in parallel
2. **Filter** (`filters.py`) hard-gates on job title (must be a PM role) and work mode (remote or hybrid)
3. **Scorer** (`scorer.py`) calls `llama-3.1-8b-instant` via Groq to score each job 1–10 and extract metadata
4. **Notifier** (`notifier.py`) sends an HTML email digest and creates a Joplin note with the top results

### Scoring rules

| Score | Meaning |
|-------|---------|
| 9–10 | PM title + Web3/DeFi/AI/crypto explicitly mentioned |
| 7–8 | PM title + Web3/crypto in description or company context |
| 5–6 | PM title, no Web3/crypto context (generalist PM) |
| 3–4 | Not a PM role (engineer, designer, BD…) even with Web3 context |
| 1–2 | Not a PM role, no Web3/crypto context |

Work mode adjustment: hybrid −1, on-site −2.

---

## Scrapers

| Source | Method | Status | Notes |
|--------|--------|--------|-------|
| **LinkedIn** | `python-jobspy` | ✅ Active | 4 queries × 20 results |
| **Indeed** | `python-jobspy` | ✅ Active | 4 queries × 9 countries |
| **Greenhouse** | Public API | ✅ Active | 30 crypto/Web3/fintech boards |
| **WeWorkRemotely** | RSS | ✅ Active | |
| **Web3Career** | HTML scraping | ✅ Active | |
| **RemoteOK** | JSON API | ✅ Active | |
| **CryptoJobsList** | `__NEXT_DATA__` | ✅ Active | RSS feed is empty (paid plan) |
| **CryptoJobs.com** | HTML scraping | ✅ Active | |
| **DeFi Jobs** | HTML scraping | ✅ Active | Falls back to crypto.jobs (defijobs.xyz is down) |
| **TieTalent** | `__NEXT_DATA__` | ✅ Active | Swiss-focused, mostly on-site |
| **Jobup.ch** | HTML scraping | ✅ Active | Swiss-focused, mostly on-site |
| **Wellfound** | RapidAPI | ⚠️ Limited | 10 calls/month on BASIC plan (resets 1st of month) |
| **Xing** | HTML scraping | ❌ Disabled | JS-rendered, no static data |
| **Malt** | — | ❌ Disabled | JS-rendered SPA |
| **BeInCrypto Jobs** | — | ❌ Disabled | JS-rendered, no static data |
| **Jobs.ch** | — | ❌ Disabled | JS-rendered, no static data |

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/jceyrac/job-agent.git
cd job-agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install httpx feedparser beautifulsoup4 python-dotenv groq python-jobspy
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in your credentials:

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_APIKEY` | ✅ | Groq API key — free at [console.groq.com](https://console.groq.com) |
| `GMAIL_FROM` | ✅ | Gmail address used to send the digest |
| `GMAIL_APP_PASSWORD` | ✅ | Gmail App Password (not your regular password) — [generate here](https://myaccount.google.com/apppasswords) |
| `NOTIFY_TO` | ✅ | Destination email address |
| `JOPLIN_TOKEN` | Optional | Joplin Web Clipper token — enables automatic note creation |
| `X_RAPIDAPI_KEY` | Optional | RapidAPI key for Wellfound scraper (10 calls/month on free plan) |

### 3. Run

```bash
python main.py
```

The agent will:
- Fetch jobs from all enabled scrapers (~4 minutes, mostly LinkedIn/Indeed)
- Score each job with the Groq LLM
- Print a summary to stdout
- Save results to `outputs/jobs_YYYY-MM-DD.json` and `outputs/jobs_YYYY-MM-DD.md`
- Send an HTML email digest
- Push a note to Joplin (if `JOPLIN_TOKEN` is set)

### 4. Customize the search

Edit the `JobFilter` in `main.py` to adjust:

```python
job_filter = JobFilter(
    titles=["product manager", "head of product", "CPO", "VP product", "product owner"],
    exclude=["junior", "intern", "stage", "apprentice"],
    remote_or_hybrid=True,          # set to False to include on-site
    company_sizes=["startup", "scaleup"],   # empty = no filter
    contract_types=[],              # empty = no filter
)
```

To add a Greenhouse board, append its token to `CRYPTO_WEB3_BOARDS` in `scrapers/greenhouse.py`:

```python
CRYPTO_WEB3_BOARDS = [
    "coinbase",
    "your-company-token-here",
    ...
]
```

---

## Project structure

```
job_agent/
├── main.py              # Entry point — orchestrates scraping, scoring, notifying
├── models.py            # JobPosting and JobFilter dataclasses
├── filters.py           # Filter engine (title, location, work mode, company size…)
├── scorer.py            # Groq LLM scoring
├── notifier.py          # Email digest + Joplin export
├── scrapers/
│   ├── base.py          # Abstract BaseScraper
│   ├── jobspy_scraper.py
│   ├── greenhouse.py
│   ├── web3career.py
│   ├── weworkremotely.py
│   ├── remoteok.py
│   ├── cryptojobslist.py
│   ├── cryptojobs_com.py
│   ├── defi_jobs.py
│   ├── tietalent.py
│   ├── jobup.py
│   ├── wellfound.py
│   └── ...
├── .env.example         # Environment variable template
└── outputs/             # Generated files (gitignored)
```

---

## Output example

```
Scrapers found: ['CryptoJobsList', 'DeFi Jobs', 'Greenhouse', 'JobSpy', ...]
[Greenhouse] coinbase: 13 PM jobs | ripple: 4 PM jobs | stripe: 29 PM jobs | ...
[JobSpy] LinkedIn: 54 | Indeed: 42 | Total unique: 96 | Time: 200s

Total unique jobs after dedup: 83
Jobs with score >= 5: 60  (🔥 31 hot  ⭐ 29 solid)

🔥 #1 [10/10] Crypto Product Manager I - Consumer @ Coinbase
🔥 #2 [9/10]  Base Senior Product Manager, Privacy @ Coinbase
🔥 #3 [9/10]  Lead Product Manager @ Gemini
```

---

## License

MIT
