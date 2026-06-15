<!--
  Sync Impact Report
  ==================
  Version change: 1.0.0 → 1.1.0
  Bump rationale: MINOR — expansion matérielle du Principe VIII pour
  refléter la décision de conserver la découverte ATS. Les sources
  company-keyed sont pilotées par une liste d'entreprises (toutes les
  connues d'un provider en découverte, ou les monitorées seulement en
  monitoring), et non par les seules entreprises monitorées.

  Modified principles:
    - VIII. Scrapers organisés par modèle d'acquisition — pilotage des
      sources company-keyed élargi à une liste paramétrable par
      l'orchestrateur (découverte vs monitoring) ; adaptateurs ATS
      qualifiés de fonctions pures ; garantie d'exécution autonome du
      monitoring (--monitored-only).

  Added sections: None
  Removed sections: None

  Templates requiring updates:
    - .specify/templates/plan-template.md     ✅ No changes needed
    - .specify/templates/spec-template.md     ✅ No changes needed
    - .specify/templates/tasks-template.md    ✅ No changes needed
    - .specify/templates/checklist-template.md ✅ No changes needed

  Follow-up TODOs: None.
-->

# Job Agent Constitution

## Core Principles

### I. Filet large, point de filtrage unique

Les scrapers d'agrégation MUST collecter largement sans aucun filtrage de
pertinence. Le scorer est le seul point de décision où un job est jugé sur
sa pertinence vis-à-vis du profil.

Exception cadrée : les sources company-keyed (adaptateurs ATS, scrapers de
sites carrières) renvoient la liste complète des postes d'une entreprise. Un
filtre grossier de famille (ex. product management) PEUT leur être appliqué —
c'est l'équivalent du scoping de requête que les job boards fournissent
gratuitement, pas du filtrage de pertinence.

Tout jugement de fit MUST rester au scorer seul.

**Rationale** : Éviter les décisions précoces irréversibles. Si le scraper
filtre, on ne sait jamais ce qu'on a perdu. Le scorer a le contexte complet.

### II. Deux chemins d'amélioration, jamais confondus

Les changements sont classés en deux chemins exclusifs :

- **Prose** (scoring context, liste d'entreprises ATS, données de
  configuration) : va directement en base de production, sans toucher au
  code. Aucun déploiement nécessaire.
- **Code** (scrapers, schéma, règles métier) : passe par dev → git →
  deploy. Testé et revu avant production.

Un changement est l'un ou l'autre, jamais les deux simultanément. Si un
changement de prose nécessite une adaptation de code, c'est un changement
code qui inclut la prose comme payload.

**Rationale** : Séparation des responsabilités. La prose évolue au rythme
de la recherche d'emploi (heures) ; le code évolue au rythme du
développement logiciel (jours/semaines). Les confondre bloque les deux.

### III. Profil unifié unique, couture multi-utilisateur préservée

Un seul profil de recherche sert toute l'intention de l'utilisateur. La
dimension `profile_id` MUST rester en base comme couture pour un futur
multi-utilisateur, mais les profils MUST NOT être multipliés pour un usage
personnel.

**Rationale** : La multiplication des profils pour un usage solo ajoute
complexité sans valeur. La couture `profile_id` est un pari architectural
peu coûteux qui préserve la possibilité d'évolution.

### IV. Structure déterministe, prose LLM uniquement

Les champs structurés dont dépend le control flow (work_mode, geo_zone,
status, etc.) MUST être dérivés de façon déterministe, sans LLM.

Le LLM MUST ONLY produire de la prose : résumé, raison du score,
qualification du candidat. Il MUST NEVER générer de données structurées
dont dépendent des décisions automatiques.

**Rationale** : Les LLM sont non-déterministes par nature. Leur faire
produire des champs structurés dont dépend le routing ou le filtrage
introduit des corruptions silencieuses impossibles à déboguer.

### V. Modification chirurgicale

Chaque changement MUST être le plus petit possible pour atteindre l'objectif.
Pas de refactoring opportuniste, pas de nouvelle dépendance sans
justification explicite, blast radius minimal.

Chaque spec MUST énoncer ses non-goals explicites.

**Rationale** : Projet solo sans filet de sécurité de code review
systématique. La discipline du diff minimal réduit le risque de régression
et facilite le bisect.

### VI. Validation empirique avant livraison

