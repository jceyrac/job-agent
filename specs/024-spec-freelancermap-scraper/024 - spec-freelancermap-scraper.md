# Feature Specification: Scraper freelancermap — ⛔ ABANDONNED

**Feature Branch**: `024-freelancermap-scraper`

**Created**: 2026-08-04

**Abandoned**: 2026-08-05

**Status**: Abandoned

## Decision Record

**Décision** : abandon complet du scraper, pas simple désactivation.
Suppression du code par commit `Remove freelancermap scraper (spec 024 abandoned)`.

**Raison** : 4 rôles PM/PO sur 665 projets suisses (0,6 %), tous derrière un
paywall de pagination React client-side (seule la page 1 est servie en SSR).
Le rendement observé est de ~22 projets/run, dont 0 titre PM/PO après filtrage.

Les annonces pertinentes sont couvertes par une alerte email côté freelancermap
— le ROI de maintenir un scraper pour zéro annonce PM/PO par run est négatif.

**Éléments conservés** (valeur propre, indépendante du scraper) :
- Le correctif `work_mode` hint dans `scorer.py` — bug pipeline, toutes sources
- Le mapping `regie` dans le scoring_context — utile aux autres sources ANÜ
- `AUTHORITATIVE_CONTRACT_SOURCES` dans `scorer.py` — mécanisme conservé vide,
  prêt pour un futur scraper qui déclare son `contract_type` source-authoritative
- Les variantes de titres PM/PO DE/FR ajoutées aux `job_titles` (prose path)

**Leçons documentées** :
- Vérifier le % de titres PM/PO *avant* développement, pas après
- Le server-side rendering peut être partiel (React hydration) — tester la
  pagination en amont
- Privilégier les sources avec API paginée documentée

**Input**: User description: "Ajouter freelancermap.com comme source de missions freelance au job_agent, en priorité pour la Suisse, extensible au DACH et à l'Europe."

---

## Clarifications

### Session 2026-08-05

- Q: SC-001 (400+ brut freelancermap) vs SC-002 (78→150 total) sont incohérents (400+78=478, pas 150). Lequel fait foi ? → A: Ni l'un ni l'autre. SC-002 supprimé. SC-001 reformulé en critère de complétude, pas de volume absolu : le scraper collecte tout ce que le plafond FR-008 autorise. Avec FR-008 fixé à 3 pages/pays (~20/pages), volume attendu ≈ 60/pays. La complétude est vérifiée par l'oracle in-page FR-013.
- Q: Librairie de parsing HTML et critère de détection de casse (FR-013) ? → A: BeautifulSoup + lxml (standard maison). Détection de casse : **oracle in-page** — parser le compteur `"N jobs & projects"` et comparer au nombre de cartes parsées dans la MÊME réponse. Si en-tête > 50 ET 0 carte → alerte. Aucun état persistant, pas de comparaison run-à-run, pas de faux positif quand le site a légitimement peu d'offres.
- Q: Qui implémente le filtre de titres multilingue FR-017 — le scraper ou filters.py ? → A: Le scraper ne filtre pas par titre, il renvoie les JobPosting bruts. Le matching reste dans la couche existante (pre_filter SQL + JobFilterEngine, substring insensible à la casse). FR-017 devient un changement de donnée (ajout de `Leiter Produktmanagement`, `Chef de produit`, `Responsable produit` aux job_titles du profil via Settings). FR-018 reclassifié optionnel : le substring matching rend la normalisation des suffixes inutile.
- Q: Quel mécanisme pour marquer `contract_type` comme source-authoritative (FR-004) ? → A: Liste blanche `AUTHORITATIVE_CONTRACT_SOURCES = {"Freelancermap"}` dans `scorer.py`. Après parsing LLM dans `extract_job_fields`, si `job.source` est dans la liste et `job.contract_type` est déjà set, la valeur source écrase l'inférence Groq. Aucun nouveau champ, aucune migration, seul `scorer.py` est touché.
- Q: Où implémenter le tunnel de filtrage FR-019 ? → A: Script autonome `scripts/filter_funnel.py --source Freelancermap`, hors pipeline. Lit la DB après un run, importe les vrais prédicats (`JobFilterEngine` + profil actif). Même patron que `scripts/audit_provenance.py`. Zéro modification de `score.py`.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Missions freelance suisses dans le digest (Priority: P1)

