"""
profile_generator.py — Generate a SearchProfile criteria dict from a
questionnaire + CV text, with LLM-written scoring_context.

QUESTIONNAIRE CONTRACT — keys the wizard must provide:
    target_titles: list[str]        # roles, e.g. ["product manager","product owner"]
    seniority: str                  # "junior" | "mid" | "senior" | "lead" | "any"
    locations: list[str]            # display names, e.g. ["Switzerland","Remote EU"]
    base_country: str               # where the user lives / is allowed to work
    work_modes: list[str]           # subset of remote/hybrid/on-site
    onsite_country_ok: list[str]    # countries where on-site/hybrid is acceptable
    company_sizes: list[str]        # subset of startup/scaleup/sme/large
    favored_industries: list[str]   # e.g. ["fintech","web3_crypto"]
    avoided_industries: list[str]   # sector codes
    work_languages: list[str]       # languages the user works in professionally
    avoid_language_required: list[str]  # job-language requirements to exclude
    remote_only_foreign: bool       # foreign employers only if fully remote
    banned_countries: list[str]     # hard-reject geographies
    salary_floor: str               # free text, optional
    dealbreakers: str               # free text, optional
    extra_notes: str                # free text, optional

All calls to the LLM go through scorer.generate_json() so quota/retry/fallback
behaviour is identical to the scorer.
"""

import json
import re

# ── Default crypto/Web3 Greenhouse boards (from CRYPTO_WEB3_BOARDS) ──────────
CRYPTO_WEB3_BOARDS = [
    "coinbase", "chainalysis", "paxos", "avalabs", "consensys",
    "fireblocks", "anchorage", "figment", "bitgo", "kraken",
    "gemini", "ripple", "near", "aptos", "mysten",
    "alchemy", "infura", "opensea", "dydx", "uniswap",
    "aave", "blockdaemon", "ledger", "blockchain", "circle",
    "robinhood", "stripe", "brex", "mercury", "ramp",
]

# ── US cities commonly listed as locations in US-only remote roles ──────────
_US_CITIES = [
    "new york", "san francisco", "los angeles", "seattle", "boston",
    "chicago", "austin", "denver", "portland", "miami", "atlanta",
    "washington dc", "washington d.c.", "dc",
]


# ══════════════════════════════════════════════════════════════════════════════
# Deterministic structured-field mapping
# ══════════════════════════════════════════════════════════════════════════════

