# SPEC — job-agent Deployment: Mac (dev) → Ubuntu Server (prod)

> Spec for Claude Code. Read existing files before starting any step.  
> Mac = development + local Docker testing. Ubuntu server = production (24/7 execution).  
> Implementation order: follow phases strictly, test each before proceeding.

---

## Overview

Migrate `job-agent` from running ad-hoc on Mac to running autonomously on the
HPE ProLiant MicroServer Gen10 Plus (Ubuntu), accessible via Tailscale.

**End state:**
- Git repo is source of truth (GitHub: `https://github.com/jceyrac/job-agent`)
- Mac: code editing, local Docker testing, git push
- Ubuntu server: production execution, persistent DB, 24/7 tracker UI, cron
- SQLite DB lives in a persistent Docker volume (on each machine independently)
- Streamlit tracker accessible locally at `http://localhost:8501` (Mac dev)
- Streamlit tracker accessible at `http://<tailscale-ip>:8501` (prod, from Mac)
- Hydroxide runs as a systemd service on Ubuntu host for Proton Mail IMAP access

---

## Environments

| | Mac (dev) | Ubuntu server (prod) |
|---|---|---|
| Docker | Docker Desktop | Docker Engine (headless) |
| DB | Local volume, seeded from `data/jobs.db` | Persistent volume, migrated from Mac |
| Hydroxide | Not needed (skip email-monitor locally) | systemd service on host |
| Cron | Not used | Host cron triggers agent + email-monitor |
| Tracker URL | `http://localhost:8501` | `http://<tailscale-ip>:8501` |
| `.env` | `~/AI-Suite/job_agent/.env` | `/opt/job-agent/.env` |

---

## Repository structure additions

New files to create (do not modify existing files unless specified):

```
job_agent/
├── Dockerfile                  ← NEW: single image for all Python services
├── docker-compose.yml          ← NEW: all services (tracker, agent, email-monitor)
├── docker-compose.override.yml ← NEW: Mac-only dev overrides (bind mount, no restart)
├── .env.example                ← UPDATE: add HYDROXIDE_* vars
├── .dockerignore               ← NEW
├── email_monitor.py            ← NEW: reads IMAP, updates job statuses
└── scripts/
    ├── deploy.sh               ← NEW: git pull + rebuild + restart on server
    └── seed-db.sh              ← NEW: copy local jobs.db into Docker volume
```

---

## Phase 0 — Prerequisites

### Mac: install Docker Desktop

Download and install Docker Desktop for Mac (Apple Silicon):
https://www.docker.com/products/docker-desktop/

Verify:
```bash
docker --version        # Docker 24+
docker compose version  # Compose v2+
```

No further configuration needed. `host.docker.internal` resolves to the Mac
host automatically on Docker Desktop — same behaviour as on Linux with
`extra_hosts: host-gateway`.

### Ubuntu server: install Docker Engine

```bash
# Install Docker Engine (not Docker Desktop)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker --version
docker compose version
```

---

## Phase 1 — Dockerfile + docker-compose.yml

### `Dockerfile`

Single image used by all services (tracker, agent, email-monitor).

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# System deps for lxml, JobSpy build tools
RUN apt-get update && apt-get install -y \
    gcc \
    libxml2-dev \
    libxslt-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data
```

### `requirements.txt`

Claude Code: audit ALL imports across `main.py`, `scrape.py`, `score.py`,
`scorer.py`, `tracker.py`, `tracker_legacy.py`, `tracker_views/`,
`email_monitor.py`, `notifier.py`, `filters.py`, `storage.py`, `models.py`,
`profiles.py`, `create_profile.py`, `job_actions.py`.

Produce a complete `requirements.txt`. Key packages that must be present:

```
streamlit
python-dotenv
groq
requests
beautifulsoup4
lxml
feedparser
jobspy
httpx
aiohttp
# add all others discovered from imports
```

Do NOT pin to `==` for now — use `>=` only where a minimum version is known.
Goal: working install, not a lockfile.

### `docker-compose.yml` (base — works on both Mac and server)

```yaml
version: "3.9"

services:

  tracker:
    build: .
    container_name: job-tracker
    command: streamlit run tracker.py --server.port 8501 --server.address 0.0.0.0
    ports:
      - "8501:8501"
    volumes:
      - job_data:/app/data
    env_file:
      - .env
    restart: unless-stopped

  agent:
    build: .
    container_name: job-agent
    command: python main.py
    volumes:
      - job_data:/app/data
    env_file:
      - .env
    profiles:
      - manual   # triggered by cron, not kept alive

  email-monitor:
    build: .
    container_name: job-email-monitor
    command: python email_monitor.py
    volumes:
      - job_data:/app/data
    env_file:
      - .env
    extra_hosts:
      - "host.docker.internal:host-gateway"   # reach Hydroxide on Ubuntu host
    profiles:
      - manual

