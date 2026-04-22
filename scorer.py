import json
import os
import time

from groq import Groq
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GROQ_API_KEY") or os.getenv("GROQ_APIKEY")
client = Groq(api_key=api_key, max_retries=0)

FALLBACK_MODELS = [
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "groq/compound",
    "llama-3.1-8b-instant",
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
- "global_remote" : ONLY when there is explicit positive evidence — description or title says "anywhere", "worldwide", "no timezone restriction", "fully async", "open to all locations", "global team" — AND there is no specific country requirement
- "unknown"       : Location is "Remote" OR "Worldwide" with no base location and no geographic clues in description — USE THIS as the safe default when in doubt. Do NOT assign global_remote unless the evidence is explicit.

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

Respond with JSON only:
{
  "score": <int 1-10>,
  "reason": "<one sentence>",
  "summary": "<2-3 sentences>",
  "work_mode": "<remote|hybrid|on-site|unknown>",
  "company_size": "<startup|scaleup|sme|large|unknown>",
  "contract_type": "<permanent|freelance|contract|internship|unknown>",
  "geo_zone": "<europe|us_only|global_remote|apac|latam|unknown>"
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
            if "404" in err or ("400" in err and "decommissioned" in err):
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
            else:
                raise  # auth error, network error → propagate immediately

    _exhausted_models.add(model)
    raise Exception(f"{model} rate limit persistant après {max_retries} tentatives")


# ---------------------------------------------------------------------------
# Multi-model fallback chain
# ---------------------------------------------------------------------------

def _call_groq_fallback_chain(messages: list) -> tuple[str, str]:
    """
    Try each model in FALLBACK_MODELS order.
    Falls through to the next model on quota/rate exhaustion.
    Re-raises immediately on non-quota errors (auth, network, bad request).
    Returns (raw_json_text, model_name) on success.
    """
    last_err: Exception | None = None
    for model in FALLBACK_MODELS:
        if model in _exhausted_models:
            continue
        try:
            raw = _call_groq(messages, model)
            return raw, model
        except Exception as e:
            err = str(e)
            if any(x in err for x in ("exhausted", "rate limit persistant", "not available")):
                print(f"  ⚠️  {model} unavailable/exhausted — essai modèle suivant")
                last_err = e
            else:
                raise  # non-quota error: don't fall through

    raise Exception("All Groq models exhausted for today. Retry tomorrow.") from last_err


# ---------------------------------------------------------------------------
# Shared parser
# ---------------------------------------------------------------------------

def _parse_result(raw: str) -> dict:
    result = json.loads(raw)
    return {
        "score":         int(result["score"]),
        "reason":        result["reason"],
        "summary":       result.get("summary", "Description non disponible — consulter l'offre directement."),
        "work_mode":     result.get("work_mode", "unknown"),
        "company_size":  result.get("company_size", "unknown"),
        "contract_type": result.get("contract_type", "unknown"),
        "geo_zone":      result.get("geo_zone", "unknown"),
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
        print(f"Model        : {result['scored_by']}")
        print(f"Score        : {result['score']}/10")
        print(f"Reason       : {result['reason']}")
        print(f"Summary      : {result['summary']}")
        print(f"Work mode    : {result['work_mode']}")
        print(f"Company size : {result['company_size']}")
        print(f"Contract type: {result['contract_type']}")
        print(f"Geo zone     : {result['geo_zone']}")
