import json
import os
import time
from datetime import datetime

from groq import Groq
from openai import OpenAI
from dotenv import load_dotenv

from models import JobPosting

load_dotenv()

api_key = os.getenv("GROQ_API_KEY") or os.getenv("GROQ_APIKEY")
client = Groq(api_key=api_key, max_retries=0)

deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY")
_deepseek_client: OpenAI | None = None
if deepseek_api_key:
    _deepseek_client = OpenAI(
        api_key=deepseek_api_key,
        base_url="https://api.deepseek.com/v1",
    )

FALLBACK_MODELS = [
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "groq/compound",
    "llama-3.1-8b-instant",
]

# Extraction models — Groq primary + fallback, then DeepSeek as last resort
EXTRACTION_MODELS = [
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
]

# Tier 1 evaluation models — small/cheap only, never 70b
EVALUATION_MODELS = [
    "llama-3.1-8b-instant",
    "meta-llama/llama-4-scout-17b-16e-instruct",
]

SYSTEM_PROMPT = """You are an expert recruiter scoring job postings for a Senior Product Manager with expertise in Web3, DeFi, AI, and Crypto.

## Scoring rules (based on JOB TITLE first, then description)

9-10 → Job title is clearly a PM/Product role AND title or description explicitly mentions Web3, DeFi, AI, blockchain, or crypto
7-8  → Job title is clearly a PM/Product role AND Web3/DeFi/AI/crypto appears in description or company context only
5-6  → Job title is clearly a PM/Product role but no Web3/AI/crypto mention anywhere — generalist PM
3-4  → Job title is NOT a PM role (engineer, designer, marketing, BD, analyst, legal…) even with Web3 context
1-2  → Not a PM role AND no Web3/AI/crypto context

Critical: Marketing, Growth, BD, Engineering, Design roles → 3-4 MAX regardless of Web3 context.

## Work mode adjustment (apply AFTER base score)
Detect work mode from title, location, and description:
- "remote" → no adjustment
- "hybrid" → subtract 1 from score (mention "Hybrid" in reason)
- "on-site" → subtract 2 from score (mention "On-site" in reason)
- "unknown" → no adjustment, note "Mode non précisé" in reason

Set work_mode to exactly one of: "remote", "hybrid", "on-site", "unknown"

## Company size
Infer from description, company name, funding stage, or context:
- "startup"  : <50 employees, early stage, seed/series A, pre-product-market-fit
- "scaleup"  : 50-500 employees, series B/C, hypergrowth, VC-backed
- "sme"      : traditional SME, 50-250 employees, not VC-backed
- "large"    : >500 employees, corporate, public company, enterprise
- "unknown"  : not enough information

## Contract type
Infer from title, description, or job type indicators:
- "permanent"   : full-time, CDI, employee, long-term
- "freelance"   : freelance, consultant, contractor, independent
- "contract"    : CDD, fixed-term, 6-month contract, temporary
- "internship"  : intern, stage, apprentice
- "unknown"     : not specified

## Geographic zone
Infer geo_zone from the Base location field, then the Location field, then the description.
Priority rule: if Base location names a specific country or city, use that country to set geo_zone — even if work arrangement is remote.

- "europe"        : Base location or description mentions EU country, UK, Germany, France, Spain, Portugal, Netherlands, Switzerland, Poland, Turkey, CET/CEST/EET timezone, "Europe", "EMEA" without US restriction, UTC+0 to UTC+4
- "us_only"       : Base location or description mentions US city/state, "United States", "US only", "must be authorized to work in the US", "US-based", EST/PST/CST/MST timezone, UTC-5 to UTC-8, "Americas", "North America only"
- "apac"          : Base location or description mentions Asia, Singapore, Hong Kong, Japan, South Korea, Australia, "UTC+5 to UTC+12", APAC
- "latam"         : Base location or description mentions Latin America, Brazil, Mexico, UTC-3 to UTC-5 (excluding US)
- "global_remote" : ONLY when one of these is true: (1) the company is on the known global-remote list below (Aave, Consensys, Gnosis, MakerDAO, Uniswap Foundation, Ethereum Foundation); OR (2) the description explicitly contains "work from anywhere", "worldwide", "no timezone restriction", "fully async", or equivalent unambiguous language. If base_location is empty AND location is just "Remote" AND neither condition is met → assign "unknown", NOT "global_remote".
- "unknown"       : Safe default when base_location is empty and location is "Remote" or "Worldwide" with no geographic clues. Do NOT assign global_remote based on absence of country information.

IMPORTANT — company geography: if the company is well-known to operate primarily from a specific region, use that region even without explicit location info:
- Binance, OKX, Bybit, Huobi, HashKey → apac
- Coinbase, Kraken, Gemini, Ripple, Chainalysis, Anchorage → us_only (unless description says open to all / worldwide)
- Aave, Consensys, Gnosis, Ethereum Foundation, MakerDAO, Uniswap Foundation → global_remote
- Deutsche Bank, UBS, Société Générale, BNP Paribas → europe

## Geographic score adjustment (apply AFTER base score and work mode)
- us_only       → subtract 3 from score (mention "US only" in reason)
- apac or latam → subtract 2 from score (mention "APAC" or "LATAM" in reason)
- europe        → no adjustment
- global_remote → no adjustment
- unknown       → no adjustment (do not penalise uncertainty)

## Summary
Write 2-3 sentences covering: company mission, key responsibilities, tech stack/context.
If description is empty → summary = "Description non disponible — consulter l'offre directement."

Always return a score — never skip.

## Additional structured fields (REQUIRED)

In addition to the existing fields, every response MUST include:

### company_country
The country where the company has its primary office relevant to this role.
Extract from explicit signals in the description:
- "Headquartered in [city, country]"
- "Based in [country]"
- Address blocks
- "Our [country] office"
- Job location field (e.g., "London, UK" → "United Kingdom")

If the description mentions multiple countries, pick the one most relevant to THIS role's location.
If no clear signal exists, return "unknown". Do NOT guess from company name alone.

### industry_sector
Pick exactly one from this controlled list:
- web3_crypto: blockchain, crypto exchanges, DeFi, DAOs, NFT, Web3 infrastructure
- fintech: payments, banking tech, neobanks, lending, insurtech, wealth tech
- tech_saas: B2B SaaS, developer tools, productivity, infrastructure software
- ai_ml: AI-first companies, ML platforms, generative AI products
- e_commerce: online retail, marketplaces, D2C platforms
- healthcare: hospitals, telemedicine, health platforms (NOT pharma)
- pharma: pharmaceutical R&D, drug development (Roche, Novartis, Sanofi, Pfizer)
- retail: physical retail, FMCG, supermarkets (Migros, Coop, M&S, IKEA, Carrefour)
- manufacturing: industrial, heavy equipment, machinery (Siemens industrial, Bosch, BOBST)
- government: public sector, federal/cantonal/state administration
- consulting: management consulting, professional services
- education: schools, edtech, universities
- media: publishing, broadcasting, gaming, entertainment
- energy: utilities, oil/gas, renewables, power systems
- other: anything that doesn't fit cleanly above

### language_required
The primary language a candidate must speak fluently for this role.
Detection signals:
- "Fluent in [language]" / "[language] required"
- "Deutschkenntnisse erforderlich" → german
- "Maîtrise du français" → french
- Job description written entirely in non-English with no English version → that language
- Multiple languages explicitly required → "multiple"
- No language mentioned and description is in English → "english"
- No clear signal → "unknown"

These three fields are REQUIRED in every JSON response. Use the fallback ("unknown" / "other" / "unknown") when uncertain — never omit the field.

Respond with JSON only:
{
  "score": <int 1-10>,
  "reason": "<one sentence>",
  "summary": "<2-3 sentences>",
  "work_mode": "<remote|hybrid|on-site|unknown>",
  "company_size": "<startup|scaleup|sme|large|unknown>",
  "contract_type": "<permanent|freelance|contract|internship|unknown>",
  "geo_zone": "<europe|us_only|global_remote|apac|latam|unknown>",
  "company_country": "<country name or unknown>",
  "industry_sector": "<one of the 15 codes above>",
  "language_required": "<english|french|german|italian|spanish|multiple|unknown>"
}"""

