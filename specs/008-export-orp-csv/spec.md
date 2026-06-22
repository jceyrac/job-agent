# Feature Specification: Export CSV ORP

**Feature Branch**: `008-export-orp-csv`

**Created**: 2026-06-22

**Status**: Draft

**Input**: User description: "Export CSV des recherches d'emploi pour le formulaire ORP suisse. CLI avec --month/--from/--to/--statuses, jointure jobs + job_tracking, colonnes formatées pour le formulaire 716.007, output data/orp_YYYY-MM.csv encodé utf-8-sig, aperçu terminal."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Export mensuel pour le rendez-vous ORP (Priority: P1)

Le demandeur d'emploi doit présenter chaque mois à son conseiller ORP la liste de ses candidatures. Il ouvre un terminal sur le serveur, exécute `python export_orp.py --month 2026-06`, et obtient un fichier CSV prêt à être importé dans Excel ou LibreOffice, avec toutes les colonnes exigées par le formulaire 716.007. Un aperçu s'affiche dans le terminal pour vérification rapide.

**Why this priority**: C'est la raison d'être de la feature — produire le document exigé par l'ORP sans saisie manuelle.

**Independent Test**: `python export_orp.py --month 2026-06` sur la DB live produit `data/orp_2026-06.csv` avec les colonnes attendues, lisible dans LibreOffice, et un résumé terminal correct.

**Acceptance Scenarios**:

1. **Given** une DB avec 5 jobs au statut `applied` en juin 2026, **When** l'utilisateur lance `python export_orp.py --month 2026-06`, **Then** un fichier `data/orp_2026-06.csv` est créé avec 5 lignes, les colonnes Jour/Mois/Entreprise/Description/Activité/Résultat sont remplies, et le terminal affiche "5 entrée(s) exportée(s)" suivi d'un aperçu.
2. **Given** une DB sans jobs `applied` sur la période, **When** l'utilisateur lance l'export, **Then** le terminal affiche "[INFO] Aucune entrée trouvée" et aucun fichier CSV vide n'est créé.
3. **Given** une DB avec des jobs `rejected` et `archived`, **When** l'utilisateur lance `python export_orp.py --month 2026-06 --statuses applied rejected archived`, **Then** tous ces jobs sont inclus avec le résultat mappé correctement (applied→"en suspens", rejected/archived→"négatif").

---

### User Story 2 - Export sur plage de dates arbitraire (Priority: P2)

Le conseiller ORP demande exceptionnellement une période qui ne correspond pas à un mois calendaire (ex. du 15 mai au 14 juin). L'utilisateur utilise `--from` et `--to` pour délimiter la période exacte.

**Why this priority**: Cas moins fréquent mais nécessaire pour répondre aux demandes spécifiques du conseiller.

**Independent Test**: `python export_orp.py --from 2026-05-15 --to 2026-06-14` produit un CSV ne contenant que les jobs dont le changement de statut est dans cette plage.

**Acceptance Scenarios**:

1. **Given** des jobs `applied` le 10 mai, 20 mai et 1er juin, **When** `--from 2026-05-15 --to 2026-06-14`, **Then** seuls les jobs du 20 mai et 1er juin sont inclus.
2. **Given** `--from` et `--to` spécifiés, **When** `--month` est aussi passé, **Then** `--from`/`--to` prend priorité et un avertissement est affiché.

---

### User Story 3 - Filtrage par statut (Priority: P3)

L'utilisateur peut choisir quels statuts inclure via `--statuses`. Par défaut, seuls les jobs `applied` sont exportés (le cas standard ORP). Pour un récapitulatif plus complet, il peut ajouter `rejected`, `archived`, ou d'autres statuts.

**Why this priority**: Le défaut `applied` couvre 90% des usages. La personnalisation est un confort additionnel.

**Independent Test**: `python export_orp.py --statuses applied saved` exporte les jobs ayant ces deux statuts.

**Acceptance Scenarios**:

1. **Given** aucun `--statuses` fourni, **When** l'export est lancé, **Then** seuls les jobs `applied` sont inclus.
2. **Given** `--statuses rejected archived`, **When** l'export est lancé, **Then** les jobs `rejected` et `archived` apparaissent avec résultat="négatif" et leurs notes dans la colonne Motif.

---

### Edge Cases

