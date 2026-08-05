# Feature Specification: Contrat régie, diagnostics, titres DE/FR

**Feature Branch**: `024b-contract-regie-diagnostics`

**Created**: 2026-08-05

**Status**: Draft

**Input**: Éléments extraits de la spec 024 (abandonnée) ayant une valeur propre,
indépendante du scraper freelancermap. Aucun scraper nouveau — uniquement des
améliorations au vocabulaire, au scoring, aux diagnostics et à la configuration.

---

## User Scenarios & Testing

### User Story 1 — Badge régie dans le digest et le tracker (Priority: P1)

En tant que chercheur d'emploi sur le marché suisse, je veux que les missions en
régie (ANÜ — *Arbeitnehmerüberlassung*) soient identifiées par un badge distinct
🤝 Régie dans le digest et le tracker, et incluses dans le digest freelance par
défaut, parce que ces missions sont fonctionnellement des missions freelance :
taux journalier, 3–12 mois, sourcées et exécutées comme du freelance, mais le
consultant est contractuellement employé par une société de location (Hays, ITech
Consult, Coopers, SThree, bbv).

**Why this priority**: 81 des 664 missions suisses relevées sur freelancermap
étaient en régie (~12 %). Le marché suisse impose ce canal pour des raisons AVS
— les clients refusent de contracter en direct avec des indépendants. Sans un
badge dédié, ces missions sont classifiées `permanent` ou `unknown` par Groq et
disparaissent silencieusement du digest freelance.

**Independent Test**: Modifier un job dans la DB avec `contract_type = "regie"`,
ouvrir le tracker → le badge 🤝 Régie apparaît. Générer un digest → la mission
est incluse avec le badge distinct.

**Acceptance Scenarios**:

1. **Given** un job avec `contract_type = "regie"`, **When** le tracker affiche
   la liste des jobs, **Then** le badge `🤝 Régie` est affiché aux côtés de
   💼 Permanent / 🔄 Freelance / 📋 Contract.
2. **Given** le profil actif a `allowed_contract_types = ["permanent", "freelance",
   "contract", "unknown"]`, **When** un job `regie` passe le Tier-0, **Then** il
   est inclus dans le digest (pas rejeté par le filtre contract_type).
3. **Given** un job `regie` dans le digest, **When** l'email est généré, **Then**
   le badge HTML/Markdown `🤝 Régie` est présent.

---

### User Story 2 — Diagnostic par source (filter_funnel.py) (Priority: P2)

En tant qu'opérateur du pipeline, je veux pouvoir reconstruire l'entonnoir de
filtrage pour une source donnée après un run, afin de distinguer "le filtre
titre coupe légitimement" de "un filtre se déclenche à tort".

**Why this priority**: Le rendement PM/PO est structurellement faible, et
« peu d'offres » a deux causes indiscernables sans ce diagnostic. Les compteurs
globaux de `score.py` donnent le total, mais pas l'attribution par source.

**Independent Test**: Lancer `python scripts/filter_funnel.py --source LinkedIn`
après un run → les 6 étapes de l'entonnoir sont affichées avec des compteurs.

**Acceptance Scenarios**:

1. **Given** un run terminé avec des jobs LinkedIn en base, **When**
   `filter_funnel.py --source LinkedIn` est exécuté, **Then** le script affiche
   les compteurs pour chaque étape (brut → titre → work_mode → langue → géo →
   digest), en important les vrais prédicats depuis `JobFilterEngine`.

---

### User Story 3 — Titres PM/PO en allemand et français (Priority: P3)

En tant que chercheur d'emploi, je veux que les annonces en allemand et en
français avec des titres PM/PO soient capturées par le filtre existant.

**Why this priority**: Le marché DACH publie majoritairement en allemand ;
"Product Owner" est un titre standard en contexte agile germanophone, et
"Chef de produit" / "Responsable produit" sont les titres français. Sans ces
variantes, des annonces pertinentes échappent au filtre.

**Independent Test**: Créer un job avec le titre "Chef de produit H/F", lancer
le filtre → le job passe le filtre de titre.

**Acceptance Scenarios**:

1. **Given** les titres `Leiter Produktmanagement`, `Chef de produit`,
   `Responsable produit` sont ajoutés aux `job_titles` du profil actif,
   **When** un job avec le titre "Chef de produit Digital (H/F)" est filtré,
   **Then** il passe le filtre titre (substring matching insensible à la casse).

---

### User Story 4 — Mécanisme contract_type source-authoritative (Priority: P3)

En tant que développeur, je veux un mécanisme dans le scorer qui permet à un
futur scraper de déclarer son `contract_type` comme faisant autorité, pour que
Groq n'écrase pas une valeur fiable avec une inférence.

**Why this priority**: Aucun scraper actuel n'en bénéficie, mais le pattern est
documenté et prêt. Le coût est nul (set vide → aucune exécution).

**Independent Test**: Ajouter `"TestSource"` au set, créer un job avec
`source="TestSource"` et `contract_type="freelance"`, lancer
`extract_job_fields` → `contract_type` reste `"freelance"`.

