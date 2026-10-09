<!--
  Sync Impact Report
  ==================
  Version change: 2.0.0 → 2.0.1
  Bump rationale: PATCH — déplacement du domaine vers `core/` (spec 034,
  étape 1c) ; réécriture des références de fichiers (Stable core, flux de
  données, DB access, tests, Principes II et VII) de la racine vers `core/`.
  Aucun changement de sens.

  Prior version (2.0.0) :
  Bump rationale: MAJOR — redéfinition incompatible du Principe III (la
  couture multi-utilisateur passe de `profile_id` à `user_id`) et
  restriction du Principe I (le périmètre d'ingestion ne peut plus être
  dérivé d'un profil). Ajout de l'architecture API (docs/roadmap-api.md).

  Modified principles:
    - I. Filet large — le périmètre d'ingestion est un paramètre
      plateforme ; tout gate dépendant d'un profil appartient au scoring.
    - III. Profil unifié unique — couture `user_id` ; l'utilisateur
      possède son profil ; `profile_id` reste la clé du scoring.
    - IV. Structure déterministe — étendu aux flux conversationnels :
      la cible d'une édition est choisie par l'UI, jamais routée par LLM.
    - VII. Sécurité — egress LLM aligné sur `llm.py` (DeepSeek) ; règles
      d'exposition de l'API (Tailscale, tokens à scopes).

  Added principles:
    - X. Catalogue partagé, espace utilisateur
    - XI. Migration progressive, application toujours opérationnelle

  Modified sections:
    - Architecture Constraints — data flow cible, API, tâches longues,
      dépendances autorisées, Stable core adapté au monorepo.
    - Development Workflow — specs SpecKit dans `specs/`, chemin de
      migration, deploy via scripts/deploy.sh.

  Known violations at ratification (résolues par la roadmap) :
    - scrape.py applique `title_matches_profile` avant sauvegarde
      (Principe I) → étape 4.
    - job_tracking, status_history, job_applications, interactions,
      contacts sans `user_id` ; `companies.monitored` sur l'entité
      partagée (Principe X) → étape 3.
    - SQL brut dans tracker_views/ et subprocess lancés par le tracker
      (Architecture Constraints) → étape 2.
    - Mentions résiduelles de Groq dans scorer.py,
      tracker_views/onboarding.py et email_monitor.py (Principe VII)
      → étape 2.

  Templates requiring updates:
    - .specify/templates/plan-template.md     ✅ No changes needed
    - .specify/templates/spec-template.md     ✅ No changes needed
    - .specify/templates/tasks-template.md    ✅ No changes needed
    - .specify/templates/checklist-template.md ✅ No changes needed

  Follow-up TODOs: None (CLAUDE.md aligné le 2026-10-03).
-->

# Job Agent Constitution

## Core Principles

### I. Filet large, point de filtrage unique

Les scrapers d'agrégation MUST collecter largement sans aucun filtrage de
pertinence. Le scorer est le seul point de décision où un job est jugé sur
sa pertinence vis-à-vis d'un profil.

Le périmètre d'ingestion (familles de postes, zones, fraîcheur, liste de
sources) est un **paramètre plateforme**, curé et versionné. Il MUST NEVER
être dérivé d'un profil ou d'un utilisateur. Tout filtre qui dépend d'un
profil (titres, exclusions, mode de travail) appartient au scoring, en
Tier 0 déterministe.

Exception cadrée : les sources company-keyed (adaptateurs ATS, scrapers de
sites carrières) renvoient la liste complète des postes d'une entreprise. Un
filtre grossier de famille (ex. product management) PEUT leur être appliqué —
c'est l'équivalent du scoping de requête que les job boards fournissent
gratuitement, pas du filtrage de pertinence.

Tout jugement de fit MUST rester au scorer seul.

**Rationale** : Éviter les décisions précoces irréversibles. Si le scraper
filtre, on ne sait jamais ce qu'on a perdu. Un catalogue façonné par le
profil d'un utilisateur prive tous les autres des offres qui les concernent.

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

Un agent qui produit du code ou des specs (ex. `core/monitoring_agent.py`) relève
du chemin code : il MUST NOT être exposé par l'API ni tourner en production.

**Rationale** : Séparation des responsabilités. La prose évolue au rythme
de la recherche d'emploi (heures) ; le code évolue au rythme du
développement logiciel (jours/semaines). Les confondre bloque les deux.

### III. Profil unifié unique, couture multi-utilisateur `user_id`

Chaque utilisateur possède **un seul** profil de recherche qui sert toute
son intention. Les profils MUST NOT être multipliés pour un usage personnel.

La couture multi-utilisateur est `user_id` : `search_profiles.user_id`
rattache le profil à son propriétaire. `profile_id` reste la clé du scoring
(`job_scores (job_id, profile_id)`), ce qui permet de versionner des
critères sans changer le modèle.

Le multi-utilisateur (auth, isolation, clés LLM par utilisateur) n'est PAS
construit tant qu'il n'est pas une décision produit explicite ; seule la
couture est maintenue.

