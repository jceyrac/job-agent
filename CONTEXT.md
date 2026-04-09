# job_agent — Context & Architecture

> Document de référence pour reprendre le projet en contexte. Mis à jour au 2026-04-09.

---

## 1. Historique du projet

### v1 — Initial commit (avril 2026)
Pipeline complet scraping → scoring → notification construit en une session :
- 12+ scrapers (LinkedIn, Indeed, Greenhouse, Web3, crypto job boards)
- Scoring Groq LLM (llama-3.3-70b-versatile) avec règles métier PM/Web3
- Notifier : email HTML dark mode + export Joplin Markdown
- Filtre en deux passes : pre-scoring (titre, work mode) + post-scoring (geo_zone réel)

### v1.1 — Storage layer + fix faux positifs (session courante)
Deux problèmes identifiés et corrigés :

**Problème 1 — Rescoring systématique à chaque run**
Chaque run appelait Groq pour 100% des jobs, même ceux déjà vus → coût ~4 min/run.

**Fix** : `storage.py` — couche SQLite avec `split_new_cached()`. Les jobs déjà scorés sont récupérés du cache ; seuls les nouveaux jobs passent par Groq. Économie typique : 0 appels Groq dès le 2e run sur les mêmes offres.

**Problème 2 — 13 faux positifs WeWorkRemotely dans le digest**
`score_job()` retournait un tuple fallback `(5, "Scoring indisponible", "unknown", ...)` sur rate limit Groq. Score 5 = seuil exact → job inclus dans le digest. `geo_zone="unknown"` → passe le filtre géo. Double faux positif.

**Fix** : `score_job()` retourne `None` sur échec. `main.py` détecte `None` → `db.save_unscored(job)` + `continue`. Job tracé en base, retenté au run suivant, jamais dans le digest.

---

## 2. Stack technique

| Composant | Choix | Raison |
|-----------|-------|--------|
| **Python** | 3.11 | Type hints union (`X \| Y`), dataclasses natives |
| **LLM scorer** | Groq — `llama-3.3-70b-versatile` | Gratuit, ~2.5s/job, JSON mode fiable |
| **HTTP client** | `httpx` | Async-capable, meilleur que requests pour les scrapers |
| **HTML parsing** | `beautifulsoup4` | Scraping statique suffisant pour la plupart des boards |
| **LinkedIn / Indeed** | `python-jobspy` | Abstraction officieuse, évite le reverse-engineering |
| **RSS** | `feedparser` | WeWorkRemotely RSS |
| **Persistence** | SQLite via `storage.py` | Zéro infra, suffisant pour usage solo, WAL mode |
| **Notification** | SMTP Gmail + Joplin API | Email HTML dark mode + note Markdown locale |
| **Config** | `python-dotenv` + `.env` | Credentials hors code |

### Dépendances principales
```
httpx feedparser beautifulsoup4 python-dotenv groq python-jobspy requests
```

---

## 3. Architecture du pipeline

```
main.py
  │
  ├─ discover_scrapers()          # auto-découverte par module
  │
  ├─ scraper.fetch()              # × N scrapers en séquence
  │     └─ JobFilterEngine.apply()   # pre-scoring filter
  │           ├─ date > 30j         → exclu
  │           ├─ titre PM requis    → filtre dur
  │           ├─ exclude terms      → filtre dur
  │           └─ remote_or_hybrid   → filtre dur (location brut)
  │
  ├─ deduplicate()                # dédup par URL
  │
  ├─ JobStorage.split_new_cached()
  │     ├─ cached_jobs → filtre geo + threshold → scored_jobs
  │     └─ new_jobs → scorer → filtre geo + threshold → scored_jobs
  │           └─ score_job() → None si échec → save_unscored
  │
  └─ scored_jobs
        ├─ notifier.send_email_digest()
        ├─ notifier.export_joplin()
        └─ outputs/jobs_YYYY-MM-DD.{json,md}
```

### Modèles de données

**`JobPosting`** (models.py) — immutable après scraping sauf `summary`, `work_mode`, `company_size`, `contract_type`, `geo_zone` ajoutés par le scorer.

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
| **Web3Career** | HTML scraping | ✅ Actif | BeautifulSoup, `web3.career/product-manager-jobs` |
| **RemoteOK** | JSON API | ✅ Actif | API publique non authentifiée |
| **CryptoJobsList** | `__NEXT_DATA__` | ✅ Actif | RSS vide (payant) → parse hydration JSON Next.js |
| **CryptoJobs.com** | HTML scraping | ✅ Actif | Parse `article[aside]`, extraction work_mode via icônes |
| **DeFi Jobs** | HTML scraping | ✅ Actif | Fallback sur `crypto.jobs` (defijobs.xyz inactif) |
| **TieTalent** | `__NEXT_DATA__` | ✅ Actif | Focalisé Suisse, majorité on-site |
| **Jobup.ch** | HTML scraping | ✅ Actif | Focalisé Suisse, majorité on-site |
| **Wellfound** | RapidAPI | ⚠️ Limité | 10 appels/mois sur plan BASIC, reset le 1er du mois |
| **Xing** | HTML scraping | ❌ Désactivé | JS-rendu, pas de données statiques |
| **Malt** | — | ❌ Désactivé | SPA JS-rendu |
| **BeInCrypto Jobs** | — | ❌ Désactivé | JS-rendu |
| **Jobs.ch** | — | ❌ Désactivé | JS-rendu |

---

## 5. Scoring

Modèle : `llama-3.3-70b-versatile` via Groq (JSON mode, temperature 0.2, max_tokens 300).

**Grille de scores :**

