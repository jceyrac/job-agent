# Research — Free-Work.com Scraper

**Date**: 2026-08-06  
**Source**: `docs/recon/free-work.md` (reconnaissance technique complète)

---

## Decision Log

### D1: API JSON vs SSR HTML

**Decision**: API JSON (`api.free-work.com/job_postings`) exclusivement.

**Rationale**: L'API JSON-LD/Hydra fournit tous les champs structurés (description complète, skills, salary, remoteMode, location structurée, company) en un seul appel, sans auth. Le SSR HTML nécessiterait 1 appel par page + 1 appel par détail (≈ 600 requêtes vs ≈ 20).

**Alternatives considered**:
- SSR HTML + BeautifulSoup : rejeté — 30x plus de requêtes, parsing fragile (classes Tailwind/Vue, `data-v-*` hashes instables).
- Fallback HTML si API down : rejeté — complexité inutile. Si l'API est down, Free-Work est probablement down.

### D2: Pagination strategy

**Decision**: `itemsPerPage=100` (max testé), itération page par page jusqu'à épuisement (`len(member) < itemsPerPage`).

**Rationale**: 100 items/page minimise le nombre de requêtes. À 563 offres sur 11 slugs, la plupart des slugs tiennent en 1 page. Seuls les slugs volumineux (>100 résultats) nécessitent une 2e page.

**Alternatives considered**:
- `itemsPerPage` plus petit (50) : rejeté — double le nombre de requêtes sans bénéfice.
- `itemsPerPage` > 100 : non testé, risqué.

### D3: Déduplication par `id` API

**Decision**: Déduplication sur l'`id` numérique de l'API (`posting["id"]`) avant de construire les `JobPosting`. Set Python en mémoire.

**Rationale**: Les offres cross-listées (`permanent` + `contractor`) apparaissent avec le même `id` mais des `contracts` différents. L'`id` API est l'identifiant canonique côté Turnover-IT.

**Alternatives considered**:
- Déduplication par URL : rejeté — l'URL est reconstruite, pas native.
- Déduplication par `slug` : rejeté — le slug pourrait théoriquement être réutilisé.

### D4: Rate limit

**Decision**: 1 requête/seconde entre slugs, pas de délai entre pages d'un même slug.

**Rationale**: Aucun rate-limit détecté dans les headers de réponse (pas de `X-RateLimit-*`, Varnish avec `no-cache, private`). Le délai de 1s est purement courtois. Le volume total (~20 requêtes) rend un délai plus agressif inutile.

**Alternatives considered**:
- 0.5s entre requêtes : acceptable mais pas nécessaire.
- Parallélisme : rejeté — 20 requêtes séquentielles = 20s, le parallélisme n'apporte rien.

### D5: Pas de fallback HTML

**Decision**: Aucun scraping HTML, aucune page détail. API-first uniquement.

**Rationale**: L'API fournit déjà `description` (HTML complet), `candidateProfile`, `companyDescription`. Les pages détail n'apportent aucune information supplémentaire. Le scraping HTML ajouterait fragilité et complexité pour zéro gain.

---

## API Confirmation

| Test | Résultat |
|------|----------|
| GET `/job_postings?page=1&itemsPerPage=100` | ✅ 200, JSON-LD valide |
| GET `/job_postings?contracts=contractor` | ✅ 7 298 offres |
| GET `/job_postings?contracts=permanent` | ✅ 5 193 offres |
| GET `/job_postings?remoteMode=full` | ✅ 213 offres |
| GET `/job_postings?jobs=product-owner` | ✅ 102 offres |
| GET `/job_postings?page=546&itemsPerPage=100` | ✅ Dernière page avec contenu |
| GET `/job_postings?order[publishedAt]=desc` | ❌ Erreur Hydra (non-scalar value) |
| GET `/job_postings?search=test` | ❌ Paramètre ignoré (retourne tout) |
| `hydra:totalItems` | 9 948 (instantané 2026-08-06) |
| Rate-limit headers | Aucun (`X-RateLimit-*`) |
| Auth required | Aucune |

---

## Risk Assessment

| Risque | Probabilité | Impact | Mitigation |
|--------|-------------|--------|------------|
| API down / maintenance | Faible | Scraper skip silencieux | FR-12 : résilience par slug, échec isolé |
| Changement de schéma API | Faible | Scraper cassé | API Hydra stable (Turnover-IT/AGSI). Monitoring via pipeline runs. |
| Slugs renommés/supprimés | Faible | 0 résultat pour ce slug | Idem FR-12, log warning |
| Volume réel >> estimé | Faible | Plus de requêtes | Pagination automatique jusqu'à épuisement |
| Blocage IP / rate-limit introduit | Très faible | Scraper bloqué | User-Agent identifiable, délai courtois. Si introduit → ajouter `time.sleep()` plus long. |
