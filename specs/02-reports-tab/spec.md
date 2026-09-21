# Feature Specification: Onglet Reports — exports CSV téléchargeables (registre de presets)

**Feature Branch**: `025-reports-tab`

**Created**: 2026-08-21

**Status**: Draft

**Input**: User description: "Ajouter un onglet 'Reports' à tracker.py qui génère des exports CSV téléchargeables depuis le navigateur, organisé autour d'un registre de presets extensible. Le premier preset réutilise la logique validée du script d'export existant."

**Design authority**: `specs/025-reports-tab/mockup.html` — source de vérité visuelle (agencement, libellés, ordre). Traduit en composants Streamlit natifs uniquement, sans CSS custom ni HTML injecté.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Générer et télécharger un export CSV depuis le navigateur (Priority: P1)

L'utilisateur doit produire régulièrement un fichier CSV depuis ses données de suivi. Aujourd'hui il se connecte en SSH au serveur (verva), exécute un script en ligne de commande, puis rapatrie le fichier par scp. Avec l'onglet Reports, il ouvre le tracker déjà accessible en permanence dans son navigateur, choisit un type d'export, la période et les statuts, génère un aperçu, puis clique sur « Télécharger » pour récupérer le CSV directement — sans SSH, sans scp, sans écriture disque.

**Why this priority**: C'est la raison d'être de la feature — produire un export depuis l'interface, en supprimant l'aller-retour SSH/scp vers le serveur, quel que soit le type d'export.

**Independent Test**: Sur la DB live, un profil onboardé ouvre Reports, sélectionne le premier preset, Mois = 2026-08, Statuts = [applied], génère l'aperçu, et télécharge un CSV `jobs_2026-08.csv` encodé utf-8-sig, identique en contenu à celui produit par la CLI existante pour les mêmes paramètres.

**Acceptance Scenarios**:

1. **Given** un utilisateur onboardé et des jobs `applied` en août 2026, **When** il ouvre l'onglet Reports, choisit le premier preset, Mois = 2026-08, Statuts = [applied] et clique « Générer l'aperçu », **Then** l'aperçu s'affiche (metrics + tableau) et un bouton « Télécharger … » fournit le CSV attendu.
2. **Given** le mode « Plage de dates » avec from = 2026-05-15 et to = 2026-06-14, **When** l'aperçu est généré, **Then** le fichier téléchargé est nommé selon la convention de plage du preset.
3. **Given** l'utilisateur n'a pas encore cliqué « Générer l'aperçu », **When** il consulte l'onglet Reports, **Then** les metrics, le tableau et le bouton de téléchargement sont absents ou vides (aucune génération anticipée).

---

### User Story 2 - Aperçu et vérification avant téléchargement (Priority: P2)

Avant de télécharger, l'utilisateur veut vérifier ce qui sera exporté : combien d'entrées, leur répartition, et quelles lignes (date, entreprise, poste, résultat) sont incluses. Trois metrics (Entrées + décompte par résultat) et un tableau d'aperçu lui donnent cette visibilité en un coup d'œil.

**Why this priority**: Le téléchargement est précédé d'une vérification visuelle — l'utilisateur ne télécharge pas un CSV aveuglément.

**Independent Test**: Sur une période contenant 6 entrées « en suspens » et 3 « négatif », l'aperçu affiche Entrées = 9, En suspens = 6, Négatif = 3, et un tableau listant les 9 lignes.

**Acceptance Scenarios**:

1. **Given** une période avec 6 résultats « en suspens » et 3 « négatif », **When** l'aperçu est généré, **Then** les metrics affichent Entrées = 9, En suspens = 6, Négatif = 3.
2. **Given** un aperçu généré, **When** l'utilisateur consulte le tableau, **Then** chaque ligne montre date, entreprise, poste et résultat.
3. **Given** une colonne du preset ne peut pas être déduite de la base, **When** l'utilisateur consulte la page, **Then** une note (caption) signale que cette colonne est à compléter à la main.

---

### User Story 3 - La CLI d'export existante reste identique (non-régression) (Priority: P2)

Le refactor en fonctions importables ne doit rien changer au comportement du script en ligne de commande, déjà validé : mêmes flags, même période par défaut, même mapping de colonnes, même format de fichier. Un opérateur qui lance la CLI obtient exactement le même CSV qu'avant le refactor.

**Why this priority**: Le script CLI est le chemin validé et déjà en production ; toute régression casserait un usage existant sans filet de sécurité automatique.

**Independent Test**: `python export_jobs.py --month 2026-05` sur une DB connue produit un fichier byte-identique (même contenu) à celui produit par la version pré-refactor, et le résumé terminal est inchangé.

**Acceptance Scenarios**:

1. **Given** le script pré-refactor et une DB de référence, **When** on exécute la CLI après refactor, **Then** le CSV produit est byte-identique (mêmes octets, même encodage utf-8-sig) et le résumé terminal est identique.
2. **Given** les flags `--from`/`--to`/`--statuses`, **When** on exécute la CLI après refactor, **Then** leur comportement (priorité, défauts, choix valides) est inchangé.
3. **Given** une DB absente, **When** on exécute la CLI, **Then** elle affiche l'erreur habituelle et sort avec code 1, comme avant.

