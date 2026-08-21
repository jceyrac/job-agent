# Free-Work.com — Reconnaissance de faisabilité

**Date** : 2026-08-06  
**Objet** : Investigation préalable à une spec SpecKit pour un nouveau scraper  
**Statut** : Finding report (PAS une spec, PAS une implémentation)

---

## Étape 1 : Contrat d'un scraper job_agent (rappel)

D'après `scrapers/base.py`, `scrapers/boards/hh_network.py` (template de référence),
`scrape.py`, `models.py`, et la constitution (`.specify/memory/constitution.md`,
Principe I) :

1. **Héritage** : étendre `BaseScraper`, déclarer `SOURCE_NAME`, `ENABLED`, `ACQUISITION_MODEL = "board"`, `SUPPORTS_DISCOVERY = True`.
2. **Signature** : `fetch(self, job_filter: JobFilter) -> list[JobPosting]` — unique méthode obligatoire.
3. **Pas de filtrage de pertinence** : le scraper est un filet large. Le `JobFilter` sert uniquement au scoping grossier (query terms, date). Le scoring de désirabilité est exclusivement dans `scorer.py`.
4. **Sortie** : une liste de dataclass `JobPosting` avec au minimum `source`, `title`, `company`, `location`, `url`, `description`. Les champs `work_mode`, `contract_type`, `geo_zone`, `salary`, `posted_date` sont facultatifs au scrape mais accélèrent le scoring.
5. **Écriture DB** : le scraper ne touche PAS à la DB. C'est `scrape.py` qui appelle `db.save_unscored()` après filtrage et déduplication.

---

## Étape 2 : Reconnaissance technique

### 2.1 Verdict technique : ✅ API JSON DISPONIBLE (préférée au scraping HTML)

Deux méthodes possibles, l'API JSON est clairement supérieure :

| Méthode | Endpoint | Auth | Données | Verdict |
|---------|----------|------|---------|---------|
| **API JSON** | `https://api.free-work.com/job_postings?page=N&itemsPerPage=M` | Aucune | Tous les champs structurés en 1 appel | **GO ✅** |
| SSR HTML | `https://www.free-work.com/fr/tech-it/jobs?page=N` | Aucune | 19 cards/page, HTML uniquement, détails nécessitent 1 appel par offre | Possible mais inefficace |

### 2.2 Détail de l'API JSON

**Endpoint** : `GET https://api.free-work.com/job_postings`

**Format** : JSON-LD / Hydra (Content-Type: `application/ld+json; charset=utf-8`)

**Auth** : Aucune. Zéro header requis (User-Agent personnalisé recommandé).

**Pagination** :
- `?page=N&itemsPerPage=M` — max testé : 100 items/page
- Total : **9 948 offres** (`hydra:totalItems`)
- Métadonnées de pagination dans `hydra:view` (first, last, next)
- Dernière page à 100 items/page : page 100

**Structure d'une offre** (`hydra:member[i]`) — champs clés :

```json
{
  "id": 659989,
  "title": "PMO Data +10 ans XP",
  "slug": "pmo-data-10-ans-xp",
  "description": "<p>HTML complet de l'offre...</p>",
  "candidateProfile": "<p>Profil recherché...</p>",
  "companyDescription": "<p>Description entreprise...</p>",
  "experienceLevel": "expert",
  "minAnnualSalary": null,
  "maxAnnualSalary": null,
  "minDailySalary": 120,
  "maxDailySalary": 600,
  "currency": "EUR",
  "duration": 12,
  "durationValue": 1,
  "durationPeriod": "year",
  "renewable": true,
  "remoteMode": "partial",
  "contracts": ["contractor"],
  "applicationType": "turnover",
  "location": {
    "locality": "Lille",
    "postalCode": "59000",
    "adminLevel1": "Hauts-de-France",
    "adminLevel2": "Nord",
    "country": "France",
    "countryCode": "FR",
    "latitude": "50.6365654",
    "longitude": "3.0635282",
    "key": "fr~hauts-de-france~nord~lille",
    "label": "Lille, Hauts-de-France"
  },
  "company": {
    "id": 22004,
    "name": "CAT-AMANIA",
    "slug": "cat-amania-8",
    "description": "Créée en 1999...",
    "location": { ... },
    "logo": { "small": "...", "medium": "..." }
  },
  "job": {
    "id": 72,
    "nameForContribution": "Directeur·rice / Responsable de la data (CDO)",
    "slug": "directeur-de-la-data-cdo"
  },
  "skills": [{ "id": 41, "name": "..." }],
  "publishedAt": "2026-08-06T11:30:50+02:00",
  "expiredAt": "2026-10-05T23:59:59+02:00",
  "createdAt": "2026-08-06T11:30:50+02:00",
  "status": "published"
}
```

### 2.3 Filtres API