def map_structured_fields(q: dict) -> dict:
    """Pure-Python mapping from questionnaire → non-prose profile fields.

    Returns a dict keyed like SearchProfile.to_criteria_dict(), with
    scoring_context and boost_keywords left as empty placeholders
    (filled later by generate_scoring_context).
    """
    titles = [t.strip().lower() for t in q.get("target_titles", [])]
    seniority = q.get("seniority", "any")
    work_modes = q.get("work_modes", [])
    remote_ok = "remote" in work_modes
    onsite_ok = "on-site" in work_modes
    favored = q.get("favored_industries", [])
    avoided = q.get("avoided_industries", [])
    banned = q.get("banned_countries", [])
    base = q.get("base_country", "")
    languages = q.get("work_languages", [])
    avoid_lang = q.get("avoid_language_required", [])

    # ── Search query titles ──────────────────────────────────────────────
    search_query_titles = list(dict.fromkeys(titles))  # dedup, preserve order

    # ── Scrape titles (broader net: target + common variants) ────────────
    scrape_titles = list(dict.fromkeys(
        titles
        + [f"head of {t}" if not t.startswith("head of") else t for t in titles if not t.startswith("head of")]
        + [f"vp {t}" if not t.startswith("vp") else t for t in titles if not t.startswith("vp")]
    ))
    # Add common role variants
    _title_variants = []
    for t in titles:
        if "product manager" in t or "product owner" in t:
            _title_variants.extend([
                "product lead", "product director", "senior product",
                "lead product manager", "staff product",
            ])
            break
    scrape_titles = list(dict.fromkeys(scrape_titles + _title_variants))

    # ── Scrape exclusions ─────────────────────────────────────────────────
    scrape_exclude = ["junior", "intern", "stage", "apprentice"]
    if seniority in ("senior", "lead"):
        scrape_exclude.append("associate")

    # ── Work mode / geo zone derivation ───────────────────────────────────
    geo_zones = ["europe", "unknown"]
    if remote_ok:
        geo_zones.append("global_remote")
    # Only add us_only for US-based users
    if base.lower() in ("united states", "usa", "us"):
        geo_zones.append("us_only")

    remote_or_hybrid = not onsite_ok  # if on-site excluded, gate at filter level
    scrape_remote_or_hybrid = remote_or_hybrid

    # ── Allowed countries ─────────────────────────────────────────────────
    allowed_countries = None  # None = no restriction
    locs = q.get("locations", [])
    if locs and "remote (anywhere)" not in [x.lower() for x in locs]:
        allowed_countries = list(locs)

    # ── Hybrid-ok countries ───────────────────────────────────────────────
    hybrid_ok = q.get("onsite_country_ok", [])
    if not hybrid_ok:
        hybrid_ok = [base] if base else []

    # ── Banned countries: explicit list + derived from US if applicable ──
    banned_countries = list(banned)

    # ── Denylisted companies (always start empty; user edits later) ────────
    denylisted = []

    # ── Excluded sectors ──────────────────────────────────────────────────
    excluded_sectors = list(avoided)

    # ── Excluded languages ────────────────────────────────────────────────
    excluded_languages = list(avoid_lang)

    # ── pre_filter ────────────────────────────────────────────────────────
    pre_exclude_location = []
    if "united states" in [b.lower() for b in banned] or "usa" in [b.lower() for b in banned]:
        pre_exclude_location = [f"united states", " usa "] + _US_CITIES

    pre_filter = {
        "title_contains": list(titles),
        "exclude_title_contains": ["junior", "intern"],
        "exclude_location_contains": pre_exclude_location,
    }

    # ── Greenhouse boards ─────────────────────────────────────────────────
    greenhouse_boards = []
    if any(kw in " ".join(favored).lower() for kw in ("web3", "crypto", "defi")):
        greenhouse_boards = list(CRYPTO_WEB3_BOARDS)

    return {
        # ── Search inputs ─────────────────────────────────────────────────
        "search_query_titles":    search_query_titles,
        "search_locations":       locs,
        "scrape_titles":          scrape_titles,
        "scrape_exclude":         scrape_exclude,
        "scrape_remote_or_hybrid": scrape_remote_or_hybrid,
        "greenhouse_boards":      greenhouse_boards,

        # ── Scoring params ────────────────────────────────────────────────
        "allowed_geo_zones":    geo_zones,
        "allowed_work_modes":   work_modes + ["unknown"],
        "location_keywords":    [],
        "boost_keywords":       [],  # filled by LLM
        "company_sizes":        q.get("company_sizes", []),
        "score_threshold":      5,
        "remote_or_hybrid":     remote_or_hybrid,

        # ── Prose (filled by LLM) ─────────────────────────────────────────
        "scoring_context":      "",

        # ── Filters ───────────────────────────────────────────────────────
        "pre_filter":           pre_filter,
        "allowed_countries":    allowed_countries,
        "banned_countries":     banned_countries,
        "hybrid_ok_countries":  hybrid_ok,
        "denylisted_companies":  denylisted,
        "excluded_sectors":     excluded_sectors,
        "excluded_languages":   excluded_languages,
    }


# ══════════════════════════════════════════════════════════════════════════════
# LLM-powered scoring_context generation
# ══════════════════════════════════════════════════════════════════════════════