---

### User Story 4 - Registre de presets extensible (Priority: P3)

Le futur ajout d'un nouvel export doit être trivial : écrire une fonction `generate(...)` + ajouter une entrée dans un registre, sans toucher au reste de l'onglet Reports. Le selectbox « Type d'export » se remplit automatiquement depuis ce registre.

**Why this priority**: C'est l'infrastructure qui donne sa valeur à long terme à la feature ; elle débloque tout futur type d'export sans travail d'UI.

**Independent Test**: Ajouter un preset factice (label + fonction `generate`) au registre le fait apparaître dans le selectbox « Type d'export » sans autre modification de l'UI.

**Acceptance Scenarios**:

1. **Given** le registre contient une entrée, **When** on ouvre Reports, **Then** son label apparaît dans le selectbox « Type d'export ».
2. **Given** un preset expose `generate(...)` → `(rows, filename)`, **When** l'utilisateur le sélectionne et génère, **Then** les rows et le nom de fichier renvoyés pilotent l'aperçu et le téléchargement sans logique spécifique dans l'UI.

---

### Edge Cases

- **Période sans résultat** : metrics à zéro, tableau vide, bouton « Télécharger » désactivé, et un message informatif (aucun fichier CSV vide n'est généré ni proposé).
- **DB absente / inaccessible** : la page affiche une erreur claire, sans crash de l'app.
- **Statuts multiples** : le décompte par résultat reflète le mapping du preset, quel que soit l'ordre des statuts cochés.
- **work_mode NULL ou `unknown`** : le poste apparaît sans suffixe entre parenthèses.
- **location NULL** : l'entreprise apparaît seule (pas de « — » terminal).
- **notes multilignes** : les sauts de ligne du motif sont préservés dans le CSV (échappement standard).
- **mode Mois vs Plage** : le nom de fichier téléchargé suit la convention du mode actif.
- **Plage inversée** (from > to) : l'aperçu renvoie zéro entrée avec un retour visuel, sans crash.

---

## Requirements *(mandatory)*

### Functional Requirements

#### Refactor du script d'export existant (sans changement de comportement CLI)

- **FR-001**: La logique de requête + formatage du script d'export existant doit être extraite en une fonction importable `build_export_rows(db_path, date_from, date_to, statuses) -> list[dict]`, produisant les lignes formatées du preset.
- **FR-002**: La sérialisation CSV doit être extraite en une fonction importable `rows_to_csv_bytes(rows) -> bytes`, encodée en UTF-8 avec BOM (`utf-8-sig`).
- **FR-003**: `main()` doit devenir un wrapper mince qui appelle `build_export_rows` puis écrit le fichier `data/jobs_*.csv` (même contenu, même dossier, même encodage ; seul le préfixe du nom de fichier est renommé `orp_` → `jobs_`), et affiche le résumé terminal inchangé.
- **FR-004**: La CLI doit accepter les mêmes flags (`--month`, `--from`, `--to`, `--statuses`) avec les mêmes défauts et la même priorité `--from`/`--to` > `--month` > mois courant.
- **FR-005**: Le mapping de colonnes validé ne doit PAS changer (mêmes 10 colonnes, même ordre).
- **FR-006**: Le mapping statut → résultat ne doit PAS changer (statuts « en cours » → « en suspens » ; statuts de clôture → « négatif »).
- **FR-007**: Le filtre de période doit s'appliquer sur `job_tracking.changed_at` (la colonne réelle en base), via la jointure `jobs JOIN job_tracking`, sans filtre par profil.

#### Registre de presets dans l'onglet Reports

- **FR-008**: L'onglet Reports doit exposer un registre de presets. Un preset expose un label, les contrôles requis (période, statuts), et une fonction `generate(...) -> (rows: list[dict], filename: str)`.
- **FR-009**: Le premier preset doit être le seul livré dans cette spec : filename `jobs_YYYY-MM.csv` en mode mois, `jobs_YYYY-MM-DD_YYYY-MM-DD.csv` en mode plage ; colonnes et mapping délégués à `build_export_rows`.
- **FR-010**: Ajouter un futur preset doit nécessiter uniquement une nouvelle fonction + une entrée dans le registre, sans modifier le reste de l'UI.
- **FR-011**: Le selectbox « Type d'export » doit lister les labels du registre.

#### UI conforme au mockup (composants Streamlit natifs uniquement)

- **FR-012**: L'onglet Reports doit être accessible depuis la navigation existante du tracker, avec le titre « Reports » et une entrée de navigation cohérente avec les pages actuelles.
- **FR-013**: « Type d'export » doit être un `st.selectbox` (options = labels du registre).
- **FR-014**: « Période » doit être un `st.radio` à deux choix (Mois / Plage de dates) : « Mois » → un champ `YYYY-MM` (défaut = mois courant) ; « Plage » → deux champs de date from/to.
- **FR-015**: « Statuts » doit être un `st.multiselect` (défaut `["applied"]`).
- **FR-016**: « Générer l'aperçu » doit être un `st.button` déclenchant la génération.
- **FR-017**: La ligne de metrics doit afficher 3 `st.metric` dans 3 colonnes : « Entrées » (total) et le décompte par résultat du preset.
- **FR-018**: Le tableau d'aperçu doit être un `st.dataframe` (colonnes minimales : Date, Entreprise, Poste, Résultat).
- **FR-019**: « Télécharger … » doit être un `st.download_button` alimenté en octets en mémoire par `rows_to_csv_bytes(rows)` — aucune écriture disque, aucun scp.
- **FR-020**: Toute note d'aide du preset (ex. colonne à compléter à la main) doit être un `st.caption`.

#### Contraintes transverses

- **FR-021**: Aucune nouvelle dépendance (stdlib + Streamlit uniquement).
- **FR-022**: Aucune écriture disque dans l'UI ; le CSV téléchargé est produit en mémoire.
- **FR-023**: Aucun autre preset que le premier dans cette spec ; l'infrastructure de registre + le premier preset sont seuls livrés.
- **FR-024**: Aucune persistance des paramètres d'export en base ; l'état des contrôles vit uniquement en session-state Streamlit.

### Key Entities *(include if feature involves data)*

- **ExportPreset** (conceptuel, en code) : un type d'export déclaré au registre — `label` (libellé affiché), contrôles requis (période, statuts), `generate(...)` → `(rows, filename)`.
- **ExportRow** (conceptuel, en mémoire) : une ligne de CSV formatée, avec les colonnes du preset, dérivée d'un Job + JobTracking.
- **Job** (table `jobs`) : `id`, `title`, `company`, `location`, `url`, `source`, `work_mode` — données descriptives de l'offre.
- **JobTracking** (table `job_tracking`) : `job_id`, `status`, `notes`, `changed_at` — statut de suivi et date de dernière modification (colonne de filtre temporel).

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un utilisateur génère et télécharge un export CSV depuis le navigateur en ≤ 3 interactions (choisir période/statuts → générer → télécharger), sans SSH ni scp.
- **SC-002**: Le CSV téléchargé depuis l'UI est byte-identique à celui produit par la CLI pour les mêmes paramètres (mêmes colonnes, même mapping, même encodage utf-8-sig).
- **SC-003**: Le CSV produit par la CLI est byte-identique avant et après refactor sur un mois connu (test de non-régression).
- **SC-004**: L'aperçu reflète exactement le nombre d'entrées et la répartition par résultat de l'export final.
- **SC-005**: L'ajout d'un futur preset ne nécessite qu'une fonction + une entrée de registre (aucun changement dans la structure de l'UI).