En tant que Senior PM basé à Lausanne cherchant des missions freelance, je veux
que le digest quotidien contienne les missions freelance publiées en Suisse sur
freelancermap, avec le même formatage (badges, score, lien) que les offres
existantes, afin d'arrêter de consulter le site manuellement.

**Why this priority**: C'est la totalité de la valeur du scraper. Constat du
04/08/2026 sur `freelancermap.com/projects/switzerland` : 664 missions actives
en Suisse, dont 528 en contrat Freelance et 81 en contrat de régie (ANÜ). À
comparer aux ~78 jobs tous scrapers confondus par run actuellement. Cette seule
source peut multiplier le volume freelance par un ordre de grandeur.

**Independent Test**: Lancer `main.py` avec le seul scraper freelancermap activé
et vérifier que le digest produit contient des missions CH avec titre, société,
localisation, type de contrat, taux de télétravail et URL exploitables.

**Acceptance Scenarios**:

1. **Given** le scraper est enregistré via l'auto-découverte de `main.py`,
   **When** un run est lancé, **Then** le nombre de `JobPosting` parsés correspond
   au compteur affiché par le site sur les 3 pages fetchées (oracle FR-013),
   soit environ 60 offres pour la Suisse.
2. **Given** une mission listée comme "Freelance" sur le site, **When** elle est
   parsée, **Then** `contract_type = "freelance"` provient de la source et non
   d'une inférence Groq.
3. **Given** une mission listée "Permanent", **When** le filtre freelance est
   actif, **Then** elle est exclue du digest freelance mais reste disponible
   pour le digest général.
4. **Given** deux runs consécutifs, **When** le second s'exécute, **Then** aucune
   mission déjà notifiée n'est renotifiée (dédoublonnage par URL).

---

### User Story 2 — Extension DACH / Europe sans nouveau code (Priority: P2)

En tant qu'utilisateur, je veux pouvoir ajouter l'Allemagne, l'Autriche, le
Royaume-Uni ou la Belgique par simple configuration, sans écrire un second
scraper.

**Why this priority**: Volumes relevés le 04/08/2026 : Allemagne 13 393,
Suisse 664, Autriche 426, UK 345, USA 63, Belgique 63.

Le rendement suisse sera faible et c'est assumé (voir SC-001b). L'extension
multi-pays n'est donc **pas** un rattrapage de volume : l'Allemagne publie
massivement en allemand, langue exclue par le profil, donc son volume 20× ne se
convertira pas en missions exploitables.

**Recommandation d'implémentation** : livrer quand même l'itération de pays dès
la première passe, parce que le coût marginal est nul (boucle sur une liste
configurable) et que ça évite d'y revenir plus tard. Mais la Suisse reste le
seul pays actif par défaut, et l'activation d'un autre pays reste un geste
délibéré, opt-in et plafonné.

⚠️ Ne pas activer l'Allemagne « pour voir » : combinée au filtre de langue, elle
produirait surtout du bruit filtré, au prix d'un run plus long.

**Independent Test**: Ajouter `"austria"` à la liste de pays configurée, relancer,
et constater l'apparition de missions autrichiennes sans modification de code.

**Acceptance Scenarios**:

1. **Given** une liste de pays en configuration, **When** le scraper tourne,
   **Then** il itère sur chaque pays et agrège les résultats.
2. **Given** l'Allemagne est activée, **When** le volume dépasse le plafond
   configuré, **Then** le scraper s'arrête proprement à la limite plutôt que de
   paginer indéfiniment.

---

### User Story 3 — Résilience au changement de structure HTML (Priority: P3)

En tant qu'utilisateur, je veux être averti quand freelancermap change son HTML,
plutôt que de voir le scraper retourner silencieusement zéro résultat.

**Why this priority**: Le site est scrapé en HTML sans API publique. Une
régression silencieuse est le mode de panne le plus probable, et le plus coûteux
puisqu'il ressemble à "il n'y a pas de missions cette semaine".

**Independent Test**: Modifier volontairement le sélecteur attendu et vérifier
qu'une alerte est levée plutôt qu'un résultat vide.

**Acceptance Scenarios**:

1. **Given** le site affiche "664 jobs & projects" dans l'en-tête mais le
   scraper parse 0 carte, **When** le run se termine, **Then** une alerte casse
   explicite apparaît dans les logs et le digest (FR-013 oracle in-page).

---

### Edge Cases

- Le site renvoie **403 sur une requête sans User-Agent navigateur** (vérifié en
  ligne de commande). Le scraper doit envoyer des en-têtes réalistes.
- Le champ localisation contient parfois du texte libre très long au lieu d'une
  ville (exemple relevé : "Hybrides Arbeitsmodell (Vor-Ort-Präsenz in Genf oder
  Bulgarien für Key-Workshops & Go-Live, ansonste"). Le parsing doit tronquer et
  ne pas planter.
- Les annonces sont majoritairement **en allemand**, y compris pour des missions
  en Suisse romande. Le scoring Groq doit rester correct sur du contenu non
  anglophone — à vérifier explicitement.
- Certains titres sont anonymisés (`ID: *****`) — ne pas traiter comme une erreur.
- Le télétravail est exprimé en pourcentage ("20% remote", "100% remote",
  "On-site"), pas en catégorie. Une règle de conversion vers `work_mode` est
  nécessaire.
- Trois types de contrat coexistent : "Freelance", "Agency contract (e.g. ANÜ)",
  "Permanent". La régie ANÜ n'est ni tout à fait du freelance ni du salariat —
  mapping tranché en FR-006 : valeur distincte `regie`, incluse dans le digest.
- Les dates de publication du jour s'affichent en heure (`16:48`) et les plus
  anciennes en date (`03.08.2026`). Deux formats à gérer.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Le système MUST récupérer les missions depuis
  `https://www.freelancermap.com/projects/{country}` en HTML server-rendered,
  sans exécution JavaScript.
- **FR-002**: Le système MUST envoyer un User-Agent de navigateur ; une requête
  sans en-têtes réalistes reçoit un 403.
- **FR-003**: Le système MUST extraire pour chaque mission : titre, société,
  URL absolue, localisation, pays, type de contrat, taux de télétravail, durée,
  date de démarrage et date de publication.
- **FR-004**: Le système MUST dériver `contract_type` **depuis la valeur affichée
  par la source** et marquer cette valeur comme faisant autorité, sans passer par
  l'inférence Groq.
  
  **Mécanisme (décision du 05/08/2026)** : liste blanche `AUTHORITATIVE_CONTRACT_SOURCES`
  dans `scorer.py`. Après parsing du résultat LLM dans `extract_job_fields`, si
  `job.source` est dans cette liste et que `job.contract_type` est déjà renseigné
  par le scraper, la valeur source écrase l'inférence Groq.
  
  Aucun nouveau champ sur `JobPosting`, aucune migration, aucun changement à
  `models.py` ni `storage.py`. Seul `scorer.py` est touché.
  
  Ce qui est volontairement abandonné : la mesure du taux de désaccord
  source/inféré (carte `prompt_version`, pas spec 024).
- **FR-005**: Le système MUST mapper le pourcentage de télétravail vers `work_mode` :
  100% → remote, 1–99% → hybrid, "On-site" → onsite.
- **FR-006**: Le système MUST mapper "Agency contract (e.g. ANÜ)" vers une valeur
  `contract_type` **distincte** — `regie` — et NE DOIT PAS l'écraser vers
  `freelance` ni vers `permanent`.
  **Décision utilisateur du 04/08/2026.** L'ANÜ (*Arbeitnehmerüberlassung*,
  location de services / Personalverleih) désigne une mission de 3 à 12 mois à
  taux journalier, sourcée et exécutée comme du freelance, mais où le consultant
  est contractuellement employé par la société de location qui le met à
  disposition du client final (Hays, ITech Consult, Coopers, SThree, bbv —
  précisément les émetteurs observés sur les annonces CH).
  Enjeu : 81 des 664 missions suisses relevées, soit ~12 % du gisement. Un
  mapping vers `permanent` les ferait disparaître silencieusement du digest
  alors qu'elles sont fonctionnellement des missions freelance.
  Contexte suisse : de nombreux clients (banques, assurances, secteur public)
  refusent de contracter directement avec un indépendant en raison du risque de
  requalification AVS, et imposent le passage par une société de location de
  services. L'ANÜ est donc une voie d'accès principale au marché suisse, pas un
  cas marginal.
