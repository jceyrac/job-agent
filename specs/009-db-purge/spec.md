# Spec 009 — Automatic DB Purge (stale jobs cleanup)

> Spec for Claude Code. Read `storage.py`, `main.py`, and `tracker.py` before starting.
> Do not modify `scorer.py`, `profiles.py`, `models.py`, or any scraper.

---

## Contexte et motivation

La DB Live compte déjà plus de 3 000 jobs, dont la grande majorité sont des offres périmées
sans aucune interaction humaine. Le pipeline tournant 3 fois par jour, la DB grossit
indéfiniment avec des entrées qui n'ont aucune valeur opérationnelle.

La règle est simple : une offre sans interaction humaine après N jours n'a aucune valeur.
Elle peut être supprimée proprement à chaque début de run.

---

## Objectif

Au début de chaque run de pipeline, supprimer automatiquement les jobs qui cumulent
les trois conditions suivantes :

1. `first_seen` est antérieur à J-N (N = paramètre configurable, défaut 30)
2. `job_tracking.status` est `'new'` **ou** aucune entrée dans `job_tracking` (jamais touché)
3. `job_tracking.notes` est `NULL` ou vide (aucune note saisie)

Les jobs avec `status` = `saved`, `applied`, `rejected`, `archived` — ou avec une note
non vide — sont **protégés** et ne seront jamais supprimés par ce mécanisme.

---

## Paramètre configurable

Stockage dans la table `config` existante :

```
key:   "purge_retention_days"
value: "30"   ← string, converti en int à l'usage
```

Valeur par défaut si la clé est absente : `30`.

---

## Fichiers à modifier

### `storage.py`

Ajouter deux méthodes. Ne pas toucher aux méthodes existantes.

#### `purge_stale_jobs(retention_days: int = 30) -> int`

Supprime les jobs purgeables et retourne le nombre de lignes supprimées dans `jobs`.

Requête cible :

```sql
DELETE FROM jobs
WHERE id IN (
    SELECT j.id FROM jobs j
    LEFT JOIN job_tracking jt ON j.id = jt.job_id
    WHERE date(j.first_seen) < date('now', '-' || ? || ' days')
      AND (
          jt.job_id IS NULL
          OR (jt.status = 'new' AND (jt.notes IS NULL OR trim(jt.notes) = ''))
      )
)
```

Cascade DELETE : vérifier dans le schéma existant si `job_scores`, `job_tracking`,
`status_history` ont `ON DELETE CASCADE` sur leur FK vers `jobs.id`.

**Résultat vérification (2026-06-25)** : Aucune FK n'a de `ON DELETE CASCADE`. Les FK
`job_scores.job_id`, `job_tracking.job_id`, `status_history.job_id` sont des
`FOREIGN KEY (job_id) REFERENCES jobs(id)` sans clause CASCADE. `interactions`
a également une FK vers `jobs(id)`.

- `job_applications` n'a pas de FK vers `jobs` — `job_id` est une `TEXT PRIMARY KEY`
  standalone. Les jobs purgeables (status=new, sans notes, sans interaction humaine)
  ne peuvent par définition pas avoir de ligne dans `job_applications` ni `interactions`.
  → Pas de nettoyage nécessaire sur ces deux tables.
- Suppression manuelle nécessaire dans l'ordre :
  1. `DELETE FROM status_history WHERE job_id IN (...)`
  2. `DELETE FROM job_scores WHERE job_id IN (...)`
  3. `DELETE FROM job_tracking WHERE job_id IN (...)`
  4. `DELETE FROM jobs WHERE id IN (...)`

Les DELETE sont encapsulés dans une transaction pour atomicité.

#### `count_purgeable_jobs(retention_days: int = 30) -> int`

Même logique que `purge_stale_jobs` mais avec `SELECT COUNT(*)` — sans effet de bord.
Utilisée par le widget de preview dans le tracker.

---

### `main.py`

Au tout début du run (avant le scraping), lire la config et appeler la purge :

```python
retention_days = int(db.get_config("purge_retention_days", default="30"))
purged = db.purge_stale_jobs(retention_days)
if purged > 0:
    logger.info(f"[purge] {purged} stale jobs removed (>{retention_days}d, untouched)")
else:
    logger.info("[purge] No stale jobs to remove")
```

---

### `tracker.py` — Settings

Dans la section Settings, ajouter un widget dans l'onglet ou la section Scraper/Pipeline :

```
Retention period (days)
[number_input ou slider, min=7, max=180, default=30]
```

Au save, appeler `db.set_config("purge_retention_days", str(value))`.

Afficher sous le widget une ligne d'info dynamique (recalculée à chaque changement de valeur,
sans bouton intermédiaire) :

> ℹ️ *With this setting, N jobs would be purged on next run (stale, untouched).*

Cette valeur est obtenue via `db.count_purgeable_jobs(value)`.

---

## Non-objectifs

- Pas d'archivage préalable avant suppression (les offres purgées disparaissent définitivement)
- Pas de corbeille ou mécanisme d'annulation
- Pas de purge des jobs avec statut autre que `new` (saved/applied/rejected/archived sont protégés)
- Pas de purge basée sur `posted_date` (trop peu fiable selon les sources) — utiliser uniquement `first_seen`
- Pas de bouton "Purge now" dans le tracker dans cette spec

---

## Clarifications

### Session 2026-06-25

- Q: Faut-il nettoyer `job_applications` et `interactions` en plus des tables listées (job_scores, job_tracking, status_history) ? → A: `interactions` doit être nettoyé (15 lignes bloquantes trouvées sur DB Live). `job_applications` n'a pas de FK vers `jobs` (TEXT PK standalone) — pas de nettoyage nécessaire mais pas de risque non plus.

## Critères d'acceptation

- [ ] `main.py` loggue le nombre de jobs supprimés à chaque run (ou "0 purged" si rien)
- [ ] Un job avec `status = 'saved'` n'est jamais supprimé, quelle que soit son ancienneté
- [ ] Un job avec une note non vide n'est jamais supprimé
- [ ] Un job sans entrée dans `job_tracking` (jamais vu dans le tracker) est bien purgeable
- [ ] Le widget Settings affiche correctement le nombre de jobs qui seraient purgés
- [ ] Modifier la valeur dans Settings et sauvegarder → le prochain run utilise la nouvelle valeur
- [ ] La purge est transactionnelle (tout ou rien en cas d'erreur)
- [ ] Les tables liées (`job_scores`, `job_tracking`, `status_history`) sont nettoyées en même temps

---

## Chiffres de référence (DB Live au 25 juin 2026)

Pour calibration et test de non-régression :

| Métrique | Valeur |
|---|---|
| Total jobs | 3 191 |
| Jobs > 30 jours | 2 810 |
| Jobs touchés (status ≠ new) | 510 |
| **Jobs purgeables avec retention=30** | **2 301** |

Le premier run après déploiement devrait supprimer ~2 301 jobs et ramener la DB à ~890 entrées.
