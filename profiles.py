"""
profiles.py — Search profile definitions for job_agent
=======================================================
Each profile drives:
  - Which geo_zones are accepted (post-scoring filter)
  - Which work_modes are accepted (post-scoring filter)
  - Which location keywords trigger pre-scoring inclusion (empty = no filter)
  - Which company_sizes are accepted (empty = no filter)
  - Score threshold for inclusion in the digest
  - Boost keywords used as search hints (passed to scrapers / scorer context)
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SearchProfile:
    id: str
    name: str
    allowed_geo_zones: list[str]          # post-scoring geo filter
    allowed_work_modes: list[str]         # post-scoring work_mode filter
    location_keywords: list[str]          # pre-scoring location filter (OR match, empty = disabled)
    boost_keywords: list[str]             # context hints for scoring
    company_sizes: list[str]              # post-scoring company_size filter (empty = no filter)
    score_threshold: int = 5              # minimum score to appear in digest
    remote_or_hybrid: bool = True         # pre-scoring: exclude fully on-site jobs
    scoring_context: str = ""             # injected at top of scorer system prompt
    pre_filter: dict = field(default_factory=dict)  # SQL pre-filter before LLM scoring
    allowed_countries: Optional[list[str]] = None   # None = no restriction; list = allowlist (unknown always passes)
    banned_countries: list[str] = field(default_factory=list)   # hard-reject at Tier-0 even when geo_zone='global_remote'; empty = disabled
    hybrid_ok_countries: list[str] = field(default_factory=list)  # hybrid roles whose company_country is set and not in this list → score 2; empty = disabled
    denylisted_companies: list[str] = field(default_factory=list)  # companies to hard-reject at Tier-0 before any LLM call; empty = disabled
    excluded_sectors: list[str] = field(default_factory=list)   # sector codes to exclude from digest
    excluded_languages: list[str] = field(default_factory=list) # language codes to exclude from digest

    # ── Search inputs (relocated from scrape.py + scraper modules) ──────────
    # When a list is empty, the consuming scraper falls back to its module
    # constant. Populate these to make the active profile the source of truth.
    scrape_titles: list[str] = field(default_factory=list)      # broad post-fetch title net (scrape.py JobFilter.titles)
    scrape_exclude: list[str] = field(default_factory=list)     # post-fetch exclusions (scrape.py JobFilter.exclude)
    scrape_remote_or_hybrid: bool = False                       # scrape.py JobFilter.remote_or_hybrid
    search_query_titles: list[str] = field(default_factory=list)  # queries sent to jobspy (LinkedIn/Indeed)
    search_locations: list[str] = field(default_factory=list)     # location display names for jobspy
    greenhouse_boards: list[str] = field(default_factory=list)    # Greenhouse board tokens

    def to_criteria_dict(self) -> dict:
        """Serialisable en JSON pour stockage dans search_profiles.criteria."""
        return {
            "allowed_geo_zones":  self.allowed_geo_zones,
            "allowed_work_modes": self.allowed_work_modes,
            "location_keywords":  self.location_keywords,
            "boost_keywords":     self.boost_keywords,
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
        return cls(
            id=id,
            name=name,
            allowed_geo_zones=criteria.get("allowed_geo_zones", []),
            allowed_work_modes=criteria.get("allowed_work_modes", []),
            location_keywords=criteria.get("location_keywords", []),
            boost_keywords=criteria.get("boost_keywords", []),
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
    return SearchProfile.from_criteria(row["id"], row["name"], row["criteria"])


# ---------------------------------------------------------------------------
# Single search profile
# ---------------------------------------------------------------------------

# The single search profile the app manages. A rich scoring_context is the
# primary preference-modelling mechanism; pre_filter carries only hard exclusions.

UNIFIED_JC = SearchProfile(
    id="unified_jc",
    name="Unified JC",
    allowed_geo_zones=["europe", "global_remote", "unknown"],
    allowed_work_modes=["remote", "hybrid", "unknown"],
    location_keywords=[],
    boost_keywords=["fintech", "web3", "defi", "crypto", "blockchain", "AI",
                    "tokenization", "RWA", "stablecoin", "neobank",
                    "payments", "wealthtech", "regtech", "embedded finance",
                    "startup", "scaleup", "SME", "product"],
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

# Hard exclusions (cap score at 3)
- Large corporates: banks, insurance companies, Big 4 consulting,
  enterprise IT services. The candidate has done this and explicitly
  does not want to return.
- Junior, intern, associate, or non-PM roles (marketing PM, sales PM,
  technical PM without product ownership).
- On-site roles outside Switzerland (no relocation).
- Roles requiring professional German or Spanish (only French and
  English are at professional level).
- US-only remote roles (timezone incompatible).

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
   Score the role on its merits but acknowledge in the reasoning that
   the candidate lacks direct Web3 PM experience (CV stretch). Cap ~1
   below the equivalent RWA/bridge role.
5. NEUTRAL BONUS — companies using AI in their product (good signal of
   innovation). AI-native companies building foundation models or core
   AI products are a stretch given no direct AI PM experience — score
   the role on the PM fit, not the AI angle.
6. LOW FIT — non-fintech B2B SaaS, e-commerce, healthtech, edtech,
   media. Score on PM fundamentals only, no industry bonus.

# Geography and work mode
- The candidate stays in Switzerland. Foreign roles are acceptable only
  when fully remote (works from Switzerland for a foreign company).
  Hybrid or on-site outside Switzerland is out (already enforced before
  scoring).
- IDEAL: Fully remote role with a Swiss company. The candidate works
  from home in Switzerland.
- ALSO STRONG: Flexible hybrid in Switzerland (Lausanne, Geneva, Zurich,
  Zug, Basel) with 2–3 office days per week. The candidate needs to
  travel to France 1–2 times per month, so flexibility matters.
- WEAKER: Rigid hybrid requiring 4+ days in a Swiss office.
- EXCLUDE: On-site or hybrid outside Switzerland (Tier-0 enforced),
  US-only remote, APAC roles.

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
    banned_countries=[
        "United States", "Canada", "Mexico", "Brazil", "Argentina", "Colombia",
        "Singapore", "Hong Kong", "Taiwan", "Japan", "South Korea",
        "Thailand", "India", "Indonesia", "Vietnam", "Philippines",
        "United Arab Emirates", "Israel", "Saudi Arabia", "South Africa",
        "Australia", "New Zealand", "China",
    ],
    hybrid_ok_countries=["Switzerland"],
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
    excluded_sectors=["pharma", "retail", "manufacturing", "government", "healthcare",
                      "energy", "media"],
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
    search_locations=[
        "Switzerland", "France", "United Kingdom", "Netherlands", "Spain",
        "Portugal", "Austria", "Belgium", "Ireland", "Italy", "Germany",
        "Czechia", "Hungary", "Türkiye",
    ],
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