_GENERATION_SYSTEM = """You are writing a SCORING RUBRIC for a job-matching system.  This
rubric will be injected as the system prompt for another LLM that scores
job postings 1–10 for THIS specific candidate.  Your output will be
pasted verbatim into the scorer's system prompt, so it must read like
documentation for the scorer model.

The candidate answered a questionnaire and (optionally) provided their CV.
Adapt every section to THEIR answers.  Be concrete — name their target
roles, base country, favoured industries, and constraints explicitly.
Do NOT copy-paste generic placeholder text.

## Required sections (order matters)

### 1. Who the candidate is (1 paragraph)
State their target roles, seniority, and a 1-sentence background summary
(from the CV if available).  Include the honest-calibration rule:
"Score from 1 to 10.  Be honest — most jobs will score 3–6.  Reserve 8+
for genuinely strong matches and 9–10 for roles that hit multiple
criteria simultaneously."

### 2. Hard exclusions (cap score at 3)
List every hard exclusion the candidate specified: off-target seniority,
non-target roles, on-site roles outside the candidate's acceptable
countries, jobs requiring a language the candidate doesn't have,
banned geographies.  End with: "These cap the score at 3 regardless of
other signals."

### 3. Company-type preference
Describe the candidate's ideal company: size (startup/scaleup/SME/large),
industries they favour vs avoid, headcount preferences, funding-stage
signals.  Tie back to the candidate's questionnaire answers.

### 4. Ranking keys (in priority order)
List the top 3–4 signals the scorer should weigh, in order.  Domain fit
is usually first, then employer-location certainty (if the candidate
prefers local employers), then work mode.  Derive these from the
questionnaire — do NOT invent a hierarchy.

### 5. Scoring bands table
A markdown table mapping tiers to score bands.  Each tier should
reference the ranking keys above.  The top tier is the perfect match;
the bottom tier is "off-domain, wrong country, or clearly underpaid."
Assign bands, not fixed scores (e.g. "8–10", not "9").

### 6. Industry fit notes
Describe which industries are PERFECT, STRONG, NEUTRAL, and LOW-FIT for
this candidate, based on their favoured/avoided industries.  Include a
note about which adjacent industries get a bonus vs which get a penalty.

### 7. Geography and work-mode rules
Explain where the candidate lives, which work modes they accept, whether
foreign employers are acceptable (and under what conditions — e.g. remote
only), and which countries trigger hard rejection.  Include the
Swiss-employer-certainty pattern if the candidate prefers local employers:
foreign-company remote scores ~1–2 below the equivalent local role.

### 8. Compensation handling
If the candidate gave a salary floor: mention it.  If a posting states a
salary and it is clearly below the candidate's market, subtract 1 and
explain why.  If no salary is stated, do NOT penalize — flag it for
manual verification.

### 9. Output schema reminder
Remind the scorer to output JSON with these keys: score (1–10), reason
(2–3 sentences referencing specific signals), summary (1–2 sentences
describing the role), work_mode, company_size, contract_type, geo_zone.
Add: "Be specific in the reason. Generic praise is not useful."

## Return format
Return ONLY a JSON object: {"scoring_context": "<the full rubric prose>",
"boost_keywords": ["keyword1", ...]}.  The boost_keywords list should
contain 10–20 single-word or short-phrase keywords derived from the
candidate's favoured industries, target company types, and role keywords
— these are used as search hints, not as scoring criteria."""


def _build_user_prompt(q: dict, cv_text: str) -> str:
    """Compact rendering of the questionnaire + CV for the LLM."""
    parts = [
        "## Candidate questionnaire",
        f"Target roles: {', '.join(q.get('target_titles', []))}",
        f"Seniority: {q.get('seniority', 'any')}",
        f"Base country / residence: {q.get('base_country', '')}",
        f"Preferred work modes: {', '.join(q.get('work_modes', []))}",
        f"Countries where on-site/hybrid is acceptable: {', '.join(q.get('onsite_country_ok', []))}",
        f"Preferred locations: {', '.join(q.get('locations', []))}",
        f"Company sizes: {', '.join(q.get('company_sizes', []))}",
        f"Favoured industries: {', '.join(q.get('favored_industries', []))}",
        f"Avoided industries: {', '.join(q.get('avoided_industries', []))}",
        f"Professional languages: {', '.join(q.get('work_languages', []))}",
        f"Avoid jobs requiring: {', '.join(q.get('avoid_language_required', []))}",
        f"Foreign employers only if fully remote: {q.get('remote_only_foreign', False)}",
        f"Banned countries: {', '.join(q.get('banned_countries', []))}",
    ]
    salary = q.get("salary_floor", "").strip()
    if salary:
        parts.append(f"Salary floor: {salary}")
    dealbreakers = q.get("dealbreakers", "").strip()
    if dealbreakers:
        parts.append(f"Dealbreakers: {dealbreakers}")
    extra = q.get("extra_notes", "").strip()
    if extra:
        parts.append(f"Extra notes: {extra}")

    if cv_text.strip():
        parts.append("")
        parts.append("## CV")
        parts.append(cv_text[:4000])  # truncate

    return "\n".join(parts)


def _parse_json_safe(raw: str) -> dict:
    """Parse JSON from LLM output, stripping ``` fences and retrying once."""
    # Strip markdown fences
    cleaned = raw.strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)

    # Try to extract a JSON object if the model wrapped it in other text
    brace_start = cleaned.find('{')
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(cleaned)):
            if cleaned[i] == '{':
                depth += 1
            elif cleaned[i] == '}':
                depth -= 1
                if depth == 0:
                    cleaned = cleaned[brace_start:i + 1]
                    break

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {}


