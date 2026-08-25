# Data Model: Pagination de la liste Companies

Aucune entité de base de données n'est créée, modifiée, ni supprimée. Aucun changement de schéma, aucun changement de `load_companies()`, `get_companies()`, ni de `storage.py`.

Le seul « état » introduit est l'état de vue en `st.session_state`.

## État de session (vue uniquement)

| Clé | Type | Valeur par défaut | Rôle |
|-----|------|-------------------|------|
| `co_per_page` | `int \| "All"` | `50` (index 1 du selectbox) | taille de page, pilotée par le selectbox « Per page » |
| `co_page` | `int` | `1` | page courante |
| `co_filter_sig` | `tuple` | `None` (initialisé au premier rerun) | signature du jeu de contrôles sidebar + `per_page`, pour le reset à la page 1 |
| `co_page_input` | `int` | `current_page` | valeur du champ « Go to » (clé de widget du `st.number_input`) |

Les clés de filtres sidebar existantes (`co_exclude_bl`, `co_status`, `co_search`, `co_country`, `co_sector`, `co_size`, `co_min_jobs`, `co_last_ix`, `co_sort`, `co_mon_status`, `co_bulk`, `co_bulk_status`) sont inchangées.

## Valeurs dérivées (calculées à chaque rerun, non persistées)

| Valeur | Formule / règle |
|--------|-----------------|
| `companies` (liste filtrée complète) | `load_companies(...)` → filtre `monitoring_status` → filtre « Never interacted » → `sort(...)` (inchangé) |
| `sig` | tuple des contrôles sidebar + `per_page` |
| `total` | `len(companies)` |
| `n_pages` | `1` si `per_page == "All"` ; sinon `max(1, (total + per_page - 1) // per_page)` |
| `current_page` | `1` si `per_page == "All"` ; sinon borné `max(1, min(st.session_state.co_page, n_pages))` |
| `page_companies` | `companies` si `per_page == "All"` ; sinon `companies[start:end]` avec `start = (current_page-1)*per_page`, `end = start + per_page` |

## Règle de reset (invariant)

À chaque rerun, si `st.session_state.get("co_filter_sig") != sig`, alors `co_page` est remis à `1` et `co_filter_sig` est mis à jour. Ceci garantit que tout changement de filtre, de recherche, de tri, ou de taille de page ramène à la page 1.

## Consommateurs

- **Caption d'en-tête** et **bulk action** : utilisent `companies` (liste filtrée complète), jamais `page_companies`.
- **Boucle de rendu des cartes** : itère sur `page_companies` uniquement.