EXTRACTION_PROMPT = """You are an extraction system. Your job is to read a job description and fill
structured fields about the job itself. You do NOT score or evaluate fit.

Given the job below, return ONLY a JSON object with these fields:

{
  "company_country": "<country name or unknown>",
  "industry_sector": "<controlled code from list below>",
  "language_required": "<code from list below>",
  "work_mode": "<remote|hybrid|on-site|unknown>",
  "geo_zone": "<europe|us_only|global_remote|apac|latam|unknown>",
  "company_size": "<startup|scaleup|sme|large|unknown>",
  "contract_type": "<permanent|freelance|contract|unknown>",
  "summary": "<2-3 sentences>"
}

## Work mode
Detect work mode from title, location, and description:
- "remote" → no adjustment
- "hybrid" → subtract 1 from score (mention "Hybrid" in reason)
- "on-site" → subtract 2 from score (mention "On-site" in reason)
- "unknown" → no adjustment, note "Mode non précisé" in reason

Set work_mode to exactly one of: "remote", "hybrid", "on-site", "unknown"

## Company size
Infer from description, company name, funding stage, or context:
- "startup"  : <50 employees, early stage, seed/series A, pre-product-market-fit
- "scaleup"  : 50-500 employees, series B/C, hypergrowth, VC-backed
- "sme"      : traditional SME, 50-250 employees, not VC-backed
- "large"    : >500 employees, corporate, public company, enterprise
- "unknown"  : not enough information

## Contract type
Infer from title, description, or job type indicators:
- "permanent"   : full-time, CDI, employee, long-term
- "freelance"   : freelance, consultant, contractor, independent
- "contract"    : CDD, fixed-term, 6-month contract, temporary
- "internship"  : intern, stage, apprentice
- "unknown"     : not specified

## Geographic zone
Infer geo_zone from the Base location field, then the Location field, then the description.
Priority rule: if Base location names a specific country or city, use that country to set geo_zone — even if work arrangement is remote.

- "europe"        : Base location or description mentions EU country, UK, Germany, France, Spain, Portugal, Netherlands, Switzerland, Poland, Turkey, CET/CEST/EET timezone, "Europe", "EMEA" without US restriction, UTC+0 to UTC+4
- "us_only"       : Base location or description mentions US city/state, "United States", "US only", "must be authorized to work in the US", "US-based", EST/PST/CST/MST timezone, UTC-5 to UTC-8, "Americas", "North America only"
- "apac"          : Base location or description mentions Asia, Singapore, Hong Kong, Japan, South Korea, Australia, "UTC+5 to UTC+12", APAC
- "latam"         : Base location or description mentions Latin America, Brazil, Mexico, UTC-3 to UTC-5 (excluding US)
- "global_remote" : ONLY when one of these is true: (1) the company is on the known global-remote list below (Aave, Consensys, Gnosis, MakerDAO, Uniswap Foundation, Ethereum Foundation); OR (2) the description explicitly contains "work from anywhere", "worldwide", "no timezone restriction", "fully async", or equivalent unambiguous language. If base_location is empty AND location is just "Remote" AND neither condition is met → assign "unknown", NOT "global_remote".
- "unknown"       : Safe default when base_location is empty and location is "Remote" or "Worldwide" with no geographic clues. Do NOT assign global_remote based on absence of country information.

IMPORTANT — company geography: if the company is well-known to operate primarily from a specific region, use that region even without explicit location info:
- Binance, OKX, Bybit, Huobi, HashKey → apac
- Coinbase, Kraken, Gemini, Ripple, Chainalysis, Anchorage → us_only (unless description says open to all / worldwide)
- Aave, Consensys, Gnosis, Ethereum Foundation, MakerDAO, Uniswap Foundation → global_remote
- Deutsche Bank, UBS, Société Générale, BNP Paribas → europe

## company_country
The country where the company has its primary office relevant to this role.
Extract from explicit signals in the description:
- "Headquartered in [city, country]"
- "Based in [country]"
- Address blocks
- "Our [country] office"
- Job location field (e.g., "London, UK" → "United Kingdom")

If the description mentions multiple countries, pick the one most relevant to THIS role's location.
If no clear signal exists, return "unknown". Do NOT guess from company name alone.

## industry_sector
Pick exactly one from this controlled list:
- web3_crypto: blockchain, crypto exchanges, DeFi, DAOs, NFT, Web3 infrastructure
- fintech: payments, banking tech, neobanks, lending, insurtech, wealth tech
- tech_saas: B2B SaaS, developer tools, productivity, infrastructure software
- ai_ml: AI-first companies, ML platforms, generative AI products
- e_commerce: online retail, marketplaces, D2C platforms
- healthcare: hospitals, telemedicine, health platforms (NOT pharma)
- pharma: pharmaceutical R&D, drug development (Roche, Novartis, Sanofi, Pfizer)
- retail: physical retail, FMCG, supermarkets (Migros, Coop, M&S, IKEA, Carrefour)
- manufacturing: industrial, heavy equipment, machinery (Siemens industrial, Bosch, BOBST)
- government: public sector, federal/cantonal/state administration
- consulting: management consulting, professional services
- education: schools, edtech, universities
- media: publishing, broadcasting, gaming, entertainment
- energy: utilities, oil/gas, renewables, power systems
- other: anything that doesn't fit cleanly above

## language_required
The primary language a candidate must speak fluently for this role.
Detection signals:
- "Fluent in [language]" / "[language] required"
- "Deutschkenntnisse erforderlich" → german
- "Maîtrise du français" → french
- Job description written entirely in non-English with no English version → that language
- Multiple languages explicitly required → "multiple"
- No language mentioned and description is in English → "english"
- No clear signal → "unknown"

## Summary
Write 2-3 sentences covering: company mission, key responsibilities, tech stack/context.
If description is empty → summary = "Description non disponible — consulter l'offre directement."

Return ONLY the JSON object — no preamble, no explanation, no markdown."""

