# Monitored Companies — Clarifications & Technical Plan

> Feature: surveiller un ensemble choisi d'entreprises via leur ATS (ou un scraper dédié), scorer fortement leurs offres pertinentes et les distinguer visuellement.
> Ce document tient lieu de sortie `/speckit.clarify` (Partie 1) et `/speckit.plan` (Partie 2). Il se lit avec la constitution et le specify déjà rédigés.

## Note de baseline

Le snapshot public GitHub est antérieur à l'arbre de travail réel : il montre encore `main.py` monolithique, `filters.py` qui pré-filtre sur le titre, et une sortie JSON/MD. Le plan vise l'état courant — split `scrape.py` (scraping → DB) / `score.py` (scoring, flags `--profile` / `--rescore`), `main.py` orchestrateur fin, SQLite (WAL) via `storage.py`, `profiles.py` avec le dataclass `SearchProfile` portant `scoring_context`, profil unique `unified_jc`, `scorer.py` injectant `scoring_context` dans le system prompt, et `scrapers/base.py` (BaseScraper ABC) + un fichier par scraper. Les signatures et noms de colonnes exacts sont à confirmer contre la `storage.py` réelle au moment des tâches.

---

# Partie 1 — Clarifications (résolues)

## C1. Famille de titres (gate de fonction, dur)

Liste d'inclusion volontairement généreuse, par ordre d'intérêt décroissant : **Product Manager** (titre principal, généralement le plus senior), Senior / Staff / Principal / Lead / Group Product Manager, Director of Product / Head of Product / VP Product / Chief Product Officer, puis **Product Owner** et **Technical Product Owner**. Variantes orthographiques et casses incluses ; matching sur sous-chaîne insensible à la casse, jamais sur égalité stricte.

Règle de sécurité : le gate doit laisser passer le cas de régression FELFEL (« Senior Tech Product Owner »). En cas de doute, on garde — un faux positif coûte un appel de scoring, un faux négatif fait perdre une cible. La liste reprend et élargit l'esprit de l'ancienne `titles` de `JobFilter` (qui contenait déjà product manager, head of product, CPO, VP product, product owner) en ajoutant les variantes de séniorité et le Technical Product Owner manquant.

## C2. Emplacement du gate de fonction

Pas un filtre au niveau du scraper. Un ATS renvoie la liste complète et non scopée des reqs d'une entreprise en **un seul appel** — il n'y a pas de fetch par poste à éviter. Le gate est donc un **skip déterministe placé juste avant l'appel LLM dans `score.py`** : le scraper reste « bête » et large, la ligne est écrite en base avec une disposition (`filtered_non_product`) pour l'audit, et aucun crédit Groq n'est dépensé sur un poste hors famille. Cela respecte « scraper = filet large, scorer = tout le filtrage » tout en protégeant le quota Groq (1000 req/jour).

## C3. Séniorité — démotion douce, pas de gate dur

La séniorité n'est souvent claire que dans le corps de l'annonce, pas dans le titre ; un gate dur au scrape supprimerait des postes en réalité seniors mais sobrement intitulés. Donc : la séniorité reste un **signal de scoring** (le LLM voit le texte complet et rétrograde un poste junior), pas un filtre de collecte. Cohérent avec C4.

## C4. Plancher de score / provenance monitorée

La provenance monitorée est un **signal positif fort, pas un override**. Elle remonte un poste pertinent ; elle ne blanchit pas une offre clairement hors-cible. Un poste clairement inadapté (séniorité junior nette, ou type d'entreprise franchement disqualifiant) **score bas**. Nuance importante : la rétrogradation « grande entreprise / banque / Big 4 » de ton profil est **douce** — un PM senior chez une banque privée atterrit au milieu, pas en bas ; un PM senior chez une scale-up crypto suisse (ton sweet spot) monte haut. Le score reste honnête ; le badge de provenance vit dans une colonne séparée, indépendant du score.

## C5. Monitoring = un booléen sur l'entreprise (mono-utilisateur par installation)

Le déploiement est : chaque utilisateur clone le repo et l'installe en local, avec sa propre DB. Il n'y a donc **qu'un utilisateur par installation** — pas de multi-tenant. Le monitoring est un simple booléen `monitored` sur `companies` (l'instinct initial), pas une relation par profil ni une table de jointure. « Comment ça marche pour François » = François clone, installe, onboard, et ajoute ses propres cibles via l'UI dans sa propre instance. Le multi-utilisateur se fait par installations séparées, pas par `profile_id`. La couture `profile_id` existante reste où elle est (jobs/scores) et n'a pas besoin d'être étendue au monitoring.

