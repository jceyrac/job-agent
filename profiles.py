"""
profiles.py — Search profile definitions for job_agent
=======================================================
Each profile drives:
  - Canonical ``job_titles`` and ``title_exclude`` — single source for search
    queries, title gate, scrape net, and scoring pre-filter (spec 017)
  - ``languages_spoken`` — positive allowlist for Tier-0 language filtering
  - ``allowed_contract_types`` — contract-type Tier-0 / digest filter
  - Geography per work mode via ``work_mode_geography`` (spec 016)
  - Search locations derived from ``work_mode_geography`` by default
    (``effective_search_locations()``), overridable via ``search_locations``
  - Which work_modes are accepted (post-scoring filter)
  - Which company_sizes are accepted (empty = no filter)
  - Score threshold for inclusion in the digest
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SearchProfile:
    id: str
    name: str
    # deprecated: superseded by work_mode_geography (spec 016); retained for back-compat round-trip
    allowed_geo_zones: list[str]          # post-scoring geo filter
    allowed_work_modes: list[str]         # post-scoring work_mode filter
    location_keywords: list[str]          # pre-scoring location filter (OR match, empty = disabled)
    company_sizes: list[str]              # post-scoring company_size filter (empty = no filter)
    score_threshold: int = 5              # minimum score to appear in digest
    remote_or_hybrid: bool = True         # pre-scoring: exclude fully on-site jobs
    scoring_context: str = ""             # injected at top of scorer system prompt
    pre_filter: dict = field(default_factory=dict)  # SQL pre-filter before LLM scoring
    # deprecated: superseded by work_mode_geography (spec 016); retained for back-compat round-trip
    allowed_countries: Optional[list[str]] = None   # None = no restriction; list = allowlist (unknown always passes)
    # deprecated: removed as enforcement mechanism (spec 017); retained for round-trip
    banned_countries: list[str] = field(default_factory=list)   # hard-reject at Tier-0 even when geo_zone='global_remote'; empty = disabled
    # deprecated: superseded by work_mode_geography (spec 016); retained for back-compat round-trip
    hybrid_ok_countries: list[str] = field(default_factory=list)  # hybrid roles whose company_country is set and not in this list → score 2; empty = disabled
    denylisted_companies: list[str] = field(default_factory=list)  # companies to hard-reject at Tier-0 before any LLM call; empty = disabled
    excluded_sectors: list[str] = field(default_factory=list)   # sector codes to exclude from digest
    # deprecated: superseded by languages_spoken (spec 017); retained for round-trip
    excluded_languages: list[str] = field(default_factory=list) # language codes to exclude from digest
    work_mode_geography: dict = field(default_factory=dict)  # per-work-mode country allowlists + geo_zone fallback (spec 016)

    # ── Canonical search fields (spec 017) ─────────────────────────────────
    job_titles: list[str] = field(default_factory=list)           # single include list — gate + jobspy query + scrape net + pre_filter
    title_exclude: list[str] = field(default_factory=list)         # single exclude list — scrape exclude + pre_filter exclude
    allowed_contract_types: list[str] = field(default_factory=list)# empty = no restriction; allowlist over extraction vocab {permanent, freelance, contract, internship, unknown}
    languages_spoken: list[str] = field(default_factory=list)      # positive allowlist; empty = no language restriction

    # ── Search inputs (relocated from scrape.py + scraper modules) ──────────
    # When a list is empty, the consuming scraper falls back to its module
    # constant. Populate these to make the active profile the source of truth.
    # deprecated: superseded by job_titles (spec 017); retained for round-trip
    scrape_titles: list[str] = field(default_factory=list)      # broad post-fetch title net (scrape.py JobFilter.titles)
    # deprecated: superseded by title_exclude (spec 017); retained for round-trip
    scrape_exclude: list[str] = field(default_factory=list)     # post-fetch exclusions (scrape.py JobFilter.exclude)
    scrape_remote_or_hybrid: bool = False                       # scrape.py JobFilter.remote_or_hybrid
    # deprecated: superseded by job_titles (spec 017); retained for round-trip
    search_query_titles: list[str] = field(default_factory=list)  # queries sent to jobspy (LinkedIn/Indeed)
    search_locations: list[str] = field(default_factory=list)     # location display names for jobspy (empty → derived from work_mode_geography)
    greenhouse_boards: list[str] = field(default_factory=list)    # Greenhouse board tokens

    def effective_search_locations(self) -> list[str]:
        """Where jobspy should search: explicit override if set, else the union of
        all work_mode_geography countries (dedup, order-preserving)."""
        if self.search_locations:
            return list(self.search_locations)
        wmg = self.work_mode_geography or {}
        out: list[str] = []
        for mode in ("on-site", "hybrid", "remote"):
            out += (wmg.get(mode, {}) or {}).get("countries", []) or []
        return list(dict.fromkeys(out))

    def to_criteria_dict(self) -> dict:
        """Serialisable en JSON pour stockage dans search_profiles.criteria."""
        return {
            "allowed_geo_zones":  self.allowed_geo_zones,
            "allowed_work_modes": self.allowed_work_modes,
            "location_keywords":  self.location_keywords,
            "company_sizes":      self.company_sizes,
            "score_threshold":    self.score_threshold,
            "remote_or_hybrid":   self.remote_or_hybrid,
            "scoring_context":    self.scoring_context,
            "pre_filter":         self.pre_filter,
            "allowed_countries":    self.allowed_countries,
            "banned_countries":     self.banned_countries,
            "hybrid_ok_countries":    self.hybrid_ok_countries,
            "denylisted_companies":  self.denylisted_companies,
            "excluded_sectors":      self.excluded_sectors,
            "excluded_languages":   self.excluded_languages,
            "work_mode_geography":  self.work_mode_geography,
            "job_titles":           self.job_titles,
            "title_exclude":        self.title_exclude,
            "allowed_contract_types": self.allowed_contract_types,
            "languages_spoken":     self.languages_spoken,
            # ── Search inputs ─────────────────────────────────────────
            "scrape_titles":          self.scrape_titles,
            "scrape_exclude":         self.scrape_exclude,
            "scrape_remote_or_hybrid": self.scrape_remote_or_hybrid,
            "search_query_titles":    self.search_query_titles,
            "search_locations":       self.search_locations,
            "greenhouse_boards":      self.greenhouse_boards,
        }

    @classmethod
    def from_criteria(cls, id: str, name: str, criteria: dict) -> "SearchProfile":
        """Build a profile from a persisted criteria dict. Forward-prep for the
        Phase-1 Settings UI; not used by get_active_profile() in Phase 0."""
        wmg = criteria.get("work_mode_geography")
        if not wmg:
            legacy_allowed = criteria.get("allowed_countries") or []
            legacy_hybrid  = criteria.get("hybrid_ok_countries") or []
            legacy_zones   = criteria.get("allowed_geo_zones") or []
            wmg = {
                "on-site": {"countries": legacy_hybrid or ["Switzerland"]},
                "hybrid":  {"countries": legacy_hybrid or ["Switzerland"]},
                "remote":  {"countries": legacy_allowed, "geo_zones": legacy_zones},
            }
        jt = criteria.get("job_titles")
        if not jt:
            legacy = (criteria.get("scrape_titles") or []) + (criteria.get("search_query_titles") or [])
            jt = list(dict.fromkeys(legacy))
        te = criteria.get("title_exclude") or criteria.get("scrape_exclude") or []
        ls = criteria.get("languages_spoken")
        if ls is None:
            ls = ["french", "english"]
        act = criteria.get("allowed_contract_types")
        if act is None:
            act = ["permanent", "freelance", "contract", "unknown"]
        return cls(
            id=id,
            name=name,
            allowed_geo_zones=criteria.get("allowed_geo_zones", []),
            allowed_work_modes=criteria.get("allowed_work_modes", []),
            location_keywords=criteria.get("location_keywords", []),
            company_sizes=criteria.get("company_sizes", []),
            score_threshold=criteria.get("score_threshold", 5),
            remote_or_hybrid=criteria.get("remote_or_hybrid", True),
            scoring_context=criteria.get("scoring_context", ""),
            pre_filter=criteria.get("pre_filter", {}),
            allowed_countries=criteria.get("allowed_countries"),
            banned_countries=criteria.get("banned_countries", []),
            hybrid_ok_countries=criteria.get("hybrid_ok_countries", []),
            denylisted_companies=criteria.get("denylisted_companies", []),
            excluded_sectors=criteria.get("excluded_sectors", []),
            excluded_languages=criteria.get("excluded_languages", []),
            work_mode_geography=wmg,
            job_titles=jt,
            title_exclude=te,
            allowed_contract_types=act,
            languages_spoken=ls,
            scrape_titles=criteria.get("scrape_titles", []),
            scrape_exclude=criteria.get("scrape_exclude", []),
            scrape_remote_or_hybrid=criteria.get("scrape_remote_or_hybrid", False),
            search_query_titles=criteria.get("search_query_titles", []),
            search_locations=criteria.get("search_locations", []),
            greenhouse_boards=criteria.get("greenhouse_boards", []),
        )


def load_active_profile(db) -> "SearchProfile":
    """Resolve the active profile from the DB, seeding from code on first run.
    DB is the source of truth; ACTIVE_PROFILE (UNIFIED_JC) is the seed."""
    pid = db.get_config("active_profile_id", DEFAULT_PROFILE_ID)
    row = db.get_profile(pid)
    if not row:
        seed = ALL_PROFILES.get(pid, ACTIVE_PROFILE)
        db.upsert_profile(seed)
        db.set_config("active_profile_id", seed.id)
        return seed
    profile = SearchProfile.from_criteria(row["id"], row["name"], row["criteria"])
    # Backfill scoring_context from the seed profile when the stored row
    # predates the preference-model v3 field (onboarding gate depends on it).
    if not profile.scoring_context.strip():
        seed = ALL_PROFILES.get(pid, ACTIVE_PROFILE)
        if seed and seed.scoring_context.strip():
            profile.scoring_context = seed.scoring_context
            db.upsert_profile(profile)
    return profile


# ---------------------------------------------------------------------------
# Single search profile
# ---------------------------------------------------------------------------

# The single search profile the app manages. A rich scoring_context is the
# primary preference-modelling mechanism; pre_filter carries only hard exclusions.

UNIFIED_JC = SearchProfile(
    id="unified_jc",
    name="Unified JC",
    allowed_geo_zones=["europe", "global_remote", "unknown"],
    allowed_work_modes=["remote", "hybrid", "on-site", "unknown"],
    location_keywords=[],
    company_sizes=["startup", "scaleup", "sme"],
    score_threshold=5,
    remote_or_hybrid=True,
    pre_filter={
        "title_contains": [
            "product manager", "product owner", "head of product",
            "vp product", "lead product", "staff product",
        ],
        "exclude_title_contains": [
            "junior", "intern",
        ],
        "exclude_location_contains": [
            "united states", " usa ", "new york", "san francisco",
            "los angeles", "seattle", "boston", "chicago",
        ],
    },
    # ── Experimental unified scoring_context ─────────────────────────────
    # This is the primary mechanism for preference modelling. Hard exclusions
    # (junior/intern, US-only remote, on-site outside CH) are handled by
    # pre_filter and remote_or_hybrid; everything else is decided by the LLM.
    scoring_context="""You are scoring jobs for a Senior Product Manager based in Switzerland