EVALUATION_PROMPT = """You are evaluating fit between a job and a candidate profile.

Scoring bands — be strict:
9-10 → exceptional match between profile intent and job requirements
7-8  → strong match in title, sector, and location
5-6  → reasonable match but missing one or more key criteria
3-4  → partial match, role differs significantly from profile intent
1-2  → clear mismatch or non-relevant role

Return ONLY a JSON object:
{
  "score": <1-10 integer>,
  "reason": "<one short sentence on why this score>"
}"""


# ---------------------------------------------------------------------------
# Per-run model exhaustion tracking
# ---------------------------------------------------------------------------

_exhausted_models: set[str] = set()


def _is_quota_exhausted(error_str: str) -> bool:
    """True when the 429/503 is a non-recoverable daily quota, not a per-minute rate limit."""
    return any(x in error_str for x in ("per day", "tokens per day", "TPD"))


# ---------------------------------------------------------------------------
# Single-model caller with RPM backoff
# ---------------------------------------------------------------------------

def _call_groq(messages: list, model: str, max_retries: int = 5) -> str:
    """
    Call one Groq model with exponential backoff on per-minute rate limits.
    Raises immediately on daily quota exhaustion (marks model exhausted).
    Raises after max_retries on persistent RPM limits (marks model exhausted).
    Any other error (auth, network, bad request) is re-raised as-is.
    """
    if model in _exhausted_models:
        raise Exception(f"{model} already exhausted this run")

    had_429 = False
    for attempt in range(max_retries):
        try:
            result = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=300,
            )
            if had_429:
                time.sleep(10)  # cooldown after a successful retry
            return result.choices[0].message.content
        except Exception as e:
            err = str(e)
            if "413" in err or "request_too_large" in err.lower():
                # Prompt too large for this model's context window — skip it
                _exhausted_models.add(model)
                raise Exception(f"{model} request too large") from e
            elif "404" in err or ("400" in err and "decommissioned" in err):
                # Model unavailable or decommissioned — fall through to next model
                _exhausted_models.add(model)
                raise Exception(f"{model} not available") from e
            elif "429" in err or "503" in err:
                if _is_quota_exhausted(err):
                    _exhausted_models.add(model)
                    raise Exception(f"{model} daily quota exhausted") from e
                # Per-minute rate limit — backoff and retry same model
                had_429 = True
                wait = 2 ** (attempt + 1)
                print(f"  ⚠️  Groq RPM 429 ({model}) — attente {wait}s "
                      f"(tentative {attempt + 1}/{max_retries})")
                time.sleep(wait)
            elif "timed out" in err.lower() or "timeout" in err.lower():
                # Transient network timeout — retry with backoff
                wait = 2 ** (attempt + 1)
                print(f"  ⚠️  Groq timeout ({model}) — retry in {wait}s "
                      f"(tentative {attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                raise  # auth error, bad request → propagate immediately

    _exhausted_models.add(model)
    raise Exception(f"{model} rate limit persistant après {max_retries} tentatives")


# ---------------------------------------------------------------------------
# Multi-model fallback chain
# ---------------------------------------------------------------------------

def _call_groq_fallback_chain(messages: list,
                               models: list[str] | None = None) -> tuple[str, str]:
    """
    Try each model in order. Defaults to FALLBACK_MODELS if no model list given.
    Falls through to the next model on quota/rate exhaustion.
    Re-raises immediately on non-quota errors (auth, network, bad request).
    Returns (raw_json_text, model_name) on success.
    """
    if models is None:
        models = FALLBACK_MODELS
    last_err: Exception | None = None
    for model in models:
        if model in _exhausted_models:
            continue
        try:
            raw = _call_groq(messages, model)
            return raw, model
        except Exception as e:
            err = str(e)
            if any(x in err for x in ("exhausted", "rate limit persistant", "not available", "request too large")):
                print(f"  ⚠️  {model} unavailable/exhausted — essai modèle suivant")
                last_err = e
            else:
                raise  # non-quota error: don't fall through

    raise Exception("All Groq models exhausted for today. Retry tomorrow.") from last_err


# ---------------------------------------------------------------------------
# DeepSeek caller (last-resort for extraction)
# ---------------------------------------------------------------------------

def _call_deepseek(messages: list) -> str:
    """Call DeepSeek V3 via OpenAI-compatible endpoint. Returns raw response text."""
    if _deepseek_client is None:
        raise Exception("DEEPSEEK_API_KEY not set — cannot call DeepSeek")

    try:
        result = _deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=300,
        )
        return result.choices[0].message.content
    except Exception as e:
        print(f"  ❌  DeepSeek API error: {e}")
        raise


