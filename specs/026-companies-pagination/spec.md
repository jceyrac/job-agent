# Feature Specification: Pagination de la liste Companies

**Feature Branch**: `026-companies-pagination`

**Created**: 2026-08-25

**Status**: Draft

**Input**: User description: "Add pagination to the Companies list view to fix a performance problem, mirroring the pattern already proven in the Jobs view."

**Design authority**: `tracker_views/jobs.py` — la pagination de la vue Jobs (selectbox « Per page » en sidebar, reset par signature de filtre, découpage de page, contrôles Prev / Next / « Go to ») est la référence comportementale à reproduire à l'identique.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Chargement responsive de la liste Companies (Priority: P1)

Un utilisateur ouvre l'onglet Companies avec plusieurs centaines d'entreprises en base. Aujourd'hui, la page gèle au chargement et le champ « Search by name » devient inutilisable, car chaque rerun re-rend l'intégralité des cartes. Après la modification, la page n'affiche qu'une page de 50 cartes par défaut : le chargement est rapide et la recherche par nom répond immédiatement.

**Why this priority**: C'est la raison d'être de la feature — éliminer le gel du rendu en plafonnant le nombre de cartes rendues par rerun. C'est le point de départ sans lequel le reste n'a pas de sens.

**Independent Test**: Sur une base avec 300+ entreprises, ouvrir Companies : la page affiche 50 cartes, et la saisie dans « Search by name » ne gèle plus.

**Acceptance Scenarios**:

1. **Given** une base avec plusieurs centaines d'entreprises, **When** l'utilisateur ouvre Companies, **Then** seules 50 cartes sont rendues et la page charge sans gel perceptible.
2. **Given** la page Companies chargée, **When** l'utilisateur tape dans « Search by name », **Then** la frappe reste fluide (pas de re-rendu de toutes les cartes à chaque caractère).

---

### User Story 2 - Navigation entre pages (Priority: P1)

L'utilisateur veut parcourir l'ensemble des entreprises filtrées sans dégrader la réactivité. Des contrôles Prev / indicateur de page / Next / « Go to » lui permettent de changer de page, visibles uniquement quand il y a plus d'une page et que « Per page » ≠ « All ».

**Why this priority**: La pagination n'a de valeur que si l'on peut naviguer ; c'est le cœur fonctionnel, au même niveau que le plafonnement.

**Independent Test**: Avec 250 entreprises et « Per page » = 50, naviguer page 1 → 2 → 5 via Prev / Next / « Go to » affiche les bons groupes de cartes.

**Acceptance Scenarios**:

1. **Given** un jeu filtré de plusieurs pages, **When** l'utilisateur clique « Next ▶ », **Then** la page suivante s'affiche et l'indicateur « Page X of Y » reflète la nouvelle position.
2. **Given** le jeu filtré tient sur une seule page (ou « Per page » = « All »), **When** l'utilisateur consulte la page, **Then** les contrôles de pagination sont absents.
3. **Given** un numéro saisi dans « Go to », **When** l'utilisateur le modifie, **Then** la page cible s'affiche.

---

### User Story 3 - Retour à la page 1 lors d'un changement de filtre, recherche, tri ou taille de page (Priority: P2)

Tout changement du jeu de résultats (filtres, recherche par nom, tri, ou taille de page) doit ramener l'affichage à la première page, pour ne jamais rester bloqué sur une page désormais vide ou inexistante.

**Why this priority**: Sans ce reset, naviguer puis filtrer produit des « pages vides » incohérentes (rester en page 5 d'un jeu qui n'a plus que 2 pages).

**Independent Test**: Aller en page 3, puis changer un filtre : l'affichage revient à la page 1.

**Acceptance Scenarios**:

1. **Given** l'utilisateur est en page > 1, **When** il change un filtre (statut, pays, secteur, taille, min jobs, dernière interaction, monitoring), **Then** l'affichage revient à la page 1.
2. **Given** l'utilisateur est en page > 1, **When** il modifie la recherche par nom, le tri, ou « Per page », **Then** l'affichage revient à la page 1.
3. **Given** un filtre réduit le jeu sous la page courante, **When** le reset s'applique, **Then** aucune « page vide » n'est affichée.

---

### User Story 4 - « All » restaure le comportement non paginé (Priority: P3)

Sélectionner « All » dans « Per page » affiche toutes les entreprises filtrées sur une seule page, sans contrôles de pagination, comme avant la feature.

**Why this priority**: C'est le chemin de compatibilité — l'utilisateur peut retrouver l'ancien comportement complet à tout moment.

**Independent Test**: Choisir « All » : toutes les entreprises filtrées s'affichent, aucun contrôle Prev / Next / « Go to ».

**Acceptance Scenarios**:

1. **Given** « Per page » = « All », **When** l'utilisateur consulte la liste, **Then** toutes les entreprises filtrées sont rendues et aucun contrôle de pagination n'apparaît.

---

### Edge Cases

