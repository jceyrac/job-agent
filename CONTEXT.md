# job_agent — Context & Architecture

> Document de référence pour reprendre le projet en contexte. Mis à jour au 2026-05-27.

---

## 1. Historique du projet

### v1 — Initial commit (avril 2026)
Pipeline complet scraping → scoring → notification construit en une session :
- 12+ scrapers (LinkedIn, Indeed, Greenhouse, Web3, crypto job boards)
- Scoring Groq LLM (llama-3.3-70b-versatile) avec règles métier PM/Web3
- Notifier : email HTML dark mode + export Joplin Markdown
- Filtre en deux passes : pre-scoring (titre, work mode) + post-scoring (geo_zone réel)

### v1.1 — Storage layer + fix faux positifs
Deux problèmes identifiés et corrigés :

**Problème 1 — Rescoring systématique à chaque run**
Chaque run appelait Groq pour 100% des jobs, même ceux déjà vus → coût ~4 min/run.

**Fix** : `storage.py` — couche SQLite avec `split_new_cached()`. Les jobs déjà scorés sont récupérés du cache ; seuls les nouveaux jobs passent par Groq.

**Problème 2 — 13 faux positifs WeWorkRemotely dans le digest**
`score_job()` retournait un tuple fallback `(5, "Scoring indisponible", "unknown", ...)` sur rate limit Groq. Score 5 = seuil exact → job inclus dans le digest.

**Fix** : `score_job()` retourne `None` sur échec. `main.py` détecte `None` → `db.save_unscored(job)` + `continue`.

### v1.2 — Tracker Streamlit, email monitor, Docker (avril–mai 2026)

**Tracker Streamlit multi-page** : UI complète avec Dashboard (widgets, hot jobs, pipeline stats), Jobs (filtres, actions, job detail), Companies (CRUD, interactions), Contacts (relationship status dérivé), Settings. Remplace l'ancien tracker single-page (préservé comme `tracker_legacy.py`).

**Expired status** : Nouveau statut terminal `expired` distinct sémantiquement de `archived` (expiration = l'offre n'existe plus, pas une décision du candidat). Migration reclassant 38 jobs archivés avec notes indiquant une expiration.

**Email monitor** : `email_monitor.py` — daemon qui scrute la boîte Proton Mail via Hydroxide IMAP, classifie les emails de recruteurs avec Groq (`llama-3.1-8b-instant`), et met à jour automatiquement le statut des candidatures (rejected / interviewing / offer).

**Docker deployment** : Migration de l'exécution ad-hoc Mac vers Docker. Le Mac devient éditeur de code + git push. Un serveur Ubuntu fait tourner les services via `docker compose` :
- `tracker` — Streamlit toujours actif (port 8501)
- `agent` — pipeline scraping/scoring (profil `manual`, lancé par cron)
- `email-monitor` — surveillance IMAP (profil `manual`, lancé par cron ou daemon)

---

## 2. Stack technique

| Composant | Choix | Raison |
|-----------|-------|--------|
| **Python** | 3.11 | Type hints union (`X \| Y`), dataclasses natives |
| **LLM scorer** | Groq + DeepSeek fallback | Modèles gratuits, chaîne de fallback automatique |
| **Runner** | Docker (`python:3.11-slim`) | Isolation, reproductible Mac/Ubuntu, zéro install sur le serveur |
| **UI** | Streamlit multi-page | Rapide à construire, suffisant pour usage solo |
| **HTTP client** | `httpx` | Async-capable, meilleur que requests pour les scrapers |
| **HTML parsing** | `beautifulsoup4`, `lxml` | Scraping statique, lxml pour speed + python-jobspy |
| **LinkedIn / Indeed** | `python-jobspy` | Abstraction officieuse, évite le reverse-engineering |
| **RSS** | `feedparser` | WeWorkRemotely RSS |
| **Persistence** | SQLite via `storage.py` | Zéro infra, WAL mode pour accès concurrent Docker |
| **Notification** | SMTP Gmail + Joplin API | Email HTML dark mode + note Markdown locale |
| **IMAP** | `imaplib` stdlib → Hydroxide | Pont Proton Mail, pas de dépendance externe |
| **Config** | `python-dotenv` + `.env` | Credentials hors code |

### Dépendances principales
```
streamlit python-dotenv openai groq httpx requests beautifulsoup4 lxml feedparser python-jobspy
```

---

## 3. Architecture du pipeline

