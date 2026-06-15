# Feature Specification: Monitored Companies

**Feature Branch**: `001-monitored-companies`

**Created**: 2026-06-15

**Status**: Draft

**Input**: User description: "Ajouter une capacité de surveillance d'entreprises ciblées à l'agent. Le filet large sur les boards est bruité et les postes visés n'y remontent souvent jamais. Il existe un ensemble fini d'entreprises — quelques centaines — où je voudrais activement travailler. Surveiller directement leurs ouvertures est un signal bien plus fort."

## Clarifications

### Session 2026-06-15

- Q: Quelle est la liste précise des titres qui passent le gate de fonction ? → A: Product Manager, Senior/Staff/Principal/Lead/Group Product Manager, Director/Head/VP/Chief Product Officer, Product Owner, Technical Product Owner. Matching sous-chaîne insensible à la casse, jamais égalité stricte. Règle de sécurité : en cas de doute on garde (faux positif = un appel LLM, faux négatif = cible perdue). Cas de régression FELFEL (« Senior Tech Product Owner ») doit passer.
- Q: Où placer le gate de fonction (titre) dans le pipeline ? → A: Skip déterministe placé juste avant l'appel LLM dans score.py. Le scraper reste large (filet complet), la ligne est écrite en base avec la disposition `filtered_non_product` pour audit, et aucun crédit Groq n'est dépensé sur un poste hors famille.
- Q: Comment traiter la séniorité dans le gate ? → A: Pas de gate dur sur la séniorité. La séniorité est souvent invisible dans le titre seul ; elle reste un signal de scoring (le LLM voit le texte complet et rétrograde un poste junior), pas un filtre de collecte.
- Q: Quel est le plancher de score pour un poste monitoré ? → A: Signal positif fort, pas un override. Un poste clairement inadapté (junior net, entreprise disqualifiante) score bas. La rétrogradation « grande entreprise / banque / Big 4 » est douce — un PM senior banque privée atterrit au milieu ; un PM senior scale-up crypto suisse monte haut. Le badge de provenance est indépendant du score.
- Q: Comment modéliser le monitoring (multi-profil vs simple booléen) ? → A: Simple booléen `monitored` sur la table `companies`. Déploiement mono-utilisateur par installation (chaque utilisateur clone le repo avec sa propre DB). La couture `profile_id` existante reste sur jobs/scores et n'est pas étendue au monitoring.
- Q: Comment les entreprises entrent-elles dans le système ? → A: Deux voies. (a) Accumulation automatique : chaque job scrapé alimente `companies` (sans ATS résolu, donc pas encore monitorable). (b) Ajout manuel via l'UI : l'utilisateur fournit l'URL carrières, le système déduit le `scrape_method`. Le toggle monitoring n'est activable que si un `scrape_method` est résolu.
- Q: Comment détecter l'ATS d'une entreprise depuis son URL carrières ? → A: D'abord match du hostname (boards.greenhouse.io, jobs.lever.co, *.ashbyhq.com, *.myworkdayjobs.com, careers.smartrecruiters.com, apply.workable.com…). Si le domaine est un vanity (fréquent en crypto : Coinbase, Ripple, Sygnum, Taurus…), un fetch unique de la page carrières repère le board sous-jacent. Fallback manuel : l'utilisateur peut saisir provider + identifiant à la main.
- Q: Quels providers ATS sont dans le scope du MVP ? → A: Priorité 1 : Greenhouse, Lever, Ashby. Priorité 2 : Workday (endpoint JSON /wday/cxs/{tenant}/{site}/jobs), SmartRecruiters (API publique), Workable. Différés hors MVP : Oracle HCM (EFG — JS-lourd) et SAP SuccessFactors (Pictet — JS-lourd), qui nécessiteraient un navigateur headless (nouvelle dépendance, contraire au principe chirurgical).
- Q: Comment le toggle monitoring interagit-il avec la config ATS ? → A: Pause = `monitored = false`. La config de scraping (`ats_provider`, `ats_identifier`, `scraper_id`) persiste et n'est jamais supprimée pour une simple pause. L'adaptateur itère les entreprises `monitored = true` — une entreprise en pause est ignorée sans toucher à sa configuration.
- Q: Quel est le mode d'exécution du monitoring ? → A: `scrape.py` gagne un flag `--monitored-only` (dans le style de `--profile` / `--rescore`). Le run ne touche que les sources company-keyed (pas les boards d'agrégation). Peut tourner plus souvent que le filet large. Dédoublonnage contre les jobs existants pour ne faire remonter que les nouvelles offres.
- Q: Quelle mise en valeur visuelle pour les jobs monitorés ? → A: Badge de provenance `🎯 Monitored · {company}`, indépendant du score, visible même sans scoring. Optionnellement, tri/épinglage des jobs monitorés en tête.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Toggle company monitoring (Priority: P1)