with 10+ years of experience across fintech, Web3, and AI. The candidate
spent years at Accenture consulting for banks and insurers, plus
freelance work for both large corporates and startups/scale-ups.

Score from 1 (terrible fit) to 10 (excellent fit). Be honest — most jobs
will score 3-6. Reserve 8+ for genuinely strong matches and 9-10 for
roles that hit multiple criteria simultaneously.

# HARD EXCLUSIONS — NEVER exceed these score caps regardless of other signals
# These rules take priority over everything else. Apply FIRST, then score within the cap.
- LARGE CORPORATES (banks, insurance, Big 4 consulting, enterprise IT,
  stock exchanges, financial market infrastructure): MAX SCORE = 3.
  The candidate spent years at Accenture and explicitly does not want
  to return to this environment. Examples: SIX Group, UBS, Credit Suisse,
  SwissRe, Zurich Insurance, BNP Paribas, Deutsche Bank, SAP, Oracle,
  IBM, Accenture, Deloitte, PwC, EY, KPMG.
- JUNIOR / INTERN / ASSOCIATE / non-PM roles: MAX SCORE = 3.

# Strong preference: company type
- Startups, scale-ups, and SMEs are the target. Lean, flat, agile
  cultures. Treat scale-up and SME as roughly equivalent.
- Headcount under ~500 is a positive signal; under ~200 is stronger.
- Recent funding rounds (Seed to Series C) are a positive signal.