| Score | Critère |
|-------|---------|
| 9–10 | Titre PM + Web3/DeFi/AI/crypto explicitement mentionné |
| 7–8 | Titre PM + contexte Web3/crypto dans la description |
| 5–6 | Titre PM, pas de contexte Web3/crypto |
| 3–4 | Pas un rôle PM (ingénieur, design, BD…) même avec Web3 |
| 1–2 | Pas PM, pas Web3 |

**Ajustements :** hybrid −1, on-site −2, us_only −3, apac/latam −2.

**Métadonnées extraites :** `work_mode`, `company_size`, `contract_type`, `geo_zone`, `summary` (2-3 phrases).

**Retry logic :** 5 tentatives exponentielles sur 429 (2s → 4s → 8s → 16s → 32s). Si toujours en échec, retourne `None` → job exclu du digest, sauvegardé sans score pour être retenté.

**Cache :** `storage.py` — un job déjà scoré n'est jamais renvoyé à Groq.

---

## 6. Storage (`storage.py`)

SQLite à `data/jobs.db`, WAL mode.

**Méthodes clés :**
- `split_new_cached(jobs)` → `(new_jobs, cached_jobs_with_scores)`
- `save_scored(job, result_dict)` — upsert avec tous les champs scoring
- `save_unscored(job)` — trace le job sans score (retry au prochain run)
- `touch_many(ids)` — met à jour `last_seen` pour les jobs toujours actifs
- `get_stats()` → `{total, scored, hot, solid, by_status}`
- `get_digest(min_score, status)` — pour futur tracker Streamlit
- `set_status(job_id, status)` — workflow `new → saved → applied → rejected → archived`

---

## 7. Décisions architecturales

### Scraping statique uniquement
JS-rendered = désactivé. Pas de Playwright/Selenium pour garder le projet zéro-infra et rapide à démarrer. Les scrapers désactivés (Malt, Xing, BeInCrypto, Jobs.ch) attendent une solution Playwright future ou une API officielle.

### Deux passes de filtrage géo
1. **Pre-scoring** (`filters.py`) : filtre sur `location` brut si `job.geo_zone` est déjà renseigné par le scraper. Limité — la plupart des scrapers ne renseignent pas `geo_zone`.
2. **Post-scoring** (`main.py`) : filtre sur `geo_zone` réel extrait par le LLM. C'est le filtre effectif. Les jobs non scorés (`score_job` → `None`) n'atteignent jamais ce filtre → ils sont systématiquement exclus.

### `scored_jobs` comme interface stable
`notifier.py`, la sérialisation JSON et le futur tracker reçoivent tous la même liste de dicts. L'interface ne dépend pas de la structure interne `JobPosting`.

### `JobFilter` déclarative dans `main.py`
La configuration du run (keywords, exclusions, zones géo, work modes) est centralisée dans `main()`. Pas de fichier de config externe — suffit pour usage solo.

### Rate limiting Groq
Délai de 4s entre chaque job (config actuelle dans `main.py`, ex-2.5s dans le commentaire du patch). Après un 429, cooldown supplémentaire de 10s post-retry pour protéger le job suivant.

---

## 8. Prochaines étapes

### Immédiat
- [ ] **Tracker Streamlit** : UI pour visualiser `data/jobs.db`, changer les statuts (`new → saved → applied`), filtrer par score/source/geo. `storage.get_all_for_tracker()` et `storage.set_status()` sont prêts.
- [ ] **Cron automation** : `crontab` ou launchd pour run quotidien automatique. Le cache storage garantit 0 re-scoring.

### À moyen terme
- [ ] **Wellfound sans limite** : passer sur le plan payant RapidAPI ou trouver une alternative directe.
- [ ] **Scrapers JS** : Playwright pour Malt (freelance), BeInCrypto Jobs, Jobs.ch — si les sources manquent.
- [ ] **`job.id` déterministe** : vérifier que l'id utilisé par `storage.py` est bien stable (basé sur URL ou hash titre+company) pour éviter les doublons cross-runs.

### Futur
- [ ] **event_agent** : projet suivant dans `/Users/jeanclaudevd/AI-Suite/` — scope TBD.

---

## 9. Structure des fichiers

```
job_agent/
├── main.py              # Orchestrateur principal
├── models.py            # JobPosting, JobFilter (dataclasses)
├── filters.py           # JobFilterEngine — pre-scoring filter
├── scorer.py            # score_job() — Groq LLM, retourne None sur échec
├── storage.py           # JobStorage — SQLite cache + tracker
├── notifier.py          # Email HTML + export Joplin Markdown
├── scrapers/
│   ├── base.py          # BaseScraper (ABC)
│   ├── jobspy_scraper.py    # LinkedIn + Indeed via python-jobspy
│   ├── greenhouse.py        # 30 boards crypto/Web3 via API publique
│   ├── weworkremotely.py    # RSS
│   ├── remoteok.py          # JSON API
│   ├── cryptojobslist.py    # __NEXT_DATA__
│   ├── cryptojobs_com.py    # HTML scraping
│   ├── defi_jobs.py         # HTML scraping (fallback crypto.jobs)
│   ├── tietalent.py         # __NEXT_DATA__
│   ├── jobup.py             # HTML scraping
│   ├── wellfound.py         # RapidAPI (limité)
│   ├── web3career.py        # HTML scraping
│   ├── xing.py              # ❌ ENABLED=False
│   ├── malt.py              # ❌ ENABLED=False
│   ├── beincrypto_jobs.py   # ❌ ENABLED=False
│   └── jobs_ch.py           # ❌ ENABLED=False
├── data/
│   └── jobs.db          # SQLite (gitignored)
├── outputs/             # JSON + MD + email preview (gitignored)
├── .env                 # Credentials (gitignored)
├── .env.example
├── README.md
└── CONTEXT.md           # Ce fichier
```
