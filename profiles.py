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
            "allowed_countries":    self.allowed_countries,
            "banned_countries":     self.banned_countries,
            "hybrid_ok_countries":    self.hybrid_ok_countries,
            "denylisted_companies":  self.denylisted_companies,
            "excluded_sectors":      self.excluded_sectors,
            "excluded_languages":   self.excluded_languages,
        }


# ---------------------------------------------------------------------------
# Profil par défaut — ex-comportement main.py
# ---------------------------------------------------------------------------

WEB3_REMOTE = SearchProfile(
    id="web3_remote",
    name="Web3 Remote",
    allowed_geo_zones=["europe", "global_remote", "unknown"],
    allowed_work_modes=["remote", "hybrid", "unknown"],
    location_keywords=[],           # pas de filtre géographique pré-scoring
    boost_keywords=["web3", "defi", "crypto", "blockchain", "AI", "fintech"],
    company_sizes=["startup", "scaleup"],
    score_threshold=5,
    remote_or_hybrid=True,
    pre_filter={
        "title_contains": [
            "product manager", "product owner", "head of product",
            "vp product", "lead product", "staff product",
        ],
        "exclude_title_contains": [
            "junior", "intern", "marketing manager", "sales",
            "engineer", "developer", "data scientist", "designer",
        ],
        "exclude_location_contains": [
            "united states", " usa ", "new york", "san francisco",
        ],
    },
    scoring_context="""You are evaluating jobs for a Senior PM with 10+ years in Web3, DeFi, and AI,
based in Europe, targeting fully remote roles globally.

Score HIGH (8-10) if: Web3, DeFi, crypto, blockchain, L2, protocol, NFT,
smart contracts, or AI-native product roles. Remote-first companies.
Senior, Staff, Lead, or Head of Product titles.

Score MEDIUM (5-7) if: fintech or AI adjacent but no explicit Web3 context.
Strong PM fundamentals with clear technical depth.

Score LOW (1-4) if: no Web3/AI/crypto context, non-tech verticals,
junior roles, US-only or no remote option, non-PM titles.""",
    allowed_countries=None,
    excluded_sectors=["pharma", "retail", "manufacturing", "government", "healthcare"],
    excluded_languages=["german"],
)

# ---------------------------------------------------------------------------
# Profil Suisse hybride
# ---------------------------------------------------------------------------

CH_HYBRID = SearchProfile(
    id="ch_hybrid",
    name="Switzerland Hybrid",
    allowed_geo_zones=["europe", "global_remote", "unknown"],
    allowed_work_modes=["hybrid", "remote", "unknown"],
    location_keywords=[
        "Switzerland", "Suisse", "Schweiz", "Svizzera",
        "Zürich", "Zurich", "Geneva", "Genève", "Genf",
        "Basel", "Bâle", "Bern", "Berne", "Lausanne", "Lugano",
        "Winterthur", "St. Gallen", "Sankt Gallen", "Zug", "Luzern", "Lucerne",
    ],
    boost_keywords=["fintech", "banking", "AI", "crypto"],
    company_sizes=["startup", "scaleup", "sme"],
    score_threshold=5,
    remote_or_hybrid=True,
    pre_filter={
        "exclude_location_contains": [
            "united states", " usa ", "new york", "san francisco",
            "los angeles", "seattle", "boston", "chicago",
        ],
        "title_contains": [
            "product manager", "product owner", "head of product",
            "vp product", "lead product", "staff product", "technical product",
        ],
        "exclude_title_contains": [
            "junior", "intern", "marketing manager", "sales",
            "engineer", "developer", "data scientist",
        ],
    },
    scoring_context="""This profile is strictly for Product Manager / Product Owner roles based in Switzerland (Zürich, Geneva, Basel, Lausanne, Bern, Zug, Lugano, etc.) where the company has a Swiss office. Hybrid and fully-remote work modes are both acceptable, but the role must be anchored to Switzerland — not 'remote anywhere in EU' or 'remote from any office.' Roles based outside Switzerland should score 1-3 regardless of how strong the company or role looks otherwise. Web3/crypto experience is welcome but not required — fintech, banking, insurance, and operations-heavy tech roles are equally valued.

OVERRIDE the default scoring scale for this profile:
- Score 7-9: Senior PM/PO role in Switzerland, hybrid or remote, with fintech/banking/Web3/AI/insurance/ops-tech/B2B SaaS context — seniority and Swiss anchoring matter most
- Score 6-7: PM/PO role in Switzerland, hybrid or remote, any vertical (food-tech, logistics, healthcare ops, government digital) — Swiss presence alone is enough to qualify
- Score 1-3: role based outside Switzerland, pure on-site with no hybrid option, or non-PM/PO title""",
    allowed_countries=["Switzerland"],
    excluded_sectors=["pharma", "retail", "manufacturing", "government"],
    excluded_languages=["german"],
)