A user browsing companies in the tracker identifies one they want to monitor closely. They open the company, toggle monitoring on, and the system records it. At the next monitoring run, openings from that company appear scored and visually distinguished.

**Why this priority**: Monitoring has no value until companies are marked. This is the foundational action that unlocks all downstream behaviour.

**Independent Test**: Can be fully tested by toggling a company's monitoring flag in the UI, verifying the flag persists across page reloads, and confirming the company appears in the monitored list in Settings.

**Acceptance Scenarios**:

1. **Given** a company that has a resolved `scrape_method` (ATS identified or dedicated scraper exists), **When** the user toggles monitoring on, **Then** the company is immediately marked as monitored and the toggle reflects the new state.
2. **Given** a company that has no resolved `scrape_method`, **When** the user attempts to toggle monitoring on, **Then** the system informs them monitoring is unavailable for this company ("no scrapable source detected") and the toggle remains off.
3. **Given** a monitored company, **When** the user toggles monitoring off, **Then** the company is immediately unmarked and will no longer be included in monitoring runs. Existing jobs and scores from that company remain untouched. The scraping configuration (`ats_provider`, `ats_identifier`, `scraper_id`) is preserved.
4. **Given** a monitored company, **When** the user toggles monitoring off and then on again, **Then** the company resumes monitoring with no configuration loss — the scraping config was never deleted.

---

### User Story 2 - Monitoring-only scrape run (Priority: P1)

The user runs the agent in monitoring-only mode (`scrape.py --monitored-only`). The agent iterates over companies where `monitored = true`, invokes the appropriate ATS scraper for each, and writes all returned openings to the database. The coarse PM-family title gate is applied later, just before scoring — not during scraping. The run completes quickly because it touches only monitored companies (hundreds, not thousands of targets) and skips all broad aggregation boards.

**Why this priority**: The monitoring run is the operational heart of the feature — without it, marking companies has no effect.

**Independent Test**: Run `scrape.py --monitored-only` with at least one monitored company that has a known ATS, verify its openings are written to the DB, and verify jobs from non-monitored companies are absent from the run output.

**Acceptance Scenarios**:

1. **Given** 5 monitored companies with active ATS scrapers, **When** the user runs `--monitored-only`, **Then** the agent scrapes only those 5 companies and writes all their openings to the DB. No broad aggregation boards are contacted.
2. **Given** 0 monitored companies, **When** the user runs `--monitored-only`, **Then** the agent reports "nothing to monitor" and exits cleanly without errors.
3. **Given** a monitored company whose ATS returns 20 openings across all functions, **When** `--monitored-only` runs, **Then** all 20 openings are written to the DB. No filtering happens at scrape time — the scraper remains a pure function (company → openings).
4. **Given** a monitored company with monitoring paused (`monitored = false`), **When** `--monitored-only` runs, **Then** that company is skipped and its openings are not collected.

---

### User Story 3 - Product management title gate (Priority: P1)

After scraping, before any LLM call is made against a job, a deterministic title check determines whether the job falls within the product management family. Jobs that pass proceed to scoring. Jobs that fail are marked with a `filtered_non_product` disposition in the database and never consume Groq quota.

The title gate uses substring matching (case-insensitive, never exact equality) against a generous inclusion list, ordered by interest: **Product Manager**, **Senior / Staff / Principal / Lead / Group Product Manager**, **Director of Product / Head of Product / VP Product / Chief Product Officer**, **Product Owner**, **Technical Product Owner**. The gate errs on the side of inclusion — a false positive costs one LLM call; a false negative loses a target.

