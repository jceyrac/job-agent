# Carte du sourcing freelance PM — Web3 / Fintech / AI

**Dernière mise à jour : 2026-08-26**
**Profil cible :** Senior Product Manager, 10+ ans, Suisse (Lausanne), français/anglais,
remote-first, domaines fintech / Web3-DeFi / AI. Ouvert CDI **et** freelance/mission.

Ce document consolide toute l'investigation « sourcing freelance » : la carte des
plateformes par modèle d'accès, les impasses vérifiées, et la stratégie
d'intégration au pipeline `job_agent`.

---

## 0. Conclusion en une phrase

**Le PM freelance ne se scrape pas** : il se capte par **profil + alerte email** sur
3-4 plateformes curées, dont deux offrent en plus un board navigable. Le seul point
d'intégration avec le pipeline est **`email-monitor`** : Malt, Free-Work et LeHibou
envoient tous les missions par email → un seul parseur email les fait entrer dans le
digest, sans aucun scraper.

---

## 1. Pourquoi le scraping ne marche pas pour le PM freelance

Mesures réelles (août 2026) :

| Source | Constat | Verdict |
|---|---|---|
| freelancermap CH | **4 PM/PO** sur **665** projets suisses (0,6 %), tous en régie, pages 2+ derrière paywall + reCAPTCHA, API `/api/v1/projects` en 401 | Scraper **abandonné** (spec 024) |
| web3.career | 800+ postes PM mais **tous CDI** ; `/product-manager+freelance-jobs` → **0** | Élargi pour le PM **permanent** remote uniquement |
| RemoteOK | tout marqué `full-time` structurellement | Aucun freelance |
| Boards généralistes (JobSpy Indeed/LinkedIn) | ~50 offres contract/30 j, mais PM freelance rare | Filtre `contract` utile, densité faible |

**Le freelance PM vit sur des marketplaces curées qu'on rejoint, pas sur des boards
qu'on lit.** C'est frustrant pour un pipeline de scraping, mais c'est le marché.

---

## 2. Les plateformes, par modèle d'accès

### Tier 1 — Boards navigables FR/EU (chercher + candidater directement)

**Free-Work** — `free-work.com/fr/tech-it/jobs`
Le plus gros board de missions IT freelance francophone (ex Freelance-Info). Recherche
libre, candidature directe, **alertes email**. Agrège de nombreux intermédiaires (dont
LeHibou). Anti-bot fort (403 au scraping nu) → profil + alerte email, ingérable via
`email-monitor`.

**LeHibou** — `lehibou.com`
900+ grands comptes clients, 140 000 consultants, missions PM/PO navigables,
orienté France / grands comptes. **Point clé** : sur ce segment, le passé Accenture
banque/assurance est un **atout** en freelance (cf. décision scoring : le plafond
grands comptes ne s'applique qu'au permanent).

### Tier 2 — Curés FR (soumettre un profil, ils poussent les missions)

- **Malt** — `malt.fr` — leader EU-natif, modèle **inbound** (le client te trouve).
  Un seul profil, domaines localisés (fr/de/ch/com), visibilité transfrontalière par
  réglages (remote + langues FR/EN). TJM PM 600-1200 €/j. *Voir carte Trello dédiée.*
- **FreelanceRepublik** — `freelancerepublik.com` — matching 48h, 11 900 freelances tech.
- **Comet** — `comet.co` — tech senior, rôles produit, TJM élevés, curated.
- **Crème de la Crème** — premium FR curated.

### Tier 3 — Global / remote curés (domaine Web3/AI)

- **Toptal** — `toptal.com/product-managers` — screening top 3 %, pas de frais côté freelance.
- **A.Team** — squads produit/eng, US+EU.
- **Braintrust** — 0 % côté talent, board public (surtout dev).

### Tier 4 — Agences de contracting suisses (marché régie / ANÜ)

Pas des plateformes : inscription, elles te sollicitent. Elles gatent le marché ANÜ
suisse (les 4 Product Owners freelancermap étaient tous en régie via ce canal).

- **ITech Consult**, **Coopers Group**, **Hays CH**, **SThree** (Computer Futures),
  **Michael Page CH**, **Bosshard & Partner**, **Prostaff**.

### À écarter

Upwork, Fiverr, Freelancer.com/Twago (mismatch seniorité/TJM), Gun.io (US/Canada only),
Codeur/5euros (micro-missions), Workana/PeoplePerHour.

---

## 3. Web3 / crypto freelance (domaine, mais densité freelance faible)

- **LaborX** — `laborx.com` — freelance crypto-native, paiement crypto, board public.
- **Remote3** — `remote3.co` — board Web3 remote, à évaluer.
- Web3.career / CryptoJobsList — déjà scrapés, mais **CDI** essentiellement.

---

## 4. Stratégie d'intégration au pipeline `job_agent`

```
Malt / Free-Work / LeHibou   (profil + alertes)
        │  emails de missions
        ▼
   email-monitor (démon IMAP, déjà en prod)
        │  parse → JobPosting
        ▼
   pipeline → scoring → digest
```

- **Ne pas** construire de scrapers pour ces sources (anti-bot, login, ou modèle inbound).
- **Un seul parseur email** bien fait couvre Malt + Free-Work + LeHibou.
- Action : une fois les premiers emails reçus, relever leur format (expéditeur, sujet,
  structure) pour écrire le parseur. Créer alors une carte « Parser missions freelance
  dans email-monitor ».

---

## 5. Plan d'action

1. **Malt** — profil optimisé + disponibilité + alertes (carte Trello dédiée).
2. **Free-Work** + **LeHibou** — profil + alerte cette semaine (board navigable + email).
3. **Une agence CH** (ITech Consult ou Coopers) — inscription pour le marché régie.
4. *(plus tard)* Comet / A.Team — screening, flux entrant passif.
5. *(pipeline)* Parseur email-monitor une fois le format des emails de missions connu.

---

## 6. Décisions et leçons transverses (issues de l'investigation)

- **Mapping `regie` (ANÜ)** ajouté au scoring : une mission en régie n'est ni permanent
  ni écartée — c'est fonctionnellement du freelance, incluse au digest.
- **Plafond grands comptes conditionnel** : ne s'applique qu'au permanent. Une mission
  freelance en banque est légitime, le passé Accenture y est un atout.
- **Biais `work_mode`** corrigé (les scrapers écrivaient `on-site` par défaut →
  619 offres réhabilitées). Voir cartes Trello.
- **Matching titre allemand** : utiliser des limites de mots (`\bprodukt\b`), pas du
  substring (`produkt` colle à `produktion`, `leiter-produkt` à `projektleiter-…`).
- **Toujours vérifier une URL/param avant de coder** : `contractTypes` vs
  `projectContractTypes`, `/product-manager+freelance-jobs` → 0. Deux impasses évitées
  en testant d'abord.