## C6. Peuplement et ajout d'entreprises

Deux voies. (a) Accumulation automatique : chaque job scrapé porte un nom d'entreprise, qui alimente `companies` ; ces lignes n'ont pas forcément d'ATS résolu et ne sont donc pas encore monitorables. (b) Ajout manuel via l'UI : l'utilisateur fournit l'URL carrières, le système tente d'en déduire le `scrape_method`. Le toggle de monitoring n'est **activable que si un `scrape_method` est résolu** (ATS identifié ou scraper dédié existant) — la règle « monitorable seulement si scrapable » tombe naturellement.

## C7. Détection ATS

Depuis l'URL carrières : d'abord match du hostname (`boards.greenhouse.io/x`, `jobs.lever.co/x`, `x.ashbyhq.com`, `*.myworkdayjobs.com`, `careers.smartrecruiters.com/x`, `apply.workable.com/x`…). Beaucoup d'entreprises (surtout crypto : Coinbase, Ripple, Sygnum, Taurus…) exposent un domaine vanity qui **embarque** un board ATS invisible dans l'URL de listing — d'où un fetch unique de la page carrières pour repérer le board sous-jacent. Fallback manuel : l'utilisateur peut saisir provider + identifiant à la main si la détection échoue.

## C8. Providers ATS au lancement (calé sur ta liste de 63 cibles)

Priorité 1 : **Greenhouse, Lever, Ashby** — couvrent le cluster crypto/Web3 (Fireblocks=Greenhouse, Kraken=Ashby, LCX=Workable, plus très probablement Coinbase/Ripple/Sygnum/Taurus/Bitcoin Suisse derrière vanity). Priorité 2 : **Workday** (Lombard Odier, Rothschild, Blackstone — endpoint JSON `/wday/cxs/{tenant}/{site}/jobs`, scrapable sans navigateur), **SmartRecruiters** (Swissquote — API publique propre), **Workable**. Différés : **Oracle HCM** (EFG) et **SAP SuccessFactors** (Pictet) — JS-lourds, nécessiteraient un navigateur headless (nouvelle dépendance, contraire au principe chirurgical), donc hors MVP sauf cible incontournable.

## C9. Couplage ATS / monitoring

Mettre en pause = `monitored = false` sur l'entreprise, identique pour ATS et scraper dédié ; la config de scraping (`ats_provider` / `ats_identifier` / `scraper_id`) persiste et n'est jamais supprimée pour une simple pause. Le run de monitoring n'itère que les entreprises `monitored = true` — une entreprise en pause est simplement absente de la liste passée à l'adaptateur, sans toucher à son token.

## C10. Mode d'exécution et cadence

