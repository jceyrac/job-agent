# Data Model — Free-Work.com Scraper

**Feature**: Spec 025  
**Date**: 2026-08-06

---

## Overview

Aucune nouvelle entité. Le scraper transforme les entités de l'API Free-Work en `JobPosting` (dataclass existante dans `models.py`). Aucune modification de `models.py` ni de `storage.py`.

---

## Source Entities (API Free-Work)

### JobPosting (API)

```
JobPosting (API) {
    id: int                    # Identifiant canonique Turnover-IT (clé de déduplication)
    title: str                 # Titre de l'offre
    slug: str                  # Slug SEO
    description: str (HTML)    # Description complète
    candidateProfile: str|null (HTML)  # Profil recherché
    companyDescription: str|null (HTML) # Description entreprise
    experienceLevel: str|null  # "expert" | "senior" | "intermediate" | "junior"
    minAnnualSalary: int|null  # Salaire annuel min (CDI)
    maxAnnualSalary: int|null  # Salaire annuel max (CDI)
    minDailySalary: int|null   # TJM min (freelance)
    maxDailySalary: int|null   # TJM max (freelance)
    currency: str              # "EUR"
    duration: int|null         # Durée en mois (freelance)
    durationValue: int|null    # Valeur durée
    durationPeriod: str|null   # "month" | "year"
    renewable: bool|null       # Mission renouvelable ?
    remoteMode: str|null       # "full" | "partial" | null (on-site)
    contracts: [str]           # ["permanent"] | ["contractor"] | ["permanent", "contractor"]
    publishedAt: str (ISO8601) # Date de publication
    createdAt: str (ISO8601)
    expiredAt: str|null (ISO8601)
    status: str                # "published" | ...
    applicationType: str       # "turnover" | "external"
    location: {
        locality: str|null     # Ville
        postalCode: str|null
        adminLevel1: str       # Région
        adminLevel2: str       # Département
        country: str           # "France"
        countryCode: str       # "FR"
        label: str             # "Ville, Région"
        latitude: str
        longitude: str
    }
    company: {
        id: int
        name: str              # Nom entreprise
        slug: str
        description: str|null (HTML)
        location: { ... }      # Même structure que location
        logo: { small: str, medium: str }
    }
    job: {
        id: int
        nameForContribution: str  # Nom du métier
        slug: str                 # Slug métier → utilisé pour l'URL
    }
    skills: [{
        id: int
        name: str
        ...
    }]
}
```

---

## Target Entity (job_agent)

### JobPosting (models.py — existant, inchangé)

```
JobPosting {
    source: str              # "free_work"
    title: str
    company: str
    location: str
    url: str
    canonical_url: str       # computed __post_init__ via normalize_url()
    norm_title: str          # computed __post_init__
    norm_company: str        # computed __post_init__
    posted_date: date|null
    description: str|null    # max 3000 chars (strip HTML)
    tags: list[str]          # skills + contract:permanent / contract:contractor
    salary: str|null         # "120-600 €/jour" ou "45k-55k €/an"
    summary: str|null        # "Niveau: expert. Mission: 12 mois (renouvelable)."
    work_mode: str|null      # "remote" | "hybrid" | "on-site"
    base_location: str|null  # "France"
    company_size: str|null   # None (non fourni par l'API)
    contract_type: str|null  # "freelance" | "permanent"
    geo_zone: str|null       # None (dérivé par le scorer)
    country_code: str|null   # "FR"
    salary_text: str|null    # texte lisible du salaire
    company_country: None    # laissé au scorer LLM
    industry_sector: None    # laissé au scorer LLM
    language_required: None  # laissé au scorer LLM
    ...
}
```

---

## Field Transformation Rules

| API Field | JobPosting Field | Rule |
|-----------|-----------------|------|
| `"free_work"` | `source` | Constante |
| `title` | `title` | Direct |
| `company.name` | `company` | Direct |
| `location.label` | `location` | Direct |
| `f"https://www.free-work.com/fr/tech-it/job-mission/{job.slug}/{slug}"` | `url` | Reconstruction |
| `publishedAt` | `posted_date` | `date.fromisoformat(publishedAt[:10])` |
| `description` + `candidateProfile` + `companyDescription` | `description` | Strip HTML → concat → truncate 3000 |
| `contracts` | `contract_type` | `"contractor"` → `"freelance"`, `"permanent"` → `"permanent"`. Si les deux : `"freelance"` |
| `contracts` | `tags` (append) | `f"contract:{c}"` pour chaque contrat |
| `skills[].name` | `tags` (append) | Chaque skill ajouté comme tag |
| `remoteMode` | `work_mode` | `"full"` → `"remote"`, `"partial"` → `"hybrid"`, null/absent → `"on-site"` |
| `location.country` | `base_location` | Direct |
| `location.countryCode` | `country_code` | Direct |
| `minDailySalary`/`maxDailySalary` (freelance) | `salary` | `f"{min}-{max} €/jour"` |
| `minAnnualSalary`/`maxAnnualSalary` (CDI) | `salary` | `f"{min//1000}k-{max//1000}k €/an"` |
| `minDailySalary`/`maxDailySalary` (freelance) | `salary_text` | `f"{min}-{max} €/jour"` |
| `minAnnualSalary`/`maxAnnualSalary` (CDI) | `salary_text` | `f"{min}-{max} €/an"` |
| `experienceLevel` | `summary` | `f"Niveau: {experienceLevel}."` si présent |
| `duration`/`durationPeriod`/`renewable` | `summary` (append) | Si freelance : `f"Mission: {durationValue} {durationPeriod}(s) (renouvelable)."` |
| `geo_zone` | `None` | Dérivé par le scorer |
| `company_size` | `None` | Inféré par le scorer LLM |

---

## Deduplication Logic

```python
seen_ids: set[int] = set()

for posting in api_response["hydra:member"]:
    if posting["id"] in seen_ids:
        continue
    seen_ids.add(posting["id"])
    # ... build JobPosting
```

Les offres cross-listées (`["permanent", "contractor"]`) ont le même `id` → seul le premier passage est conservé. L'array `contracts` complet est préservé dans `tags`.

---

## Validation Rules

- `title` ne doit pas être vide.
- `url` doit commencer par `https://www.free-work.com/fr/tech-it/job-mission/`.
- `contract_type` ∈ `{"freelance", "permanent"}`.
- `work_mode` ∈ `{"remote", "hybrid", "on-site"}`.
- `country_code` doit être une string ISO 3166-1 alpha-2 (2 lettres) si présente.
