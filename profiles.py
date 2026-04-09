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
)

# ---------------------------------------------------------------------------
# Registre
# ---------------------------------------------------------------------------

ALL_PROFILES: dict[str, SearchProfile] = {
    WEB3_REMOTE.id: WEB3_REMOTE,
    CH_HYBRID.id:   CH_HYBRID,
}

DEFAULT_PROFILE_ID = WEB3_REMOTE.id