`scrape.py` gagne un flag `--monitored-only` (catégorie company-keyed uniquement, dans le style de `--profile` / `--rescore` existants). Comme ce run est peu coûteux (quelques centaines d'appels API), il peut tourner **plus souvent** que le filet large : on garde les 3 runs complets et on ajoute un créneau cron monitored-only plus fréquent. Dédoublonnage contre les jobs existants pour ne faire remonter que les nouvelles offres.

## C11. Mise en valeur visuelle

Badge de provenance dans le tracker (ex. `🎯 Monitored · {company}`), **indépendant du score**, visible même en mode minimaliste sans scoring. Optionnellement, tri/épinglage des jobs monitorés en tête.

## C12. Découverte ATS conservée

On garde la découverte ATS (les ~30 boards crypto comme filet large, entreprises **non** monitorées, scoring normal) **en plus** du monitoring ciblé. Conséquence mécanique : les adaptateurs ATS servent deux chemins (découverte / monitoring) sans logique de mode interne — c'est l'orchestrateur qui passe la liste d'entreprises. Détail en Partie 2 (« Monitoring — deux chemins, service séparé, delta »).

---

# Partie 2 — Plan technique

## Modèle de données

`companies` — `id`, `name`, `careers_url`, `ats_provider` (nullable), `ats_identifier` (nullable), `scraper_id` (nullable, pour les scrapers dédiés), `monitored` (bool, défaut `false`), `detected_at`, `status`. Une entreprise est *scrapable* si `ats_provider` **ou** `scraper_id` est renseigné ; *monitorable* (toggle UI activable) seulement si scrapable. Pause = `monitored = false` ; pas de table de jointure. À réconcilier avec la table `companies` déjà conçue (CRM) : on l'étend ; `contacts`/`interactions` restent hors-périmètre.

Pas de dimension par profil sur le monitoring : un seul utilisateur par installation (clone + install local, DB propre). Le multi-utilisateur passe par des installations séparées, pas par multi-tenant. La couture `profile_id` existante (jobs/scores) reste inchangée. Seed : les ~30 boards Greenhouse livrés avec le repo peuplent `companies` à l'install ; chaque utilisateur ajoute ensuite ses propres cibles via l'UI.

Provenance sur les jobs — colonne nullable `monitored_company_id` (FK) sur la table des jobs (ou des scores), alimentée au scrape. Le badge UI et l'injection de scoring lisent toutes deux ce signal unique ; le score numérique reste dans sa colonne propre, non pollué.

Migrations — suivre le pattern déjà utilisé pour `comp_flag` ; préférer une **table `migrations` dédiée** au garde `COUNT(*) = 0` (jugé fragile). Backfill : les entreprises déjà présentes dans les jobs historiques peuplent `companies` (sans `scrape_method`, donc non monitorables tant que non enrichies).

## Taxonomie des scrapers et dispatcher

Réorganiser `scrapers/` en trois sous-paquets, BaseScraper ABC conservé :
- `scrapers/boards/` — agrégateurs existants (jobspy, web3career, remoteok, weworkremotely, cryptojobslist, cryptojobs_com, defi_jobs, tietalent, jobup, wellfound). Query-driven, filet large. Inchangés.
- `scrapers/ats/` — adaptateurs company-keyed : `greenhouse` (refactor de l'existant), `lever`, `ashby`, `workday`, `smartrecruiters`, `workable`. Prennent un **identifiant** (token/tenant), pas une requête.
- `scrapers/company_sites/` — scrapers dédiés bespoke, contribution développeur (chemin dev → git → deploy), rares.

Migration de `greenhouse.py` : la liste `CRYPTO_WEB3_BOARDS` codée en dur devient des **lignes `companies`** (`ats_provider='greenhouse'`, `ats_identifier=token`). Les ~30 boards servent de seed. L'adaptateur ne lit plus une constante : il reçoit de l'orchestrateur la liste des entreprises `greenhouse` à interroger (toutes les connues en découverte, ou seulement les `monitored = true` en monitoring).

Dispatcher company-keyed : il reçoit **une liste d'entreprises** (décidée par l'appelant, jamais un filtre câblé) et, pour chacune, route par `ats_provider` vers l'adaptateur correspondant, ou par `scraper_id` vers un scraper de `company_sites/`. Les adaptateurs ATS sont des **fonctions pures** (liste d'entreprises → offres), sans logique de mode interne — c'est l'orchestrateur qui décide quelle liste passer (cf. « Monitoring — deux chemins »). Pull large (toutes les reqs), puis gate de famille produit (C2) au scoring. Politesse / rate-limit par adaptateur ; quelques centaines de boards restent gérables. Réutiliser la stack HTTP existante (`httpx` / `curl_cffi` impersonation, proxy `gluetun-scrape`) — **aucune nouvelle dépendance** pour l'ensemble MVP.

## Notes par adaptateur ATS

- **Greenhouse** : `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` — JSON, trivial.
- **Lever** : `api.lever.co/v0/postings/{token}?mode=json` — trivial.
- **Ashby** : API de postings JSON (`api.ashbyhq.com` / board public) — simple.
- **Workday** : POST `https://{tenant}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` (corps JSON paginé) — scrapable sans navigateur ; il faut capter le `site` path par tenant lors de la détection.
- **SmartRecruiters** : `api.smartrecruiters.com/v1/companies/{id}/postings` — API publique propre, paginée.
- **Workable** : board public JSON (`apply.workable.com/api/v3/accounts/{id}/jobs` ou le widget) — simple.
- **Oracle HCM / SuccessFactors** : différés (JS-lourd, navigateur headless requis = nouvelle dépendance).

## Intégration du scoring