---

## Requirements

### Functional Requirements

- **FR-001**: Le vocabulaire `contract_type` MUST inclure la valeur `regie`,
  distincte de `freelance`, `permanent`, `contract`, `internship` et `unknown`.
- **FR-002**: Les prompts de scoring (`SYSTEM_PROMPT` et `EXTRACTION_PROMPT`
  dans `scorer.py`) MUST documenter `regie` dans la section Contract type, avec
  la définition : "regie : ANÜ / employee leasing / agency contract — mission à
  taux journalier via une société de location de services (Hays, Coopers, etc.),
  fonctionnellement équivalent à du freelance".
- **FR-003**: Les profils MUST inclure `"regie"` dans leur
  `allowed_contract_types` par défaut (dans `profiles.py` et `from_criteria()`).
- **FR-004**: Le tracker (UI) MUST afficher un badge distinct pour `regie` :
  `🤝 Régie`, aux côtés des badges existants dans `notifier.py` et
  `tracker_views/shared.py`.
- **FR-005**: Le filtre contract_type du tracker (`tracker_views/jobs.py`) MUST
  inclure `regie` dans la liste des valeurs filtrables.
- **FR-006**: Le digest (email HTML et Markdown dans `notifier.py`) MUST
  afficher le badge `🤝 Régie` pour les jobs `regie`.

- **FR-007**: Un script `scripts/filter_funnel.py` MUST pouvoir reconstruire
  l'entonnoir de filtrage par source. Usage :
  `python scripts/filter_funnel.py --source <NomSource> [--db chemin]`.
  - Importe les vrais prédicats depuis `JobFilterEngine` + `profiles.py`
  - Affiche les compteurs cumulatifs pour chaque étape
  - Même patron que `scripts/audit_provenance.py` (lecture seule, argparse,
    garde-fou sur DB périmée)
  - Réutilisable pour toute source, pas seulement une source spécifique

- **FR-008**: Un set `AUTHORITATIVE_CONTRACT_SOURCES: set[str]` vide MUST être
  défini dans `scorer.py` (près de `_EVAL_PASSTHROUGH_KEYS`). Dans
  `extract_job_fields()`, après parsing du résultat LLM, si `job.source` est
  dans ce set et que `job.contract_type` est déjà renseigné (non-None, non-
  `"unknown"`), la valeur source écrase l'inférence Groq. Avec un set vide,
  cette logique ne s'exécute jamais — aucun changement fonctionnel.

- **FR-009**: *(Prose path — pas de code.)* Les titres suivants MUST être
  ajoutés aux `job_titles` du profil actif via l'interface Settings :
  - `Leiter Produktmanagement`
  - `Chef de produit`
  - `Responsable produit`
  Le matching existant (substring, insensible à la casse) couvre déjà
  `product owner`, `produkt manager`, `produktmanager`.

### Non-Requirements (explicitement exclus)

- Aucun nouveau scraper. Cette spec est purement une amélioration du vocabulaire,
  des diagnostics et de la configuration.
- Aucune modification de `models.py` ni `storage.py`. Le champ `contract_type`
  est un TEXT libre en base — `regie` est accepté sans migration.
- Aucune normalisation automatique des titres. Le substring matching rend la
  normalisation des suffixes `(m/w/d)`, `(m/f/d)`, `:in`, des quotités
  (`80-100%`) inutile. Ne pas écrire de normaliseur.

---

## Success Criteria

- **SC-001**: `regie` apparaît dans la liste des contract_types filtrables
  dans le tracker, dans les badges du digest, et dans les prompts Groq.
- **SC-002**: Un job avec `contract_type="regie"` n'est pas rejeté par le
  Tier-0 quand le profil autorise les types standard (`permanent`, `freelance`,
  `contract`, `unknown`).
- **SC-003**: `filter_funnel.py --source LinkedIn` (ou toute autre source
  active) produit des compteurs d'entonnoir cohérents avec les logs de `score.py`.
- **SC-004**: `AUTHORITATIVE_CONTRACT_SOURCES` est un set vide — aucun
  comportement modifié, aucun test cassé, aucune régression.
- **SC-005**: 190/190 tests passent après implémentation.

---

## Technical Notes

- `regie` est ajouté au vocabulaire mais Groq ne le générera jamais
  spontanément — il n'est peuplé QUE par un scraper qui le déclare
  explicitement (futur scraper ANÜ-aware, ou scraping d'une source
  qui annonce des missions de location de services). Ce n'est pas un
  problème : c'est une valeur "scraper-only" comme `regie` l'était
  dans la spec 024.
- Le badge mapping est dans `notifier.py` (HTML + Markdown) et
  `tracker_views/shared.py` (Streamlit).
- Le filtre contract_type dans `tracker_views/jobs.py` utilise une
  liste déroulée des valeurs distinctes présentes en base — `regie`
  apparaîtra automatiquement dès qu'un job `regie` existe.
