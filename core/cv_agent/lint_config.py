"""lint_config.py — tracked, generic style rules for the CV lint (spec 036, US3).

Only **public, non-personal** rules live here (Constitution §V, clarify Q2):
banned phrases, the retryable-check set, the in-progress-course allowlist and
generic geography. Personal data (allowed numeric tokens, employers, extra
roles, claims) is derived at runtime from ``cv_data_master.json`` or a sidecar
next to the master in ``.cv_pipeline/`` — never committed to the repo.
"""

# Configurable banned phrases — checked verbatim (case-insensitive) in every CV
# text field. Extend this list to ban more wording the agent keeps producing.
BANNED_PHRASES = [
    "fully willing to relocate",
]

# Checks whose failure routes back to ``tailor_cv`` under REVISION_LIMIT (US5).
# The other checks are informational only: they surface in review.json but are
# fixed in config/prompts, not by per-run LLM revision.
RETRYABLE_LINT_IDS = frozenset(
    {"facts_numbers", "roles_match_master", "em_dash", "pages"}
)

# Allowlisted in-progress courses that must carry an "in progress" label in the
# education section (US3). A course listed here but not labelled is a defect.
IN_PROGRESS_COURSES = ["Hugging Face AI Agents"]

# Generic geography for contact_consistency (public place names — not personal
# data). A claim of a base in the "wrong" country for the active contact profile
# is a contradiction (e.g. "Based in Lausanne" with contact "FR").
CH_LOCATIONS = ["lausanne", "pully", "zurich", "zürich", "geneva", "genève", "switzerland", "suisse"]
FR_LOCATIONS = ["paris", "france", "lyon", "lille"]
