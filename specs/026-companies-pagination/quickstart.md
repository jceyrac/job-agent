# Quickstart / Validation: Pagination de la liste Companies

Guide de vérification manuelle (feature view-layer, aucun test automatisé requis). Voir [spec.md](./spec.md) pour les critères d'acceptation complets.

## Prérequis

- Environnement dev Mac : `python -m streamlit run tracker.py` (venv `.venv/`).
- Base `data/jobs.db` avec un jeu d'entreprises suffisant (plusieurs centaines idéalement ; à défaut, réduire « Per page » à 25 pour forcer plusieurs pages).

## Scénarios de validation

### 1. Chargement responsive + défaut 50

1. Ouvrir l'onglet **Companies**.
2. **Attendu** : le selectbox « Per page » apparaît dans la sidebar, valeur `50` ; seules 50 cartes (ou moins) sont rendues.
3. Taper dans **« Search by name »** : la frappe reste fluide (pas de re-rendu complet de toutes les cartes).

### 2. Navigation

1. Avec un jeu > 50 (ou réduire « Per page » à 25), cliquer **« Next ▶ »** → page suivante ; l'indicateur « Page X of Y · showing A of B companies » se met à jour.
2. Cliquer **« ◀ Prev »** → page précédente.
3. Saisir un numéro dans **« Go to »** → la page cible s'affiche.
4. Vérifier que **« Prev »** est désactivé en page 1 et **« Next »** désactivé en dernière page.

### 3. Reset à la page 1

1. Aller en page ≥ 2.
2. Changer un filtre (status, country, sector, size, min jobs, last interaction, monitoring), la recherche, le tri, ou « Per page ».
3. **Attendu** : l'affichage revient à la page 1 (aucune page vide).

### 4. « All »

1. Choisir **« Per page » = « All »**.
2. **Attendu** : toutes les entreprises filtrées sont rendues, aucun contrôle Prev / Next / « Go to ».

### 5. En-tête et bulk action = jeu filtré complet

1. Avec un jeu > une page, vérifier que le caption **« N companies · M open jobs across them »** affiche le total filtré (N > taille de la page affichée).
2. Ouvrir **« Bulk status change »** : le sélecteur « Select companies » liste toutes les entreprises filtrées, pas seulement la page courante.

### 6. Non-régression Jobs

1. Ouvrir l'onglet **Jobs**, naviguer ses pages.
2. Vérifier que la pagination Jobs est inchangée et qu'aucune clé d'état n'interfère entre les deux onglets.

## Issue connue possible

- Si la base contient < 50 entreprises, les contrôles de pagination sont absents (comportement correct) : utiliser un jeu plus grand ou « Per page » = 25 pour exercer la navigation.
