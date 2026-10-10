# Roadmap — Migration vers une architecture API

> Statut : validée en principe le 2026-10-03 (Claude.ai). Document de cadrage, pas une spec.
> Chaque étape fera l'objet d'une spec SpecKit (`specs/03X-*`). La constitution prévaut.

## Objectif

Découpler le backend du tracker Streamlit derrière une API, pour :
- permettre plusieurs clients (nouveau front `web/`, agents, futur MCP) ;
- distinguer le **catalogue partagé** (ingestion, jobs, extraction, faits entreprises — sans utilisateur)
  de l'**espace utilisateur** (profil, scoring, suivi, candidatures, CRM) ;
- préparer le multi-utilisateur (couture `user_id`) sans le construire maintenant ;
- ouvrir la voie au chat avec le CV agent (LangGraph, interrupts, checkpointer existants).

## Décisions d'architecture

- **Monorepo, pas de nouveau repo.** `core/` (domaine existant déplacé, non réécrit), `api/` (FastAPI),
  `web/` (nouveau front). Une seule source de vérité pour `storage`.
- **Remplacement complet de Streamlit à terme** (décision 2026-10-09). `web/` reprend toutes les pages,
  une par une ; Streamlit reste la référence tant qu'une page n'a pas atteint la parité, puis est retiré
  à l'étape 10. Conséquence : `tracker.py` et `tracker_views/` **restent à la racine** jusqu'à leur
  suppression — pas de déplacement vers `tracker/` (effort perdu sur un code destiné à disparaître).
- **`core` est un paquet installable** (`pyproject.toml`) : imports identiques partout, aucun bricolage
  de `sys.path`, scripts lancés en `python -m`, aucune donnée localisée à partir de `__file__`
  ailleurs que dans `core/paths.py`, configuration de prod explicite par l'environnement. Pas de shims.
- **Monolithe modulaire** : un service API, un router par domaine, SQLite, l'API devient seul écrivain à terme.
- **Le scoring est une vue de l'utilisateur sur le catalogue**, porté par le user, pas par le job.
  `job_scores` reste clé `(job_id, profile_id)` avec `search_profiles.user_id`.
- **Ingestion pilotée par un périmètre plateforme**, jamais dérivé d'un profil. Le gate de titre par
  profil passe en Tier 0 déterministe du scoring.
- **Données utilisateur clés par `user_id`** : `job_tracking`, `status_history`, `job_applications`,
  `interactions`, `contacts`, et nouvelle table `user_companies` (monitored, statut relationnel, notes).
- **Contacts privés par utilisateur** (décision 2026-10-03). Index unique → `(user_id, linkedin_url)`.
- **Scrapers indépendants par contrat** (`POST /ingest/batch`), pas par déploiement : un seul worker d'ingestion.
- **Tâches longues** : table `tasks` + worker, progression en SSE. Aucun appel LLM dans une requête HTTP.
- **Sessions conversationnelles** (chat CV) : ressource `cv-sessions` pilotant le graphe LangGraph existant.
  Cible d'édition déterministe (choisie dans l'UI), jamais routée par un LLM (principe IV).