# Ranking keys (in order)
Rank a job by, in order: (1) domain — Web3-RWA / crypto-fintech bridge
is the target, traditional fintech is solid, pure Web3/DeFi is
high-interest but a CV stretch; (2) Swiss-employer certainty — a role
with a Swiss company outranks an equivalent remote role from a foreign
company, because pay and the right to keep living in Switzerland are
guaranteed rather than needing verification; (3) work mode — remote >
hybrid, with flexible 2–3 day hybrid ranking near remote and rigid 4+
day hybrid lower.

# Scoring bands
Assign the band of the highest tier the job satisfies, then nudge ±1
within the band on soft signals (AI in product, DeFi vs generic, funding
stage, headcount, flexibility).

| Tier | Criteria                                            | Band |
|------|-----------------------------------------------------|------|
| 1    | CH employer, Web3-RWA / crypto-fintech, remote      | 9–10 |
| 2    | CH employer, Web3-RWA / crypto-fintech, hybrid      | 8–9  |
| 3    | Any HQ, Web3-RWA / crypto-fintech, remote, CH-viable | 7–8 |
| 4    | CH employer, fintech ±AI, remote                    | 6–7  |
| 5    | CH employer, fintech ±AI, hybrid                    | 5–6  |
| 6    | EU / Türkiye employer, fintech ±AI, remote          | 4–5  |
| 7    | Leaves CH, off-domain, or clearly underpaid         | <3   |