def generate_scoring_context(q: dict, cv_text: str) -> dict:
    """Call the LLM to produce scoring_context prose + boost_keywords.

    Returns {"scoring_context": str, "boost_keywords": list}.
    Raises on auth/quota exhaustion (caller should catch and surface).
    """
    from scorer import generate_json

    messages = [
        {"role": "system", "content": _GENERATION_SYSTEM},
        {"role": "user", "content": _build_user_prompt(q, cv_text)},
    ]

    raw = generate_json(messages, max_tokens=3000)
    result = _parse_json_safe(raw)

    if not result or "scoring_context" not in result:
        # Retry once with an explicit nudge
        retry = messages + [{"role": "user", "content": "You MUST respond with ONLY the JSON object. No markdown, no explanation, just {\"scoring_context\": \"...\", \"boost_keywords\": [...]}."}]
        raw2 = generate_json(retry, max_tokens=3000)
        result = _parse_json_safe(raw2)

    if not result or "scoring_context" not in result:
        raise RuntimeError(
            "The LLM did not return a valid scoring_context after two attempts. "
            "Please try again or check your API key."
        )

    return {
        "scoring_context": result.get("scoring_context", ""),
        "boost_keywords": result.get("boost_keywords", []),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Full profile generation
# ══════════════════════════════════════════════════════════════════════════════

# Keys that SearchProfile.to_criteria_dict() must contain (the contract).
_EXPECTED_KEYS = {
    "allowed_geo_zones", "allowed_work_modes", "location_keywords",
    "boost_keywords", "company_sizes", "score_threshold", "remote_or_hybrid",
    "scoring_context", "pre_filter", "allowed_countries", "banned_countries",
    "hybrid_ok_countries", "denylisted_companies", "excluded_sectors",
    "excluded_languages", "scrape_titles", "scrape_exclude",
    "scrape_remote_or_hybrid", "search_query_titles", "search_locations",
    "greenhouse_boards",
}

_SAFE_DEFAULTS = {
    "location_keywords": [],
    "denylisted_companies": [],
}


def generate_profile(q: dict, cv_text: str) -> dict:
    """Generate a complete criteria dict from questionnaire + CV.

    Returns a dict ready for SearchProfile.from_criteria().  All keys
    from to_criteria_dict() are present; any missing ones get safe defaults.
    """
    criteria = map_structured_fields(q)

    try:
        generated = generate_scoring_context(q, cv_text)
    except Exception:
        # LLM unavailable — include a placeholder so the user can fill it in
        generated = {
            "scoring_context": (
                "# Scoring rubric\n\n"
                "The LLM could not generate your scoring rubric "
                "(API key missing or all models exhausted).  "
                "Please hand-write your scoring criteria here, "
                "or check your API key in Settings → Setup and re-run the wizard."
            ),
            "boost_keywords": [],
        }

    criteria["scoring_context"] = generated["scoring_context"]
    criteria["boost_keywords"] = generated.get("boost_keywords", []) or criteria["boost_keywords"]

    # Ensure every expected key is present
    for key in _EXPECTED_KEYS:
        if key not in criteria:
            criteria[key] = _SAFE_DEFAULTS.get(key, [] if key.endswith("s") or key.startswith("_") else "")

    return criteria


# ══════════════════════════════════════════════════════════════════════════════
# Plain-language refinement
# ══════════════════════════════════════════════════════════════════════════════

_REFINE_SYSTEM = """You are editing an existing job-scoring rubric based on the user's
plain-language instruction.  Follow the instruction precisely, but
preserve the overall structure and everything NOT implicated by the
instruction.  Do not reorder sections, drop sections, or change the
scoring bands unless asked.

Return ONLY a JSON object: {"scoring_context": "<the edited prose>"}."""


def refine_scoring_context(current: str, instruction: str, q: dict) -> str:
    """Edit a scoring_context per a plain-language instruction.

    Returns the edited scoring_context string.
    """
    from scorer import generate_json

    summary = f"Target: {', '.join(q.get('target_titles', []))} | "
    summary += f"Base: {q.get('base_country', '')} | "
    summary += f"Industries: {', '.join(q.get('favored_industries', []))}"

    messages = [
        {"role": "system", "content": _REFINE_SYSTEM},
        {"role": "user", "content": (
            f"## Candidate context\n{summary}\n\n"
            f"## Current scoring_context\n{current}\n\n"
            f"## Instruction\n{instruction}"
        )},
    ]

    raw = generate_json(messages, max_tokens=3000)
    result = _parse_json_safe(raw)

    if not result or "scoring_context" not in result:
        # Retry once
        retry = messages + [{"role": "user", "content": "Return ONLY the JSON object {\"scoring_context\": \"...\"}."}]
        raw2 = generate_json(retry, max_tokens=3000)
        result = _parse_json_safe(raw2)

    if not result or "scoring_context" not in result:
        raise RuntimeError("Refinement failed — the LLM did not return a valid response.")

    return result["scoring_context"]