- **DB absente** : Si `data/jobs.db` n'existe pas, afficher une erreur claire `[ERROR] DB introuvable : data/jobs.db` et sortir avec code 1.
- **Période sans jobs** : Afficher `[INFO] Aucune entrée trouvée pour cette période et ces statuts.` sans créer de fichier.
- **Statuts invalides** : Si l'utilisateur passe un statut non reconnu, argparse rejette la commande avec la liste des choix valides.
- **Colonnes vides** : Les champs optionnels (location, notes, work_mode) sont laissés vides s'ils sont NULL en base — pas de crash.
- **Work mode absent** : Si `jobs.work_mode` est NULL ou `unknown`, le titre du poste apparaît sans suffixe entre parenthèses.
- **Notes multilignes** : Les sauts de ligne dans `job_tracking.notes` sont préservés dans le CSV (échappement standard du module csv).
- **Chevauchement --month et --from/--to** : `--from`/`--to` prend priorité ; un avertissement informatif est affiché.
- **DB verrouillée** : SQLite WAL avec busy_timeout 5s — le script réessaie automatiquement. Si timeout dépassé, erreur et exit 1.
- **Fuseau horaire** : Les dates en base sont en ISO 8601 UTC. Le script les interprète telles quelles sans conversion de fuseau.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Le script MUST accepter `--month YYYY-MM` (défaut: mois courant) pour définir la période d'export.
- **FR-002**: Le script MUST accepter `--from YYYY-MM-DD` et `--to YYYY-MM-DD` comme alternative à `--month`. Si les deux sont fournis, `--from`/`--to` prend priorité.
- **FR-003**: Le script MUST accepter `--statuses` (choix multiples parmi: new, queued, ready, applied, rejected, archived, expired; défaut: applied).
- **FR-004**: Le script MUST interroger la DB avec une jointure `jobs JOIN job_tracking` et filtrer sur `job_tracking.changed_at` entre les dates de début et de fin.
- **FR-005**: Le script MUST mapper les statuts internes vers les résultats ORP : `applied` → "en suspens", `rejected`/`archived` → "négatif", tous les autres → "en suspens".
- **FR-006**: Le script MUST générer un CSV avec les colonnes : Jour, Mois, Entreprise / Adresse, Personne contactée / Tél., Description du poste, Assignation ORP, Activité, Résultat, Motif si négatif, URL.
- **FR-007**: Le CSV MUST être encodé en UTF-8 avec BOM (`utf-8-sig`) pour compatibilité Excel/LibreOffice.
- **FR-008**: Le fichier de sortie MUST être nommé `orp_YYYY-MM.csv` dans le dossier `data/`.
- **FR-009**: Le script MUST afficher un aperçu dans le terminal après export : nombre de lignes exportées, et pour chaque entrée : jour/mois, entreprise (tronqué à 40 caractères), résultat.
- **FR-010**: Le script MUST aussi afficher un résumé par statut (ex: "applied: 5, rejected: 2").
- **FR-011**: L'activité MUST être déduite comme "par lettre / électronique" pour tous les jobs (toutes les sources sont des candidatures en ligne).
- **FR-012**: La colonne "Motif si négatif" MUST être remplie avec `job_tracking.notes` pour les statuts rejected/archived, vide sinon.
- **FR-013**: La colonne "Description du poste" MUST contenir le titre du job, suivi du work_mode entre parenthèses si celui-ci est connu et différent de "unknown".
- **FR-014**: La colonne "Entreprise / Adresse" MUST contenir le nom de l'entreprise suivi de " — " et de la localisation si disponible.
- **FR-015**: Les colonnes "Personne contactée / Tél." et "Assignation ORP" MUST être laissées vides (remplissage manuel).
- **FR-016**: Le script MUST être un fichier standalone sans imports hors stdlib (`sqlite3`, `csv`, `argparse`, `datetime`, `calendar`, `pathlib`, `sys`).
- **FR-017**: Le script MUST utiliser `DB_PATH = Path(__file__).parent / "data" / "jobs.db"` pour localiser la DB et fonctionner quel que soit le CWD.

### Key Entities

- **Job** (table `jobs`) : `id`, `title`, `company`, `location`, `source`, `work_mode`, `url` — données descriptives de l'offre.
- **JobTracking** (table `job_tracking`) : `job_id`, `status`, `notes`, `changed_at` — statut de suivi et date de dernière modification.
- **ExportRow** (conceptuel, pas en DB) : une ligne du CSV avec les 10 colonnes ORP, dérivée d'un Job + JobTracking.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un utilisateur peut générer le CSV mensuel ORP en une seule commande shell sans édition manuelle.
- **SC-002**: Le CSV généré s'ouvre dans LibreOffice Calc et Microsoft Excel sans caractères corrompus (validation utf-8-sig avec BOM).
- **SC-003**: Toutes les colonnes exigées par le formulaire ORP 716.007 sont présentes et correctement remplies.
- **SC-004**: Le script s'exécute en moins de 2 secondes sur une DB de 4000+ jobs (requête SQL unique, pas de traitement ligne par ligne).
- **SC-005**: Le script fonctionne sans modification sur le serveur Ubuntu Live (`/opt/job-agent/`) et sur le Mac de dev.
- **SC-006**: Le résumé terminal permet de vérifier rapidement le contenu sans ouvrir le CSV (nombre de lignes, répartition par statut).

## Assumptions

- La DB est en SQLite WAL mode, accessible en lecture seule (le script ne fait que SELECT).
- `job_tracking.changed_at` reflète la date du dernier changement de statut — c'est la date pertinente pour le formulaire ORP. Pour les jobs actuellement `applied`, c'est la date de candidature. Pour les jobs `rejected`/`archived`, c'est la date du résultat.
- `jobs.work_mode` est disponible sur la table `jobs` (migration Phase 1e déjà appliquée en production).
- Toutes les candidatures dans la DB proviennent de sources en ligne → l'activité "par lettre / électronique" est toujours correcte.
- Le script est exécuté manuellement par le demandeur d'emploi (pas de cron, pas d'automatisation).
- Le formulaire ORP 716.007 exige le format jour/mois en colonnes séparées — c'est une contrainte du formulaire, pas un choix technique.
- Le script existant `export_orp.py` à la racine sert de référence de logique mais contient des bugs (ex. `updated_at` au lieu de `changed_at`) qui seront corrigés.
- Pas de filtre par profil — l'export couvre tous les jobs quel que soit le profil de scoring.