# ---------------------------------------------------------------------------
# Profil unifié expérimental — see conversation 2026-05-01
# Tests whether a single rich scoring_context can replace the WEB3_REMOTE +
# CH_HYBRID multi-profile approach. Uses the LLM scoring_context as the primary
# preference-modelling mechanism with minimal hard exclusions in pre_filter.
# ---------------------------------------------------------------------------

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

# Industry fit (in order of preference, but apply realistic weighting)
1. PERFECT but rare — Web3 RWA (real-world assets), tokenization of
   traditional finance, regulated DeFi, stablecoins with real use cases.
   Score these high even if the role is a stretch.
2. REALISTIC SWEET SPOT — fintech companies bridging Web2 and Web3:
   crypto-friendly neobanks, custody, on-ramps/off-ramps, traditional
   fintech adding tokenization or crypto rails, embedded finance with
   blockchain components. The candidate's profile is a strong fit here.
3. SOLID — traditional fintech startups/scale-ups: payments, lending,
   wealthtech, regtech, embedded finance, B2B SaaS for financial
   services. The candidate has direct experience.
4. INTERESTING BUT HARDER — pure Web3 / DeFi / crypto-native companies.
   Score the role on its merits but acknowledge in the reasoning that
   the candidate lacks direct Web3 PM experience, which may make this
   aspirational.
5. NEUTRAL BONUS — companies using AI in their product (good signal of
   innovation). AI-native companies building foundation models or core
   AI products are a stretch given no direct AI PM experience — score
   the role on the PM fit, not the AI angle.
6. LOW FIT — non-fintech B2B SaaS, e-commerce, healthtech, edtech,
   media. Score on PM fundamentals only, no industry bonus.

# Geography and work mode
- IDEAL: Hybrid in Switzerland (Lausanne, Geneva, Zurich, Zug, Basel)
  with 2-3 office days per week. The candidate needs to travel to
  France 1-2 times per month and travels regularly for personal
  reasons, so flexibility matters. Rigid "4+ days in office" hybrid
  roles are a weaker fit than flexible "2-3 days" hybrid.
- ALSO STRONG: Fully remote roles based anywhere in Europe (EU timezone).
  The candidate stays in Switzerland but works for a European company.
- ACCEPTABLE: Hybrid roles in France, Spain, Portugal, Italy, the
  Netherlands, Germany (if English-speaking), Ireland, the Nordics —
  countries the candidate would consider relocating to.
- WEAKER: Hybrid in other European countries not listed above.
- EXCLUDE: On-site or hybrid outside Switzerland (Tier-0 enforced), US-only remote, APAC roles.

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
)

# ---------------------------------------------------------------------------
# Registre
# ---------------------------------------------------------------------------

ALL_PROFILES: dict[str, SearchProfile] = {
    WEB3_REMOTE.id: WEB3_REMOTE,
    CH_HYBRID.id:   CH_HYBRID,
    UNIFIED_JC.id:  UNIFIED_JC,
}

DEFAULT_PROFILE_ID = WEB3_REMOTE.id