# ---------------------------------------------------------------------------
# Shared parser
# ---------------------------------------------------------------------------

_VALID_SECTORS = {
    "web3_crypto", "fintech", "tech_saas", "ai_ml", "e_commerce",
    "healthcare", "pharma", "retail", "manufacturing", "government",
    "consulting", "education", "media", "energy", "other",
}
_VALID_LANGUAGES = {
    "english", "french", "german", "italian", "spanish", "multiple", "unknown",
}


def _parse_result(raw: str) -> dict:
    result = json.loads(raw)

    sector = (result.get("industry_sector") or "other").strip().lower()
    if sector not in _VALID_SECTORS:
        print(f"  ⚠️  Unknown industry_sector {sector!r} — coercing to 'other'")
        sector = "other"

    language = (result.get("language_required") or "unknown").strip().lower()
    if language not in _VALID_LANGUAGES:
        print(f"  ⚠️  Unknown language_required {language!r} — coercing to 'unknown'")
        language = "unknown"

    return {
        "score":            int(result["score"]),
        "reason":           result["reason"],
        "summary":          result.get("summary") or "Description non disponible — consulter l'offre directement.",
        "work_mode":        result.get("work_mode", "unknown"),
        "company_size":     result.get("company_size", "unknown"),
        "contract_type":    result.get("contract_type", "unknown"),
        "geo_zone":         result.get("geo_zone", "unknown"),
        "company_country":  (result.get("company_country") or "unknown").strip(),
        "industry_sector":  sector,
        "language_required": language,
    }