Les changements MUST être vérifiés contre des cas de régression connus (ex.
l'offre FELFEL) avant d'être considérés comme terminés.

Chaque phase MUST être confirmée avant de passer à la suivante. Pas de
déploiement sans validation.

**Rationale** : Les tests automatisés couvrent le stockage, mais pas le
comportement des scrapers ni la qualité du scoring. La vérification
empirique sur des cas connus est le filet de sécurité pour le reste.

### VII. Sécurité d'abord

Les secrets (clés API, tokens) MUST NEVER être commités ni loggés.

L'egress réseau MUST être contrôlé : les scrapers ne contactent que leurs
cibles déclarées, l'appel LLM ne sort que vers Groq et DeepSeek.

Toute nouvelle intégration (scraper, API, service) MUST partir du moindre
privilège : pas d'accès aux données qu'elle n'a pas besoin de lire, pas de
write sans explicitation.

**Rationale** : Les secrets exposés sont irrévocables. Le contrôle
d'egress évite les fuites de données et les appels non désirés. Le moindre
privilège limite le blast radius d'un bug ou d'une compromission.

### VIII. Scrapers organisés par modèle d'acquisition

Les scrapers sont classés en deux catégories, invoquables indépendamment :

- **Boards d'agrégation** (LinkedIn, Indeed, Wellfound, etc.) :
  query-driven, filet large. Paramétrés par des requêtes de recherche.
- **Sources company-keyed** (adaptateurs ATS Greenhouse/Lever/Ashby,
  scrapers de sites carrières) : pilotées par **une liste d'entreprises**
  décidée par l'orchestrateur, pas par une requête. Les adaptateurs sont
  des fonctions pures (liste → offres), sans logique de mode interne. La
  liste varie selon le chemin : **toutes** les entreprises connues d'un
  provider en découverte (filet large), ou les seules `monitored = true`
  en monitoring.

Les deux catégories MUST pouvoir tourner séparément (ex. `--source
linkedin` vs `--source greenhouse`), et le monitoring MUST pouvoir tourner
seul (`--monitored-only`) sans déclencher le filet large.

**Rationale** : Les deux modèles ont des rythmes de changement différents
(liste d'entreprises vs. termes de recherche) et des contraintes de
rate-limiting distinctes. Découpler le filtre (la liste passée) de
l'adaptateur (fonction pure) permet de servir découverte et monitoring
avec un seul code par provider.

### IX. Le scoring est une couche optionnelle

Le pipeline MUST fonctionner de bout en bout sans profil ni clé LLM : les
scrapers écrivent en base, l'interface affiche les jobs non scorés sans
dégradation fonctionnelle.

La définition du profil de scoring est toujours différable et MUST NOT être
un prérequis à l'usage de l'application.

**Rationale** : Permet l'onboarding immédiat (pas de clé LLM requise),
rend l'application utile même sans scoring configuré, et garantit que la
couche scraping est autonome et testable isolément.

## Architecture Constraints

Ces contraintes découlent des principes I, IV, VIII et IX.

**Data flow** : `scrape.py → SQLite → score.py (extract) → score.py
(per-profile) → tracker.py`. Chaque étape est optionnelle et indépendante.

**DB access** : Tout accès à la base MUST passer par `JobStorage`. Pas de
`sqlite3` direct hors de `storage.py`, sauf blocs diagnostiques explicites
dans `settings.py` ou `dashboard.py`.

**No new dependencies** : L'UI est Streamlit-native. Toute nouvelle
dépendance MUST être justifiée dans la spec correspondante.

**Stable core** : `storage.py`, `models.py`, `profiles.py`, `scrape.py`,
`scorer.py`, `main.py`, les fichiers de `scrapers/`, `tracker_views/shared.py`,
`tracker_views/onboarding.py` et les migrations sont NEVER modified sauf si
la tâche les concerne explicitement.

## Development Workflow

Ces règles découlent des principes II, V, VI et VII.

**Code path** :
1. Lire la spec applicable dans `prompts/`
2. Modifier le code — diffs minimaux, pas de refactoring opportuniste
3. Si `storage.py` ou `models.py` touché : `python -m pytest tests/` (162 tests)
4. Vérification empirique contre les cas de régression connus
5. Commit avec message descriptif
6. `git push` → `docker compose up -d` sur le serveur

**Prose path** :
1. Modifier le scoring context ou la config directement en base
2. Pas de déploiement, pas de commit, pas de touché au code
3. Vérifier le résultat sur un scoring suivant

**Tests** : `tests/test_storage.py` (unit, in-memory DB) à exécuter avant
tout commit touchant `storage.py` ou `models.py`. `tests/run_all.py`
(intégration, hit live scrapers) à exécuter intentionnellement seulement.

**Secrets** : Stockés dans `.venv/` et variables d'environnement, jamais
dans le repo. `.env` et fichiers de secrets sont `.gitignore`d.

## Governance

Cette constitution a priorité sur toute autre pratique ou convention du
projet. En cas de conflit entre un document de spécification et la
constitution, la constitution prévaut.

**Amendements** :
- Toute modification de la constitution MUST être documentée avec un
  message de commit `docs: amend constitution to vX.Y.Z (<summary>)`
- Les changements de principes (MAJOR), ajouts (MINOR), et clarifications
  (PATCH) suivent le versionnement sémantique.
- Les amendements MUST inclure un Sync Impact Report en commentaire HTML
  en tête du fichier.

**Versioning** : MAJOR.MINOR.PATCH selon les règles suivantes :
- MAJOR : suppression ou redéfinition incompatible d'un principe
- MINOR : nouveau principe, nouvelle section, ou expansion matérielle
- PATCH : clarification, correction typographique, reformulation sans
  changement de sens

**Compliance Review** :
- Chaque plan (`/speckit-plan`) MUST inclure une Constitution Check qui
  vérifie la conformité de la feature aux principes applicables.
- Toute violation MUST être justifiée dans la section Complexity Tracking
  du plan, avec la raison et l'alternative plus simple rejetée.

**Version**: 1.1.0 | **Ratified**: 2026-06-12 | **Last Amended**: 2026-06-15
