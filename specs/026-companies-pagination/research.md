# Research: Pagination de la liste Companies

Aucun `NEEDS CLARIFICATION` — la description de la feature était entièrement prescriptive et un pattern canonique existe déjà dans le repo.

## Décisions

### D1 — Reproduire le mécanisme de pagination de la vue Jobs à l'identique

- **Decision**: Port direct du mécanisme de `tracker_views/jobs.py` (lignes ~241–293 et ~376–396) : selectbox « Per page » en sidebar (`[25, 50, 100, 250, "All"]`, défaut index 1 = 50), reset de page par signature de filtre, découpage `[start:end]`, contrôles Prev / indicateur / Next / « Go to ».
- **Rationale**: Pattern déjà éprouvé en production, cohérence d'UX entre les deux onglets, risque minimal (pas de nouveau design). La Constitution V impose un diff minimal.
- **Alternatives considered**:
  - `st.dataframe` — rejeté : change la disposition en cartes bordées (non-goal explicite de la spec).
  - `st.paginator` / composant tiers — rejeté : nouvelle dépendance (interdit par la Constitution, contrainte « No new dependencies »).
  - Virtualisation/lazy render — rejeté : Streamlit ne virtualise pas nativement une boucle `st.container` ; le plafonnement + pagination est le pattern idiomatique.

### D2 — Clés de session-state dédiées `co_*`

- **Decision**: `co_per_page`, `co_page`, `co_filter_sig` (et `co_page_input` pour le champ « Go to », si nécessaire).
- **Rationale**: Les clés Jobs utilisent `jobs_*` ; un préfixe distinct évite toute collision d'état entre les deux onglets (critère d'acceptation explicite). Les clés `co_*` existantes (filtres sidebar) suivent déjà ce préfixe.
- **Alternatives considered**: réutiliser `jobs_page` — rejeté : couplage inter-onglets et collisions.

### D3 — La signature de filtre couvre tout le jeu de contrôles sidebar + `per_page`

- **Decision**: Le tuple de signature inclut `exclude_bl`, `status_filter`, `search`, `country_filter`, `sector_filter`, `size_filter`, `min_jobs`, `last_ix_choice`, `sort_by`, `mon_status_filter`, et `per_page`.
- **Rationale**: C'est l'ensemble exact qui pilote déjà `load_companies(...)`, les deux filtres in-memory, et `companies.sort(...)` — le même ensemble qui change le jeu de résultats. Inclure `per_page` garantit le reset quand la taille de page change (exigence explicite de la spec).
- **Alternatives considered**: exclure `per_page` — rejeté : un changement de taille de page laisserait potentiellement une page courante hors bornes.

### D4 — Découpage appliqué en aval de tout le travail in-memory existant

- **Decision**: Le slice est inséré après `monitoring_status`, après le filtre « Never interacted », et après `companies.sort(...)`, immédiatement avant la boucle de rendu. La variable `companies` (liste filtrée complète) reste intacte pour le caption d'en-tête et le sélecteur de bulk action.
- **Rationale**: Garantit que l'en-tête et le bulk action opèrent sur le jeu filtré complet (exigence « CRITICAL ORDERING » de la spec), et que la pagination porte sur la liste finale triée.
- **Alternatives considered**: paginer avant le tri — rejeté : le tri doit porter sur le jeu complet pour que la pagination soit stable et correcte.