- **`core/monitoring_agent.py` reste un outil de dev** (il écrit du code) — hors API.
- **Les agents sont des clients de l'API** (décision 2026-10-09). En attendant l'API, la frontière
  agents ↔ domaine est rendue explicite dans le monorepo (garde d'imports, spec 036 US6). Après l'étape 6b,
  un agent n'importe plus `core` : il lit et écrit par HTTP avec un token à scopes. Sortir un agent dans
  un repo séparé devient alors une option à coût quasi nul, à décider sur critères : rythme de mise en
  production différent, réutilisation hors job_agent, dépendances lourdes (LangGraph) inutiles à l'image du tracker.
- **Sécurité** : API joignable via Tailscale uniquement, tokens à scopes (ingest / tasks / user).

## Règles de migration (valables à chaque étape)

1. **Expand / contract** : on ajoute + backfill ; la suppression de l'ancien est une étape séparée.
2. **Flags** : tout nouveau chemin est activable par clé `config`, rollback sans redéploiement.
3. **Définition de terminé** : cron de la nuit suivante OK + smoke test tracker (feed, statut, fiche job)
   + script de parité vert (comptes du feed, scores, cas FELFEL, job Lausanne).
4. **Pas de déploiement touchant le schéma en fin de mois** (batch de mise à jour des statuts).
5. Backup `jobs.db` avant chaque déploiement de schéma.

## Étapes

### Phase A — Fondations (aucun changement visible)

| # | Étape | Contenu | Jours |
|---|-------|---------|-------|
| 0 | Filet de sécurité | Amendement constitution (MAJOR), script de parité, **test de restauration réelle** du backup sur verva | 1–2 |
| 1a | Fin des dépendances à l'emplacement | Sans rien déplacer : `JOB_AGENT_DATA_DIR` explicite dans compose, `core/paths.py` refuse de créer une base vide en prod, tous les chemins dérivés de `__file__` centralisés dans `core/paths.py`, scripts lancés en `python -m` (tracker, `core.main`, `core.scrape`) — spec 032 | 1 |
| 1b | Projet installable + préprod | `pyproject.toml`, `pip install -e .` dans l'image, suppression des bricolages `sys.path` (scripts, tests), scripts en `python -m scripts.*`, défauts des scripts de diag via `paths` ; `scripts/staging.sh` (tracker de préprod :8502 sur une copie du backup, sans toucher à la prod) — spec 033 | 1,5 |
| 1c | Déplacement vers `core/` | Phase A (sans déplacer) : derniers `__file__` supprimés (`core/cv_agent/renderer.py` → `.cv_pipeline`, `core/monitoring_agent`, découverte des scrapers) + garde qui résout chaque nom de module écrit en texte (`-m`, `importlib`, `mock.patch`). Phase B : un commit mécanique `git mv` + réécriture des imports par script, **sans shims** ; tracker laissé à la racine. Validation par le flux de mise en prod (préprod sur SHA exact) — spec 034 | 1,5–2 |
| 1d | Suppression des fichiers morts | `migrate_*.py` exécutés, `tracker_legacy.py`, `test_wellfound.py` **supprimés** (`git rm`, pas de dossier `archive/` : l'historique git les conserve, une note liste le dernier commit qui les contient) — spec 035 | 0,5 |
| 2 | Assainissement | 2a : SQL brut des vues → méthodes `JobStorage`. 2b : `subprocess` du tracker → table `tasks` + conteneur `worker`. 2c : retrait des mentions résiduelles de Groq (`core/scorer.py`, `tracker_views/onboarding.py`, `core/email_monitor.py`) — seul `core/llm.py` connaît le fournisseur | 3–5 |

### Phase B — Bon modèle de données (risque concentré ici)

| # | Étape | Contenu | Jours |
|---|-------|---------|-------|
| 3 | Séparation des espaces | Table `users` (1 ligne), `user_id` sur tracking/history/applications/interactions/contacts, `user_companies`, backfill ; anciennes colonnes conservées | 3–5 |
| 4 | Périmètre d'ingestion plateforme | Gate titre → Tier 0 scoring ; config plateforme ; surveiller volume d'extraction LLM dans `runs` | 2–3 |

### Phase C — API en parallèle (additive)

| # | Étape | Contenu | Jours |
|---|-------|---------|-------|
| 5 | API lecture | Service `api` (Tailscale), tokens à scopes, `/jobs`, `/me/feed`, `/me/companies`, `/me/contacts` | 4–6 |
| 6 | API écriture + tâches | Statuts, notes, cycle de vie, `POST /tasks` + SSE ; worker consomme via l'API | 4–6 |
| 6b | Agents via l'API | Le cv_agent (puis les autres agents) lit le job et écrit ses documents de candidature par l'API (token à scopes), plus aucun import de `core` hors `llm`/`paths` côté agent ; la garde de frontière (spec 036) est resserrée en conséquence. Prérequis de l'étape 9 | 2–4 |
| 7 | Ingestion via API | `core/scrape.py` → `/ingest/batch` derrière `INGEST_VIA_API` ; `BaseScraper` sans `JobStorage` ; une nuit en double pour comparer | 4–6 |

### Phase D — Front et chat

| # | Étape | Contenu | Jours |
|---|-------|---------|-------|
| 8 | Front `web/` | Page par page (feed lecture d'abord), chaque page = spec + mockup validé ; Streamlit reste référence jusqu'à parité de chaque page. Objectif : **toutes** les pages (remplacement complet) | 15–25 |
| 9 | Chat CV agent | Nœud d'édition ciblée dans le graphe, sessions `cv-sessions` streamées (SSE), page de session dans `web/` (aperçu cliquable + chat + révisions). Dès l'étape 6 + une fiche job dans `web/` ; en parallèle de l'étape 8 | 6–10 |
| 10 | Contraction | Suppression des anciennes colonnes ; **retrait complet de Streamlit** (`tracker.py`, `tracker_views/`, dépendance `streamlit`, service `tracker` du compose) une fois toutes les pages migrées | 2–3 |

**Total : ~50–79 jours de travail**, chat CV et passage des agents par l'API inclus (réestimé le 2026-10-09 ; l'ancien total de 42–65 n'incluait ni l'étape 9 ni l'étape 6b). Mesure à date : étapes 0 à 1c faites en ~6 jours de calendrier, conforme aux estimations. Chaque étape laisse un système en production ; arrêt possible après n'importe laquelle.

## Points ouverts

- Stack du front `web/` (React/Vite pressenti) — à confirmer avant l'étape 8.
- Emplacement du checkpointer `cv_agent_checkpoints.sqlite` sur le volume `job_data` + inclusion dans les backups (avant étape 9).
- ~~Amendements constitution~~ — faits (v2.0.0 le 2026-10-03, v2.0.1 le 2026-10-08).
- La règle constitutionnelle « UI Streamlit-native » devra être amendée avant l'étape 8 (stack de `web/`).