- **Jeu filtré vide** : le message « No companies match the current filters. » s'affiche ; aucun contrôle de pagination, aucun découpage.
- **Per page = « All »** : pas de découpage, pas de contrôles de pagination, page courante forcée à 1.
- **Page courante hors bornes** (ex. après suppression d'entreprises en base) : la page courante est bornée à `[1, n_pages]` sans erreur.
- **n_pages == 1** (jeu ≤ taille de page) : aucun contrôle de pagination affiché.
- **Bulk status change** : le sélecteur « Select companies » liste toutes les entreprises du jeu filtré (pas seulement la page visible) ; l'application du changement opère sur le jeu filtré complet.
- **Header count** : le caption « N companies · M open jobs across them » reflète le jeu filtré complet, indépendamment de la page affichée.
- **Collision d'état de session** : les clés Companies (`co_*`) et Jobs (`jobs_*`) sont distinctes — naviguer dans Jobs n'affecte pas la pagination Companies, et inversement.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: La vue Companies doit exposer un selectbox « Per page » dans la sidebar, avec les options `[25, 50, 100, 250, "All"]` et le défaut `50` (index 1), identique à la vue Jobs.
- **FR-002**: Le découpage de page doit être appliqué sur la liste finale — après le filtre `monitoring_status`, après le filtre « Never interacted », et après `companies.sort(...)` — immédiatement avant la boucle de rendu des cartes.
- **FR-003**: Des contrôles de pagination (Prev / indicateur « Page X of Y · showing A of B companies » / Next / « Go to ») doivent être rendus uniquement lorsque « Per page » ≠ « All » et que le nombre de pages est > 1, avec le même comportement et la même ergonomie que la vue Jobs.
- **FR-004**: Le reset à la page 1 doit s'opérer via une signature de filtre en session-state (mécanisme identique à `jobs_filter_sig` / `jobs_page`), recalculée quand un filtre, la recherche par nom, le tri, ou « Per page » change.
- **FR-005**: La pagination Companies doit utiliser des clés de session-state dédiées (ex. `co_per_page`, `co_page`, `co_filter_sig`), sans jamais partager ni écraser les clés Jobs (`jobs_per_page`, `jobs_page`, `jobs_filter_sig`).
- **FR-006**: Le caption d'en-tête (« N companies · M open jobs across them ») doit continuer de refléter le jeu filtré complet (N = total filtré, M = somme des jobs), pas la seule page visible.
- **FR-007**: Le sélecteur de bulk action et l'application du changement de statut doivent opérer sur le jeu filtré complet, pas sur la seule page visible.
- **FR-008**: La mise en page des cartes (conteneur bordé, deux colonnes, ~6 widgets markdown/caption) et les liens `/company_detail?id=…` doivent rester inchangés.
- **FR-009**: Aucune modification de `load_companies()`, `get_companies()`, `storage.py`, ni de la couche SQL/données — changement purement au niveau de la vue.
- **FR-010**: Aucune dépendance nouvelle ; Streamlit natif uniquement.
- **FR-011**: Aucune modification de la logique de filtrage ni de tri existante — le découpage s'ajoute en aval, sans altérer le calcul des listes.

### Key Entities

Aucune entité de données n'est créée ni modifiée. La feature porte uniquement sur le rendu : l'unité concernée est la **Company (ligne d'entreprise)** déjà produite par `load_companies()` — dict en mémoire inchangé, dont seule la quantité rendue par rerun est plafonnée.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Avec un jeu de plusieurs centaines d'entreprises, la page Companies se charge et la saisie dans « Search by name » reste réactive (aucun gel perceptible de plusieurs secondes).
- **SC-002**: La vue par défaut affiche 50 cartes ; Prev / Next / « Go to » naviguent correctement entre les pages ; « All » restaure l'affichage complet non paginé.
- **SC-003**: Tout changement de filtre, de recherche, de tri ou de « Per page » ramène à la page 1 (aucune page vide résiduelle).
- **SC-004**: Le caption d'en-tête et le sélecteur de bulk action listent toutes les entreprises correspondantes, pas seulement la page courante.
- **SC-005**: La pagination de la vue Jobs reste inchangée (clés de session-state indépendantes).

---

## Non-Goals *(explicit — Constitution V)*

- **Pas de remplacement** de la liste par `st.dataframe` ni par un autre widget — la disposition en cartes bordées existante est conservée.
- **Pas de modification** de `load_companies()`, `get_companies()`, `storage.py`, ni de la couche SQL/données.
- **Pas de modification** de `tracker_views/jobs.py` ni d'aucune autre vue.
- **Pas de nouvelle dépendance** (Streamlit natif uniquement).
- **Pas de changement** des filtres, du tri, ni de la navigation `/company_detail`.

---

## Assumptions

- **Portage direct** : la vue Jobs (`tracker_views/jobs.py`, lignes ~241–293 et ~377–396) est la référence exacte ; le comportement et l'UX sont reproduits tels quels (mêmes options, même défaut, même libellé « Per page », même structure des contrôles Prev / Next / « Go to »).
- **Clés de session** : préfixe `co_` pour les clés Companies afin d'éviter toute collision avec `jobs_*`.
- **Signature de filtre** : inclut tous les champs de la sidebar Companies (exclude blacklisted, status, search, country, sector, size, min jobs, last interaction, sort, monitoring status) plus `per_page` — c'est le même ensemble qui pilote déjà le rendu.
- **Pas d'impact sur les tests de stockage** : feature purement au niveau de la vue, `storage.py` et `models.py` intacts, donc `tests/test_storage.py` n'est pas concerné.
- **Dev uniquement sur Mac** ; jamais de déploiement vers verva pendant ce cycle (déploiement via git pull + deploy.sh après implémentation et validation).