- **FR-006b**: Le digest MUST afficher un badge distinct pour `regie`
  (proposition : 🤝 Régie), aux côtés des badges existants
  💼 Permanent / 🔄 Freelance / 📋 Contract, afin que l'arbitrage se fasse
  mission par mission et non par filtre global.
- **FR-006c**: Les missions `regie` MUST être incluses dans le digest freelance
  par défaut.
- **FR-007**: Le système MUST supporter une liste de pays configurable, la Suisse
  étant le seul pays actif par défaut.
- **FR-008**: Le système MUST plafonner le nombre de pages paginées par pays à
  **3 pages par pays** (≈ 60 offres à ~20/pages). Ce plafond est le même pour
  tous les pays activés. Il pilote SC-001 et SC-003 — pas l'inverse.
- **FR-009**: Le système MUST respecter un délai entre requêtes cohérent avec les
  autres scrapers.
- **FR-010**: Le système MUST hériter de `BaseScraper` et être découvert
  automatiquement par `main.py`, sans modification de `main.py`.
- **FR-011**: Le système MUST produire des `JobPosting` conformes à la dataclass
  existante et sérialisables via `to_json()`.
- **FR-012**: Le système MUST dédoublonner par URL de mission.
- **FR-013**: Le système MUST détecter une casse structurelle du HTML via un
  **oracle in-page** (sans état persistant, sans comparaison run-à-run) :
  1. Parser le compteur d'en-tête `"N jobs & projects"` affiché par le site.
  2. Comparer `N` au nombre de cartes effectivement parsées dans la **même**
     réponse HTTP.
  3. Si l'en-tête est illisible → `warning` (dégradation non critique).
  4. Si l'en-tête affiche `N > 50` ET 0 carte parsée → **alerte casse**
     explicite dans les logs et le digest.
  
  Ce mécanisme évite les faux positifs quand le site a légitimement peu
  d'offres (ex. vendredi soir sur un petit pays), et n'a besoin d'aucun
  stockage par source. Il ne remplace pas les compteurs globaux de `score.py`.