Known regression: the title "Senior Tech Product Owner" (FELFEL) MUST pass the gate.

**Why this priority**: The gate protects the Groq daily quota (1000 req/day). Without it, every opening from every monitored company — including engineering, sales, legal — would consume a scoring call.

**Independent Test**: Feed the gate a set of known titles (both PM and non-PM) and verify correct pass/fail decisions against the inclusion list.

**Acceptance Scenarios**:

1. **Given** a job titled "Senior Product Manager, Crypto", **When** the title gate runs, **Then** the job passes and proceeds to scoring.
2. **Given** a job titled "Senior Tech Product Owner", **When** the title gate runs, **Then** the job passes (FELFEL regression case).
3. **Given** a job titled "Software Engineer, Backend", **When** the title gate runs, **Then** the job is marked `filtered_non_product` and never reaches the LLM.
4. **Given** a job titled "Product Marketing Manager", **When** the title gate runs, **Then** the job passes (substring match on "product manager" — a false positive that costs one LLM call, which is acceptable per the safety rule).

---

### User Story 4 - Monitored signal in scoring (Priority: P2)

When a job originates from a monitored company and passes the title gate, the scorer receives context indicating "this company is actively targeted by the user." This is a strong positive signal injected into the LLM scoring context (prose), not a hard-coded arithmetic bonus.

