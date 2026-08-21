# Phase 1 — Data Model: Onglet Reports

Aucune nouvelle table DB. Les entités ci-dessous sont soit des tables existantes (lecture), soit des structures conceptuelles en code/en mémoire.

## Entités existantes (lecture seule)

### Job — table `jobs`
Colonnes utilisées par le premier preset d'export :

| Champ | Type | Usage |
|-------|------|-------|
| `id` | TEXT PK | clé de jointure |
| `title` | TEXT | « Description du poste » |
| `company` | TEXT | « Entreprise / Adresse » (préfixe) |
| `location` | TEXT | « Entreprise / Adresse » (suffixe, optionnel) |
| `url` | TEXT | « URL » |
| `source` | TEXT | (lu, non utilisé dans le mapping actuel) |
| `work_mode` | TEXT | suffixe « (work_mode) » de la description, si non NULL et ≠ `unknown` |

### JobTracking — table `job_tracking`
| Champ | Type | Usage |
|-------|------|-------|
| `job_id` | TEXT PK | clé de jointure vers `jobs.id` |
| `status` | TEXT | filtre + mapping « Résultat » |
| `notes` | TEXT | « Motif » (si résultat = négatif) |
| `changed_at` | TEXT | **colonne de filtre temporel** (`date(changed_at) BETWEEN …`) |

## Entités conceptuelles (code / mémoire)

### ExportPreset (registre)
Déclaration d'un type d'export. Une entrée expose :

| Champ | Type | Description |
|-------|------|-------------|
| `label` | str | Libellé affiché dans le selectbox « Type d'export » |
| `controls` | concept | Contrôles requis : période (mois/plage) + statuts |
| `generate` | callable | `generate(date_from, date_to, statuses, mode) -> (rows, filename)` |

Le registre est une structure associative simple : `label -> preset`. Le selectbox est rempli depuis `list(registry)`.

### ExportRow (ligne CSV formatée)
Une ligne de CSV au format du preset. Le premier preset produit 10 colonnes (contrat validé, inchangé — formulaire 716.007) :

| # | Colonne | Source / règle |
|---|---------|----------------|
| 1 | `Jour` | jour du mois de `changed_at` |
| 2 | `Mois` | numéro du mois de `changed_at` |
| 3 | `Entreprise / Adresse` | `company` (+ ` — location` si location présente) |
| 4 | `Personne contactée / Tél.` | vide (remplissage manuel) |
| 5 | `Description du poste` | `title` (+ ` (work_mode)` si connu) |
| 6 | `Assignation ORP` | `Non` (constante) |
| 7 | `Activité` | `par lettre / électronique` (constante) |
| 8 | `Résultat` | mapping déterministe depuis `status` |
| 9 | `Motif si négatif` | `notes` si résultat = « négatif », sinon vide |
| 10 | `URL` | `url` |

**Mapping statut → résultat** (inchangé) :

| Statut DB | Résultat |
|-----------|----------|
| `applied`, `queued`, `ready`, `saved`, `new` | `en suspens` |
| `rejected`, `archived`, `expired` | `négatif` |

### Aperçu UI (dérivé, non persisté)
Le tableau d'aperçu est une projection réduite de `rows` pour affichage : `Date`, `Entreprise`, `Poste`, `Résultat`. Les metrics sont des comptages sur `rows` : total = `len(rows)` ; puis décompte par valeur du champ résultat du preset.

## Règles de validation / invariants

- Le nombre et l'ordre des colonnes CSV du premier preset sont **toujours identiques** (10 colonnes).
- L'encodage est **toujours** `utf-8-sig`.
- `rows` vide ⇒ `rows_to_csv_bytes` retourne `b""` ; l'UI désactive le téléchargement.
- Aucun filtrage par `profile_id` ; l'export couvre tous les jobs.
- Le filtre temporel porte sur `date(job_tracking.changed_at)` inclusivement entre `date_from` et `date_to`.
- La couche générique (`ExportPreset.generate`, `rows_to_csv_bytes`) ne dépend d'aucun format de colonne particulier : c'est le preset qui porte son propre mapping.
