# Feature Spec — Free-Work.com Scraper

> **Spec** : `specs/025-free-work-scraper/spec.md` *(confirmer le numéro contre le dossier `specs/` — Filesystem MCP indisponible à la rédaction)*
> **Statut** : Spec (prête pour `/speckit.plan` côté Claude Code)
> **Source de vérité** : `docs/recon/free-work.md` (2026-08-06)
> **Template de référence** : `scrapers/boards/hh_network.py` (Spec 023)

---

## 1. Overview

Ajouter un scraper board pour **free-work.com** via son API JSON publique (`api.free-work.com/job_postings`). Source filet-large de missions freelance et postes CDI orientés PM / PO / Agile / leadership produit, majoritairement en France, alimentant le pipeline de scoring `unified_jc`.

L'API JSON rend le scraper simple et robuste : tous les champs structurés en un appel, pas de scraping HTML, pas de page détail, pas d'auth.

---

## Clarifications

### Session 2026-08-06

- Q: Pattern exact de reconstruction d'URL publique depuis `slug` + `job.slug` ? → A: `https://www.free-work.com/fr/tech-it/job-mission/{job.slug}/{posting.slug}` (2 segments)
- Q: Format du champ `summary` pour `experienceLevel` / `duration` / `durationPeriod` / `renewable` ? → A: Texte lisible FR (`"Niveau: expert. Mission: 12 mois (renouvelable)."`)

---

## 2. Alignement constitution (contraintes dures)

- **Filet large** : aucun filtrage de désirabilité au scrape. Le scoping par slug est un choix « où chercher » (équivalent aux query terms), pas un filtre de pertinence.
- **Pas d'écriture DB** depuis le scraper : `fetch()` retourne une `list[JobPosting]`. C'est `scrape.py` qui appelle `db.save_unscored()` après dédup.
- **Fichiers à ne pas modifier** : `models.py`, `storage.py`, `main.py`, `scrape.py`, les autres scrapers. Le scraper vit dans un fichier neuf : `scrapers/boards/free_work.py`.
- **Géographie canonique** : le scraper ne fixe PAS `geo_zone`. Il passe `country_code` / `base_location` ; la dérivation reste dans `scorer.py` via `work_mode_geography` (Spec 016).
- **Tier-0 ≠ LLM** : aucun jugement de séniorité/désirabilité dans le scraper.

---

## 3. Source — détails API

| Élément | Valeur |
|---------|--------|
| Endpoint | `GET https://api.free-work.com/job_postings` |
| Format | JSON-LD / Hydra (`application/ld+json`) |
| Auth | Aucune |
| Pagination | `?page=N&itemsPerPage=M` (max 100/page) ; métadonnées dans `hydra:view` |
| Scoping | `?jobs=<slug>` — **un seul slug par requête**, itérer |
| Recherche texte | ❌ non supportée (`search`/`q`/`title` inopérants) |
| Tri | ❌ `order[publishedAt]` renvoie une erreur Hydra — ne pas utiliser |
| Total offres | ~9 948 (photographie 2026-08-06) |
| Backend | Turnover-IT (AGSI) ; front public via Varnish (`no-cache, private`) |

---

## 4. Scoping source — slugs verrouillés

**11 slugs** (décision figée) :

| Bucket | Slugs |
|--------|-------|
| Cœur PM/PO | `product-owner`, `responsable-produit`, `chef-de-projet-informatique`, `chef-de-projet-digital` |
| Agile / Leadership projet | `project-management-officer`, `scrum-master`, `directeur-de-projet`, `coach-agile` |
| Data / Transformation (PM-adjacent) | `directeur-de-la-data-cdo`, `directeur-de-la-transformation-digitale-cdo`, `manager-de-transition` |

**Volume estimé** : ~563 offres avant scoring → ~15-18 requêtes API.

**Exclus explicitement** (avec raison) :
- `consultant-moa-amoa` — BA banque/assurance à forte densité, secteur évité.
- `data-scientist`, `developpeur-ia-machine-learning` — rôles IC hands-on, mauvais type de rôle. Les missions IA de transformation/conseil sont captées par les slugs CDO/transition.
- `data-analyst`, `business-analyst`, `data-engineer`, slugs de dev pur — hors scope PM.

La liste de slugs est une **constante de config** en tête de fichier (`FREE_WORK_SLUGS`), modifiable sans toucher la logique.

---

## 5. Exigences fonctionnelles