volumes:
  job_data:
    driver: local
```

**Key decisions:**
- `tracker` runs permanently (`restart: unless-stopped`)
- `agent` and `email-monitor` use `profiles: [manual]` — cron-triggered only
- Single `job_data` volume shared across all services → one `data/jobs.db`
- `extra_hosts: host-gateway` lets containers reach `host.docker.internal`
  (works natively on Docker Desktop for Mac; explicit on Linux)

### `docker-compose.override.yml` (Mac dev only — gitignored)

This file is automatically merged by `docker compose` on the Mac.
It overrides two things for local dev:
1. Bind-mounts source code so edits reflect without rebuilding
2. Disables `restart: unless-stopped` to avoid background noise

```yaml
# docker-compose.override.yml
# Mac dev only — NOT committed to git (add to .gitignore)
version: "3.9"

services:
  tracker:
    volumes:
      - .:/app          # live code reload — edits visible instantly
      - job_data:/app/data
    restart: "no"

  agent:
    volumes:
      - .:/app
      - job_data:/app/data

  email-monitor:
    volumes:
      - .:/app
      - job_data:/app/data
```

Add to `.gitignore`:
```
docker-compose.override.yml
```

> On the Ubuntu server, no override file exists — base `docker-compose.yml`
> runs as-is, with the image baked at build time (no bind mount).

### `.dockerignore`

```
.env
.venv/
__pycache__/
*.pyc
*.pyo
.git/
.DS_Store
data/
outputs/
.claude/
.pytest_cache/
tests/
docker-compose.override.yml
```

---

## Phase 2 — Local dev workflow on Mac

### First-time setup

```bash
cd ~/AI-Suite/job_agent

# Build image
docker compose build

# Seed the Docker volume from existing local DB
bash scripts/seed-db.sh

# Start tracker
docker compose up tracker
# → open http://localhost:8501
```

### `scripts/seed-db.sh`

Seeds the local Docker volume from the existing `data/jobs.db` on disk.
Safe to re-run — overwrites the volume copy with the local file.

```bash
#!/bin/bash
# Copy local data/jobs.db into the Docker volume (Mac dev only)
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ ! -f "$PROJECT_DIR/data/jobs.db" ]; then
  echo "No data/jobs.db found — starting with empty DB."
  exit 0
fi

echo "Seeding Docker volume from data/jobs.db..."
docker run --rm \
  -v "${PWD}/data:/src" \
  -v "job_agent_job_data:/data" \
  alpine cp /src/jobs.db /data/jobs.db

echo "Done. Volume now contains:"
docker run --rm -v job_agent_job_data:/data alpine ls -lh /data/
```

### Run the agent pipeline locally

```bash
# Full pipeline (scrape → extract → score)
docker compose run --rm agent

# Or individual steps
docker compose run --rm agent python scrape.py
docker compose run --rm agent python score.py --profile web3_remote
```

The agent writes to the shared `job_data` volume. The running tracker picks
up changes on next page load.

### Iterating on code

With `docker-compose.override.yml` in place, source code is bind-mounted.
For `tracker.py` changes: Streamlit hot-reloads automatically.
For `scrape.py` / `score.py` changes: just re-run `docker compose run --rm agent`.
For `Dockerfile` / `requirements.txt` changes: rebuild with `docker compose build`.

### Skipping email-monitor locally

`email_monitor.py` requires Hydroxide IMAP (Ubuntu server only). On Mac:
- Do not run the `email-monitor` service locally
- Test the classification logic in isolation using `--dry-run` flag (see Phase 3)

---

## Phase 3 — `email_monitor.py`

New file at project root. Implement after Phase 1 is validated on Mac.

### Responsibilities

1. Connect to Hydroxide IMAP at `$HYDROXIDE_IMAP_HOST:$HYDROXIDE_IMAP_PORT`
2. Fetch UNSEEN emails from INBOX
3. For each email: extract sender, subject, body (first 1000 chars)
4. Call Groq to classify: company name + application status
5. Query DB for matching applied job by company name
6. If high-confidence single match: call `db.set_status()`
7. Mark email as SEEN
8. Log all actions to stdout (visible via `docker compose logs email-monitor`)

### `--dry-run` flag (required for local testing)

```bash
python email_monitor.py --dry-run
```

In dry-run mode:
- Skip IMAP connection entirely
- Process a hardcoded list of fake test emails (defined in the script)
- Print classification results and matched jobs without writing to DB

This allows testing the Groq classification logic and DB matching on Mac
without Hydroxide running.

### Classification prompt

```python
CLASSIFY_PROMPT = """
Analyze this email about a job application and return ONLY valid JSON.
No explanation, no markdown, just JSON.

{
  "company": "<company name extracted from email, or null>",
  "status": "rejected" | "interview_scheduled" | "offer" | "follow_up" | "unknown",
  "confidence": "high" | "low",
  "reason": "<one sentence>"
}

Rules:
- "rejected": clear rejection language ("unfortunately", "not moving forward", etc.)
- "interview_scheduled": invitation to interview, call, or assessment
- "offer": job offer, contract sent
- "follow_up": automated acknowledgement, "we received your application"
- "unknown": anything else

Email sender: {sender}
Email subject: {subject}
Email body (first 1000 chars): {body}
"""
```

### Matching logic

Only update status if:
- Groq confidence == "high", AND
- Exactly 1 job in DB matches the company name with status `applied`

If 0 or 2+ matches → log as "unmatched" and skip. Never guess.

### New method to add to `storage.py`

```python
def find_jobs_by_company(self, company: str) -> list[dict]:
    """Find applied jobs matching company name (case-insensitive, partial match)."""
    with self._conn() as conn:
        rows = conn.execute(
            """
            SELECT j.id, j.title, j.company, js.status, js.profile_id
            FROM jobs j
            JOIN job_scores js ON j.id = js.job_id
            WHERE lower(j.company) LIKE lower(?)
              AND js.status = 'applied'
            """,
            (f"%{company}%",)
        ).fetchall()
        return [dict(r) for r in rows]