```
scrape.py → SQLite DB ─┬─ score.py --extract (field extraction, profile-independent)
                        ├─ score.py (per-profile Tier 0 + Tier 1 evaluation)
                        │      └→ email digest + Joplin note
                        ├─ prepare.py --ready (application packages: queued → ready)
                        └─ tracker.py (multi-page Streamlit UI + CRM)
```

### Architecture Docker (production)

```
┌─────────────────────────────────────────────┐
│  Ubuntu Server                               │
│                                              │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐ │
│  │ tracker   │  │  agent    │  │ email-     │ │
│  │ (always)  │  │ (cron)    │  │ monitor    │ │
│  │ :8501     │  │           │  │ (cron)     │ │
│  └─────┬─────┘  └─────┬─────┘  └─────┬──────┘ │
│        │              │              │         │
│        └──────────────┼──────────────┘         │
│                       │                        │
│               ┌───────┴───────┐                │
│               │  job_data      │                │
│               │  (named volume) │               │
│               └───────────────┘                │
│                                                │
│  Hydroxide (host) ←── email-monitor            │
└────────────────────────────────────────────────┘
```

### Modèles de données

**`JobPosting`** (models.py) — immutable après scraping sauf `summary`, `work_mode`, `company_size`, `contract_type`, `geo_zone` ajoutés par l'extraction.

**`JobFilter`** (models.py) — configuration du run : keywords, titles, exclude, remote_or_hybrid, allowed_geo_zones.

**`scored_jobs`** — liste de `dict` (issu de `job.to_json()` + résultat scorer). Structure stable consommée par notifier.py et sérialisée en JSON.

---

## 4. Scrapers

| Source | Méthode | Statut | Notes |
|--------|---------|--------|-------|
| **LinkedIn** | `python-jobspy` | ✅ Actif | 4 requêtes × 20 résultats, `hours_old=120` |
| **Indeed** | `python-jobspy` | ✅ Actif | 4 requêtes × 9 pays, fallback per-country si worldwide < 15 |
| **Greenhouse** | API publique | ✅ Actif | 30 boards crypto/Web3/fintech (coinbase, ripple, stripe…) |
| **WeWorkRemotely** | RSS | ✅ Actif | `feedparser`, location extraite du champ `region` |
| **Web3Career** | HTML scraping | ✅ Actif | Fetches individual job pages for descriptions |
| **RemoteOK** | JSON API | ✅ Actif | API publique non authentifiée |
| **CryptoJobsList** | `__NEXT_DATA__` + JSON-LD | ✅ Actif | Individual pages at `/jobs/<slug>` |
| **CryptoJobs.com** | HTML scraping | ✅ Actif | Fetches individual job pages for descriptions |
| **DeFi Jobs** | HTML scraping | ✅ Actif | Fallback sur `crypto.jobs` (defijobs.xyz inactif) |
| **TieTalent** | `__NEXT_DATA__` | ✅ Actif | Focalisé Suisse, majorité on-site |
| **Jobup.ch** | HTML scraping | ✅ Actif | Focalisé Suisse, descriptions indisponibles (JS) |
| **Wellfound** | RapidAPI | ⚠️ Limité | 10 appels/mois sur plan BASIC, reset le 1er du mois |
| **Xing** | HTML scraping | ❌ Désactivé | JS-rendu, pas de données statiques |
| **Malt** | — | ❌ Désactivé | SPA JS-rendu |
| **BeInCrypto Jobs** | — | ❌ Désactivé | JS-rendu |
| **Jobs.ch** | — | ❌ Désactivé | JS-rendu |

---

## 5. Scoring

### Extraction (profile-independent)
Modèle primaire : `llama-3.3-70b-versatile` (Groq), fallback automatique vers `llama-4-scout` (Groq) puis `deepseek-v4-pro` (DeepSeek). Remplit `company_country`, `industry_sector`, `language_required`, `work_mode`, `geo_zone`, `company_size`, `contract_type`, `summary`.

### Évaluation (per-profile)
Deux tiers :
- **Tier 0** — déterministe : rejette sur langue, secteur, pays, work mode mismatch (score 0, `scored_by = tier_0`)
- **Tier 1** — LLM via modèles légers : `llama-3.1-8b-instant` (Groq), fallback `llama-4-scout` (Groq)

**Grille de scores :**