| Paramètre | Valeurs testées | Effet |
|-----------|----------------|-------|
| `contracts` | `permanent` (5 193 offres), `contractor` (7 298 offres) | Filtre CDI vs Freelance |
| `remoteMode` | `full` (213), `partial`, `none` | Télétravail |
| `jobs` | slug de catégorie métier (ex: `product-owner` → 102) | Un seul slug à la fois |
| `search`, `q`, `title` | — | ❌ Pas de recherche texte dans l'API |
| `order[publishedAt]` | — | ❌ Retourne une erreur Hydra (non-scalar value) |

**Note** : L'API ne supporte pas le filtrage multi-slug en une requête. Il faut itérer sur les slugs.

### 2.4 robots.txt

```
User-agent: *
Disallow: /login
Disallow: /logout
Disallow: /fw-deals
```

**Aucune restriction** sur `/fr/tech-it/jobs` ni sur `api.free-work.com`. Le sitemap est public. Les CGU (page 404) n'ont pas pu être inspectées.

**Politique polie recommandée** :
- User-Agent : `job-agent/1.0 (jceyrac@pm.me)`
- Délai entre requêtes : 1s (pas de rate-limit détecté dans les headers, restons courtois)
- Pas de parallélisme sauvage — itération séquentielle
- L'API passe par Varnish — le cache est configuré `no-cache, private` mais aucun throttling explicite

### 2.5 Backend

Le backend est **Turnover-IT** (plateforme AGSI). L'API `api.free-work.com` est le front public de cette plateforme. Aucune clé API ni auth n'est nécessaire.

---

## Étape 3 : Mapping Free-Work → JobPosting

### Mapping direct

| Champ Free-Work API | Champ JobPosting | Transformation |
|---------------------|------------------|----------------|
| `title` | `title` | Direct |
| `company.name` | `company` | Direct |
| `location.label` | `location` | Direct ("Lille, Hauts-de-France") |
| `location.countryCode` | `country_code` | Direct ("FR") |
| `location.country` | `base_location` | Direct ("France") |
| `description` | `description` | Strip HTML tags, truncate à 3000 chars |
| `publishedAt` | `posted_date` | Parse ISO 8601 → `date` |
| `contracts[0]` | `contract_type` | `"contractor"` → `"freelance"`, `"permanent"` → `"permanent"` |
| `remoteMode` | `work_mode` | `"full"` → `"remote"`, `"partial"` → `"hybrid"`, absent/`null` → `"on-site"` |
| `minDailySalary` + `maxDailySalary` | `salary` | Si contractor : `"{min}-{max} €/jour"`. Si permanent : utiliser `minAnnualSalary`/`maxAnnualSalary` |
| `skills[].name` | `tags` | Liste des noms de skills |
| `minDailySalary`, `maxDailySalary`, `minAnnualSalary`, `maxAnnualSalary` | `salary_text` | Format lisible |
| `companyDescription` | `description` append | Info entreprise en complément |
| `candidateProfile` | `description` append | Profil recherché en complément |

### Champs non fournis → laissés à `None` / inférés par le scorer LLM

| Champ JobPosting | Statut | Raison |
|------------------|--------|--------|
| `company_size` | ❌ Absent de l'API | Le scorer LLM inférera depuis `company.description` |
| `geo_zone` | Dérivable | `countryCode == "FR"` → `"europe"` (règle déterministe). À affiner si offres hors France. |
| `industry_sector` | ❌ Absent | Le scorer LLM inférera |
| `language_required` | ❌ Absent | Le scorer LLM inférera |
| `comp_annual_eur` | Calculable | Si `minAnnualSalary`/`maxAnnualSalary` + `currency == "EUR"` → prendre le midpoint |

### Ambiguités

1. **`contracts` est un array** — certaines offres ont les deux `["permanent", "contractor"]`. Prendre le premier, ou `"permanent"` si les deux sont présents (décision à trancher).
2. **`experienceLevel`** : `"expert"`, `"senior"`, `"junior"`, etc. Pas de champ direct dans `JobPosting`. Peut être stocké dans `summary` ou utilisé comme hint pour le scorer. À trancher.
3. **`duration`/`durationValue`/`durationPeriod`** : présents uniquement pour les missions freelance. Pas de champ dans `JobPosting`. À merger dans `summary` ou `salary_text`.

---

## Étape 4 : Scoping source — slugs métier proposés

**Principe constitutionnel** (Principe I) : le scoping de slugs est un choix de « où chercher » (équivalent à choisir les query terms sur un job board), pas un filtrage de désirabilité. Le filtrage fin reste au scorer.

**Profil cible** : Senior PM/PO, fintech/Web3/AI, Europe (France prioritaire)

### Slugs retenus (avec volumes au 2026-08-06)

#### Cœur PM/PO — 124 offres
| Slug | Intitulé | Volume |
|------|----------|--------|
| `product-owner` | Product Owner | 102 |
| `responsable-produit` | Product Manager / CPO | 22 |
| `chef-de-projet-informatique` | Chef de projet IT | 11 |
| `chef-de-projet-digital` | Chef de projet digital | 10 |