The signal meaningfully boosts scores for relevant roles — a PM senior at a monitored scale-up crypto company (the user's sweet spot) scores high. However, the signal is not an override: a clearly junior role or a role at a fundamentally mismatched company type still scores low. The downgrade for "large enterprise / bank / Big 4" company types is soft — a senior PM at a private bank lands in the middle, not the bottom.

Seniority is not a hard gate — it is assessed by the LLM from the full job description text and used as a soft demotion signal, not a collection-time filter.

**Why this priority**: The scoring signal is the "why" behind monitoring — but scoring can be tuned iteratively after the collection pipeline works.

**Independent Test**: Score two otherwise-identical jobs (same title, location, description quality) where one is from a monitored company and one is not. Verify the monitored one scores higher. Then score a clearly irrelevant role from a monitored company and verify it still gets a low score.

**Acceptance Scenarios**:

1. **Given** two jobs with identical characteristics except one is from a monitored company, **When** both are scored, **Then** the monitored-company job receives a meaningfully higher score.
2. **Given** a monitored-company job that is clearly junior (explicitly "Junior PM" or "0-2 years experience"), **When** scored, **Then** it scores low despite the monitoring signal.
3. **Given** a monitored-company job at a large private bank for a senior PM role, **When** scored, **Then** it lands in the middle range (soft downgrade, not rejection).
4. **Given** a monitored-company job that passes Tier 0 deterministic filters, **When** scored, **Then** the monitoring signal is reflected in the LLM evaluation context (scoring context prose), not as a hard-coded score bonus.

---

### User Story 5 - Visual distinction in job listings (Priority: P2)

Jobs from monitored companies are visually distinguished in the tracker UI with a provenance badge (`🎯 Monitored · {company}`) independent of the score. A mediocre-scoring job from a dream company catches the eye before a high-scoring job from an unknown company. The badge is visible even when the tracker is viewed in a minimalist mode without scoring.

Optionally, monitored jobs can be pinned or sorted to the top of the list.

**Why this priority**: Visual distinction is the UI payoff, but it can be added after the collection and scoring pipeline works.

**Independent Test**: Load the Jobs page with a mix of monitored and non-monitored jobs at various scores. Verify monitored-company jobs carry the provenance badge distinct from the score badge.

**Acceptance Scenarios**:

1. **Given** the Jobs list contains jobs from both monitored and non-monitored companies, **When** displayed, **Then** monitored-company jobs show the badge `🎯 Monitored · {company}` visible without clicking into the job.
2. **Given** a monitored-company job with a low score (e.g., 3/10), **When** displayed next to a non-monitored job with a high score (e.g., 8/10), **Then** the monitored job's provenance badge is equally prominent — it does not depend on score.
3. **Given** the user filters jobs by minimum score, **When** a monitored-company job falls below the filter threshold, **Then** it is still hidden like any other low-score job (the badge does not override filters).
4. **Given** the tracker is viewed without scoring enabled, **When** the Jobs list renders, **Then** the provenance badge is still visible.

---

### User Story 6 - Add a company to monitor (Priority: P2)

The user wants to monitor a company that is not yet in the system. They provide the company's careers URL via the UI. The system attempts to detect the ATS provider: first by matching the hostname against known ATS patterns (boards.greenhouse.io, jobs.lever.co, *.ashbyhq.com, *.myworkdayjobs.com, careers.smartrecruiters.com, apply.workable.com…), then — if the domain is a vanity (common in crypto: Coinbase, Ripple, Sygnum, Taurus…) — by fetching the careers page once to discover the embedded ATS board. If detection succeeds, the company is created with a resolved `scrape_method` and the monitoring toggle becomes available. If detection fails, the user can manually enter the provider and identifier, or the company is saved without a resolved method (not monitorable).

**Why this priority**: Adding companies is the growth path for the monitoring feature. It's P2 because existing companies from broad scrapes already flow into the system automatically.

**Independent Test**: Enter a known Greenhouse careers URL, verify the system resolves `scrape_method = greenhouse` and the monitoring toggle activates. Enter a non-careers URL, verify the system reports detection failure.

**Acceptance Scenarios**:

1. **Given** the user enters `https://boards.greenhouse.io/fireblocks`, **When** the system processes it, **Then** it detects Greenhouse, resolves `ats_provider = greenhouse` and `ats_identifier = fireblocks`, and the monitoring toggle becomes active.
2. **Given** the user enters a vanity domain `https://careers.coinbase.com`, **When** the system fetches the page, **Then** it discovers the underlying ATS board, resolves the scrape method, and activates the toggle.
3. **Given** the user enters a URL that doesn't match any known ATS and whose page doesn't reveal a board, **When** detection completes, **Then** the system reports "no scrapable source detected — you can enter the provider manually" and the toggle remains inactive.
4. **Given** detection failed, **When** the user manually enters provider + identifier, **Then** the company becomes monitorable and the toggle activates.

---

### User Story 7 - Manage monitored companies in Settings (Priority: P3)

The Settings page includes a panel listing all monitored companies with their ATS provider, monitoring status (active/paused), and last monitoring check timestamp. The user can pause, resume, or remove companies from this list. Pausing sets `monitored = false` while preserving all scraping configuration.

**Why this priority**: Bulk management is useful at scale but can be handled company-by-company initially.

**Independent Test**: Navigate to Settings, view the monitored companies list, pause one, verify it's skipped in the next `--monitored-only` run.

**Acceptance Scenarios**:

1. **Given** 50 monitored companies, **When** the user opens Settings, **Then** all 50 are listed with their ATS provider, monitoring status, and last check date.
2. **Given** a monitored company in the Settings list, **When** the user clicks "Pause", **Then** `monitored` is set to `false` but the scraping configuration persists. The company no longer appears in monitoring runs.
3. **Given** a paused company, **When** the user clicks "Resume", **Then** monitoring resumes with the same scraping configuration as before the pause.

---

### Edge Cases

- What happens when a monitored company changes its ATS provider (e.g., Greenhouse → Lever)? The company's `ats_provider` and `ats_identifier` fields must be updatable without affecting monitoring status.
- What happens when a monitored company's ATS returns zero openings for multiple consecutive runs? The company remains monitored; no special action is taken. Stale companies are a user-managed concern.
- What happens when the user monitors a company, then that company's scraper is later disabled globally (e.g., the scraper's `ENABLED = False`)? The monitoring toggle should show a warning state ("scraper unavailable").
- What happens when a job appears in both a broad scrape and a monitoring run? The deduplication by URL ensures only one row exists; the job's monitored-company status is derived from the company relationship, not the scrape source.
- What happens when the user tries to monitor a company that already has 200+ monitored siblings? The system allows it — the "hundreds not thousands" limit is user-driven, not system-enforced. Performance targets assume hundreds; degradation beyond that is acceptable.
- What happens when the title gate encounters a genuinely ambiguous title (e.g., "Product Specialist" — could be PM or could be sales)? Per the safety rule, it passes — a false positive costs one LLM call, a false negative loses a target.

## Requirements *(mandatory)*