def _parse_extraction_result(raw: str) -> dict:
    """Parse extraction-only JSON response (no score, no reason)."""
    result = json.loads(raw)

    sector = (result.get("industry_sector") or "other").strip().lower()
    if sector not in _VALID_SECTORS:
        print(f"  ⚠️  Unknown industry_sector {sector!r} — coercing to 'other'")
        sector = "other"

    language = (result.get("language_required") or "unknown").strip().lower()
    if language not in _VALID_LANGUAGES:
        print(f"  ⚠️  Unknown language_required {language!r} — coercing to 'unknown'")
        language = "unknown"

    return {
        "company_country":  (result.get("company_country") or "unknown").strip(),
        "industry_sector":  sector,
        "language_required": language,
        "work_mode":        result.get("work_mode", "unknown"),
        "geo_zone":         result.get("geo_zone", "unknown"),
        "company_size":     result.get("company_size", "unknown"),
        "contract_type":    result.get("contract_type", "unknown"),
        "summary":          result.get("summary") or "Description non disponible — consulter l'offre directement.",
    }


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def score_job(job: dict, scoring_context=None) -> dict | None:
    """
    Scores a job posting using the Groq fallback chain.

    Args:
        job: dict with keys title, company, location, base_location, description.
        scoring_context: profile-specific instructions — either a plain string or
            a SearchProfile object (its .scoring_context attribute is used).

    Returns a dict with keys: score, reason, summary, work_mode, company_size,
    contract_type, geo_zone, scored_by (model name that succeeded).
    Returns None only when all models are exhausted — caller should save_unscored.
    Raises on non-quota errors (bad JSON, auth failure, etc.).
    """
    if scoring_context is None:
        scoring_context = ""
    elif hasattr(scoring_context, "scoring_context"):
        scoring_context = scoring_context.scoring_context

    effective_system = (
        f"## Profile Context\n{scoring_context.strip()}\n\n---\n\n{SYSTEM_PROMPT}"
        if scoring_context
        else SYSTEM_PROMPT
    )

    base_loc = job.get("base_location") or ""
    prompt = (
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Base location: {base_loc}\n"
        f"Description: {job.get('description', '')}"
    )
    messages = [
        {"role": "system", "content": effective_system},
        {"role": "user",   "content": prompt},
    ]

    try:
        raw, model = _call_groq_fallback_chain(messages)
        result = _parse_result(raw)
        result["scored_by"] = model
        return result
    except Exception as e:
        msg = str(e)
        if "All Groq models exhausted" in msg:
            print(f"  ❌  {msg}")
            return None
        print(f"  ❌  score_job failed for '{job.get('title', '')}': {e}")
        raise


