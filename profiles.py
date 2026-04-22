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
)

# ---------------------------------------------------------------------------
# Profil Suisse hybride
# ---------------------------------------------------------------------------

CH_HYBRID = SearchProfile(
    id="ch_hybrid",
    name="Switzerland Hybrid",
    allowed_geo_zones=["europe", "unknown"],
    allowed_work_modes=["hybrid", "on-site", "unknown"],
    location_keywords=[
        "switzerland", "suisse", "zürich", "zurich",
        "geneva", "genève", "lausanne", "bern", "basel",
    ],
    boost_keywords=["fintech", "banking", "AI", "crypto"],
    company_sizes=["startup", "scaleup", "sme"],
    score_threshold=5,
    remote_or_hybrid=False,         # on-site jobs autorisés
    pre_filter={
        "location_contains": [
            "remote", "europe", "hybrid", "worldwide", "anywhere",
        ],
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
    scoring_context="""You are evaluating jobs for a Senior PM with 10+ years in fintech, Web3, and AI,
based in Lausanne Switzerland, open to hybrid or onsite roles in Switzerland
and remote roles across Europe.

Score HIGH (8-10) if: Senior PM/PO role in Switzerland (any tech vertical),
fintech, banking, AI products, ops-tech, B2B SaaS, or Web3 companies with
Swiss presence. Hybrid or remote. Swiss companies or EU remote.

Score MEDIUM (5-7) if: solid PM role in Europe remote, or Switzerland-based
but non-tech vertical (food-tech, logistics, healthcare ops). Product Owner
titles with real ownership and technical context.

Score LOW (1-4) if: junior roles, non-PM titles, US-only, no European
eligibility, pure non-tech sectors with no digital product angle.

Do NOT penalize for absence of Web3 — Swiss market depth and seniority
matter more for this profile.""",
)

# ---------------------------------------------------------------------------
# Registre
# ---------------------------------------------------------------------------

ALL_PROFILES: dict[str, SearchProfile] = {
    WEB3_REMOTE.id: WEB3_REMOTE,
    CH_HYBRID.id:   CH_HYBRID,
}

DEFAULT_PROFILE_ID = WEB3_REMOTE.id