### Functional Requirements

**Monitoring state (on companies)**

- **FR-001**: The system MUST allow the user to mark any company that has a resolved `scrape_method` (ATS identified or dedicated scraper exists) as monitored via a toggle.
- **FR-002**: The system MUST prevent monitoring from being enabled for companies that have no resolved `scrape_method` and MUST inform the user why ("no scrapable source detected").
- **FR-003**: The system MUST allow the user to disable monitoring for any monitored company by setting `monitored = false`. The scraping configuration (`ats_provider`, `ats_identifier`, `scraper_id`) MUST persist and MUST NOT be deleted.
- **FR-003a**: Pausing and fully removing a company from monitoring are the same operation (`monitored = false`). Re-enabling restores the same scraping configuration that was preserved.

**Company population**

- **FR-004**: Companies MUST enter the system through two paths: (a) automatic accumulation — every scraped job with a new company name creates a row in `companies` (without a resolved `scrape_method`, therefore not monitorable until enriched); (b) manual addition via the UI — the user provides a careers URL, and the system attempts to detect the ATS and resolve the `scrape_method`.
- **FR-005**: The system MUST detect the ATS provider from a careers URL using hostname pattern matching against a known list: `boards.greenhouse.io`, `jobs.lever.co`, `*.ashbyhq.com`, `*.myworkdayjobs.com`, `careers.smartrecruiters.com`, `apply.workable.com`, and others as added.
- **FR-006**: When hostname matching fails (vanity domain), the system MUST attempt a single HTTP fetch of the careers page to discover an embedded ATS board URL.
- **FR-007**: The system MUST provide a manual fallback: the user can directly enter the ATS provider and identifier (e.g., `greenhouse` + `fireblocks`) when automatic detection fails.

**ATS providers — MVP scope**

- **FR-008**: The system MUST support scraping from Greenhouse, Lever, and Ashby ATS providers at launch (Priority 1 — these cover the crypto/Web3 cluster).
- **FR-009**: The system MUST support scraping from Workday (via JSON endpoint `/wday/cxs/{tenant}/{site}/jobs`), SmartRecruiters (public API), and Workable at launch (Priority 2).
- **FR-010**: Oracle HCM and SAP SuccessFactors are explicitly OUT OF SCOPE for the MVP — they require a headless browser (new dependency, violates the surgical-change principle) and are deferred until a monitored target makes them unavoidable.

**Monitoring-only execution mode**

- **FR-011**: `scrape.py` MUST support a `--monitored-only` flag that limits scraping to company-keyed sources for companies where `monitored = true`. Broad aggregation boards MUST NOT be contacted in this mode.
- **FR-012**: In `--monitored-only` mode, the scraper MUST act as a pure function: company → all openings. No title filtering, no relevance filtering — every opening returned by the ATS is written to the database.
- **FR-013**: The existing broad scraping pipeline (aggregation boards + company-keyed discovery) MUST continue operating unchanged when `--monitored-only` is added. `--monitored-only` is additive, not a replacement.
- **FR-014**: Job deduplication by URL MUST ensure that jobs appearing in both broad and monitoring runs are stored once, with monitored-company status derived from the company relationship.

**Title gate (product management family filter)**

- **FR-015**: A deterministic title gate MUST run against every job BEFORE any LLM call is made. The gate uses case-insensitive substring matching (never exact equality) against this inclusion list: Product Manager, Senior Product Manager, Staff Product Manager, Principal Product Manager, Lead Product Manager, Group Product Manager, Director of Product, Head of Product, VP Product, Chief Product Officer, Product Owner, Technical Product Owner.
- **FR-016**: Jobs whose title matches the inclusion list MUST proceed to scoring. Jobs whose title does not match MUST be marked with the disposition `filtered_non_product` in the database and MUST NOT consume any LLM quota.
- **FR-017**: The gate MUST err on the side of inclusion. Known regression: the title "Senior Tech Product Owner" MUST pass.

**Scoring signal**