**Rationale** : La multiplication des profils pour un usage solo ajoute
complexité sans valeur. Porter la couture sur l'utilisateur plutôt que sur
le profil place la propriété des données au bon endroit, pour un coût
négligeable tant qu'il n'existe qu'un utilisateur.

### IV. Structure déterministe, prose LLM uniquement

Les champs structurés dont dépend le control flow (work_mode, geo_zone,
status, etc.) MUST être dérivés de façon déterministe, sans LLM.

Le LLM MUST ONLY produire de la prose : résumé, raison du score,
qualification du candidat, contenu de CV et de lettre. Il MUST NEVER
générer de données structurées dont dépendent des décisions automatiques.

Cela s'applique aux flux conversationnels (chat avec un agent) : la cible
d'une édition (section du CV, paragraphe de lettre) et les décisions
(approuver, abandonner) MUST être fournies par l'interface, jamais
inférées par un LLM routeur d'intention.

**Rationale** : Les LLM sont non-déterministes par nature. Leur faire
produire des champs structurés dont dépend le routing ou le filtrage
introduit des corruptions silencieuses impossibles à déboguer.

### V. Modification chirurgicale

Chaque changement MUST être le plus petit possible pour atteindre l'objectif.
Pas de refactoring opportuniste, pas de nouvelle dépendance sans
justification explicite, blast radius minimal.

Les étapes de la roadmap API (`docs/roadmap-api.md`) sont des refactorings
planifiés, pas opportunistes : chacune est une spec avec ses non-goals.

Chaque spec MUST énoncer ses non-goals explicites.

**Rationale** : Projet solo sans filet de sécurité de code review
systématique. La discipline du diff minimal réduit le risque de régression
et facilite le bisect.

### VI. Validation empirique avant livraison

