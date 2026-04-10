import json
import os
import time

import httpx
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GROQ_API_KEY") or os.getenv("GROQ_APIKEY")
client = Groq(api_key=api_key, max_retries=0)

GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

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
# Groq
# ---------------------------------------------------------------------------

# Run-level flag: set to True when Groq's daily token quota is exhausted.
# Avoids wasting 62+ seconds of retries on a non-recoverable daily limit.
_groq_daily_limit_hit = False


def _is_groq_daily_limit(error_str: str) -> bool:
    """True when the 429 is a tokens-per-day quota, not a per-minute rate limit."""
    return "per day" in error_str or "tokens per day" in error_str or "TPD" in error_str


def _call_groq_with_retry(messages: list, max_retries: int = 5) -> str:
    """
    Returns raw LLM response text.
    Raises immediately on daily quota exhaustion (TPD).
    Retries with exponential backoff on per-minute rate limits (RPM).
    """
    global _groq_daily_limit_hit

    if _groq_daily_limit_hit:
        raise Exception("Groq daily token limit épuisé pour ce run — Gemini only")

    had_429 = False
    for attempt in range(max_retries):
        try:
            result = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=300,
            )
            if had_429:
                time.sleep(10)  # cooldown post-retry pour protéger le job suivant
            return result.choices[0].message.content
        except Exception as e:
            if "429" in str(e):
                if _is_groq_daily_limit(str(e)):
                    _groq_daily_limit_hit = True
                    raise Exception(
                        f"Groq daily token limit (TPD) épuisé — bascule sur Gemini"
                    ) from e
                # RPM — exponential backoff
                had_429 = True
                wait = 2 ** (attempt + 1)
                print(f"  ⚠️  Groq RPM 429 — attente {wait}s (tentative {attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                raise

    _groq_daily_limit_hit = True
    raise Exception(f"Groq rate limit persistant après {max_retries} tentatives")


# ---------------------------------------------------------------------------
# Gemini fallback
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str, max_retries: int = 3) -> str | None:
    """
    Calls Gemini Flash via REST (httpx, no extra SDK needed).
    Retries with backoff on 429.
    Returns raw JSON string on success, None if key absent or all retries fail.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("  ⚠️  GEMINI_API_KEY non configurée — pas de fallback Gemini")
        return None

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
            "maxOutputTokens": 512,
            # Disable thinking: gemini-2.5-flash is a thinking model — without this,
            # thinking tokens consume the maxOutputTokens budget before any JSON is emitted.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    for attempt in range(max_retries):
        try:
            r = httpx.post(GEMINI_URL, json=payload, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
            candidate = data["candidates"][0]
            finish = candidate.get("finishReason", "")
            parts = candidate.get("content", {}).get("parts", [])
            # Filter out thinking parts (thought=True); take last text part
            text_parts = [p["text"] for p in parts if "text" in p and not p.get("thought", False)]
            if not text_parts:
                raise ValueError(f"Gemini returned no text parts (finishReason={finish})")
            return text_parts[-1]
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                wait = 15 * (2 ** attempt)  # 15s, 30s, 60s
                print(f"  ⚠️  Gemini 429 — attente {wait}s (tentative {attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                print(f"  ⚠️  Gemini HTTP {e.response.status_code}: {e}")
                return None
        except Exception as e:
            print(f"  ⚠️  Gemini erreur: {e}")
            return None

    print(f"  ⚠️  Gemini rate limit persistant après {max_retries} tentatives")
    return None


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

def score_job(job: dict) -> dict | None:
    """
    Scores a job posting.

    Returns a dict with keys: score, reason, summary, work_mode, company_size,
    contract_type, geo_zone, scored_by ("groq" | "gemini").
    Returns None if all backends fail — caller should save_unscored and skip.
    """
    base_loc = job.get("base_location") or ""
    prompt = (
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Base location: {base_loc}\n"
        f"Description: {job.get('description', '')}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ]

    # ── Groq first ────────────────────────────────────────────────────────
    try:
        raw = _call_groq_with_retry(messages)
        result = _parse_result(raw)
        result["scored_by"] = "groq"
        return result
    except Exception as groq_err:
        print(f"  ⚠️  Groq failed: {groq_err} — essai Gemini Flash")

    # ── Gemini fallback ───────────────────────────────────────────────────
    raw = _call_gemini(prompt)
    if raw is not None:
        try:
            result = _parse_result(raw)
            result["scored_by"] = "gemini"
            return result
        except Exception as parse_err:
            print(f"  ⚠️  Gemini parse error: {parse_err}")

    print(f"  ❌  score_job failed for '{job.get('title', '')}' — job exclu du digest")
    return None


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
        print("Scoring failed")
    else:
        print(f"Model        : {result['scored_by']}")
        print(f"Score        : {result['score']}/10")
        print(f"Reason       : {result['reason']}")
        print(f"Summary      : {result['summary']}")
        print(f"Work mode    : {result['work_mode']}")
        print(f"Company size : {result['company_size']}")
        print(f"Contract type: {result['contract_type']}")
        print(f"Geo zone     : {result['geo_zone']}")