- **FR-018**: The scorer MUST treat "job is from a monitored company" as a strong positive signal.
- **FR-019**: The monitoring signal MUST be injected into the LLM evaluation context as prose (scoring context), NOT applied as a hard-coded arithmetic score bonus.
- **FR-020**: The monitoring signal MUST meaningfully boost scores for well-matched roles while allowing clearly mismatched roles (junior, wrong company type) to score low. Soft downgrades apply for enterprise/bank/Big 4 company types — a senior PM at a private bank lands in the middle range, not the bottom.
- **FR-021**: Seniority MUST be assessed by the LLM from the full job description as a soft signal, NOT as a hard collection-time gate. A job with a modest title but clearly senior responsibilities must still be evaluated fairly.

**Visual distinction**

- **FR-022**: Jobs from monitored companies MUST display a provenance badge in the format `🎯 Monitored · {company}` in the job list, independent of score.
- **FR-023**: The badge MUST be visible even when the tracker is viewed without scoring enabled.
- **FR-024**: Optionally, monitored jobs MAY be sortable/pinnable to the top of the list.

**Settings management**

- **FR-025**: The Settings page MUST include a panel listing all monitored companies with their ATS provider, monitoring status (active/paused), and last monitoring check timestamp.
- **FR-026**: The Settings panel MUST allow pausing (setting `monitored = false`) and resuming (setting `monitored = true`) per company without losing scraping configuration.

### Key Entities

- **Company**: Gains a `monitored` boolean (opt-in flag). The scraping configuration fields (`ats_provider`, `ats_identifier`, `scraper_id`) already exist or are added; they persist across pause/resume cycles and are never deleted for a simple pause. `scrape_method` is derived or stored to determine whether the toggle is available.
- **Job**: Gains a `filtered_non_product` disposition (boolean or status field) set by the title gate when a job's title doesn't match the PM family. No other new fields — monitored status is derived through `company_id → companies.monitored`.
- **Monitoring Run**: A pipeline run with `run_type = monitored_only`, tracked in the existing `pipeline_runs` table (or equivalent). Distinct from the broad scrape run type.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Toggling monitoring on/off for a company takes effect immediately (next page render reflects the new state).
- **SC-002**: A `--monitored-only` run completes in under 60 seconds for up to 100 monitored companies (assuming standard ATS API response times).
- **SC-003**: Jobs from monitored companies are visually identifiable in the job list within 1 second of page load (the badge is part of the initial render).
- **SC-004**: The title gate correctly passes the FELFEL regression case ("Senior Tech Product Owner") and all standard PM title variants.
- **SC-005**: A job from a monitored company scores at least 1 point higher than an otherwise-identical job from a non-monitored company, while a clearly irrelevant monitored-company job (junior, wrong domain) can still score below the user's relevance threshold.
- **SC-006**: Non-PM jobs (engineering, sales, legal, etc.) from monitored companies never reach the LLM — they are caught by the title gate and marked `filtered_non_product`.
- **SC-007**: The user can add a company via careers URL, have its ATS detected, toggle monitoring on, and see its openings appear scored and badged within two monitoring runs.
- **SC-008**: Pausing a company prevents it from being contacted during the next `--monitored-only` run, verified by checking run output.

## Assumptions

- The project already has ATS scrapers (Greenhouse, Lever, Ashby) that can be invoked per-company given a provider + identifier pair. This feature adds the company-level toggle, the ATS detection from URL, the `--monitored-only` execution mode, the title gate, and the provenance badge — it does not build new ATS scraper adapters from scratch (except Workday/SmartRecruiters/Workable as Priority 2).
- The user runs the system alone (single-user per installation). Monitoring state is a simple boolean on the `companies` table, not a per-profile relation. Multi-user scenarios are handled by separate installations, each with their own database.
- The number of monitored companies will stay in the hundreds. Performance targets assume this scale; degradation beyond hundreds is acceptable and not a priority.
- The Groq free tier daily quota (1000 req/day) is a binding constraint. The title gate exists primarily to protect this quota by filtering out non-PM openings before they reach the LLM.
- Companies that enter via automatic accumulation (scraped jobs) may not have a resolved ATS — they are not monitorable until the user manually enriches them with a careers URL or provider + identifier.
- The title gate placement (post-scrape, pre-LLM) is intentional: it respects the constitution's principle that scrapers are broad and filtering is the scorer's domain, while pragmatically protecting the LLM quota. The `filtered_non_product` disposition preserves auditability.