| Score | Critère |
|-------|---------|
| 9–10 | Titre PM + Web3/DeFi/AI/crypto explicitement mentionné |
| 7–8 | Titre PM + contexte Web3/crypto dans la description |
| 5–6 | Titre PM, pas de contexte Web3/crypto |
| 3–4 | Pas un rôle PM (ingénieur, design, BD…) même avec Web3 |
| 1–2 | Pas PM, pas Web3 |

**Ajustements :** hybrid −1, on-site −2, us_only −3, apac/latam −2.

**Retry logic :** 5 tentatives exponentielles sur 429 (2s → 4s → 8s → 16s → 32s). Si toujours en échec, retourne `None` → job exclu du digest, sauvegardé sans score pour être retenté.

**Cache :** `storage.py` — un job déjà scoré pour un profil donné n'est jamais renvoyé au LLM.

---

## 6. Storage (`storage.py`)

SQLite à `data/jobs.db`, WAL mode (compatible accès concurrent depuis plusieurs conteneurs Docker).

**Méthodes clés :**
- `split_new_cached(jobs)` → `(new_jobs, cached_jobs_with_scores)`
- `save_scored(job, result_dict)` — upsert avec tous les champs scoring
- `save_unscored(job)` — trace le job sans score (retry au prochain run)
- `touch_many(ids)` — met à jour `last_seen` pour les jobs toujours actifs
- `get_stats()` → `{total, scored, hot, solid, by_status}`
- `get_digest(min_score, status)` — pour le rapport de préférences
- `set_status(job_id, status)` — workflow `new → queued → ready → applied → rejected / expired / archived`
- `find_jobs_by_company(company)` — recherche insensible à la casse pour l'email monitor

**Statuts valides :** `new`, `queued`, `ready`, `applied`, `rejected`, `archived`, `expired`

`expired` = l'offre n'existe plus (décision externe). `archived` = mis de côté par le candidat. `rejected` = refus explicite du recruteur. Ces trois statuts sont exclus du scoring.

---

## 7. Email monitor (`email_monitor.py`)

Daemon autonome qui connecte Hydroxide IMAP (pont Proton Mail), fetch les emails UNSEEN, les classifie via Groq, et met à jour les statuts dans la DB.

**Modes :**
- `--dry-run` : 4 emails de test, pas d'IMAP, pas d'écriture DB
- `--once` : un passage IMAP → exit (pour cron)
- `--interval N` : boucle continue (daemon)

**Classification :** modèle `llama-3.1-8b-instant` avec `response_format={"type": "json_object"}`. Extrait `{company, status, confidence, reason}`. Agit seulement si confidence = `"high"`, un nom de company est extrait, et exactement 1 job correspond dans la DB.

**Actions sur statut :** `rejected` → rejeté, `interview_scheduled` → interviewing, `offer` → offer, `follow_up` → log-only.

---

## 8. Décisions architecturales

### Docker multi-service, image unique
Un seul `Dockerfile` (`python:3.11-slim`) partagé par trois services. `docker-compose.yml` définit les services, `docker-compose.override.yml` (gitignoré) ajoute les overrides dev Mac (bind mount du code source, pas de restart auto).

### Volume nommé pour la DB
`job_data` volume Docker nommé — persiste la DB indépendamment des conteneurs. En dev Mac, un bind mount `./data` permet de voir la DB locale. `scripts/seed-db.sh` copie la DB locale dans le volume Docker.

### Email monitor indépendant
L'email monitor utilise `storage.JobStorage` directement (pas de sqlite3 brut) pour rester cohérent avec le reste du code. La classification est conservative : confidence haute + match unique obligatoire avant toute modification de statut.

### Scraping statique uniquement
JS-rendered = désactivé. Pas de Playwright/Selenium pour garder le projet léger. Les scrapers désactivés (Malt, Xing, BeInCrypto, Jobs.ch) attendent une solution Playwright future ou une API officielle.

### Deux passes de filtrage géo
1. **Pre-scoring** (`filters.py`) : filtre sur `location` brut si `job.geo_zone` est déjà renseigné par le scraper.
2. **Post-scoring** (`main.py`) : filtre sur `geo_zone` réel extrait par le LLM. C'est le filtre effectif.

### Rate limiting Groq
Délai de 4s entre chaque job. Après un 429, cooldown supplémentaire de 10s post-retry.

### Extraction profile-independent, scoring per-profile
Les champs structurés (country, secteur, langue, work mode…) sont extraits une fois et réutilisés par tous les profils. Le scoring Tier 0/Tier 1 est par profil. Le statut de candidature (job_tracking) est profile-independent.

---