def extract_job_fields(job: JobPosting) -> JobPosting | None:
    """
    Profile-independent extraction of structured fields from a job description.

    Skips jobs where extracted_at is already set (idempotent).
    Tries Groq extraction models first, falls back to DeepSeek V3 as last resort.
    Returns the updated JobPosting with fields populated, or None on exhaustion.
    """
    if job.extracted_at is not None:
        return job

    base_loc = job.base_location or ""
    prompt = (
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location}\n"
        f"Base location: {base_loc}\n"
        f"Description: {job.description or ''}"
    )
    messages = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user",   "content": prompt},
    ]

    # Try Groq extraction models first
    raw = None
    model = None
    try:
        raw, model = _call_groq_fallback_chain(messages, models=EXTRACTION_MODELS)
    except Exception as e:
        if "All Groq models exhausted" in str(e):
            print(f"  ⚠️  Groq extraction exhausted — falling back to DeepSeek")
        else:
            print(f"  ❌  extract_job_fields failed (Groq) for '{job.title}': {e}")
            raise

    # Fall back to DeepSeek if Groq chain exhausted
    if raw is None:
        try:
            raw = _call_deepseek(messages)
            model = "deepseek-chat"
            time.sleep(1)  # rate-limit safety
        except Exception as e:
            print(f"  ❌  DeepSeek extraction failed for '{job.title}': {e}")
            return None

    try:
        result = _parse_extraction_result(raw)

        job.company_country   = result["company_country"]
        job.industry_sector   = result["industry_sector"]
        job.language_required = result["language_required"]
        job.work_mode         = result["work_mode"]
        job.geo_zone          = result["geo_zone"]
        job.company_size      = result["company_size"]
        job.contract_type     = result["contract_type"]
        job.summary           = result["summary"]
        job.extracted_at      = datetime.now()
        job.extracted_by      = model

        print(f"  ✅ [{result['geo_zone']}] {job.company_country} / {result['industry_sector']} / {result['language_required']} (model: {model})")
        return job
    except Exception as e:
        print(f"  ❌  extract_job_fields parse failed for '{job.title}': {e}")
        return None


# ---------------------------------------------------------------------------
# Tiered evaluation
# ---------------------------------------------------------------------------

_EVAL_PASSTHROUGH_KEYS = [
    "summary", "work_mode", "company_size", "contract_type",
    "geo_zone", "company_country", "industry_sector", "language_required",
]


def _evaluation_result(score: int, reason: str, scored_by: str, job: JobPosting,
                       profile) -> dict:
    """Build a result dict from evaluation outcome + job's pre-extracted fields."""
    d = {
        "score": score,
        "reason": reason,
        "scored_by": scored_by,
    }
    for k in _EVAL_PASSTHROUGH_KEYS:
        val = getattr(job, k, None)
        if k == "industry_sector":
            d[k] = (val or "other").strip().lower()
        elif k == "language_required":
            d[k] = (val or "unknown").strip().lower()
        elif k in ("company_country",):
            d[k] = (val or "unknown").strip()
        else:
            d[k] = val or "unknown"
    return d