# Industry fit (domain context)
1. PERFECT but rare — Web3 RWA (real-world assets), tokenization of
   traditional finance, regulated DeFi, stablecoins with real use cases.
2. REALISTIC SWEET SPOT — fintech companies bridging Web2 and Web3:
   crypto-friendly neobanks, custody, on-ramps/off-ramps, traditional
   fintech adding tokenization or crypto rails, embedded finance with
   blockchain components.
3. SOLID — traditional fintech startups/scale-ups: payments, lending,
   wealthtech, regtech, embedded finance, B2B SaaS for financial
   services.
4. INTERESTING BUT HARDER — pure Web3 / DeFi / crypto-native companies.
   Score the role on its merits but CAP THE SCORE at 7-8 maximum. The
   candidate lacks direct Web3 PM experience (CV stretch). Even if the
   role looks perfect on paper, it is NOT a 9 or 10 — the candidate
   has never worked at a crypto-native company. This cap applies even
   when the company is based in Switzerland. A pure Web3 role that
   would score 9-10 as an RWA/bridge role MUST score 7-8 max.
5. NEUTRAL BONUS — companies using AI in their product (good signal of
   innovation). AI-native companies building foundation models or core
   AI products are a stretch given no direct AI PM experience — score
   the role on the PM fit, not the AI angle.
6. LOW FIT — non-fintech B2B SaaS, e-commerce, healthtech, edtech,
   media. Score on PM fundamentals only, no industry bonus.
7. AVOID (soft signal, not a hard filter): pharma, government, pure
   retail/manufacturing, energy — score low unless a genuine fintech/Web3
   angle is present.

# Geography and work mode
- The candidate stays in Switzerland. Foreign roles are acceptable only
  when fully remote (works from Switzerland for a foreign company).
  Hybrid or on-site outside Switzerland is out (already enforced before
  scoring).
- ON-SITE IN SWITZERLAND IS OK. Do NOT penalize on-site roles at Swiss
  companies. The candidate can commute within Switzerland. Score these
  roles on their domain/company fit, not the work mode.
- IDEAL: Fully remote role with a Swiss company. The candidate works
  from home in Switzerland.
- ALSO STRONG: Flexible hybrid in Switzerland (Lausanne, Geneva, Zurich,
  Zug, Basel) with 2–3 office days per week. The candidate needs to
  travel to France 1–2 times per month, so flexibility matters.
- ACCEPTABLE: On-site in Switzerland — score based on domain fit, not
  work mode.
- WEAKER: Rigid hybrid requiring 4+ days in a Swiss office.

# Swiss-employer certainty discount
A remote role whose company is based outside Switzerland (EU/Türkiye)
scores ~1–2 below the same role at a Swiss company — it still lets the
candidate stay in Switzerland, but pay and residence eligibility are
unverified.

# Compensation
If the posting states a salary and the role is remote for a non-Swiss
company, judge whether it plausibly sustains Swiss cost of living; if
clearly low, subtract 1 and say so. If no salary is stated, do NOT
penalize — set comp_flag true (see schema) so it can be verified
manually.

# Output
Return JSON with: score (1-10), reason (2-3 sentences explaining the
score, referencing specific signals from the job posting), summary
(1-2 sentences describing the role), work_mode, company_size,
contract_type, geo_zone.

