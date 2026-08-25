# Tasks: Pagination de la liste Companies

**Input**: Design documents from `/specs/026-companies-pagination/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: Non requis (feature view-layer, aucune modification de `storage.py`/`models.py` ; validation manuelle via `quickstart.md`).

**Organization**: Tâches regroupées par user story. **Toutes les tâches modifient le même fichier** (`tracker_views/companies.py`) — c'est un changement cohérent et séquentiel, pas un travail parallélisable par fichier. Les labels `[USn]` indiquent la valeur livrée par chaque tâche ; l'ordre d'exécution est strict.

**Fichier canonique de référence** : `tracker_views/jobs.py` — selectbox `Per page` (~l. 241–245), reset par signature (~l. 267–280), découpage (~l. 282–293), contrôles Prev/Next/« Go to » (~l. 376–396).

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Aucun nouveau fichier ni dépendance — confirmation du périmètre.

- [X] T001 [P] Confirmer le périmètre et lire le pattern canonique : aucune nouvelle dépendance ni nouveau fichier ; seul `tracker_views/companies.py` est modifié. Relever les lignes de référence dans `tracker_views/jobs.py` (selectbox ~241–245, signature/reset ~267–280, slice ~282–293, contrôles ~376–396) et le point d'insertion dans `tracker_views/companies.py` (après `companies.sort(...)`, avant la boucle de rendu `for c in companies:`).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Le selectbox `Per page` est le prérequis partagé par toutes les stories.

- [X] T002 Ajouter le selectbox « Per page » dans la sidebar de `_render_list()` dans `tracker_views/companies.py` (après le selectbox « Sort by »), options `[25, 50, 100, 250, "All"]`, `index=1` (défaut 50), clé `co_per_page`.

**Checkpoint**: Le selectbox apparaît dans la sidebar ; la page continue de rendre toutes les cartes (aucun découpage encore).

---

## Phase 3: User Story 1 - Chargement responsive (Priority: P1) 🎯 MVP

**Goal**: Plafonner le rendu à une page (50 cartes par défaut).

**Independent Test**: Avec 300+ entreprises, ouvrir Companies → 50 cartes rendues, la recherche par nom reste fluide.

- [X] T003 [US1] Dans `tracker_views/companies.py`, après `companies.sort(...)` et avant le bloc « Header », ajouter le calcul de pagination : `total = len(companies)` ; si `per_page == "All"` → `page_companies = companies`, `n_pages = 1`, `current_page = 1` ; sinon `n_pages = max(1, (total + per_page - 1) // per_page)`, `current_page = max(1, min(st.session_state.get("co_page", 1), n_pages))`, puis `page_companies = companies[start:end]`.
- [X] T004 [US1] Dans `tracker_views/companies.py`, remplacer la boucle de rendu `for c in companies:` par `for c in page_companies:`. Le caption d'en-tête et le sélecteur de bulk action continuent d'utiliser `companies` (jeu filtré complet).

**Checkpoint**: 50 cartes max rendues ; le caption « N companies · M open jobs » affiche le total filtré complet ; le bulk action liste toutes les entreprises filtrées.

---

## Phase 4: User Story 2 - Navigation entre pages (Priority: P1)

**Goal**: Naviguer entre pages via Prev / indicateur / Next / « Go to ».

**Independent Test**: Avec 250 entreprises et `Per page` = 50, naviguer page 1 → 2 → 5 affiche les bons groupes de cartes ; contrôles absents si 1 page ou « All ».

- [X] T005 [US2] Dans `tracker_views/companies.py`, insérer les contrôles de pagination immédiatement avant la boucle de rendu, gated sur `per_page != "All" and n_pages > 1` : `st.columns([1, 2, 1, 1])` avec bouton « ◀ Prev » (disabled si `current_page <= 1`, met à jour `co_page = current_page - 1` + `st.rerun()`), `st.caption("Page {current_page} of {n_pages} · showing {len(page_companies)} of {total} companies")`, bouton « Next ▶ » (disabled si `current_page >= n_pages`), et `st.number_input("Go to", min_value=1, max_value=n_pages, value=current_page, key="co_page_input", label_visibility="collapsed")` (si différent de `current_page` → `co_page = int(...)` + `st.rerun()`).

**Checkpoint**: Navigation Prev/Next/« Go to » fonctionne ; indicateur de page correct ; contrôles masqués pour « All » ou une seule page.

---

## Phase 5: User Story 3 - Reset à la page 1 (Priority: P2)

**Goal**: Tout changement de filtre/recherche/tri/per_page ramène à la page 1.

**Independent Test**: Aller en page 3 puis changer un filtre → retour page 1 ; aucune page vide.

- [X] T006 [US3] Dans `tracker_views/companies.py`, construire le tuple de signature `sig` couvrant tous les contrôles de la sidebar (`exclude_bl`, `status_filter`, `search`, `country_filter`, `sector_filter`, `size_filter`, `min_jobs`, `last_ix_choice`, `sort_by`, `mon_status_filter`) **plus** `per_page`, puis : si `st.session_state.get("co_filter_sig") != sig`, alors `st.session_state["co_page"] = 1` et `st.session_state["co_filter_sig"] = sig`. Insérer ce bloc avant le calcul de slice (dans le même passage que T003).

**Checkpoint**: Changement de n'importe quel filtre/recherche/tri/per_page depuis une page > 1 ramène à la page 1, sans page vide.

---

## Phase 6: User Story 4 - « All » restaure le comportement non paginé (Priority: P3)

**Goal**: « All » affiche tout sans contrôles, comme avant la feature.

**Independent Test**: « Per page » = « All » → toutes les entreprises filtrées rendues, aucun contrôle Prev/Next/« Go to ».

- [X] T007 [US4] Vérifier/compléter la branche « All » dans `tracker_views/companies.py` : `page_companies = companies`, `n_pages = 1`, `current_page = 1`, contrôles absents (gating T005), et l'en-tête + bulk action continuent d'opérer sur le jeu filtré complet (déjà garanti par T004 — confirmer, pas de code dupliqué).

**Checkpoint**: « All » reproduit exactement l'ancien comportement (liste complète, sans contrôles).

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Validation manuelle et non-régression.

- [X] T008 Exécuter la validation manuelle de `quickstart.md` (6 scénarios) : chargement responsive + défaut 50, navigation, reset à la page 1, « All », en-tête/bulk = jeu complet, non-régression Jobs. Confirmer qu'aucune clé `jobs_*` n'est touchée et qu'aucun fichier autre que `tracker_views/companies.py` n'a été modifié.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** : aucune dépendance.
- **Foundational (Phase 2)** : dépend de Setup. **Bloque** toutes les stories.
- **User Stories (Phase 3–6)** : dépendent du selectbox (T002). Ordre d'exécution **strict** dans le fichier : T006 (reset) s'écrit physiquement avant T003 (slice) dans le même passage, car `co_page` est lu par le slice.
- **Polish (Phase 7)** : dépend de toutes les stories.

### User Story Dependencies

- **US1 (P1)** : dépend de T002. Indépendant des autres stories pour la valeur livrée, mais nécessite T006 pour le reset correct.
- **US2 (P1)** : dépend de T003 (a besoin de `n_pages`, `current_page`, `page_companies`).
- **US3 (P2)** : dépend de T002 ; physiquement inséré avant le slice (T003).
- **US4 (P3)** : couvert par la branche « All » déjà écrite en T003/T005 — tâche de vérification.

### Within Each User Story

- Selectbox (T002) avant tout.
- Signature/reset (T006) avant slice (T003).
- Slice (T003) avant contrôles (T005) et changement de boucle (T004).
- Validation (T008) en dernier.

### Parallel Opportunities

- Aucun parallélisme réel : **un seul fichier**, ordre séquentiel strict. La tâche T001 (lecture du pattern) peut s'exécuter en parallèle avec rien d'autre — elle précède l'édition.

---

## Parallel Example: User Story 1

```text
# Aucune exécution parallèle : tout le travail modifie tracker_views/companies.py
# et doit être appliqué en séquence. Appliquer T002 → T006 → T003 → T004 → T005 → T007 → T008.
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. T001 (contexte) + T002 (selectbox).
2. T006 + T003 + T004 → 50 cartes plafonnées, en-tête/bulk intacts.
3. **STOP et VALIDER** : ouvrir Companies, vérifier 50 cartes + fluidité recherche.
4. Démo si satisfaisant.

### Incremental Delivery

1. T002 → selectbox.
2. T006 + T003 + T004 → plafonnement (MVP).
3. T005 → navigation.
4. T007 → « All » confirmé.
5. T008 → validation manuelle complète.

### Notes

- Toutes les tâches modifient `tracker_views/companies.py` ; committer une seule fois (ou par groupe logique) après validation.
- Respecter les clés de session `co_per_page`, `co_page`, `co_filter_sig`, `co_page_input` — ne jamais réutiliser `jobs_*`.
- Le découpage doit être appliqué **après** le filtre `monitoring_status`, le filtre « Never interacted » et `companies.sort(...)`, **avant** la boucle de rendu.
