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
# Registre
# ---------------------------------------------------------------------------

ALL_PROFILES: dict[str, SearchProfile] = {
    WEB3_REMOTE.id: WEB3_REMOTE,
    CH_HYBRID.id:   CH_HYBRID,
}

DEFAULT_PROFILE_ID = WEB3_REMOTE.id