```

---

## Phase 4 — Hydroxide on Ubuntu server (systemd)

> Runs on the Ubuntu HOST, not inside Docker.
> Survives Docker restarts. Credentials persisted via `pass`.

### Installation

```bash
# Install Go
sudo apt-get install -y golang-go

# Install hydroxide
go install github.com/emersion/hydroxide/cmd/hydroxide@latest

# Add to PATH permanently
echo 'export PATH=$PATH:$(go env GOPATH)/bin' >> ~/.bashrc
source ~/.bashrc
```

### First-time login (interactive — once only)

```bash
hydroxide auth jceyrac@pm.me
# Enter Proton Mail password + 2FA when prompted
# A bridge password is printed — copy it immediately into .env
```

### systemd service

Create `/etc/systemd/system/hydroxide.service`:

```ini
[Unit]
Description=Hydroxide ProtonMail IMAP Bridge
After=network.target

[Service]
Type=simple
User=<your-ubuntu-username>
ExecStart=/home/<your-ubuntu-username>/go/bin/hydroxide imap
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable hydroxide
sudo systemctl start hydroxide
sudo systemctl status hydroxide
```

Hydroxide listens on `localhost:1143`. Docker containers reach it via
`host.docker.internal:1143` (enabled by `extra_hosts: host-gateway` in compose).

### `.env` additions

```env
# Hydroxide IMAP (Ubuntu server only — leave blank or omit on Mac)
HYDROXIDE_IMAP_HOST=host.docker.internal
HYDROXIDE_IMAP_PORT=1143
HYDROXIDE_PASSWORD=<bridge-password-from-auth-step>
HYDROXIDE_EMAIL=jceyrac@pm.me
```

---

## Phase 5 — Server deployment

### Deploy location

```
/opt/job-agent/   ← production clone of GitHub repo
```

### Initial server setup (one-time)

```bash
# Clone repo
sudo mkdir -p /opt/job-agent
sudo chown $USER:$USER /opt/job-agent
git clone https://github.com/jceyrac/job-agent /opt/job-agent
cd /opt/job-agent

# Copy .env from Mac
# (run from Mac): scp ~/AI-Suite/job_agent/.env youruser@server:/opt/job-agent/.env

# Build and start tracker
docker compose build
docker compose up -d tracker
```

No `docker-compose.override.yml` on the server → base compose runs with
baked image, `restart: unless-stopped`, no bind mounts.

### `scripts/deploy.sh`

```bash
#!/bin/bash
# Run on Ubuntu server: pulls latest code and restarts tracker
set -e

DEPLOY_DIR="/opt/job-agent"
cd "$DEPLOY_DIR"

echo "=== Pulling latest code ==="
git pull origin main

echo "=== Rebuilding image ==="
docker compose build

echo "=== Restarting tracker ==="
docker compose up -d tracker

echo "=== Deploy complete ==="
docker compose ps
```

```bash
chmod +x scripts/deploy.sh
```

### Day-to-day workflow

```bash
# Mac: edit → commit → push
git add . && git commit -m "feat: ..." && git push origin main