- **FR-1** — Le scraper étend `BaseScraper`, déclare `SOURCE_NAME = "free_work"`, `ENABLED = True`, `ACQUISITION_MODEL = "board"`, `SUPPORTS_DISCOVERY = True`.
- **FR-2** — Méthode unique `fetch(self, job_filter: JobFilter) -> list[JobPosting]`.
- **FR-3** — Itérer sur `FREE_WORK_SLUGS`, un appel API par slug, paginer si `hydra:totalItems > itemsPerPage` (100/page).
- **FR-4** — Mapper chaque `hydra:member` vers `JobPosting` selon §6.
- **FR-5** — **Déduplication par `id` API** : les offres cross-listées (`permanent` + `contractor`) apparaissent plusieurs fois ; dédupliquer sur l'`id` avant de retourner.
- **FR-6** — **Rate limit** : 1 requête/seconde entre appels, petit jitter. `User-Agent: job-agent/1.0 (jceyrac@pm.me)`. Itération séquentielle, pas de parallélisme.
- **FR-7** — Nettoyer le HTML de `description` / `candidateProfile` / `companyDescription` (strip tags), concaténer, tronquer à ~3000 chars.
- **FR-8** — `contract_type` : `contractor` → `freelance`, `permanent` → `permanent`. **Sur double-contrat `["permanent","contractor"]` → `freelance`** (actionnabilité transfrontalière CH), et **préserver l'array brut dans `tags`** (ex. `contract:permanent`, `contract:contractor`) pour ne pas perdre l'info de flexibilité.
- **FR-9** — `work_mode` : `full` → `remote`, `partial` → `hybrid`, absent/`null` → `on-site`.
- **FR-10** — `experienceLevel` (`expert`/`senior`/`junior`/…) → intégré dans `summary` au format lisible FR : `"Niveau: {experienceLevel}."`. `duration`/`durationPeriod`/`renewable` pour les missions freelance → `"Mission: {durationValue} {durationPeriod}(s) (renouvelable)."` ou `"Mission: {durationValue} {durationPeriod}(s) (non renouvelable)."`. Si absent, omettre la phrase correspondante. (**pas** de nouveau champ — voir §8).
- **FR-11** — Ne PAS fixer `geo_zone` (laissé à `None`). Passer `country_code` (`location.countryCode`) et `base_location` (`location.country`).
- **FR-12** — **Résilience par slug** : l'échec d'un slug (404, timeout, JSON invalide) est loggé et n'interrompt pas les autres. Le scraper retourne ce qu'il a pu collecter.
- **FR-13** — Aucune écriture DB.

---

## 6. Mapping des champs

| API Free-Work | JobPosting | Transformation |
|---------------|------------|----------------|
| `title` | `title` | direct |
| `company.name` | `company` | direct |
| `location.label` | `location` | direct |
| `location.countryCode` | `country_code` | direct |
| `location.country` | `base_location` | direct |
| `description` + `candidateProfile` + `companyDescription` | `description` | strip HTML, concat, tronque ~3000 |
| `publishedAt` | `posted_date` | parse ISO 8601 → `date` |
| `contracts[]` | `contract_type` + `tags` | voir FR-8 |
| `remoteMode` | `work_mode` | voir FR-9 |
| `minDailySalary`/`maxDailySalary` (freelance) ou `minAnnualSalary`/`maxAnnualSalary` (CDI) | `salary` / `salary_text` | format lisible (`"120-600 €/jour"` ou `"{min}-{max} €/an"`) |
| `skills[].name` | `tags` | append aux tags contrat |
| `experienceLevel`, `duration`, `durationPeriod`, `renewable` | `summary` | résumé lisible |
| `slug` + `job.slug` | `url` | `f"https://www.free-work.com/fr/tech-it/job-mission/{job['slug']}/{posting['slug']}"` |
| — | `geo_zone` | **None** (dérivé par le scorer) |
| — | `company_size` | **None** (inféré par le scorer LLM) |

---

## 7. Approche recommandée

1. **API-first exclusif** — pas de fallback HTML, pas de scraping de page détail.
2. **Config en tête** — `FREE_WORK_SLUGS`, `ITEMS_PER_PAGE = 100`, `REQUEST_DELAY = 1.0`, `USER_AGENT`.
3. **Boucle** slug → pagination → map → accumule → dédup globale par `id`.
4. **Effort** — ~150 lignes, calqué sur `hh_network.py`.

---

## 8. Hors scope (ne pas construire ici)

- Fallback HTML / scraping DOM.
- Scraping des pages détail `free-work.com/.../job-mission/...`.
- **Champ `experience_level` + porte Tier-0 séniorité** → *spec séparée dédiée* (touche `models.py`/`storage.py`, bénéficie à tous les scrapers). Le présent scraper range `experienceLevel` dans `summary` en attendant.
- Ajout de slugs IA spécifiques (l'API ne fait pas de recherche texte ; couverture jugée suffisante via CDO/transition).

---

## 9. Items ouverts / suites

1. **Spec séniorité** : `experience_level` (enum) + gate Tier-0 junior/intern dans `scorer.py`, migration `storage.py`. À prioriser juste après ce scraper (résout ta douleur de filtrage junior sur toutes les sources).
2. **Couverture missions IA** : angle mort résiduel (missions IA sous slug consultant générique hors liste). À réévaluer sur données réelles — pas de slug spéculatif ajouté maintenant.
3. **Numéro de spec** : confirmer `025` contre `specs/`.

---

## 10. Critères d'acceptation

- [ ] `python scrape.py` découvre la source `free_work`, retourne des `JobPosting`, sans crash.
- [ ] La dédup par `id` élimine les doublons des offres cross-listées permanent/contractor.
- [ ] L'échec d'un slug isolé n'interrompt pas la collecte des autres.
- [ ] `work_mode`, `contract_type`, `salary` correctement mappés (vérif sur un échantillon).
- [ ] `geo_zone` reste `None` en sortie de scraper (dérivé par le scorer).
- [ ] Le run respecte ~1 req/s et envoie le User-Agent attendu.
- [ ] **Régression FELFEL** inchangée (`ch_hybrid`/`unified_jc` stables — le nouveau scraper n'affecte pas le scoring existant).