def evaluate_for_profile(job: JobPosting, profile) -> dict | None:
    """
    Tiered evaluation: deterministic filters first, then small-model LLM.

    Tier 0 (first match wins):
      1. language_required in profile.excluded_languages → score=1
      2. industry_sector in profile.excluded_sectors    → score=1
      3. country not in profile.allowed_countries       → score=2
      4. work_mode not in profile.allowed_work_modes    → score=1

    Tier 1: small-model LLM (llama-3.1-8b-instant → llama-4-scout).

    Returns a dict compatible with storage.save_scored(), or None on exhaustion.
    """
    language_required = (job.language_required or "unknown").strip().lower()
    industry_sector   = (job.industry_sector or "other").strip().lower()
    company_country   = (job.company_country or "unknown").strip()
    work_mode         = (job.work_mode or "unknown").strip().lower()

    # ── Tier 0: deterministic filters —─────────────────────────────────────
    # 1. Language exclusion
    if language_required in (profile.excluded_languages or []):
        return _evaluation_result(1, f"filtered: language ({language_required})",
                                  "tier_0", job, profile)

    # 2. Sector exclusion
    if industry_sector in (profile.excluded_sectors or []):
        return _evaluation_result(1, f"filtered: sector ({industry_sector})",
                                  "tier_0", job, profile)

    # 3. Country allowlist
    allowed = profile.allowed_countries
    if allowed is not None and company_country != "unknown" and company_country not in allowed:
        return _evaluation_result(2, f"filtered: country ({company_country})",
                                  "tier_0", job, profile)

    # 4. Work mode filter
    allowed_modes = profile.allowed_work_modes or []
    if allowed_modes and work_mode not in allowed_modes:
        return _evaluation_result(1, f"filtered: work_mode ({work_mode})",
                                  "tier_0", job, profile)

    # ── Tier 1: LLM evaluation with small model ────────────────────────────
    profile_context = profile.scoring_context if hasattr(profile, "scoring_context") else ""

    prompt = (
        f"Profile context:\n{profile_context.strip()}\n\n"
        f"Job (already classified):\n"
        f"- Title: {job.title}\n"
        f"- Company: {job.company}\n"
        f"- Country: {company_country}\n"
        f"- Sector: {industry_sector}\n"
        f"- Language: {language_required}\n"
        f"- Work mode: {work_mode}\n"
        f"- Summary: {job.summary or ''}\n\n"
        f"Description:\n{(job.description or '')[:1500]}\n\n"
        f"Return ONLY a JSON object:\n"
        f"{{\n"
        f'  "score": <1-10 integer>,\n'
        f'  "reason": "<one short sentence on why this score>"\n'
        f"}}"
    )

    messages = [
        {"role": "system", "content": EVALUATION_PROMPT},
        {"role": "user", "content": prompt},
    ]

    try:
        raw, model = _call_groq_fallback_chain(messages, models=EVALUATION_MODELS)
        result = json.loads(raw)
        score = int(result["score"])
        reason = result.get("reason", "")
        return _evaluation_result(score, reason, model, job, profile)
    except Exception as e:
        msg = str(e)
        if "All Groq models exhausted" in msg:
            print(f"  ❌  {msg}")
            return None
        print(f"  ❌  evaluate_for_profile failed for '{job.title}': {e}")
        raise


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_job = {
        "title": "Senior PM DeFi",
        "company": "Aave",
        "location": "Remote",
        "description": "Lead DeFi product strategy across lending and borrowing protocols. Aave is a Series B DeFi protocol with 80 employees.",
    }

    result = score_job(test_job)
    if result is None:
        print("Scoring failed — all models exhausted")
    else:
        print(f"Model          : {result['scored_by']}")
        print(f"Score          : {result['score']}/10")
        print(f"Reason         : {result['reason']}")
        print(f"Summary        : {result['summary']}")
        print(f"Work mode      : {result['work_mode']}")
        print(f"Company size   : {result['company_size']}")
        print(f"Contract type  : {result['contract_type']}")
        print(f"Geo zone       : {result['geo_zone']}")
        print(f"Company country: {result['company_country']}")
        print(f"Industry sector: {result['industry_sector']}")
        print(f"Language req'd : {result['language_required']}")