## 9. Prochaines étapes

### Immédiat
- [x] **Tracker Streamlit** — implémenté (multi-page, CRM intégré)
- [x] **Cron automation** — Docker + cron serveur
- [x] **Email auto-status** — email_monitor.py avec Hydroxide IMAP
- [x] **Expired status** — implémenté + migration exécutée

### À moyen terme
- [ ] **Wellfound sans limite** : passer sur le plan payant RapidAPI ou trouver une alternative directe.
- [ ] **Scrapers JS** : Playwright pour Malt, BeInCrypto Jobs, Jobs.ch — si les sources manquent.
- [ ] **event_agent** : projet suivant dans `/Users/jeanclaudevd/AI-Suite/` — scope TBD.

---

## 10. Structure des fichiers

```
job_agent/
├── main.py                              # Orchestrateur scraping → scoring
├── scrape.py                            # Scrape all enabled sources → SQLite
├── score.py                             # Field extraction + per-profile evaluation
├── prepare.py                           # Application packages (queued → ready)
├── tracker.py                           # Multi-page Streamlit UI entry point
├── tracker_legacy.py                    # Original single-page tracker (preserved)
├── tracker_views/                       # Tracker page modules
│   ├── shared.py                        # Constants, cached loaders, badges, filters
│   ├── dashboard.py                     # Landing page: widgets + hot jobs feed
│   ├── jobs.py                          # Job list + detail view
│   ├── job_detail.py                    # Job detail page (status, actions, description)
│   ├── job_helpers.py                   # Action buttons + state machine
│   ├── companies.py                     # Company list + detail view
│   ├── contacts.py                      # Contact list + detail view
│   ├── settings.py                      # Settings + DB stats
│   ├── preferences.py                   # Preference report
│   └── forms.py                         # @st.dialog modals
├── models.py                            # JobPosting, JobFilter (dataclasses)
├── filters.py                           # JobFilterEngine — pre-scoring filter
├── scorer.py                            # LLM scoring (extraction + evaluation)
├── storage.py                           # JobStorage — SQLite persistence
├── profiles.py                          # Built-in profile definitions
├── notifier.py                          # Email HTML + Joplin Markdown export
├── email_monitor.py                     # Hydroxide IMAP → Groq classification → auto-status
├── create_profile.py                    # CLI: create / list / delete profiles
├── Dockerfile                           # python:3.11-slim, shared by all services
├── docker-compose.yml                   # tracker + agent + email-monitor services
├── docker-compose.override.yml          # Mac dev overrides (gitignored)
├── .dockerignore                        # Exclude venv, data, tests, etc.
├── scripts/
│   ├── deploy.sh                        # git pull + docker compose up -d tracker
│   └── seed-db.sh                       # Seed Docker volume from local jobs.db
├── migrate_expired_status.py            # One-shot: reclassify archived → expired
├── migrate_single_status.py             # Merge application_status → status
├── migrate_profile_independent_tracking.py  # Status/notes → job_tracking table
├── scrapers/
│   ├── base.py                          # BaseScraper (ABC)
│   ├── jobspy_scraper.py                # LinkedIn + Indeed
│   ├── greenhouse.py                    # 30 boards via public API
│   ├── weworkremotely.py                # RSS
│   ├── remoteok.py                      # JSON API
│   ├── cryptojobslist.py                # __NEXT_DATA__ + JSON-LD
│   ├── cryptojobs_com.py                # HTML scraping
│   ├── defi_jobs.py                     # HTML scraping (fallback crypto.jobs)
│   ├── tietalent.py                     # __NEXT_DATA__
│   ├── jobup.py                         # HTML scraping
│   ├── wellfound.py                     # RapidAPI (limité)
│   ├── web3career.py                    # HTML scraping
│   ├── xing.py                          # ❌ ENABLED=False
│   ├── malt.py                          # ❌ ENABLED=False
│   ├── beincrypto_jobs.py               # ❌ ENABLED=False
│   └── jobs_ch.py                       # ❌ ENABLED=False
├── tests/
│   ├── test_storage.py                  # 162 unit tests (in-memory DB)
│   └── run_all.py                       # Live scraper integration tests
├── data/jobs.db                         # SQLite database (gitignored, 162 MB)
├── outputs/                             # JSON + MD + email preview (gitignored)
├── .env                                 # Credentials (gitignored)
├── .env.example
├── requirements.txt
├── README.md
└── CONTEXT.md                           # Ce fichier
```