# Deploy to server via SSH
ssh youruser@server "bash /opt/job-agent/scripts/deploy.sh"
```

---

## Phase 6 — Cron on Ubuntu server

All cron jobs run on the Ubuntu HOST.

```cron
# Full pipeline — twice daily (08:00 and 18:00 server time)
0 8,18 * * * cd /opt/job-agent && docker compose run --rm agent >> /var/log/job-agent/agent.log 2>&1

# Email monitor — every 30 minutes
*/30 * * * * cd /opt/job-agent && docker compose run --rm email-monitor >> /var/log/job-agent/email-monitor.log 2>&1
```

```bash
# Create log directory
sudo mkdir -p /var/log/job-agent
sudo chown $USER:$USER /var/log/job-agent

# Edit crontab
crontab -e
```

---

## Phase 7 — Data migration (Mac → Server)

```bash
# From Mac: copy DB to server
scp ~/AI-Suite/job_agent/data/jobs.db youruser@server:/tmp/jobs.db

# On server: inject into Docker volume
docker run --rm \
  -v /tmp:/src \
  -v job_agent_job_data:/data \
  alpine cp /src/jobs.db /data/jobs.db

# Verify
docker run --rm -v job_agent_job_data:/data alpine ls -lh /data/
```

---

## Tailscale access

```bash
# On server: get Tailscale IP
tailscale ip -4

# From Mac browser
http://<tailscale-ip>:8501
```

Port 8501 is bound to all interfaces in Docker but only reachable via Tailscale
— not exposed to the public internet.

---

## Acceptance criteria

### Mac (local Docker)
- [ ] `docker compose build` completes without error
- [ ] `docker compose up tracker` → tracker loads at `http://localhost:8501`
- [ ] `docker compose run --rm agent` → pipeline runs, DB updated in volume
- [ ] Tracker reflects agent output after page reload
- [ ] `python email_monitor.py --dry-run` → classifies test emails, prints matches
- [ ] Code edits to `tracker.py` hot-reload without rebuilding

### Ubuntu server (prod)
- [ ] `docker compose up -d tracker` → tracker running
- [ ] Tracker accessible at `http://<tailscale-ip>:8501` from Mac
- [ ] `docker compose run --rm agent` → full pipeline writes to prod volume
- [ ] `hydroxide.service` active and survives server reboot
- [ ] `email_monitor.py` connects to Hydroxide, classifies real emails, updates DB
- [ ] Cron jobs execute on schedule, logs written to `/var/log/job-agent/`
- [ ] `scripts/deploy.sh` completes cleanly after a `git push` from Mac
- [ ] Migrated `jobs.db` from Mac is present and queryable on server

---

## What does NOT change

- `storage.py`, `profiles.py`, `models.py`, `filters.py` — no modifications
- `scrape.py`, `score.py`, `scorer.py`, `notifier.py` — no modifications
- `tracker.py` and `tracker_views/` — no modifications
- `.env` structure — only additive (Hydroxide vars)
- GitHub repo URL and branch (`main`)

---

## Implementation order for Claude Code

**Step 1 — Mac Claude Code (Docker setup):**
1. Audit all imports → produce complete `requirements.txt`
2. Create `Dockerfile`, `.dockerignore`
3. Create `docker-compose.yml` (base)
4. Create `docker-compose.override.yml` (Mac dev) + add to `.gitignore`
5. Create `scripts/seed-db.sh` and `scripts/deploy.sh`
6. Update `.env.example` with Hydroxide vars
7. `docker compose build` → fix any build errors
8. `bash scripts/seed-db.sh` → seed volume
9. `docker compose up tracker` → verify at `http://localhost:8501`
10. `docker compose run --rm agent` → verify pipeline runs
11. Commit and push to GitHub

**Step 2 — Mac Claude Code (`email_monitor.py`):**
1. Implement `email_monitor.py` with `--dry-run` flag
2. Add `find_jobs_by_company()` to `storage.py`
3. Test with `python email_monitor.py --dry-run` (outside Docker, uses .venv)
4. Commit and push

**Step 3 — Server Claude Code (production):**
1. Install Docker Engine on server
2. `git clone` repo to `/opt/job-agent`
3. Copy `.env` from Mac via scp
4. Install Hydroxide → interactive auth → save bridge password to `.env`
5. Create and enable `hydroxide.service`
6. `docker compose build && docker compose up -d tracker`
7. Migrate DB from Mac (Phase 7)
8. Set up cron jobs + log directory
9. Run `docker compose run --rm agent` once manually to verify
10. Verify all prod acceptance criteria

**Step 4 — Mac Claude Code (cleanup):**
1. Remove any ad-hoc local cron or launch agent scripts from Mac
2. Update `CONTEXT.md` / `README.md` to reflect new architecture