#### Leadership projet / Agile — 245 offres
| Slug | Intitulé | Volume |
|------|----------|--------|
| `project-management-officer` | PMO | 118 |
| `scrum-master` | Scrum Master | 63 |
| `directeur-de-projet` | Directeur de projet | 47 |
| `coach-agile` | Coach Agile | 17 |

#### Data / AI / Transformation (PM-adjacent) — 261 offres
| Slug | Intitulé | Volume |
|------|----------|--------|
| `directeur-de-la-data-cdo` | CDO / Head of Data | 172 |
| `data-scientist` | Data Scientist | 45 |
| `developpeur-ia-machine-learning` | Développeur IA/ML | 25 |
| `directeur-de-la-transformation-digitale-cdo` | CDO Transformation | 17 |
| `manager-de-transition` | Manager de transition | 5 |

#### Optionnel — ~100 offres supplémentaires
| Slug | Intitulé | Volume |
|------|----------|--------|
| `consultant-moa-amoa` | Consultant MOA/AMOA | 100 |

### Total estimé : **~630 offres** avant filtrage LLM

Ce volume est raisonnable : à 100 items/page, 7 appels API suffisent. À 1 requête par slug × 1-2 pages par slug, ~15-20 requêtes total.

### Justification

- **Core PM/PO** : cible directe du profil. `product-owner` est le slug le plus volumineux.
- **Agile/Leadership** : `scrum-master`, `coach-agile`, `directeur-de-projet` capturent les rôles PM hybrides et les postes de direction de produit qui ne passent pas par le slug `product-owner`.
- **Data/AI/Transformation** : `directeur-de-la-data-cdo` (172 offres) est riche pour un PM orienté data/AI. `developpeur-ia-machine-learning` peut contenir des PM AI mal catégorisés.
- **`consultant-moa-amoa`** en optionnel : la MOA est souvent un rôle PM dans les ESN françaises. 100 offres, mais beaucoup de bruit — à ne prendre que si le scorer gère bien.
- **Exclus** : `data-engineer` (290), les slugs de développeur pur, `concepteur-multimedia`, `content-manager` — hors scope PM.

---

## Synthèse et recommandation

### Verdict : **GO** 🟢

| Critère | Évaluation |
|---------|------------|
| **Accès technique** | Excellente — API JSON propre, sans auth, sans rate-limit visible |
| **Volume** | ~630 offres ciblées via slugs, sur 9 948 total — charge raisonnable |
| **Qualité des données** | Excellente — tous les champs structurés en un appel (description complète, skills, salaire, remote, company) |
| **Fragilité** | Faible — API Hydra stable, pas de DOM scraping |
| **ToS / Légal** | robots.txt permissif. CGU non trouvables (404). Usage raisonnable. |
| **Effort estimé** | **~150 lignes de Python** (équivalent à `hh_network.py`). 2-3h de dev + 1h de tests. |

### Approche recommandée

1. **API-first** : utiliser exclusivement `api.free-work.com/job_postings` — ne pas implémenter le fallback HTML. L'API fournit déjà toutes les données.
2. **Itération par slug** : boucler sur la liste de slugs, 1 appel par slug, paginer si >100 résultats.
3. **Pas de détail** : inutile de scraper les pages `free-work.com/fr/tech-it/job-mission/...` — l'API contient déjà `description`, `candidateProfile`, `companyDescription`.
4. **Déduplication** : l'API a des offres cross-listées (permanent + contractor). Utiliser l'`id` API comme clé de déduplication.

### Décisions ouvertes à trancher avant la spec

1. **Slugs définitifs** : valider la liste ci-dessus. Ajoute-t-on `consultant-moa-amoa` (100 offres, beaucoup de bruit) ? Retire-t-on `data-scientist` et `developpeur-ia-machine-learning` (pas PM) ? Faut-il ajouter `data-analyst` (52) ou `business-analyst` ?

2. **Contrat `contracts` ambigu** : quand une offre a `["permanent", "contractor"]`, quel `contract_type` choisir ? Proposition : prendre le premier, ou prioriser `permanent` si les deux sont présents (c'est souvent un CDI avec TJM indicatif).

3. **`experienceLevel`** : faut-il créer un nouveau champ dans `JobPosting` (`experience_level`), ou le stocker dans `summary` en attendant ? La création d'un champ nécessite de toucher `models.py` et `storage.py` (fichiers NEVER modify) → spec dédiée requise.

4. **Taux de requêtes** : 1 req/s entre chaque slug (courtois), ou plus agressif (pas de rate-limit détecté) ? Le volume total (~20 requêtes) rend la question quasi-anecdotique, mais à fixer pour la spec.

---

*Rapport produit le 2026-08-06. Les volumes d'offres sont une photographie instantanée.*