Les changements MUST être vérifiés contre des cas de régression connus (ex.
l'offre FELFEL, le job de Lausanne) avant d'être considérés comme terminés.

Chaque phase MUST être confirmée avant de passer à la suivante. Pas de
déploiement sans validation.

**Rationale** : Les tests automatisés couvrent le stockage, mais pas le
comportement des scrapers ni la qualité du scoring. La vérification
empirique sur des cas connus est le filet de sécurité pour le reste.

### VII. Sécurité d'abord

Les secrets (clés API, tokens) MUST NEVER être commités ni loggés.

L'egress réseau MUST être contrôlé : les scrapers ne contactent que leurs
cibles déclarées, l'appel LLM ne sort que vers le fournisseur déclaré dans
`core/llm.py` (DeepSeek). Aucun autre fichier ne nomme un fournisseur LLM.

L'API MUST être joignable uniquement via Tailscale, jamais exposée
publiquement. Chaque client reçoit un token à scopes minimaux : un scraper
n'a accès qu'à l'ingestion, un agent qu'aux tâches et à l'espace de
l'utilisateur pour lequel il agit.

Toute nouvelle intégration (scraper, API, service) MUST partir du moindre
privilège : pas d'accès aux données qu'elle n'a pas besoin de lire, pas de
write sans explicitation.

**Rationale** : Les secrets exposés sont irrévocables. Le contrôle
d'egress évite les fuites de données et les appels non désirés. Le moindre
privilège limite le blast radius d'un bug ou d'une compromission.

### VIII. Scrapers organisés par modèle d'acquisition

Les scrapers sont classés en deux catégories, invoquables indépendamment :

- **Boards d'agrégation** (LinkedIn, Indeed, Wellfound, etc.) :
  query-driven, filet large. Paramétrés par des requêtes de recherche
  issues du périmètre plateforme (Principe I).
- **Sources company-keyed** (adaptateurs ATS Greenhouse/Lever/Ashby,
  scrapers de sites carrières) : pilotées par **une liste d'entreprises**
  décidée par l'orchestrateur, pas par une requête. Les adaptateurs sont
  des fonctions pures (liste → offres), sans logique de mode interne. La
  liste varie selon le chemin : **toutes** les entreprises connues d'un
  provider en découverte (filet large), ou l'**union des entreprises
  surveillées par les utilisateurs** en monitoring.

Les deux catégories MUST pouvoir tourner séparément (ex. `--source
linkedin` vs `--source greenhouse`), et le monitoring MUST pouvoir tourner
seul (`--monitored-only`) sans déclencher le filet large.

Un scraper ne connaît que son contrat de sortie (`JobPosting`) ; à terme il
MUST NOT accéder à la base autrement que par l'endpoint d'ingestion. Son
indépendance est contractuelle, pas de déploiement : un seul worker
d'ingestion les exécute.

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

### X. Catalogue partagé, espace utilisateur

Les données sont réparties en deux espaces exclusifs :

- **Catalogue partagé** (aucun utilisateur) : ingestion, `jobs`, champs
  extraits (une extraction par job, quel que soit le nombre
  d'utilisateurs), `runs`, faits sur les entreprises (site, ATS, taille,
  secteur).
- **Espace utilisateur** (clé `user_id`) : profil, scores, suivi et
  historique de statut, candidatures et documents, relation aux
  entreprises (`user_companies` : surveillance, statut, notes), contacts
  et interactions.

Le scoring est une **vue de l'utilisateur sur le catalogue** : il est porté
par l'utilisateur (via son profil), jamais par le job. Une décision ou une
donnée personnelle MUST NEVER être stockée sur une entité du catalogue.

Les contacts sont **privés par utilisateur** : un contact trouvé par un
utilisateur n'est jamais visible d'un autre.

**Rationale** : L'ingestion ne dépend de personne, le jugement dépend de
chacun. Mélanger les deux rend le multi-utilisateur impossible sans
migration lourde et expose des données personnelles (RGPD).

### XI. Migration progressive, application toujours opérationnelle

L'application est l'outil de recherche d'emploi en production : chaque
étape de migration MUST laisser un système pleinement utilisable.

- **Expand / contract** : une migration de schéma ajoute et backfill ; la
  suppression de l'ancien est une étape séparée, après usage éprouvé.
- **Flags** : tout nouveau chemin d'exécution est activable par une clé
  `config`, avec retour arrière sans redéploiement.
- **Définition de terminé** : cron de la nuit suivante OK, smoke test du
  tracker (feed, changement de statut, fiche job), script de parité vert.
- **Backup** de la base avant tout déploiement touchant le schéma.

Pas de réécriture parallèle dans un repo séparé : le nouveau se construit
dans le monorepo existant, sur le code existant.

**Rationale** : Une réécriture redécouvre les bugs déjà corrigés et laisse
l'utilisateur sans outil pendant des mois. La migration progressive livre
de la valeur à chaque étape et peut s'arrêter après n'importe laquelle.

## Architecture Constraints

Ces contraintes découlent des principes I, IV, VIII, IX, X et XI.

**Data flow (cible)** : scrapers → `POST /ingest` → catalogue → extraction
(tâche) → scoring par utilisateur (tâche) → clients (tracker, web, agents)
via l'API. Chaque étape est optionnelle et indépendante. Tant que la
roadmap n'est pas terminée, le flux actuel `core/scrape.py → SQLite →
core/score.py → tracker.py` reste valide.

**Monorepo** : `core/` (domaine), `api/` (FastAPI), `web/` (front),
`tracker/` (Streamlit). `api/` et les clients MUST passer par `core/` ;
aucune duplication de la logique de stockage.

**DB access** : Tout accès à la base MUST passer par `JobStorage`. Pas de
`sqlite3` direct hors de `core/storage.py`. À terme, l'API est le seul écrivain ;
les clients et workers passent par elle.

**API** : monolithe modulaire, un router par domaine, chaque router
n'utilisant que les méthodes de son domaine.

**Tâches longues** : aucun appel LLM ni traitement de plus de quelques
secondes dans une requête HTTP. Batch → table `tasks` + worker ;
conversationnel → session pilotant le graphe LangGraph, progression en SSE.

**Dépendances** : FastAPI, uvicorn et pydantic sont autorisés pour `api/`.
La stack de `web/` est fixée par sa première spec. Toute autre nouvelle
dépendance MUST être justifiée dans la spec correspondante.

**Stable core** : `core/storage.py`, `core/models.py`, `core/profiles.py`,
`core/scrape.py`, `core/scorer.py`, `core/main.py`, les fichiers de
`core/scrapers/`, `tracker_views/shared.py`, `tracker_views/onboarding.py` et
les migrations sont NEVER modified sauf si la tâche, ou l'étape de roadmap
en cours, les concerne explicitement.

## Development Workflow

Ces règles découlent des principes II, V, VI, VII et XI.

**Code path** :
1. Lire la spec SpecKit applicable dans `specs/`
2. Modifier le code — diffs minimaux, pas de refactoring opportuniste
3. Si `core/storage.py` ou `core/models.py` touché : `python -m pytest tests/`
4. Vérification empirique contre les cas de régression connus
5. Commit avec message descriptif
6. `git push` → `scripts/deploy.sh` sur le serveur

**Migration path** (étapes de `docs/roadmap-api.md`) : le code path,
plus backup avant déploiement, flag de retour arrière, et validation de la
définition de terminé (Principe XI) avant l'étape suivante. Les déploiements
touchant le schéma évitent la fin de mois (mise à jour des statuts en batch).

**Prose path** :
1. Modifier le scoring context ou la config directement en base
2. Pas de déploiement, pas de commit, pas de touché au code
3. Vérifier le résultat sur un scoring suivant

**Tests** : `tests/test_storage.py` (unit, in-memory DB) à exécuter avant
tout commit touchant `core/storage.py` ou `core/models.py`. `tests/run_all.py`
(intégration, hit live scrapers) à exécuter intentionnellement seulement.

**Secrets** : Stockés dans des variables d'environnement (`.env`), jamais
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
- Les violations connues listées dans le Sync Impact Report sont tolérées
  jusqu'à l'étape de roadmap qui les résout.

**Version**: 2.0.1 | **Ratified**: 2026-06-12 | **Last Amended**: 2026-10-09