Be specific in the reason. "Good fintech role" is not useful.
"Series B Swiss neobank adding tokenized asset custody, hybrid in
Geneva, headcount ~150 — strong Web2-Web3 bridge fit" is useful.""",
    allowed_countries=[
        "Switzerland", "France", "Spain", "Portugal", "Italy", "Netherlands",
        "Germany", "Ireland", "United Kingdom", "Belgium", "Austria",
        "Sweden", "Denmark", "Finland", "Norway", "Estonia", "Czech Republic",
        "Poland", "Romania", "Greece", "Luxembourg",
        "Türkiye", "Turkey",            # enable EU/Türkiye fintech remote (tier 6)
    ],
    work_mode_geography={
        "on-site": {"countries": ["Switzerland"]},
        "hybrid":  {"countries": ["Switzerland"]},   # user may widen in Settings (e.g. add "France")
        "remote":  {
            "countries": [
                "Switzerland", "France", "Spain", "Portugal", "Italy", "Netherlands",
                "Germany", "Ireland", "United Kingdom", "Belgium", "Austria",
                "Sweden", "Denmark", "Finland", "Norway", "Estonia", "Czech Republic",
                "Poland", "Romania", "Greece", "Luxembourg", "Türkiye", "Turkey",
            ],
            "geo_zones": ["europe", "global_remote", "unknown"],
        },
    },
    banned_countries=[],   # inert — removed as enforcement mechanism (spec 017)
    hybrid_ok_countries=["Switzerland"],
    job_titles=[
        "product manager", "senior product manager", "staff product manager",
        "principal product manager", "lead product manager", "group product manager",
        "director of product", "head of product", "vp product",
        "chief product officer", "product owner", "technical product owner",
        "product lead",
    ],
    title_exclude=["junior", "intern", "stage", "apprentice"],
    allowed_contract_types=["permanent", "freelance", "contract", "unknown"],
    languages_spoken=["french", "english"],
    # Companies repeatedly archived in past digests — extend when a recruiter
    # or aggregator keeps wasting reviewer time.
    denylisted_companies=[
        "EWOR", "EWOR GmbH",
        "Mercor",
        "Agoda",
        "Swiss Federal Administration",
        "Optum",
        "Hire Feed",                   # generic reposter
        "Top Recruit",                 # generic reposter
        "Themesoft Inc.",              # US contracting agency
        "TechHuman",
    ],
    excluded_sectors=[],  # soft via scoring_context (spec 017)
    excluded_languages=["german", "spanish", "dutch", "italian", "czech", "hungarian",
                        "polish", "mandarin", "turkish"],
    # ── Search inputs (exact current values from scrape.py + scraper modules) ──
    scrape_titles=[
        "product manager", "head of product", "cpo", "vp product",
        "product owner", "product lead", "product engineer", "project manager",
    ],
    scrape_exclude=["junior", "intern", "stage", "apprentice"],
    scrape_remote_or_hybrid=False,
    search_query_titles=[
        "product manager", "product owner", "head of product",
        "product lead", "product director", "senior product",
        "lead product manager",
    ],
    search_locations=[],  # empty → derive from work_mode_geography (spec 017)
    greenhouse_boards=[
        # Crypto / Web3 / fintech boards (from CRYPTO_WEB3_BOARDS)
        "coinbase", "chainalysis", "paxos", "avalabs", "consensys",
        "fireblocks", "anchorage", "figment", "bitgo", "kraken",
        "gemini", "ripple", "near", "aptos", "mysten",
        "alchemy", "infura", "opensea", "dydx", "uniswap",
        "aave", "blockdaemon", "ledger", "blockchain", "circle",
        "robinhood", "stripe", "brex", "mercury", "ramp",
    ],
)

# ---------------------------------------------------------------------------
# Active profile — single-profile mode
# ---------------------------------------------------------------------------

ACTIVE_PROFILE: SearchProfile = UNIFIED_JC
ACTIVE_PROFILE_ID: str = UNIFIED_JC.id

# The active profile is the single source of truth for the app.
ALL_PROFILES: dict[str, SearchProfile] = {
    UNIFIED_JC.id: UNIFIED_JC,
}

DEFAULT_PROFILE_ID = UNIFIED_JC.id


def get_active_profile() -> SearchProfile:
    """The single profile the app manages. One source of truth."""
    return ACTIVE_PROFILE