Le flag de provenance posé au scrape est lu par `score.py`, qui l'injecte dans le `scoring_context` (prose, appliquée côté serveur via `--apply-context` — chemin « prompt fix », jamais de code) : « provenance monitorée = signal positif fort ; scorer néanmoins bas tout poste clairement inadapté ». Le gate de famille produit (C2) est un skip déterministe avant l'appel LLM. La séniorité reste une démotion douce gérée par le LLM (C3). La rétrogradation taille/type reste douce (C4).

## Monitoring — deux chemins, service séparé, delta

**Découverte ATS conservée** (décision confirmée). Les adaptateurs servent deux chemins d'orchestration qui partagent le même code et ne diffèrent que par (quelle liste d'entreprises) + (traitement de scoring) + (cadence) :

- **Découverte** — run large, 3×/jour. Boards d'agrégation + **toutes** les entreprises ATS connues (`ats_provider IS NOT NULL`, dont les ~30 seeds crypto). Scoring normal. C'est l'élargissement du filet existant aux entreprises non monitorées.
- **Monitoring** — service fréquent, séparé. **Seulement** les entreprises `monitored = true` (ATS + `company_sites`). Provenance injectée → scoré haut + badgé. Invoqué via `scrape.py --monitored-only`.

Le booléen `monitored` **est** le filtre (clause `WHERE`), pas un mode câblé dans l'adaptateur : un seul adaptateur par provider, deux appelants qui lui passent des listes différentes. Aucune duplication.

**Service séparé au niveau déploiement, pas en code.** Le monitoring est un `--monitored-only` planifié à part — un service Compose dédié *ou* une entrée cron propre — qui réutilise dispatcher et adaptateurs. Indépendant pour la cadence et les pannes, sans second code à maintenir.

**Delta / nouvelles offres uniquement.** Chaque run de monitoring diffe contre les jobs déjà vus par **id stable de l'offre** (fourni par l'ATS ; Greenhouse expose un `id` par poste) et ne fait remonter/notifier que les nouvelles. Couche orthogonale aux deux chemins, nécessaire dans les deux.

## UI (tracker.py / Streamlit)

Settings → panneau « Monitored companies » : liste, ajout (coller l'URL carrières → détection ATS → si résolu, activer le toggle, sinon afficher « non scrapable »), pause/réactivation, retrait. Jobs → badge de provenance indépendant du score, tri/épinglage optionnel. À aligner avec le `SPEC_tracker_redesign` (contrôles de run en haut, Settings limité au config-only).

## Découpage en phases (validation séquentielle)

- **Phase A** — modèle de données : `companies` étendu (+ booléen `monitored`) + colonne provenance `monitored_company_id` + table `migrations`. Valider avec 2-3 entreprises seed.
- **Phase B** — refactor taxonomie `scrapers/` + Greenhouse piloté par la DB + dispatcher company-keyed + `--monitored-only`. Valider la parité Greenhouse (les 30 boards fonctionnent en tant que lignes).
- **Phase C** — adaptateurs Lever, Ashby, puis Workday, SmartRecruiters, Workable. Valider chacun contre une entreprise connue (ex. Kraken/Ashby, Swissquote/SmartRecruiters, Lombard Odier/Workday).
- **Phase D** — détection ATS depuis l'URL carrières (+ fetch du board embarqué) et ajout manuel UI.
- **Phase E** — injection de provenance au scoring + gate famille produit + badge UI + gestion Settings.

Chaque phase : check empirique (Coinbase/Kraken/Fireblocks résolvent et renvoient des reqs ; régression FELFEL toujours verte).

## Non-goals (cette feature)

Pas d'auto-candidature. Pas d'ATS à navigateur (Oracle/SuccessFactors) au MVP. Pas de découverte ATS en masse au-delà de la détection par entreprise. Pas de CRM (`contacts`/`interactions`) — séparé.

## Points à vérifier contre le code réel (au moment de `/speckit.tasks` dans Claude Code)

Schéma et méthodes exacts de `storage.py` ; tables CRM déjà conçues (étendre vs créer) ; conventions de flags de `scrape.py` (calquer sur `--profile` / `--rescore`) ; point d'injection du `scoring_context` dans `scorer.py` (déjà existant) ; format de la table des scores (`job_scores`, colonne `comp_flag` déjà ajoutée) pour y greffer la provenance.