- **FR-014**: Le système MUST trier par date de publication décroissante
  (`sort=1` produit l'ordre "Newest projects first" — vérifié).
- **FR-015**: Le système MUST filtrer par type de contrat via le paramètre
  `projectContractTypes[N]`, indexé à partir de 0, une entrée par type retenu.
  **Relevé et vérifié dans le navigateur le 04/08/2026** (clic sur les cases du
  bloc "Contract type", lecture de l'URL produite).

  Vocabulaire des valeurs (issu des `id` des cases à cocher) :
  | Valeur | Libellé site | `contract_type` cible |
  |---|---|---|
  | `contracting` | Freelance | `freelance` |
  | `employee_leasing` | Agency contract (e.g. ANÜ) | `regie` |
  | `permanent_position` | Permanent | `permanent` |

  URL de référence à utiliser (Suisse, freelance + régie, tri par date) :
  ```
  https://www.freelancermap.com/projects/switzerland
    ?projectContractTypes[0]=contracting
    &projectContractTypes[1]=employee_leasing
    &countries[]=3
    &sort=1
    &pagenr=1
    &excludeDachProjects=false
  ```

  ⚠️ Piège documenté : le paramètre `contractTypes[]` (sans le préfixe `project`)
  est **accepté silencieusement** par le site, produit un chip de filtre visible
  dans l'interface, et **ne filtre rien**. Ne pas s'y fier.

  **Test d'acceptation (oracle par facettes)** : le panneau latéral affiche les
  compteurs par facette. L'URL ci-dessus doit renvoyer exactement
  528 + 81 = **609** résultats. Si elle en renvoie 664 (total non filtré), le
  paramètre est ignoré et le scraper doit basculer sur FR-016. Vérifié
  manuellement le 04/08/2026 : 609 confirmé.
- **FR-015b**: Les identifiants de pays pour `countries[]` sont numériques et
  MUST être configurés depuis cette table (relevée le 04/08/2026, volumes
  freelance + régie) :
  | id | Pays | Volume |
  |---|---|---|
  | 3 | Suisse | 609 |
  | 1 | Allemagne | 12 764 |
  | 2 | Autriche | 413 |
  | 4 | Royaume-Uni | 339 |
  | 39 | Belgique | 57 |
  | 5 | USA | 51 |

  Le slug d'URL (`/projects/switzerland`) et `countries[]=3` coexistent ; c'est
  `countries[]` qui fait foi pour l'itération multi-pays de l'User Story 2.
- **FR-016**: *(voie de repli — FR-015 étant vérifié, ce requirement ne
  s'applique que si le paramètre cesse de fonctionner.)*
  En cas d'échec de FR-015, le système MUST filtrer par type de
  contrat **côté client**, à partir de la valeur lue sur chaque annonce. Cette
  voie de repli est acceptable et suffisante, car le type de contrat est affiché
  dans le listing lui-même. L'ensemble retenu MUST être {`freelance`, `regie`}
  conformément à FR-006.
- **FR-017**: Les variantes linguistiques DE/FR des titres PM/PO MUST être
  ajoutées aux `job_titles` du profil de recherche (donnée de configuration, via
  l'interface Settings), **sans élargir à d'autres rôles**. Le matching existant
  (pre_filter SQL + `JobFilterEngine`) est en substring insensible à la casse —
  il couvre déjà `product owner`, `produktmanager`, `produkt manager` sans
  modification de code. Les ajouts requis sont :
  - allemand : `Leiter Produktmanagement`
  - français : `Chef de produit`, `Responsable produit`
  
  Ce changement de donnée bénéficie à **toutes** les sources, pas seulement
  freelancermap. Aucun code de matching n'est à écrire dans le scraper.
- **FR-018**: *(Optionnel — reclassifié le 05/08/2026.)* Le substring matching
  insensible à la casse rend la normalisation des suffixes d'inclusion
  (`(m/w/d)`, `(w/m/d)`, `(m/f/d)`, `:in`), des quotités (`80-100%`) et des
  numéros de poste (`Pos. 2982`) inutile pour le matching. Seules les graphies
  alternatives comptent (ex. `Produktmanager` ET `Produkt Manager`). Ne pas
  écrire de normaliseur de titres.
- **FR-019**: Un script de diagnostic autonome `scripts/filter_funnel.py` MUST
  pouvoir reconstruire l'entonnoir de filtrage par source après un run, en
  important les vrais prédicats (`JobFilterEngine` + profil actif) — sans les
  réécrire, pour garantir zéro dérive. Usage :

  ```
  python scripts/filter_funnel.py --source Freelancermap
  ```

  Sortie attendue :

  ```
  collectés bruts          →  N
    après filtre titre     →  N
    après filtre work_mode →  N
    après filtre langue    →  N
    après filtre géo       →  N
    au digest (score≥seuil)→  N
  ```

  Même patron que `scripts/audit_provenance.py`. Zéro modification de `score.py`
  ni du pipeline. Réutilisable pour toute source.

  **Note sur les compteurs globaux** : les compteurs déjà présents dans `score.py`
  restent utiles comme signal grossier immédiat. Ne pas les retirer. Ce script
  apporte l'attribution par source qui leur manque.

  **Justification.** Le rendement PM/PO attendu est faible et cet arbitrage est
  assumé (voir SC-001b). Mais « peu d'offres » a deux causes indiscernables sans
  ce diagnostic :
  - le filtre titre coupe la majorité → **comportement attendu**, rien à faire ;
  - un filtre langue ou work_mode se déclenche à tort → **bug**, à corriger.

  Sans le tunnel, ces deux cas produisent le même digest vide et ne peuvent pas
  être distingués. Le script est l'instrument qui permet de dire *quel* filtre
  coupe, pas seulement *que* le rendement est bas.

### Key Entities

- **FreelancermapProject**: une mission telle que publiée. Attributs : titre,
  société émettrice (souvent une agence : Hays, ITech Consult, RM Group,
  Ironforge, bbv, PROSTAFF, SThree), lieu, pays, type de contrat, pourcentage de
  télétravail, durée, date de début, date de publication, URL.
- **JobPosting**: dataclass existante du projet. Le mapping vers cette entité est
  le livrable du scraper.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Le scraper collecte **toutes les missions que le plafond de pages
  autorise, sans perte**. Le critère de succès est la complétude, pas le volume
  absolu : le nombre d'offres parsées doit correspondre au nombre d'offres
  affichées par le site sur les pages fetchées (vérifié via l'oracle in-page
  FR-013). Avec FR-008 à 3 pages/pays et ~20 offres/pages, le volume attendu
  pour la Suisse est d'environ 60 offres brutes par run.
- **SC-001b**: Le nombre de missions PM/PO suisses survivant au filtre est
  **mesuré et rapporté à chaque run, sans seuil imposé**.
  Justification : sur les ~40 annonces suisses effectivement lues lors de la
  rédaction de cette spec (2 pages, 03 et 04/08/2026), **zéro** portait un titre
  Product Manager ou Product Owner. Titres observés : Business Analyst,
  Requirements Engineer, ServiceNow Consultant, SAP (SD/MM, EWM, SEM-BCS),
  Java/Fullstack, ICT Service Manager, ICT Security, PMO Manager, PMO Specialist,
  Projektleiter SAP S4/Hana, Project Manager IT Security, Platform Engineer,
  Data Scientist, Human Factors Engineer.
  Échantillon partiel, donc non concluant — mais le signal est net : le freelance
  IT suisse est dominé par le SAP, la BA et l'ingénierie, et le Product Management
  y est majoritairement un rôle salarié.
  **Arbitrage utilisateur du 04/08/2026 — qualité plutôt que volume.**
  Un rendement faible est explicitement accepté. Un scraper qui remonte deux
  missions vraiment pertinentes par mois remplit son office. Il n'y a donc
  **aucun seuil de rendement en dessous duquel la feature serait considérée
  en échec**, et aucune re-priorisation automatique n'est déclenchée.
  Le chiffre est mesuré et rapporté à titre informatif uniquement.

  Corollaire pour l'implémentation : ne pas « compenser » un faible rendement
  en élargissant les filtres de titre, de langue ou de work_mode. Ces filtres
  sont le mécanisme de qualité, pas un obstacle à contourner.

- **SC-003**: Le run complet reste sous 25 minutes malgré la source supplémentaire.
- **SC-004**: `contract_type` provient de la source pour 100 % des missions
  freelancermap, sans appel Groq supplémentaire pour ce champ.
- **SC-005**: Zéro doublon entre deux runs consécutifs.
- **SC-006**: Aucune régression : le nombre de jobs remontés par les 11 scrapers
  existants est inchangé.
- **SC-007**: Le tunnel de filtrage (FR-019) est présent dans les logs du premier
  run et permet d'attribuer chaque perte d'offre à une étape précise. Critère de
  validation : à la lecture du log, on peut répondre à « pourquoi seulement N
  offres au digest ? » en nommant le filtre responsable, sans relancer le pipeline.

---

## Assumptions

- Le contenu de freelancermap est server-rendered et le restera à court terme
  (vérifié le 03 et 04/08/2026 : deux récupérations HTML complètes réussies).
- Les grandes agences de contracting suisses publient sur freelancermap plutôt
  que uniquement sur leur propre site — observé dans les listings (Hays, ITech
  Consult, PROSTAFF, Coopers, Wavestone, SThree). Cette spec suppose donc qu'il
  est inutile de scraper ces agences séparément.
- Le stockage reste JSON/Markdown ; la migration SQLite (`storage.py`, étape 5 de
  la roadmap) est hors périmètre. Si SQLite arrive d'abord, le dédoublonnage
  FR-012 devra y être déporté.
- Aucune authentification n'est requise pour consulter les missions.
- Les conditions d'utilisation du site n'ont pas été auditées. À vérifier avant
  déploiement en production sur le serveur HPE — un scraping à faible fréquence
  et usage personnel est le cadre supposé ici.
- Le filtrage par titre PM strict existant (`filters.py`) s'applique en aval.
  **Décision utilisateur du 04/08/2026 — tranché : le périmètre reste
  strictement Product Manager / Product Owner.** Business Analyst, Requirements
  Engineer, PMO, Projektleiter et Project Manager restent exclus : ce sont des
  rôles adjacents, pas le rôle cible. Le taux de rejet élevé attendu sur le
  marché suisse est accepté sciemment (voir SC-001b).