---

## Non-Goals *(explicit — Constitution V)*

- **Pas de génération de PDF** dans le tracker (le remplissage de formulaire PDF reste hors-ligne).
- **Pas d'analytics ni de graphiques** (future tracker v2).
- **Pas de CSS custom ni de HTML injecté** dans Streamlit (composants natifs uniquement — le `mockup.html` est illustratif, pas un asset à injecter).
- **Pas de modification** de `storage.py`, `main.py`, `profiles.py`, `models.py` ni d'aucun scraper.
- **Pas d'autre preset** que le premier dans cette spec (infrastructure de registre + premier preset uniquement).
- **Pas de persistance** des paramètres d'export en base (session-state uniquement).

---

## Assumptions

- **Montage de l'onglet** : `tracker.py` utilise une navigation latérale (`st.navigation` / `st.Page`), pas de `st.tabs`. La barre « Jobs | Settings | Reports » du mockup est illustrative. « Reports » est donc ajouté comme une nouvelle page de navigation (un `tracker_views/reports.py` référencé dans la liste des pages), cohérente avec les pages existantes. La correspondance mockup → composant s'applique aux éléments *internes* de la page (selectbox, radio, multiselect, button, metric, dataframe, download_button, caption).
- **Colonne de date** : la période se filtre sur `job_tracking.changed_at` (colonne réelle en base).
- **Importabilité en conteneur** : le Dockerfile fixe `WORKDIR /app` et copie le repo à la racine ; `tracker.py` et le script d'export partagent donc le même working directory, et le script est importable depuis l'UI. Le refactor garde le script à la racine (pas de déplacement).
- **Pas de filtre par profil** : l'export couvre tous les jobs, comme la CLI actuelle (projet solo, profil unique).
- **Le premier preset** réutilise la logique validée du script `export_jobs.py` (label affiché « Candidatures », colonnes du formulaire 716.007, préfixe de fichier `jobs_`). Le nom du script et le préfixe de fichier sont renommés (`export_orp.py` → `export_jobs.py`, `orp_` → `jobs_`) — dé-branding ORP. Seul l'en-tête de colonne « Assignation ORP » est conservé : c'est le contrat de données du formulaire 716.007, pas du branding.
- **Dev uniquement sur Mac** ; jamais de déploiement vers verva pendant ce cycle (le déploiement suit le workflow standard après implémentation et tests).
